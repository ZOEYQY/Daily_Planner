"""Habit check-in (打卡) — a tiny SQLite-backed API mounted under the calendar.

The calendar itself is per-browser localStorage; habit data instead lives in a
single SQLite file on whatever machine runs the server, so the same list + streaks
show up from the phone, another laptop, etc. (as long as they reach this server).

Mounted at /calendar/api by the combined app.py, and at /api by Calendar/app.py.
Call habits.init(db_path) once before registering the blueprint.
"""
import datetime as dt
import os
import sqlite3

from flask import Blueprint, jsonify, request

bp = Blueprint("habits", __name__)

_DB_PATH = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS habits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    emoji      TEXT    NOT NULL DEFAULT '',
    color      TEXT    NOT NULL DEFAULT '#3a9163',
    sort       INTEGER NOT NULL DEFAULT 0,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS checkins (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    date     TEXT    NOT NULL,
    note     TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (habit_id, date)
);
"""


def init(db_path):
    global _DB_PATH
    _DB_PATH = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.executescript(_SCHEMA)
    con.commit()
    con.close()


def _db():
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _valid_date(s):
    try:
        dt.date.fromisoformat(s)
        return True
    except (ValueError, TypeError):
        return False


@bp.get("/habits")
def list_habits():
    con = _db()
    habits = [
        dict(r) for r in con.execute(
            "SELECT id, name, emoji, color, sort FROM habits WHERE archived = 0 ORDER BY sort, id")
    ]
    since = (dt.date.today() - dt.timedelta(days=210)).isoformat()
    checkins = {}
    for r in con.execute("SELECT habit_id, date FROM checkins WHERE date >= ? ORDER BY date", (since,)):
        checkins.setdefault(str(r["habit_id"]), []).append(r["date"])
    con.close()
    return jsonify({"habits": habits, "checkins": checkins, "today": dt.date.today().isoformat()})


@bp.post("/habits")
def create_habit():
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    con = _db()
    nxt = con.execute("SELECT COALESCE(MAX(sort), -1) + 1 AS s FROM habits").fetchone()["s"]
    cur = con.execute(
        "INSERT INTO habits (name, emoji, color, sort) VALUES (?, ?, ?, ?)",
        (name[:60], (d.get("emoji") or "")[:12], (d.get("color") or "#3a9163")[:9], nxt))
    con.commit()
    hid = cur.lastrowid
    con.close()
    return jsonify({"id": hid})


@bp.patch("/habits/<int:hid>")
def update_habit(hid):
    d = request.get_json(silent=True) or {}
    fields = {}
    for k in ("name", "emoji", "color", "sort", "archived"):
        if k in d:
            fields[k] = d[k]
    if not fields:
        return jsonify({"ok": True})
    sets = ", ".join(f"{k} = ?" for k in fields)
    con = _db()
    con.execute(f"UPDATE habits SET {sets} WHERE id = ?", (*fields.values(), hid))
    con.commit()
    con.close()
    return jsonify({"ok": True})


@bp.delete("/habits/<int:hid>")
def delete_habit(hid):
    con = _db()
    con.execute("DELETE FROM habits WHERE id = ?", (hid,))
    con.commit()
    con.close()
    return jsonify({"ok": True})


@bp.post("/checkins")
def toggle_checkin():
    d = request.get_json(silent=True) or {}
    hid = d.get("habit_id")
    date = (d.get("date") or "").strip()
    if not isinstance(hid, int) or not _valid_date(date):
        return jsonify({"error": "bad input"}), 400
    con = _db()
    exists = con.execute(
        "SELECT 1 FROM checkins WHERE habit_id = ? AND date = ?", (hid, date)).fetchone()
    if exists:
        # removing is always allowed — lets you undo an accidental check-in
        con.execute("DELETE FROM checkins WHERE habit_id = ? AND date = ?", (hid, date))
        con.commit()
        con.close()
        return jsonify({"on": False})
    # adding is TODAY ONLY — no back-filling, so a streak is honest
    if date != dt.date.today().isoformat():
        con.close()
        return jsonify({"error": "只能打今天的卡 · today only"}), 403
    con.execute("INSERT INTO checkins (habit_id, date) VALUES (?, ?)", (hid, date))
    con.commit()
    con.close()
    return jsonify({"on": True})

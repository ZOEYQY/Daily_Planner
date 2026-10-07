"""Habit check-in (打卡) — a tiny SQLite-backed API mounted under the calendar.

The calendar itself is per-browser localStorage; habit data instead lives in a
single SQLite file on whatever machine runs the server, so the same list + streaks
show up from the phone, another laptop, etc. (as long as they reach this server).

Mounted at /calendar/api by the combined app.py, and at /api by Calendar/app.py.
Call habits.init(db_path) once before registering the blueprint.

Each habit belongs to the logged-in Finance profile (session["auth_profile"]),
so profiles never see each other's habits. Standalone Calendar/app.py has no
login, so its habits all share the empty owner "".
"""
import datetime as dt
import os
import sqlite3

from flask import Blueprint, jsonify, request, session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from Finance import database as finance_database
except ImportError:
    finance_database = None

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
    if os.environ.get("RENDER"):
        if not finance_database:
            raise RuntimeError("Shared Finance PostgreSQL module is unavailable on Render.")
        finance_database.require_postgres()
        _DB_PATH = None
        return
    if finance_database and finance_database.configured():
        _DB_PATH = None
        finance_database._engine(finance_database.database_url())
        return
    _DB_PATH = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.executescript(_SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(habits)")}
    if "owner" not in cols:
        con.execute("ALTER TABLE habits ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
    con.commit()
    con.close()


def _db():
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _postgres_session():
    return Session(finance_database._engine(finance_database.database_url()))


def _calendar_tables():
    return finance_database.calendar_tables()


@bp.get("/calendar-state")
def get_calendar_state():
    if not _owner() or not finance_database or not finance_database.configured():
        return jsonify({"error": "authenticated PostgreSQL storage required"}), 401
    if request.args.get("profile_id") != _owner():
        return jsonify({"error": "Profile session changed; reload Calendar"}), 403
    from Finance.database import CalendarState

    with _postgres_session() as db:
        row = db.get(CalendarState, _owner())
        return jsonify({"state": _calendar_tables().load_state(db, row), "version": row.version if row else 0})


@bp.put("/calendar-state")
def put_calendar_state():
    if not _owner() or not finance_database or not finance_database.configured():
        return jsonify({"error": "authenticated PostgreSQL storage required"}), 401
    data = request.get_json(silent=True) or {}
    state = data.get("state")
    expected_version = data.get("version")
    if data.get("profile_id") != _owner():
        return jsonify({"error": "Profile session changed; reload Calendar"}), 403
    if not isinstance(state, dict) or not isinstance(expected_version, int) or expected_version < 0:
        return jsonify({"error": "invalid calendar state"}), 400
    from Finance.database import CalendarState

    with _postgres_session() as db:
        row = db.scalar(select(CalendarState).where(CalendarState.profile_id == _owner()).with_for_update())
        if row and row.version != expected_version:
            return jsonify({"error": "calendar state changed on another device", "version": row.version}), 409
        if not row and expected_version != 0:
            return jsonify({"error": "calendar state changed on another device", "version": 0}), 409
        try:
            if row:
                row.version += 1
            else:
                row = CalendarState(profile_id=_owner(), payload={}, version=1)
                db.add(row)
                db.flush()
            _calendar_tables().store_state(db, row, state)
            db.commit()
        except IntegrityError:
            db.rollback()
            return jsonify({"error": "calendar state was created on another device", "version": 1}), 409
        return jsonify({"version": row.version})


def _merge_calendar_import(current, incoming):
    merged = {**current, **{key: value for key, value in incoming.items() if key not in {
        "categories", "tasks", "events", "specialDays", "customFieldDefs",
    }}}
    for key in ("categories", "tasks", "events", "specialDays", "customFieldDefs"):
        old_items = current.get(key, [])
        new_items = incoming.get(key, [])
        if not isinstance(old_items, list) or not isinstance(new_items, list):
            continue
        by_id = {item["id"]: item for item in old_items if isinstance(item, dict) and item.get("id")}
        for item in new_items:
            if isinstance(item, dict) and item.get("id"):
                by_id[item["id"]] = {**by_id.get(item["id"], {}), **item}
        merged[key] = list(by_id.values())
    return merged


@bp.post("/calendar-state/import")
def import_calendar_state():
    if not _owner() or not finance_database or not finance_database.configured():
        return jsonify({"error": "authenticated PostgreSQL storage required"}), 401
    data = request.get_json(silent=True) or {}
    state = data.get("state")
    source_id = data.get("source_id")
    if data.get("profile_id") != _owner():
        return jsonify({"error": "Profile session changed; reload Calendar"}), 403
    if not isinstance(state, dict) or not isinstance(source_id, str) or not source_id or len(source_id) > 128:
        return jsonify({"error": "invalid calendar import"}), 400
    from Finance.database import CalendarImport, CalendarState

    with _postgres_session() as db:
        tables = _calendar_tables()
        existing_import = db.get(CalendarImport, (_owner(), source_id))
        if existing_import:
            row = db.get(CalendarState, _owner())
            return jsonify({"state": tables.load_state(db, row), "version": row.version if row else 0, "imported": False})
        row = db.scalar(select(CalendarState).where(CalendarState.profile_id == _owner()).with_for_update())
        try:
            if row:
                merged = _merge_calendar_import(tables.load_state(db, row), state)
                row.version += 1
            else:
                merged = state
                row = CalendarState(profile_id=_owner(), payload={}, version=1)
                db.add(row)
                db.flush()
            tables.store_state(db, row, merged)
            db.add(CalendarImport(profile_id=_owner(), source_id=source_id))
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.get(CalendarState, _owner())
            return jsonify({"state": tables.load_state(db, row), "version": row.version if row else 0, "imported": False})
        return jsonify({"state": merged, "version": row.version, "imported": True})


def _owner():
    return session.get("auth_profile") or ""


def _owns(con, hid):
    return con.execute(
        "SELECT 1 FROM habits WHERE id = ? AND owner = ?", (hid, _owner())).fetchone() is not None


def _valid_date(s):
    try:
        dt.date.fromisoformat(s)
        return True
    except (ValueError, TypeError):
        return False


@bp.get("/habits")
def list_habits():
    if _DB_PATH is None:
        from Finance.database import Habit, HabitCheckin

        with _postgres_session() as db:
            owner = _owner()
            habits = db.scalars(
                select(Habit).where(Habit.owner == owner, Habit.archived.is_(False)).order_by(Habit.sort, Habit.id)
            ).all()
            since = (dt.date.today() - dt.timedelta(days=210)).isoformat()
            checkins = {}
            rows = db.execute(
                select(HabitCheckin.habit_id, HabitCheckin.date)
                .join(Habit, Habit.id == HabitCheckin.habit_id)
                .where(Habit.owner == owner, HabitCheckin.date >= since)
                .order_by(HabitCheckin.date)
            )
            for habit_id, checkin_date in rows:
                checkins.setdefault(str(habit_id), []).append(checkin_date)
            return jsonify({
                "habits": [{"id": h.id, "name": h.name, "emoji": h.emoji, "color": h.color, "sort": h.sort} for h in habits],
                "checkins": checkins,
                "today": dt.date.today().isoformat(),
            })

    con = _db()
    owner = _owner()
    if owner:
        # Habits made before profiles had owners go to the first profile that looks.
        con.execute("UPDATE habits SET owner = ? WHERE owner = ''", (owner,))
        con.commit()
    habits = [
        dict(r) for r in con.execute(
            "SELECT id, name, emoji, color, sort FROM habits WHERE archived = 0 AND owner = ? "
            "ORDER BY sort, id", (owner,))
    ]
    since = (dt.date.today() - dt.timedelta(days=210)).isoformat()
    checkins = {}
    for r in con.execute(
            "SELECT c.habit_id, c.date FROM checkins c JOIN habits h ON h.id = c.habit_id "
            "WHERE h.owner = ? AND c.date >= ? ORDER BY c.date", (owner, since)):
        checkins.setdefault(str(r["habit_id"]), []).append(r["date"])
    con.close()
    return jsonify({"habits": habits, "checkins": checkins, "today": dt.date.today().isoformat()})


@bp.post("/habits")
def create_habit():
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    if _DB_PATH is None:
        from Finance.database import Habit

        with _postgres_session() as db:
            sort = db.scalar(select(Habit.sort).where(Habit.owner == _owner()).order_by(Habit.sort.desc()).limit(1))
            habit = Habit(
                owner=_owner(), name=name[:60], emoji=(d.get("emoji") or "")[:12],
                color=(d.get("color") or "#3a9163")[:9], sort=(sort if sort is not None else -1) + 1,
            )
            db.add(habit)
            db.commit()
            return jsonify({"id": habit.id})

    con = _db()
    nxt = con.execute("SELECT COALESCE(MAX(sort), -1) + 1 AS s FROM habits WHERE owner = ?",
                      (_owner(),)).fetchone()["s"]
    cur = con.execute(
        "INSERT INTO habits (name, emoji, color, sort, owner) VALUES (?, ?, ?, ?, ?)",
        (name[:60], (d.get("emoji") or "")[:12], (d.get("color") or "#3a9163")[:9], nxt, _owner()))
    con.commit()
    hid = cur.lastrowid
    con.close()
    return jsonify({"id": hid})


@bp.patch("/habits/<int:hid>")
def update_habit(hid):
    d = request.get_json(silent=True) or {}
    fields = {}
    limits = {"name": 60, "emoji": 12, "color": 9}
    for k in ("name", "emoji", "color", "sort", "archived"):
        if k not in d:
            continue
        value = d[k]
        if k in limits:
            if not isinstance(value, str) or (k == "name" and not value.strip()):
                return jsonify({"error": f"invalid {k}"}), 400
            value = value.strip()[:limits[k]] if k == "name" else value[:limits[k]]
        elif k == "sort":
            if not isinstance(value, int) or isinstance(value, bool):
                return jsonify({"error": "invalid sort"}), 400
        elif not isinstance(value, bool):
            return jsonify({"error": "invalid archived"}), 400
        fields[k] = value
    if not fields:
        return jsonify({"ok": True})
    if _DB_PATH is None:
        from Finance.database import Habit

        with _postgres_session() as db:
            habit = db.scalar(select(Habit).where(Habit.id == hid, Habit.owner == _owner()))
            if habit:
                for key, value in fields.items():
                    setattr(habit, key, value)
                db.commit()
            return jsonify({"ok": True})

    sets = ", ".join(f"{k} = ?" for k in fields)
    con = _db()
    con.execute(f"UPDATE habits SET {sets} WHERE id = ? AND owner = ?", (*fields.values(), hid, _owner()))
    con.commit()
    con.close()
    return jsonify({"ok": True})


@bp.delete("/habits/<int:hid>")
def delete_habit(hid):
    if _DB_PATH is None:
        from Finance.database import Habit

        with _postgres_session() as db:
            habit = db.scalar(select(Habit).where(Habit.id == hid, Habit.owner == _owner()))
            if habit:
                db.delete(habit)
                db.commit()
            return jsonify({"ok": True})

    con = _db()
    con.execute("DELETE FROM habits WHERE id = ? AND owner = ?", (hid, _owner()))
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
    if _DB_PATH is None:
        from Finance.database import Habit, HabitCheckin

        with _postgres_session() as db:
            habit = db.scalar(select(Habit).where(Habit.id == hid, Habit.owner == _owner()))
            if not habit:
                return jsonify({"error": "not found"}), 404
            checkin = db.get(HabitCheckin, (hid, date))
            if checkin:
                db.delete(checkin)
                db.commit()
                return jsonify({"on": False})
            if date != dt.date.today().isoformat():
                return jsonify({"error": "只能打今天的卡 · today only"}), 403
            db.add(HabitCheckin(habit_id=hid, date=date))
            db.commit()
            return jsonify({"on": True})

    con = _db()
    if not _owns(con, hid):
        con.close()
        return jsonify({"error": "not found"}), 404
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

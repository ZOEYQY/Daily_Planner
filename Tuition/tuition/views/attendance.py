import datetime as dt
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from ..auth import login_required
from ..db import query, execute, log
from ..i18n import t
from ..engine import (this_month, add_months, month_bounds, class_scheduled_dates,
                      attendance_stats, enrollment_overlaps_month,
                      STATUS_KEYS, NO_SHOW, MAKEUP_STATUSES)

bp = Blueprint("attendance", __name__)

STATUSES = STATUS_KEYS
ICON = {"present": "/", "absent": "✗", "": ""}


def roster(class_id, on_date):
    """Students whose enrollment covers `on_date`."""
    return query(
        """SELECT s.id, s.full_name, s.status AS student_status
             FROM enrollments e JOIN students s ON s.id = e.student_id
            WHERE e.class_id = ?
              AND (e.start_date IS NULL OR e.start_date <= ?)
              AND (e.end_date IS NULL OR e.end_date = '' OR e.end_date >= ?)
            ORDER BY s.full_name""", (class_id, on_date, on_date))


def month_roster(class_id, ym):
    first, last = month_bounds(ym)
    return query(
        """SELECT DISTINCT s.id, s.full_name, s.status AS student_status
             FROM enrollments e JOIN students s ON s.id = e.student_id
            WHERE e.class_id = ?
              AND (e.start_date IS NULL OR e.start_date <= ?)
              AND (e.end_date IS NULL OR e.end_date = '' OR e.end_date >= ?)
            ORDER BY s.full_name""", (class_id, last, first))


def build_matrix(class_id, ym):
    cls = query("SELECT * FROM classes WHERE id = ?", (class_id,), one=True)
    if not cls:
        return None
    first, last = month_bounds(ym)
    recorded = query(
        "SELECT DISTINCT date FROM attendance WHERE class_id = ? AND date BETWEEN ? AND ? ORDER BY date",
        (class_id, first, last))
    dates = sorted(set([r["date"] for r in recorded]) | set(class_scheduled_dates(class_id, ym)))
    students = month_roster(class_id, ym)
    cells, makeup, plan, final = {}, {}, {}, {}
    for r in query(
            """SELECT student_id, date, status, makeup, makeup_plan, makeup_final
                 FROM attendance WHERE class_id = ? AND date BETWEEN ? AND ?""",
            (class_id, first, last)):
        k = (r["student_id"], r["date"])
        cells[k] = r["status"]
        if r["makeup"]:
            makeup[k] = r["makeup"]
        if r["makeup_plan"]:
            plan[k] = r["makeup_plan"]
        if r["makeup_final"]:
            final[k] = r["makeup_final"]
    return {"class": cls, "dates": dates, "students": students, "cells": cells,
            "makeup": makeup, "plan": plan, "final": final,
            "stats": attendance_stats(class_id=class_id, date_from=first, date_to=last)}


def _upsert_attendance(sid, cid, date, status, mk, plan, final):
    existing = query(
        "SELECT id FROM attendance WHERE student_id=? AND class_id=? AND date=?",
        (sid, cid, date), one=True)
    if status == "":
        if existing:
            execute("DELETE FROM attendance WHERE id = ?", (existing["id"],))
        return
    if status not in STATUSES:
        return
    if existing:
        execute(
            """UPDATE attendance SET status=?, makeup=?, makeup_plan=?, makeup_final=?,
                 updated_at=datetime('now') WHERE id=?""",
            (status, mk, plan, final, existing["id"]))
    else:
        execute(
            """INSERT INTO attendance (student_id, class_id, date, status, makeup, makeup_plan, makeup_final)
               VALUES (?,?,?,?,?,?,?)""",
            (sid, cid, date, status, mk, plan, final))


@bp.route("/today")
@bp.route("/today/<date>")
@login_required
def today(date=None):
    try:
        dobj = dt.date.fromisoformat(date) if date else dt.date.today()
    except ValueError:
        dobj = dt.date.today()
    date = dobj.isoformat()
    wd = dobj.weekday()

    ids = {r["class_id"] for r in
           query("SELECT class_id FROM class_schedule WHERE weekday = ?", (wd,))}
    ids |= {r["class_id"] for r in
            query("SELECT DISTINCT class_id FROM attendance WHERE date = ?", (date,))}
    classes = [c for c in query("SELECT * FROM classes WHERE status='active' ORDER BY name")
               if c["id"] in ids]

    blocks = []
    for c in classes:
        students = roster(c["id"], date)
        cur, plan, final = {}, {}, {}
        for r in query(
                """SELECT student_id, status, makeup_plan, makeup_final
                     FROM attendance WHERE class_id=? AND date=?""", (c["id"], date)):
            cur[r["student_id"]] = r["status"]
            if r["makeup_plan"]:
                plan[r["student_id"]] = r["makeup_plan"]
            if r["makeup_final"]:
                final[r["student_id"]] = r["makeup_final"]
        sch = query(
            "SELECT start_time, end_time FROM class_schedule WHERE class_id=? AND weekday=?",
            (c["id"], wd), one=True)
        blocks.append({"class": c, "students": students, "current": cur,
                       "plan": plan, "final": final, "sched": sch})

    return render_template(
        "attendance/today.html", date=date, dobj=dobj, blocks=blocks, statuses=STATUS_KEYS,
        is_today=(date == dt.date.today().isoformat()),
        prev_day=(dobj - dt.timedelta(days=1)).isoformat(),
        next_day=(dobj + dt.timedelta(days=1)).isoformat())


@bp.route("/today/save", methods=["POST"])
@login_required
def save_today():
    date = request.form.get("date") or dt.date.today().isoformat()
    for key, val in request.form.items():
        if not key.startswith("st_"):
            continue
        try:
            _, cid, sid = key.split("_", 2)
            cid, sid = int(cid), int(sid)
        except ValueError:
            continue
        val = val.strip()
        mk, plan, final = _read_makeup(request.form, f"{cid}_{sid}", date, val)
        _upsert_attendance(sid, cid, date, val, mk, plan, final)
    log("attendance_today", date)
    flash(t("saved"), "ok")
    # "Save & Finance" button on a class block — jump to that class's finance.
    goto = request.form.get("goto") or ""
    if goto.startswith("finance:"):
        try:
            return redirect(url_for("classes.detail", cid=int(goto[8:]), tab="finance"))
        except ValueError:
            pass
    return redirect(url_for("attendance.today", date=date))


@bp.route("/")
@login_required
def index():
    classes = query("SELECT * FROM classes WHERE status='active' ORDER BY name")
    raw = request.args.get("class")
    ym = request.args.get("month") or this_month()
    show_all = raw == "all"
    class_id = None
    if not show_all:
        try:
            class_id = int(raw) if raw else (classes[0]["id"] if classes else None)
        except (TypeError, ValueError):
            class_id = classes[0]["id"] if classes else None

    if show_all:
        all_matrix = build_all_matrix(ym)
        matrix = None
    else:
        all_matrix = None
        matrix = build_matrix(class_id, ym) if class_id else None

    return render_template(
        "attendance/index.html", classes=classes, class_id=class_id, ym=ym,
        show_all=show_all, matrix=matrix, all_matrix=all_matrix,
        prev_month=add_months(ym, -1), next_month=add_months(ym, 1))


def _read_makeup(form, key_id, date, status):
    """(makeup, makeup_plan, makeup_final) for this cell, based on its status.
    key_id is a student id (matrix/session) or an enrollment id (all-students view)."""
    if status in MAKEUP_STATUSES:
        plan = form.get(f"mkplan_{key_id}_{date}", "").strip()
        final = form.get(f"mkfinal_{key_id}_{date}", "").strip()
        return "", plan, final
    return "", "", ""


def build_all_matrix(ym):
    """One grid: every active enrollment as a row, every class-date as a column."""
    first, last = month_bounds(ym)
    enr = query(
        """SELECT e.id AS eid, e.student_id, e.class_id, e.start_date, e.end_date,
                  s.full_name, s.avatar_color, c.name AS class_name
             FROM enrollments e
             JOIN students s ON s.id = e.student_id
             JOIN classes  c ON c.id = e.class_id
            WHERE e.status = 'active' AND c.status = 'active'
            ORDER BY s.full_name, c.name""")
    enr = [r for r in enr if enrollment_overlaps_month(r["start_date"], r["end_date"], ym)]

    class_dates = {}
    for cid in {r["class_id"] for r in enr}:
        ds = set(class_scheduled_dates(cid, ym))
        for row in query(
                "SELECT DISTINCT date FROM attendance WHERE class_id=? AND date BETWEEN ? AND ?",
                (cid, first, last)):
            ds.add(row["date"])
        class_dates[cid] = ds
    _dates = sorted(set().union(*class_dates.values())) if class_dates else []
    all_dates = [(d, dt.date.fromisoformat(d).weekday()) for d in _dates]

    cells, plan, final = {}, {}, {}
    for cid in class_dates:
        for r in query(
                """SELECT student_id, date, status, makeup_plan, makeup_final
                     FROM attendance WHERE class_id=? AND date BETWEEN ? AND ?""",
                (cid, first, last)):
            k = (r["student_id"], cid, r["date"])
            cells[k] = r["status"]
            if r["makeup_plan"]:
                plan[k] = r["makeup_plan"]
            if r["makeup_final"]:
                final[k] = r["makeup_final"]

    rows = [{
        "eid": r["eid"], "student_id": r["student_id"], "class_id": r["class_id"],
        "name": r["full_name"], "color": r["avatar_color"], "class_name": r["class_name"],
        "dates": class_dates.get(r["class_id"], set()),
    } for r in enr]
    return {"dates": all_dates, "rows": rows, "cells": cells, "plan": plan, "final": final}


@bp.route("/matrix/save", methods=["POST"])
@login_required
def save_matrix():
    class_id = request.form.get("class", type=int)
    ym = request.form.get("month") or this_month()
    changed = 0
    for key, val in request.form.items():
        if not key.startswith("cell_"):
            continue
        _, sid, date = key.split("_", 2)
        sid = int(sid)
        val = val.strip()
        mk, plan, final = _read_makeup(request.form, sid, date, val)
        existing = query(
            """SELECT id, status, makeup, makeup_plan, makeup_final
                 FROM attendance WHERE student_id=? AND class_id=? AND date=?""",
            (sid, class_id, date), one=True)
        if val == "":
            if existing:
                execute("DELETE FROM attendance WHERE id = ?", (existing["id"],))
                changed += 1
            continue
        if val not in STATUSES:
            continue
        if existing:
            if (existing["status"] != val or (existing["makeup"] or "") != mk
                    or (existing["makeup_plan"] or "") != plan
                    or (existing["makeup_final"] or "") != final):
                execute(
                    """UPDATE attendance SET status=?, makeup=?, makeup_plan=?, makeup_final=?,
                         updated_at=datetime('now') WHERE id=?""",
                    (val, mk, plan, final, existing["id"]))
                changed += 1
        else:
            execute(
                """INSERT INTO attendance (student_id, class_id, date, status, makeup, makeup_plan, makeup_final)
                   VALUES (?,?,?,?,?,?,?)""",
                (sid, class_id, date, val, mk, plan, final))
            changed += 1
    log("attendance_matrix", f"class {class_id} {ym}: {changed} changes")
    flash(t("saved"), "ok")
    # "Save & Finance" button — straight to this class's finance instead of back
    # to the matrix.
    if request.form.get("goto") == "finance" and class_id:
        return redirect(url_for("classes.detail", cid=class_id, tab="finance", month=ym))
    back = request.form.get("back")
    if back and (back.startswith("/attendance") or back.startswith("/classes/")):
        return redirect(back)
    return redirect(url_for("attendance.index", **{"class": class_id, "month": ym}))


@bp.route("/all/save", methods=["POST"])
@login_required
def save_all():
    ym = request.form.get("month") or this_month()
    enr_cache = {}
    changed = 0
    for key, val in request.form.items():
        if not key.startswith("cell_"):
            continue
        _, eid, date = key.split("_", 2)
        eid = int(eid)
        if eid not in enr_cache:
            enr_cache[eid] = query(
                "SELECT student_id, class_id FROM enrollments WHERE id = ?", (eid,), one=True)
        e = enr_cache[eid]
        if not e:
            continue
        sid, cid = e["student_id"], e["class_id"]
        val = val.strip()
        mk, plan, final = _read_makeup(request.form, eid, date, val)
        existing = query(
            """SELECT id, status, makeup_plan, makeup_final
                 FROM attendance WHERE student_id=? AND class_id=? AND date=?""",
            (sid, cid, date), one=True)
        if val == "":
            if existing:
                execute("DELETE FROM attendance WHERE id = ?", (existing["id"],))
                changed += 1
            continue
        if val not in STATUSES:
            continue
        if existing:
            if (existing["status"] != val or (existing["makeup_plan"] or "") != plan
                    or (existing["makeup_final"] or "") != final):
                execute(
                    """UPDATE attendance SET status=?, makeup=?, makeup_plan=?, makeup_final=?,
                         updated_at=datetime('now') WHERE id=?""",
                    (val, mk, plan, final, existing["id"]))
                changed += 1
        else:
            execute(
                """INSERT INTO attendance (student_id, class_id, date, status, makeup, makeup_plan, makeup_final)
                   VALUES (?,?,?,?,?,?,?)""",
                (sid, cid, date, val, mk, plan, final))
            changed += 1
    log("attendance_all", f"{ym}: {changed} changes")
    flash(t("saved"), "ok")
    return redirect(url_for("attendance.index", **{"class": "all", "month": ym}))


@bp.route("/session", methods=["GET", "POST"])
@login_required
def session_view():
    classes = query("SELECT * FROM classes WHERE status='active' ORDER BY name")
    class_id = request.values.get("class", type=int)
    date = request.values.get("date") or dt.date.today().isoformat()

    if request.method == "POST":
        for key, val in request.form.items():
            if not key.startswith("st_"):
                continue
            sid = int(key[3:])
            val = val.strip()
            mk, plan, final = _read_makeup(request.form, sid, date, val)
            existing = query(
                "SELECT id FROM attendance WHERE student_id=? AND class_id=? AND date=?",
                (sid, class_id, date), one=True)
            if val == "":
                if existing:
                    execute("DELETE FROM attendance WHERE id = ?", (existing["id"],))
                continue
            if val not in STATUSES:
                continue
            if existing:
                execute(
                    """UPDATE attendance SET status=?, makeup=?, makeup_plan=?, makeup_final=?,
                         updated_at=datetime('now') WHERE id=?""",
                    (val, mk, plan, final, existing["id"]))
            else:
                execute(
                    """INSERT INTO attendance (student_id, class_id, date, status, makeup, makeup_plan, makeup_final)
                       VALUES (?,?,?,?,?,?,?)""",
                    (sid, class_id, date, val, mk, plan, final))
        log("attendance_session", f"class {class_id} {date}")
        flash(t("saved"), "ok")
        if request.form.get("goto") == "finance" and class_id:
            return redirect(url_for("classes.detail", cid=class_id, tab="finance"))
        return redirect(url_for("attendance.session_view", **{"class": class_id, "date": date}))

    students = roster(class_id, date) if class_id else []
    current, makeup, plan, final = {}, {}, {}, {}
    if class_id:
        for r in query(
                """SELECT student_id, status, makeup, makeup_plan, makeup_final
                     FROM attendance WHERE class_id=? AND date=?""", (class_id, date)):
            current[r["student_id"]] = r["status"]
            if r["makeup"]:
                makeup[r["student_id"]] = r["makeup"]
            if r["makeup_plan"]:
                plan[r["student_id"]] = r["makeup_plan"]
            if r["makeup_final"]:
                final[r["student_id"]] = r["makeup_final"]
    return render_template("attendance/session.html", classes=classes, class_id=class_id,
                           date=date, students=students, current=current, makeup=makeup,
                           plan=plan, final=final, statuses=STATUSES)

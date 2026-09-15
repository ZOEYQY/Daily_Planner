"""Prospects — people still deciding or trying us out.

Deliberately lightweight: enough to match them to a teacher (see
views/teachers.py's match()) and to talk about a trial/start — no full fee
history, no attendance, none of the real student machinery. "转为正式学生"
(Convert to Student) hands a ready one off to the real Add Student form.
"""
import json

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from ..auth import login_required
from ..db import query, execute, log
from ..i18n import t
from ..util import smartcase, titlecase_name, parse_int, known_subjects, known_grades, json_list
from ..engine import to_cents
from .teachers import RATE_UNITS

bp = Blueprint("prospects", __name__)

STATUSES = ("open", "converted", "dropped")


# ── availability ──────────────────────────────────────────────────
# Unlike the teacher form's fixed 7-day grid, the prospect form asks "how many
# times a week" first, then shows that many day+time pickers (avail_weekday /
# avail_start / avail_end, one value per row — see prospects/form.html's JS).
def _availability(pid):
    return query(
        "SELECT weekday, start_time, end_time FROM prospect_availability "
        "WHERE prospect_id = ? ORDER BY weekday", (pid,))


def _save_availability(pid, f):
    """Rebuild prospect_availability from the avail_weekday/avail_start/
    avail_end row lists, then sync the legacy single match_day/match_time
    columns to the earliest row so the teacher-match page (which only
    searches one day at a time) still has a sensible default — same pattern
    classes.py uses for class_schedule."""
    execute("DELETE FROM prospect_availability WHERE prospect_id = ?", (pid,))
    rows = {}  # weekday -> (start, end); a later row for the same day wins
    for wd_raw, s, e in zip(f.getlist("avail_weekday"), f.getlist("avail_start"), f.getlist("avail_end")):
        wd = parse_int(wd_raw)
        if wd is not None and 0 <= wd <= 6:
            rows[wd] = (s or None, e or None)
    for wd, (s, e) in rows.items():
        execute(
            "INSERT INTO prospect_availability (prospect_id, weekday, start_time, end_time) "
            "VALUES (?,?,?,?)", (pid, wd, s, e))
    first = query(
        "SELECT weekday, start_time, end_time FROM prospect_availability "
        "WHERE prospect_id = ? ORDER BY weekday LIMIT 1", (pid,), one=True)
    if first:
        execute("UPDATE prospects SET match_day=?, match_time=? WHERE id=?",
                (first["weekday"], first["start_time"], pid))
    else:
        execute("UPDATE prospects SET match_day=NULL, match_time=NULL WHERE id=?", (pid,))


def _form_prospect(f):
    """(name, phone, age, grade, match_subject_json, budget_cents, budget_unit,
    start_date, notes, parent_name, parent_phone, parent_relationship,
    teacher_id). Subjects and grade come from chip_picker groups (checkboxes /
    a radio — see partials/macros.html), not typed CSV text."""
    unit = f.get("budget_unit") if f.get("budget_unit") in RATE_UNITS else "month"
    return (
        titlecase_name(f.get("name")) or "Unnamed",
        (f.get("phone") or "").strip() or None,
        parse_int(f.get("age")),
        smartcase(f.get("grade")) or None,
        json.dumps([smartcase(s) for s in f.getlist("match_subject")]),
        to_cents(f.get("budget")),
        unit,
        f.get("start_date") or None,
        f.get("trial_date") or None,
        f.get("trial_time") or None,
        (f.get("notes") or "").strip() or None,
        titlecase_name(f.get("parent_name")) or None,
        (f.get("parent_phone") or "").strip() or None,
        smartcase(f.get("parent_relationship")) or None,
        parse_int(f.get("teacher_id")) or None,
    )


def _teacher_options(current_id=None):
    """Active teachers, plus the currently-assigned one even if since
    deactivated — same reasoning as classes.py's teacher dropdown."""
    return query("SELECT id, full_name FROM teachers WHERE status = 'active' OR id = ? "
                 "ORDER BY full_name", (current_id or 0,))


def _commission_estimate(p, teacher):
    """Rough "what I'd keep" preview from budget vs. the assigned teacher's
    rate — only meaningful when both use the same unit (an inquiry has no
    lessons yet to multiply by, unlike the real per-lesson figure on a class
    once it exists — see engine.teacher_commission)."""
    if not teacher or not teacher["rate_cents"] or not p["budget_cents"]:
        return None
    if p["budget_unit"] != teacher["rate_unit"]:
        return None
    return {
        "unit": teacher["rate_unit"],
        "budget": p["budget_cents"],
        "rate": teacher["rate_cents"],
        "commission": p["budget_cents"] - teacher["rate_cents"],
    }


@bp.route("/")
@login_required
def index():
    status = request.args.get("status") or "open"
    q = (request.args.get("q") or "").strip()
    where, args = ["1=1"], []
    if status in STATUSES:
        where.append("status = ?"); args.append(status)
    if q:
        where.append("(name LIKE ? OR match_subject LIKE ? OR phone LIKE ?)")
        args += [f"%{q}%"] * 3
    prospects = query(
        f"SELECT * FROM prospects WHERE {' AND '.join(where)} ORDER BY created_at DESC", args)
    rows = [{"p": p, "subjects": json_list(p["match_subject"]), "avail": _availability(p["id"])}
            for p in prospects]
    return render_template("prospects/index.html", rows=rows, status=status, q=q,
                           statuses=STATUSES)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "POST":
        f = request.form
        (name, phone, age, grade, subjects_json, budget, unit, start_date, trial_date, trial_time,
         notes, parent_name, parent_phone, parent_rel, teacher_id) = _form_prospect(f)
        pid = execute(
            """INSERT INTO prospects (name, phone, age, grade, match_subject,
                                      budget_cents, budget_unit, start_date, trial_date, trial_time,
                                      notes, parent_name, parent_phone, parent_relationship, teacher_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, phone, age, grade, subjects_json, budget, unit, start_date, trial_date, trial_time,
             notes, parent_name, parent_phone, parent_rel, teacher_id))
        _save_availability(pid, f)
        log("prospect_add", name)
        flash(t("saved"), "ok")
        # The whole point of an inquiry is matching — go straight there.
        return redirect(url_for("teachers.match", prospect=pid))
    return render_template(
        "prospects/form.html", p=None,
        known_subjects=known_subjects(), known_grades=known_grades(),
        rate_units=RATE_UNITS, teachers=_teacher_options(), avail=[])


@bp.route("/<int:pid>")
@login_required
def detail(pid):
    p = query("SELECT * FROM prospects WHERE id = ?", (pid,), one=True)
    if not p:
        abort(404)
    converted = (
        query("SELECT id, full_name FROM students WHERE id = ?", (p["converted_student_id"],), one=True)
        if p["converted_student_id"] else None
    )
    teacher = (
        query("SELECT * FROM teachers WHERE id = ?", (p["teacher_id"],), one=True)
        if p["teacher_id"] else None
    )
    return render_template(
        "prospects/detail.html", p=p, converted=converted, teacher=teacher,
        commission=_commission_estimate(p, teacher) if teacher else None,
        subjects=json_list(p["match_subject"]), avail=_availability(pid))


@bp.route("/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def edit(pid):
    p = query("SELECT * FROM prospects WHERE id = ?", (pid,), one=True)
    if not p:
        abort(404)
    if request.method == "POST":
        f = request.form
        (name, phone, age, grade, subjects_json, budget, unit, start_date, trial_date, trial_time,
         notes, parent_name, parent_phone, parent_rel, teacher_id) = _form_prospect(f)
        execute(
            """UPDATE prospects SET name=?, phone=?, age=?, grade=?, match_subject=?,
                  budget_cents=?, budget_unit=?, start_date=?, trial_date=?, trial_time=?, notes=?,
                  parent_name=?, parent_phone=?, parent_relationship=?, teacher_id=?,
                  updated_at=datetime('now')
                WHERE id=?""",
            (name, phone, age, grade, subjects_json, budget, unit, start_date, trial_date, trial_time,
             notes, parent_name, parent_phone, parent_rel, teacher_id, pid))
        _save_availability(pid, f)
        log("prospect_edit", name)
        flash(t("saved"), "ok")
        return redirect(url_for("prospects.detail", pid=pid))
    return render_template(
        "prospects/form.html", p=p,
        known_subjects=known_subjects(), known_grades=known_grades(),
        rate_units=RATE_UNITS, teachers=_teacher_options(p["teacher_id"]),
        avail=_availability(pid))


@bp.route("/<int:pid>/status", methods=["POST"])
@login_required
def set_status(pid):
    status = request.form.get("status")
    if status not in STATUSES:
        abort(400)
    p = query("SELECT name FROM prospects WHERE id = ?", (pid,), one=True)
    if not p:
        abort(404)
    execute("UPDATE prospects SET status=?, updated_at=datetime('now') WHERE id=?", (status, pid))
    log("prospect_status", f"{p['name']} -> {status}")
    flash(t("saved"), "ok")
    return redirect(url_for("prospects.detail", pid=pid))


@bp.route("/<int:pid>/delete", methods=["POST"])
@login_required
def delete(pid):
    p = query("SELECT name FROM prospects WHERE id = ?", (pid,), one=True)
    if not p:
        abort(404)
    execute("DELETE FROM prospects WHERE id = ?", (pid,))
    log("prospect_delete", p["name"])
    flash(t("deleted"), "ok")
    return redirect(url_for("prospects.index"))

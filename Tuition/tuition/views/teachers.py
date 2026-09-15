"""Teacher profiles + new-student ↔ teacher matching.

Standalone module: classes keep their free-text `teacher` field. A teacher
profile records the subjects / grades they can teach, their weekly availability
and some background, so a new student can be matched to the right teacher.
"""
import json

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from ..auth import login_required
from ..db import query, execute, log
from ..i18n import t, LANGS
from ..util import (parse_int, smartcase, titlecase_name, pick_avatar_color,
                    derive_full_name, known_subjects, known_grades, known_experience,
                    known_academic_results, json_list)
from ..engine import to_cents

bp = Blueprint("teachers", __name__)

STATUSES = ("active", "inactive")
RATE_UNITS = ("hour", "lesson", "month")


def _availability(tid):
    return query(
        "SELECT weekday, start_time, end_time FROM teacher_availability "
        "WHERE teacher_id = ? ORDER BY weekday", (tid,))


def _save_availability(tid, f):
    execute("DELETE FROM teacher_availability WHERE teacher_id = ?", (tid,))
    for wd in range(7):
        if not f.get(f"day_{wd}"):
            continue
        execute(
            "INSERT INTO teacher_availability (teacher_id, weekday, start_time, end_time) "
            "VALUES (?,?,?,?)",
            (tid, wd, f.get(f"start_{wd}") or None, f.get(f"end_{wd}") or None))


def _form_ctx(tc):
    """Shared context for the add / edit form."""
    return dict(
        tc=tc, langs=LANGS, statuses=STATUSES, rate_units=RATE_UNITS,
        known_subjects=known_subjects(), known_grades=known_grades(),
        known_experience=known_experience(), known_academic_results=known_academic_results(),
        subjects_selected=json_list(tc["subjects"]) if tc else [],
        levels_selected=json_list(tc["levels"]) if tc else [],
        experience_selected=json_list(tc["experience_summary"]) if tc else [],
        academic_results_selected=json_list(tc["academic_results"]) if tc else [],
        have_langs=json_list(tc["languages"]) if tc else [],
        schedule={r["weekday"]: r for r in _availability(tc["id"])} if tc else {},
    )


def _form_teacher(f):
    name_en = titlecase_name(f.get("name_en"))
    name_zh = (f.get("name_zh") or "").strip()
    full = derive_full_name(name_en, name_zh)
    langs = [c for c in f.getlist("languages") if c in LANGS]
    status = f.get("status") if f.get("status") in STATUSES else "active"
    unit = f.get("rate_unit") if f.get("rate_unit") in RATE_UNITS else "hour"
    has_tablet = {"1": 1, "0": 0}.get(f.get("has_tablet"))  # "" (not asked) -> None
    return (
        name_zh or None, name_en or None, full,
        (f.get("phone") or "").strip() or None,
        (f.get("gender") or "").strip() or None,
        json.dumps([smartcase(s) for s in f.getlist("subjects")]),
        json.dumps([smartcase(s) for s in f.getlist("levels")]),
        json.dumps(langs),
        parse_int(f.get("experience_years")),
        to_cents(f.get("rate")), unit,
        (f.get("bio") or "").strip() or None,
        # ── screening / basic-info intake ──
        parse_int(f.get("age")),
        json.dumps([smartcase(s) for s in f.getlist("experience_summary")]),
        (f.get("current_work") or "").strip() or None,
        json.dumps([smartcase(s) for s in f.getlist("academic_results")]),
        (f.get("subjects_notes") or "").strip() or None,
        to_cents(f.get("rate_1v1_min")) or None,
        to_cents(f.get("rate_1v1_max")) or None,
        to_cents(f.get("rate_group_min")) or None,
        to_cents(f.get("rate_group_max")) or None,
        has_tablet,
        status,
        (f.get("remarks") or "").strip() or None,
    )


# ── list ───────────────────────────────────────────────────────────
@bp.route("/")
@login_required
def index():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status") or ""
    where, args = ["1=1"], []
    if q:
        where.append("(full_name LIKE ? OR name_zh LIKE ? OR subjects LIKE ?)")
        args += [f"%{q}%"] * 3
    if status in STATUSES:
        where.append("status = ?"); args.append(status)
    teachers = query(
        f"SELECT * FROM teachers WHERE {' AND '.join(where)} "
        "ORDER BY status, full_name", args)
    rows = [{"t": tc, "subjects": json_list(tc["subjects"]),
             "langs": json_list(tc["languages"]), "avail": _availability(tc["id"])}
            for tc in teachers]
    return render_template("teachers/index.html", rows=rows, q=q, status=status,
                           statuses=STATUSES)


# ── create / edit ──────────────────────────────────────────────────
@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "POST":
        f = request.form
        vals = _form_teacher(f)
        tid = execute(
            """INSERT INTO teachers
                 (name_zh, name_en, full_name, phone, gender, subjects, levels,
                  languages, experience_years, rate_cents, rate_unit, bio,
                  age, experience_summary, current_work, academic_results, subjects_notes,
                  rate_1v1_min_cents, rate_1v1_max_cents, rate_group_min_cents, rate_group_max_cents,
                  has_tablet, status, remarks, avatar_color)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            vals + (pick_avatar_color(vals[2]),))
        _save_availability(tid, f)
        log("teacher_add", vals[2])
        flash(t("saved"), "ok")
        return redirect(url_for("teachers.detail", tid=tid))
    return render_template("teachers/form.html", **_form_ctx(None))


@bp.route("/<int:tid>/edit", methods=["GET", "POST"])
@login_required
def edit(tid):
    tc = query("SELECT * FROM teachers WHERE id = ?", (tid,), one=True)
    if not tc:
        abort(404)
    if request.method == "POST":
        f = request.form
        vals = _form_teacher(f)
        execute(
            """UPDATE teachers SET name_zh=?, name_en=?, full_name=?, phone=?,
                  gender=?, subjects=?, levels=?, languages=?, experience_years=?,
                  rate_cents=?, rate_unit=?, bio=?,
                  age=?, experience_summary=?, current_work=?, academic_results=?, subjects_notes=?,
                  rate_1v1_min_cents=?, rate_1v1_max_cents=?, rate_group_min_cents=?, rate_group_max_cents=?,
                  has_tablet=?, status=?, remarks=?,
                  updated_at=datetime('now')
                WHERE id=?""",
            vals + (tid,))
        _save_availability(tid, f)
        log("teacher_edit", vals[2])
        flash(t("saved"), "ok")
        return redirect(url_for("teachers.detail", tid=tid))
    return render_template("teachers/form.html", **_form_ctx(tc))


@bp.route("/<int:tid>")
@login_required
def detail(tid):
    tc = query("SELECT * FROM teachers WHERE id = ?", (tid,), one=True)
    if not tc:
        abort(404)
    return render_template(
        "teachers/detail.html", tc=tc,
        subjects=json_list(tc["subjects"]), levels=json_list(tc["levels"]),
        experience=json_list(tc["experience_summary"]),
        academic_results=json_list(tc["academic_results"]),
        langs=json_list(tc["languages"]), avail=_availability(tid))


@bp.route("/<int:tid>/delete", methods=["POST"])
@login_required
def delete(tid):
    tc = query("SELECT full_name FROM teachers WHERE id = ?", (tid,), one=True)
    if not tc:
        abort(404)
    execute("DELETE FROM teachers WHERE id = ?", (tid,))
    log("teacher_delete", tc["full_name"])
    flash(t("deleted"), "ok")
    return redirect(url_for("teachers.index"))


# ── matching ───────────────────────────────────────────────────────
def _text_hit(needle, haystack_list):
    """Loose case-insensitive membership: 'math' matches 'Mathematics'."""
    n = (needle or "").strip().lower()
    if not n:
        return False
    for h in haystack_list:
        h = h.lower()
        if n in h or h in n:
            return True
    return False


def _score(tc, want):
    """Return (score, reasons[]) for one teacher against the wanted criteria."""
    subjects = json_list(tc["subjects"])
    levels = json_list(tc["levels"])
    langs = json_list(tc["languages"])
    avail = {r["weekday"]: r for r in _availability(tc["id"])}
    score, why = 0, []

    if want["subject"]:
        if _text_hit(want["subject"], subjects):
            score += 45; why.append("m_subject")
        else:
            return 0, []                       # wrong subject -> not a match

    if want["level"] and _text_hit(want["level"], levels):
        score += 20; why.append("m_level")

    if want["lang"] and want["lang"] in langs:
        score += 15; why.append("m_language")

    day, tm = want["day"], want["time"]
    if day != "":
        row = avail.get(day)
        if not row:
            if want["subject"]:
                score += 0                     # subject ok but day clashes
            else:
                return 0, []
        else:
            why.append("m_day")
            s, e = row["start_time"], row["end_time"]
            if tm:
                if s and e and s <= tm <= e:
                    score += 30; why.append("m_time")
                elif not s and not e:
                    score += 24; why.append("m_time")
                else:
                    score += 8
            else:
                score += 22
    elif tm:
        for row in avail.values():
            s, e = row["start_time"], row["end_time"]
            if (s and e and s <= tm <= e) or (not s and not e):
                score += 14; why.append("m_time")
                break
    elif avail:
        score += 4

    return score, why


@bp.route("/match")
@login_required
def match():
    # Coming from an inquiry's page (prospects.detail's "Find a teacher" link,
    # or the redirect straight out of Add Inquiry) passes ?prospect=<id> so the
    # criteria default to what that person is looking for, instead of an empty
    # form — see prospects.match_subject/match_day/match_time. Only ever used
    # to PREFILL: an explicitly-submitted (even blank) field always wins, so
    # widening the search by clearing a box still works.
    pid = parse_int(request.args.get("prospect"))
    prospect = query("SELECT * FROM prospects WHERE id = ?", (pid,), one=True) if pid else None

    def arg(name, default=""):
        if name in request.args:
            return request.args[name].strip()
        return "" if default is None else default

    day_default = prospect["match_day"] if prospect else ""  # may be 0 (Monday) — keep falsy-safe
    # A prospect can want more than one subject (match_subject is a JSON array,
    # like a teacher's) — default the search to the first one; the detail page
    # links to a specific ?subject= for the others.
    prospect_subjects = json_list(prospect["match_subject"]) if prospect else []
    want = {
        "subject": arg("subject", prospect_subjects[0] if prospect_subjects else ""),
        "level": arg("level", prospect["grade"] if prospect else ""),
        "lang": request.args.get("lang") if request.args.get("lang") in LANGS else "",
        "day": parse_int(arg("day", day_default)) if arg("day", day_default) != "" else "",
        "time": arg("time", prospect["match_time"] if prospect else ""),
    }
    if want["day"] not in ("", 0, 1, 2, 3, 4, 5, 6):
        want["day"] = ""

    any_criteria = any(v != "" for v in want.values())
    teachers = query("SELECT * FROM teachers WHERE status = 'active' ORDER BY full_name")
    ranked = []
    for tc in teachers:
        sc, why = _score(tc, want) if any_criteria else (0, [])
        if any_criteria and sc <= 0:
            continue
        ranked.append({
            "t": tc, "score": sc, "why": why,
            "subjects": json_list(tc["subjects"]),
            "levels": json_list(tc["levels"]),
            "langs": json_list(tc["languages"]),
            "avail": _availability(tc["id"]),
        })
    ranked.sort(key=lambda r: (-r["score"], r["t"]["full_name"]))
    max_score = max([r["score"] for r in ranked] + [1])

    return render_template(
        "teachers/match.html", ranked=ranked, want=want, any_criteria=any_criteria,
        max_score=max_score, known_subjects=known_subjects(), langs=LANGS,
        total_teachers=len(teachers), prospect=prospect)

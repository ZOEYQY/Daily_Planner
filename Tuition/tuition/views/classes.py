import re

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from ..auth import login_required
from ..db import query, execute, log
from ..i18n import t, WEEKDAYS
from ..util import (parse_int, smartcase, titlecase_name, pick_avatar_color,
                    derive_full_name, age_from_dob)
from ..engine import (this_month, add_months, to_cents, resolve_lesson_fee, STATUS_KEYS,
                      forecast_expected_for, class_month_finance, class_stats, sync_month,
                      class_bill_mode, CLASS_LEVEL_MODES, PAYMENT_METHODS)
from .attendance import build_matrix

bp = Blueprint("classes", __name__)

FEE_MODELS = ("monthly", "per_lesson")
PRICINGS = ("fixed", "per_student")
KINDS = ("1v1", "1v3", "small")
KIND_TOKEN = {"1v1": "1v1", "1v3": "1v3", "小班": "小班", "small": "小班"}
BILL_MODES = ("student_attend", "class_ran", "class_flat", "agent_headcount")


def _agent_missing(bill_mode, agent_name):
    """agent_headcount bills are addressed to an agent — that name is required.
    Every other mode (incl. the flat class fee) never asks for one."""
    return bill_mode == "agent_headcount" and not (agent_name or "").strip()


def _form_bill(f, kind):
    """(bill_mode, lesson_fee_cents, base_fee_cents, base_head_count, per_head_cents,
        agent_name, agent_phone, billed_family_id) from the class form."""
    mode = f.get("bill_mode") if f.get("bill_mode") in BILL_MODES else "student_attend"
    if kind == "1v1":
        mode = "student_attend"
    agent = mode == "agent_headcount"
    fam = parse_int(f.get("billed_family_id")) if mode == "class_flat" else None
    return (
        mode,
        to_cents(f.get("lesson_fee")),
        to_cents(f.get("base_fee")) if agent else 0,
        max(0, parse_int(f.get("base_heads")) or 0) if agent else 0,
        to_cents(f.get("per_head")) if agent else 0,
        smartcase(f.get("agent_name")) if agent else None,
        (f.get("agent_phone") or "").strip() or None if agent else None,
        fam,
    )


def _subject_lang(subject):
    """Which language's weekday name to use in the auto class name."""
    s = (subject or "").lower()
    if "melayu" in s or "bahasa m" in s or re.search(r"\bbm\b", s):
        return "ms"
    return "en"


def _name_taken(name, cid=None):
    return query("SELECT 1 FROM classes WHERE lower(name) = lower(?) AND id != ?",
                 (name, cid or 0), one=True) is not None


def _form_weekdays(f):
    return [wd for wd in range(7) if f.get(f"day_{wd}")]


def _resolve_class_name(f, cid=None):
    """Use the typed name if given, else build: Subject + Level + Type,
    adding the (subject-language) weekday when that collides."""
    typed = smartcase(f.get("name"))
    if typed:
        return typed
    subject = smartcase(f.get("subject"))
    level = smartcase(f.get("level"))
    kind = KIND_TOKEN.get(f.get("kind"), "")
    base = " ".join(x for x in (subject, level, kind) if x).strip() or "Class"
    if not _name_taken(base, cid):
        return base
    weekdays = _form_weekdays(f)
    if weekdays:
        wname = WEEKDAYS[_subject_lang(subject)][weekdays[0]]
        cand = f"{base} {wname}"
    else:
        cand = base
    if not _name_taken(cand, cid):
        return cand
    n = 2
    while _name_taken(f"{cand} {n}", cid):
        n += 1
    return f"{cand} {n}"


def _save_schedule(cid, f):
    """Rebuild class_schedule from day_<n> checkboxes + start_<n>/end_<n> times."""
    execute("DELETE FROM class_schedule WHERE class_id = ?", (cid,))
    for wd in range(7):
        if not f.get(f"day_{wd}"):
            continue
        execute(
            "INSERT INTO class_schedule (class_id, weekday, start_time, end_time) VALUES (?,?,?,?)",
            (cid, wd, f.get(f"start_{wd}") or None, f.get(f"end_{wd}") or None))
    first = query(
        "SELECT weekday, start_time, end_time FROM class_schedule WHERE class_id = ? ORDER BY weekday LIMIT 1",
        (cid,), one=True)
    if first:
        execute("UPDATE classes SET weekday=?, start_time=?, end_time=? WHERE id=?",
                (first["weekday"], first["start_time"], first["end_time"], cid))
    else:
        execute("UPDATE classes SET weekday=NULL, start_time=NULL, end_time=NULL WHERE id=?", (cid,))


def _schedule(cid):
    return query(
        "SELECT weekday, start_time, end_time FROM class_schedule WHERE class_id = ? ORDER BY weekday",
        (cid,))


@bp.route("/")
@login_required
def index():
    q = (request.args.get("q") or "").strip()
    where = ["1=1"]
    args = []
    if q:
        where.append("(name LIKE ? OR subject LIKE ? OR teacher LIKE ?)")
        args += [f"%{q}%"] * 3
    classes = query(
        f"""SELECT c.*,
                   (SELECT COUNT(*) FROM enrollments e
                     WHERE e.class_id = c.id AND e.status='active') AS n_students
              FROM classes c WHERE {' AND '.join(where)}
             ORDER BY c.status, c.weekday, c.start_time""", args)
    ym = this_month()
    view = []
    for c in classes:
        est = 0
        for e in query("""SELECT student_id, status FROM enrollments
                            WHERE class_id = ? AND status='active'""", (c["id"],)):
            est += forecast_expected_for(e["student_id"], c["id"], ym)
        view.append({"c": c, "est": est, "days": _schedule(c["id"])})
    return render_template("classes/index.html", classes=view, q=q, ym=ym)


def _form_fee_model(f):
    # one fixed fee per month now; the column is kept for compatibility
    return "monthly"


def _form_pricing(f):
    return f.get("pricing") if f.get("pricing") in PRICINGS else "fixed"


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "POST":
        f = request.form
        kind = f.get("kind") if f.get("kind") in KINDS else None
        pricing = "fixed" if kind == "1v1" else _form_pricing(f)
        fee_cents = 0 if pricing == "per_student" else to_cents(f.get("default_fee"))
        bm, lesson_fee, base_fee, base_heads, per_head, agent_name, agent_phone, fam_id = _form_bill(f, kind)
        if _agent_missing(bm, agent_name):
            flash(t("agent_required"), "error")
            return redirect(url_for("classes.new"))
        cid = execute(
            """INSERT INTO classes (name, subject, level, kind, teacher,
                                    fee_model, pricing, default_fee_cents, status,
                                    bill_mode, lesson_fee_cents, base_fee_cents, base_head_count,
                                    per_head_cents, agent_name, agent_phone, billed_family_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_resolve_class_name(f), smartcase(f.get("subject")), smartcase(f.get("level")),
             kind, smartcase(f.get("teacher")),
             _form_fee_model(f), pricing, fee_cents,
             "active" if f.get("status", "active") == "active" else "inactive",
             bm, lesson_fee, base_fee, base_heads, per_head, agent_name, agent_phone, fam_id))
        _save_schedule(cid, f)
        _add_students_inline(cid, f)
        log("class_add", str(cid))
        flash(t("saved"), "ok")
        return redirect(url_for("classes.detail", cid=cid))
    return render_template(
        "classes/form.html", cls=None, schedule={}, kinds=KINDS,
        families=query("SELECT id, name FROM families ORDER BY name"),
        students=query("SELECT id, full_name FROM students WHERE status != 'left' ORDER BY full_name"))


NEW_STUDENT_FIELDS = ("name_en", "name_zh", "phone", "gender", "dob", "age",
                      "school", "grade", "start", "fee", "status",
                      "family_id", "new_family", "remarks")


def _create_inline_students(f):
    """Full-detail new students entered in the add-class form's repeatable
    'New students' block (same columns as the standalone Add Student form, plus
    a per-row class join date and per-lesson fee). Returns
    [{id, start, fee_cents}] so each can be enrolled on its own terms. Rows with
    no name at all are skipped."""
    columns = [f.getlist(f"ns_{name}") for name in NEW_STUDENT_FIELDS]
    default_start = this_month() + "-01"
    out = []
    for row in zip(*columns):
        d = dict(zip(NEW_STUDENT_FIELDS, row))
        name_en = titlecase_name(d["name_en"])
        name_zh = (d["name_zh"] or "").strip()
        if not (name_en or name_zh):
            continue
        full = derive_full_name(name_en, name_zh)
        if (d["new_family"] or "").strip():
            fam = execute("INSERT INTO families (name) VALUES (?)", (smartcase(d["new_family"]),))
        else:
            fam = parse_int(d["family_id"]) or None
        status = d["status"] if d["status"] in ("active", "trial", "inactive", "left") else "active"
        sid = execute(
            """INSERT INTO students
                 (name_zh, name_en, full_name, phone, gender, dob, age, school, grade,
                  family_id, status, avatar_color, remarks)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name_zh, name_en, full, (d["phone"] or "").strip(), d["gender"] or "",
             d["dob"] or None, parse_int(d["age"]) or age_from_dob(d["dob"]),
             smartcase(d["school"]), smartcase(d["grade"]), fam, status,
             pick_avatar_color(full), (d["remarks"] or "").strip()))
        out.append({"id": sid, "start": d["start"] or default_start, "fee_cents": to_cents(d["fee"])})
    return out


def _add_students_inline(cid, f):
    """Enroll picked existing students + create-and-enroll the new students
    entered inline, each on its own join date with its own per-lesson fee."""
    default_start = this_month() + "-01"
    rows = [{"id": parse_int(r), "start": default_start, "fee_cents": 0}
            for r in f.getlist("enroll_ids") if parse_int(r)]
    rows += _create_inline_students(f)
    touched = False
    for r in rows:
        sid = r["id"]
        if query("SELECT 1 FROM enrollments WHERE student_id=? AND class_id=? AND status='active'",
                 (sid, cid), one=True):
            continue
        execute("INSERT INTO enrollments (student_id, class_id, start_date, status) VALUES (?,?,?,'active')",
                (sid, cid, r["start"]))
        if r["fee_cents"]:
            execute(
                """INSERT INTO fees (student_id, class_id, fee_model, amount_cents,
                                     discount_cents, effective_from, remarks)
                   VALUES (?,?,?,?,0,?,?)""",
                (sid, cid, "per_lesson", r["fee_cents"], r["start"], "set when class created"))
        touched = True
    if touched:
        sync_month(this_month())


@bp.route("/<int:cid>")
@login_required
def detail(cid):
    c = query("SELECT * FROM classes WHERE id = ?", (cid,), one=True)
    if not c:
        abort(404)
    tab = request.args.get("tab") or "overview"
    ym = request.args.get("month") or this_month()
    sync_month(ym)

    enrollments = query(
        """SELECT e.*, s.full_name, s.status AS student_status
             FROM enrollments e JOIN students s ON s.id = e.student_id
            WHERE e.class_id = ? ORDER BY e.status='ended', s.full_name""", (cid,))
    today = ym + "-28"
    enr_view = [{"e": e, "lesson_fee": resolve_lesson_fee(e["student_id"], cid, today)}
                for e in enrollments]

    all_students = query("SELECT id, full_name FROM students WHERE status != 'left' ORDER BY full_name")
    enrolled_ids = {e["student_id"] for e in enrollments if e["status"] == "active"}

    finance = class_month_finance(cid, ym)
    stats = class_stats(cid, ym)
    matrix = build_matrix(cid, ym)

    return render_template(
        "classes/detail.html", c=c, tab=tab, ym=ym, schedule=_schedule(cid), stats=stats,
        prev_month=add_months(ym, -1), next_month=add_months(ym, 1),
        enrollments=enr_view, all_students=all_students, enrolled_ids=enrolled_ids,
        finance=finance, matrix=matrix, methods=PAYMENT_METHODS,
        statuses=STATUS_KEYS)


@bp.route("/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def edit(cid):
    c = query("SELECT * FROM classes WHERE id = ?", (cid,), one=True)
    if not c:
        abort(404)
    if request.method == "POST":
        f = request.form
        kind = f.get("kind") if f.get("kind") in KINDS else None
        pricing = "fixed" if kind == "1v1" else _form_pricing(f)
        fee_cents = 0 if pricing == "per_student" else to_cents(f.get("default_fee"))
        bm, lesson_fee, base_fee, base_heads, per_head, agent_name, agent_phone, fam_id = _form_bill(f, kind)
        if _agent_missing(bm, agent_name):
            flash(t("agent_required"), "error")
            return redirect(url_for("classes.edit", cid=cid))
        execute(
            """UPDATE classes SET name=?, subject=?, level=?, kind=?, teacher=?,
                  fee_model=?, pricing=?, default_fee_cents=?, status=?,
                  bill_mode=?, lesson_fee_cents=?, base_fee_cents=?, base_head_count=?, per_head_cents=?,
                  agent_name=?, agent_phone=?, billed_family_id=?
                WHERE id=?""",
            (_resolve_class_name(f, cid=cid), smartcase(f.get("subject")), smartcase(f.get("level")),
             kind, smartcase(f.get("teacher")),
             _form_fee_model(f), pricing, fee_cents,
             "active" if f.get("status", "active") == "active" else "inactive",
             bm, lesson_fee, base_fee, base_heads, per_head, agent_name, agent_phone, fam_id, cid))
        _save_schedule(cid, f)
        # bill_mode may have flipped per-student <-> class-level: resync so stale
        # payments / class_bills rows for the current month get cleaned up.
        sync_month(this_month())
        log("class_edit", str(cid))
        flash(t("saved"), "ok")
        return redirect(url_for("classes.detail", cid=cid))
    families = query("SELECT id, name FROM families ORDER BY name")
    return render_template("classes/form.html", cls=c, kinds=KINDS, families=families,
                           schedule={r["weekday"]: r for r in _schedule(cid)})


@bp.route("/<int:cid>/delete", methods=["POST"])
@login_required
def delete(cid):
    c = query("SELECT name FROM classes WHERE id = ?", (cid,), one=True)
    if not c:
        abort(404)
    execute("DELETE FROM classes WHERE id = ?", (cid,))
    log("class_delete", c["name"])
    flash(t("deleted"), "ok")
    return redirect(url_for("classes.index"))


# ── enrollments ────────────────────────────────────────────────────
@bp.route("/<int:cid>/enroll", methods=["POST"])
@login_required
def enroll(cid):
    cls = query("SELECT pricing FROM classes WHERE id = ?", (cid,), one=True)
    if not cls:
        abort(404)
    f = request.form
    sid = parse_int(f.get("student_id"))
    new_name = titlecase_name(f.get("new_student"))
    if not sid and new_name:
        full = derive_full_name(new_name, "")
        sid = execute(
            "INSERT INTO students (name_en, full_name, avatar_color, status) VALUES (?,?,?,'active')",
            (new_name, full, pick_avatar_color(full)))
    if not sid:
        flash("Choose a student.", "error")
        return redirect(url_for("classes.detail", cid=cid, tab="students"))
    start = f.get("start_date") or (this_month() + "-01")
    existing = query(
        "SELECT id FROM enrollments WHERE student_id=? AND class_id=? AND status='active'",
        (sid, cid), one=True)
    if existing:
        flash("Already enrolled.", "error")
        return redirect(url_for("classes.detail", cid=cid, tab="students"))
    execute(
        "INSERT INTO enrollments (student_id, class_id, start_date, status) VALUES (?,?,?,'active')",
        (sid, cid, start))
    # optional inline fee
    if (f.get("fee_amount") or "").strip():
        model = "monthly"
        execute(
            """INSERT INTO fees (student_id, class_id, fee_model, amount_cents, discount_cents,
                                 effective_from, remarks)
               VALUES (?,?,?,?,?,?,?)""",
            (sid, cid, model, to_cents(f.get("fee_amount")), to_cents(f.get("fee_discount")),
             start, "set on enrollment"))
    elif cls["pricing"] == "per_student":
        flash(t("fee_reminder"), "error")
    sync_month(this_month())
    log("enroll", f"student {sid} -> class {cid}")
    flash(t("saved"), "ok")
    return redirect(url_for("classes.detail", cid=cid, tab="students"))


@bp.route("/enrollments/<int:eid>/dates", methods=["POST"])
@login_required
def edit_enrollment(eid):
    """Adjust an enrollment's join (start) date and, optionally, its end date.
    An end date implies the enrollment has ended; clearing it re-activates it.
    Re-syncs every affected month so this month's fees reflect the new window."""
    e = query("SELECT * FROM enrollments WHERE id = ?", (eid,), one=True)
    if not e:
        abort(404)
    f = request.form
    start = f.get("start_date") or e["start_date"]
    end = (f.get("end_date") or "").strip() or None
    status = "ended" if end else "active"
    execute("UPDATE enrollments SET start_date=?, end_date=?, status=? WHERE id=?",
            (start, end, status, eid))
    for ym in {this_month(), (start or "")[:7], (end or "")[:7], (e["start_date"] or "")[:7]}:
        if ym:
            sync_month(ym)  # no-ops on a closed month
    log("enroll_dates", f"enrollment {eid}: {start} -> {end or '—'}")
    flash(t("saved"), "ok")
    return redirect(f.get("back") or url_for("classes.detail", cid=e["class_id"], tab="students"))


@bp.route("/enrollments/<int:eid>/end", methods=["POST"])
@login_required
def end_enrollment(eid):
    e = query("SELECT * FROM enrollments WHERE id = ?", (eid,), one=True)
    if not e:
        abort(404)
    end = request.form.get("end_date") or this_month() + "-28"
    execute("UPDATE enrollments SET status='ended', end_date=? WHERE id=?", (end, eid))
    log("enroll_end", f"enrollment {eid}")
    flash(t("saved"), "ok")
    ref = request.form.get("back") or url_for("classes.detail", cid=e["class_id"], tab="students")
    return redirect(ref)


@bp.route("/enrollments/<int:eid>/delete", methods=["POST"])
@login_required
def delete_enrollment(eid):
    e = query("SELECT * FROM enrollments WHERE id = ?", (eid,), one=True)
    if not e:
        abort(404)
    execute("DELETE FROM enrollments WHERE id = ?", (eid,))
    flash(t("deleted"), "ok")
    ref = request.form.get("back") or url_for("classes.detail", cid=e["class_id"], tab="students")
    return redirect(ref)


@bp.route("/<int:cid>/fee", methods=["POST"])
@login_required
def set_default_fee(cid):
    f = request.form
    execute("UPDATE classes SET fee_model=?, default_fee_cents=? WHERE id=?",
            (_form_fee_model(f), to_cents(f.get("default_fee")), cid))
    flash(t("saved"), "ok")
    return redirect(url_for("classes.detail", cid=cid, tab="finance"))

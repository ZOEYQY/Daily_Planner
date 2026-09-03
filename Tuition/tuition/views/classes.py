import re

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from ..auth import login_required
from ..db import query, execute, log
from ..i18n import t, WEEKDAYS
from ..util import parse_int, smartcase, titlecase_name, pick_avatar_color, derive_full_name
from ..engine import (this_month, add_months, to_cents, resolve_fee, STATUS_KEYS,
                      forecast_expected_for, class_month_finance, class_stats, sync_month)
from .attendance import build_matrix

bp = Blueprint("classes", __name__)

FEE_MODELS = ("monthly", "per_lesson")
PRICINGS = ("fixed", "per_student")
KINDS = ("1v1", "small")
KIND_TOKEN = {"1v1": "1v1", "small": "小班"}


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
        cid = execute(
            """INSERT INTO classes (name, subject, level, kind, teacher,
                                    fee_model, pricing, default_fee_cents, status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (_resolve_class_name(f), smartcase(f.get("subject")), smartcase(f.get("level")),
             kind, smartcase(f.get("teacher")),
             _form_fee_model(f), pricing, fee_cents,
             "active" if f.get("status", "active") == "active" else "inactive"))
        _save_schedule(cid, f)
        _add_students_inline(cid, f)
        log("class_add", str(cid))
        flash(t("saved"), "ok")
        return redirect(url_for("classes.detail", cid=cid))
    return render_template(
        "classes/form.html", cls=None, schedule={}, kinds=KINDS,
        students=query("SELECT id, full_name FROM students WHERE status != 'left' ORDER BY full_name"))


def _add_students_inline(cid, f):
    """Enroll picked students + create-and-enroll any typed new names, with an
    optional shared fee, from the add-class form."""
    start = this_month() + "-01"
    sids = []
    for raw in f.getlist("enroll_ids"):
        sid = parse_int(raw)
        if sid:
            sids.append(sid)
    for line in (f.get("new_students") or "").splitlines():
        name = titlecase_name(line)
        if not name:
            continue
        full = derive_full_name(name, "")
        sids.append(execute(
            "INSERT INTO students (name_en, full_name, avatar_color, status) VALUES (?,?,?,'active')",
            (name, full, pick_avatar_color(full))))
    fee_cents = to_cents(f.get("student_fee"))
    for sid in sids:
        if query("SELECT 1 FROM enrollments WHERE student_id=? AND class_id=? AND status='active'",
                 (sid, cid), one=True):
            continue
        execute("INSERT INTO enrollments (student_id, class_id, start_date, status) VALUES (?,?,?,'active')",
                (sid, cid, start))
        if fee_cents:
            execute(
                """INSERT INTO fees (student_id, class_id, fee_model, amount_cents,
                                     discount_cents, effective_from, remarks)
                   VALUES (?,?,?,?,0,?,?)""",
                (sid, cid, "monthly", fee_cents, start, "set when class created"))
    if sids:
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
    enr_view = [{"e": e, "fee": resolve_fee(e["student_id"], cid, today)} for e in enrollments]

    all_students = query("SELECT id, full_name FROM students WHERE status != 'left' ORDER BY full_name")
    enrolled_ids = {e["student_id"] for e in enrollments if e["status"] == "active"}

    finance = class_month_finance(cid, ym)
    stats = class_stats(cid, ym)
    matrix = build_matrix(cid, ym)

    return render_template(
        "classes/detail.html", c=c, tab=tab, ym=ym, schedule=_schedule(cid), stats=stats,
        prev_month=add_months(ym, -1), next_month=add_months(ym, 1),
        enrollments=enr_view, all_students=all_students, enrolled_ids=enrolled_ids,
        finance=finance, matrix=matrix,
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
        execute(
            """UPDATE classes SET name=?, subject=?, level=?, kind=?, teacher=?,
                  fee_model=?, pricing=?, default_fee_cents=?, status=?
                WHERE id=?""",
            (_resolve_class_name(f, cid=cid), smartcase(f.get("subject")), smartcase(f.get("level")),
             kind, smartcase(f.get("teacher")),
             _form_fee_model(f), pricing, fee_cents,
             "active" if f.get("status", "active") == "active" else "inactive", cid))
        _save_schedule(cid, f)
        log("class_edit", str(cid))
        flash(t("saved"), "ok")
        return redirect(url_for("classes.detail", cid=cid))
    return render_template("classes/form.html", cls=c, kinds=KINDS,
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

import json
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from ..auth import login_required
from ..db import query, execute, log
from ..i18n import t
from ..util import derive_full_name, pick_avatar_color, age_from_dob, parse_int, smartcase
from ..engine import (this_month, resolve_lesson_fee, month_bounds, to_cents, rm,
                      billed_expected_for, attendance_stats, PAYMENT_METHODS)

bp = Blueprint("students", __name__)

STATUSES = ["active", "trial", "inactive", "left"]


def _subjects():
    row = query("SELECT result_subjects FROM settings WHERE id = 1", one=True)
    try:
        return json.loads(row["result_subjects"]) if row else []
    except (ValueError, TypeError):
        return []


def _resolve_family(f):
    """Return a family_id from the form: an existing pick, a newly typed name, or None."""
    new_name = smartcase(f.get("new_family"))
    if new_name:
        return execute("INSERT INTO families (name) VALUES (?)", (new_name,))
    fid = parse_int(f.get("family_id"))
    return fid or None


@bp.route("/")
@login_required
def index():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status") or ""
    sort = request.args.get("sort") or "name"

    where = ["1=1"]
    args = []
    if q:
        where.append("(s.full_name LIKE ? OR s.name_zh LIKE ? OR s.name_en LIKE ? OR s.school LIKE ?)")
        args += [f"%{q}%"] * 4
    if status in STATUSES:
        where.append("s.status = ?"); args.append(status)

    order = {"name": "s.full_name", "school": "s.school", "status": "s.status",
             "joined": "s.joined_date DESC"}.get(sort, "s.full_name")

    students = query(f"""
        SELECT s.*,
               (SELECT c.name FROM enrollments e JOIN classes c ON c.id = e.class_id
                 WHERE e.student_id = s.id AND e.status='active'
                 ORDER BY e.start_date DESC LIMIT 1) AS current_class
          FROM students s
         WHERE {' AND '.join(where)}
         ORDER BY {order}""", args)

    ym = this_month()
    rows = []
    for s in students:
        pr = query(
            """SELECT COALESCE(SUM(expected_cents),0) exp, COALESCE(SUM(paid_cents),0) paid,
                      MIN(CASE status WHEN 'overdue' THEN 0 WHEN 'pending' THEN 1
                                      WHEN 'partial' THEN 2 ELSE 3 END) worst
                 FROM payments WHERE student_id = ? AND month = ?""",
            (s["id"], ym), one=True)
        pay_status = "paid"
        if pr["exp"] or pr["paid"]:
            pay_status = {0: "overdue", 1: "pending", 2: "partial", 3: "paid"}.get(pr["worst"], "pending")
        elif s["status"] == "trial":
            pay_status = "trial"
        else:
            pay_status = "pending" if s["status"] == "active" else "—"
        fee_total = query(
            """SELECT COALESCE(SUM(expected_cents),0) e FROM payments
                 WHERE student_id = ? AND month = ?""", (s["id"], ym), one=True)["e"]
        rows.append({"s": s, "pay_status": pay_status, "fee": fee_total})

    return render_template("students/index.html", rows=rows, q=q, status=status,
                           sort=sort, statuses=STATUSES, ym=ym)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "POST":
        f = request.form
        name_en = smartcase(f.get("name_en"))
        name_zh = f.get("name_zh", "").strip()
        full = derive_full_name(name_en, name_zh)
        status = f.get("status") if f.get("status") in STATUSES else "active"
        age = parse_int(f.get("age")) or age_from_dob(f.get("dob"))
        sid = execute(
            """INSERT INTO students
                 (name_zh, name_en, full_name, phone, gender, dob, age, school, grade, address,
                  family_id, status, joined_date, trial_date, trial_remarks, avatar_color, remarks)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name_zh, name_en, full, f.get("phone", "").strip(), f.get("gender", ""),
             f.get("dob") or None, age, smartcase(f.get("school")), smartcase(f.get("grade")),
             smartcase(f.get("address")), _resolve_family(f),
             status, f.get("joined_date") or None, f.get("trial_date") or None,
             f.get("trial_remarks", "").strip(), pick_avatar_color(full),
             f.get("remarks", "").strip()))
        # optional parent 1
        _save_parent(sid, 1, f, prefix="p1_")
        log("student_add", full)
        flash(t("saved"), "ok")
        return redirect(url_for("students.detail", sid=sid))
    return render_template("students/form.html", student=None, statuses=STATUSES,
                           families=query("SELECT id, name FROM families ORDER BY name"))


def _save_parent(sid, slot, f, prefix):
    name_en = f.get(prefix + "name_en", "").strip()
    name_zh = f.get(prefix + "name_zh", "").strip()
    phone = f.get(prefix + "phone", "").strip()
    if not (name_en or name_zh or phone):
        execute("DELETE FROM parents WHERE student_id = ? AND slot = ?", (sid, slot))
        return
    existing = query("SELECT id FROM parents WHERE student_id = ? AND slot = ?", (sid, slot), one=True)
    vals = (name_zh, smartcase(name_en), phone, f.get(prefix + "relationship", "").strip(),
            f.get(prefix + "email", "").strip())
    if existing:
        execute("""UPDATE parents SET name_zh=?, name_en=?, phone=?, relationship=?, email=?
                    WHERE id=?""", vals + (existing["id"],))
    else:
        execute("""INSERT INTO parents (student_id, slot, name_zh, name_en, phone, relationship, email)
                   VALUES (?,?,?,?,?,?,?)""", (sid, slot) + vals)


@bp.route("/<int:sid>")
@login_required
def detail(sid):
    s = query("SELECT * FROM students WHERE id = ?", (sid,), one=True)
    if not s:
        abort(404)
    tab = request.args.get("tab") or "overview"
    parents = {p["slot"]: p for p in query("SELECT * FROM parents WHERE student_id = ? ORDER BY slot", (sid,))}

    enrollments = query(
        """SELECT e.*, c.name AS class_name, c.subject, c.fee_model AS class_fee_model
             FROM enrollments e JOIN classes c ON c.id = e.class_id
            WHERE e.student_id = ? ORDER BY e.status='ended', e.start_date DESC""", (sid,))
    today = this_month() + "-28"
    enr_view = [{"e": e, "lesson_fee": resolve_lesson_fee(sid, e["class_id"], today)}
                for e in enrollments]

    fees = query(
        """SELECT fx.*, c.name AS class_name FROM fees fx
             JOIN classes c ON c.id = fx.class_id
            WHERE fx.student_id = ? ORDER BY fx.effective_from DESC, fx.id DESC""", (sid,))

    all_results = query("SELECT * FROM results WHERE student_id = ?", (sid,))
    grouped = {}
    for r in all_results:
        grouped.setdefault(r["subject"] or "—", []).append(r)

    academic = {}   # subj -> list of {r, pct, delta} in chronological order
    spark = {}      # subj -> list of pct floats (chronological)
    for subj, rows in grouped.items():
        rows = sorted(rows, key=lambda x: (x["exam_date"] or "", x["id"]))
        entries, pts, prev = [], [], None
        for r in rows:
            pct = round(r["score"] / r["total"] * 100, 1) if (r["score"] is not None and r["total"]) else None
            delta = round(pct - prev, 1) if (pct is not None and prev is not None) else None
            entries.append({"r": r, "pct": pct, "delta": delta})
            if pct is not None:
                pts.append(pct)
                prev = pct
        academic[subj] = entries
        spark[subj] = pts

    ym = this_month()
    payments = query(
        """SELECT p.*, c.name AS class_name FROM payments p
             JOIN classes c ON c.id = p.class_id
            WHERE p.student_id = ? ORDER BY p.month DESC, c.name LIMIT 24""", (sid,))

    att = attendance_stats(student_id=sid)
    all_classes = query("SELECT id, name, fee_model, default_fee_cents FROM classes ORDER BY name")
    active_class_ids = {e["e"]["class_id"] for e in enr_view if e["e"]["status"] == "active"}

    family = None
    siblings = []
    if s["family_id"]:
        family = query("SELECT * FROM families WHERE id = ?", (s["family_id"],), one=True)
        siblings = query(
            "SELECT id, full_name, status FROM students WHERE family_id = ? AND id != ? ORDER BY full_name",
            (s["family_id"], sid))

    return render_template(
        "students/detail.html", s=s, tab=tab, parents=parents,
        enrollments=enr_view, fees=fees, academic=academic, spark=spark,
        payments=payments, att=att, subjects=_subjects(), ym=ym, statuses=STATUSES,
        all_classes=all_classes, active_class_ids=active_class_ids, methods=PAYMENT_METHODS,
        family=family, siblings=siblings)


@bp.route("/<int:sid>/edit", methods=["GET", "POST"])
@login_required
def edit(sid):
    s = query("SELECT * FROM students WHERE id = ?", (sid,), one=True)
    if not s:
        abort(404)
    if request.method == "POST":
        f = request.form
        name_en = smartcase(f.get("name_en"))
        name_zh = f.get("name_zh", "").strip()
        full = derive_full_name(name_en, name_zh)
        status = f.get("status") if f.get("status") in STATUSES else s["status"]
        age = parse_int(f.get("age")) or age_from_dob(f.get("dob"))
        execute(
            """UPDATE students SET name_zh=?, name_en=?, full_name=?, phone=?, gender=?,
                  dob=?, age=?, school=?, grade=?, address=?, family_id=?, status=?, joined_date=?,
                  trial_date=?, trial_remarks=?, left_date=?, left_reason=?, remarks=?,
                  updated_at=datetime('now')
                WHERE id=?""",
            (name_zh, name_en, full, f.get("phone", "").strip(), f.get("gender", ""),
             f.get("dob") or None, age, smartcase(f.get("school")), smartcase(f.get("grade")),
             smartcase(f.get("address")), _resolve_family(f),
             status, f.get("joined_date") or None, f.get("trial_date") or None,
             f.get("trial_remarks", "").strip(), f.get("left_date") or None,
             f.get("left_reason", "").strip(), f.get("remarks", "").strip(), sid))
        log("student_edit", full)
        flash(t("saved"), "ok")
        return redirect(url_for("students.detail", sid=sid))
    return render_template("students/form.html", student=s, statuses=STATUSES,
                           families=query("SELECT id, name FROM families ORDER BY name"))


@bp.route("/<int:sid>/delete", methods=["POST"])
@login_required
def delete(sid):
    s = query("SELECT full_name FROM students WHERE id = ?", (sid,), one=True)
    if not s:
        abort(404)
    execute("DELETE FROM students WHERE id = ?", (sid,))
    log("student_delete", s["full_name"])
    flash(t("deleted"), "ok")
    return redirect(url_for("students.index"))


@bp.route("/<int:sid>/parents", methods=["POST"])
@login_required
def save_parents(sid):
    if not query("SELECT 1 FROM students WHERE id = ?", (sid,), one=True):
        abort(404)
    f = request.form
    _save_parent(sid, 1, f, "p1_")
    _save_parent(sid, 2, f, "p2_")
    flash(t("saved"), "ok")
    return redirect(url_for("students.detail", sid=sid, tab="parents"))


@bp.route("/<int:sid>/results", methods=["POST"])
@login_required
def add_result(sid):
    if not query("SELECT 1 FROM students WHERE id = ?", (sid,), one=True):
        abort(404)
    f = request.form
    execute(
        """INSERT INTO results (student_id, exam_name, exam_date, subject, score, total, grade, remarks)
           VALUES (?,?,?,?,?,?,?,?)""",
        (sid, smartcase(f.get("exam_name")), f.get("exam_date") or None,
         smartcase(f.get("subject")), _tofloat(f.get("score")),
         _tofloat(f.get("total")) or 100, smartcase(f.get("grade")), f.get("remarks", "").strip()))
    flash(t("saved"), "ok")
    return redirect(url_for("students.detail", sid=sid, tab="results"))


def _tofloat(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@bp.route("/<int:sid>/results/<int:rid>/delete", methods=["POST"])
@login_required
def delete_result(sid, rid):
    execute("DELETE FROM results WHERE id = ? AND student_id = ?", (rid, sid))
    flash(t("deleted"), "ok")
    return redirect(url_for("students.detail", sid=sid, tab="results"))


@bp.route("/<int:sid>/enroll", methods=["POST"])
@login_required
def enroll(sid):
    if not query("SELECT 1 FROM students WHERE id = ?", (sid,), one=True):
        abort(404)
    f = request.form
    class_id = parse_int(f.get("class_id"))
    if not class_id:
        flash("Choose a class.", "error")
        return redirect(url_for("students.detail", sid=sid, tab="history"))
    start = f.get("start_date") or (this_month() + "-01")
    if query("SELECT 1 FROM enrollments WHERE student_id=? AND class_id=? AND status='active'",
             (sid, class_id), one=True):
        flash("Already enrolled.", "error")
        return redirect(url_for("students.detail", sid=sid, tab="history"))
    execute("INSERT INTO enrollments (student_id, class_id, start_date, status) VALUES (?,?,?,'active')",
            (sid, class_id, start))
    if (f.get("fee_amount") or "").strip():
        execute(
            """INSERT INTO fees (student_id, class_id, fee_model, amount_cents, discount_cents,
                                 effective_from, remarks)
               VALUES (?,?,'per_lesson',?,?,?,?)""",
            (sid, class_id, to_cents(f.get("fee_amount")), to_cents(f.get("fee_discount")),
             start, "set on enrollment"))
    else:
        cls = query("SELECT pricing FROM classes WHERE id = ?", (class_id,), one=True)
        if cls and cls["pricing"] == "per_student":
            flash(t("fee_reminder"), "error")
    log("enroll", f"student {sid} -> class {class_id}")
    flash(t("saved"), "ok")
    return redirect(url_for("students.detail", sid=sid, tab="history"))


@bp.route("/<int:sid>/fees", methods=["POST"])
@login_required
def add_fee(sid):
    if not query("SELECT 1 FROM students WHERE id = ?", (sid,), one=True):
        abort(404)
    f = request.form
    class_id = parse_int(f.get("class_id"))
    if not class_id:
        flash("Choose a class.", "error")
        return redirect(url_for("students.detail", sid=sid, tab="finance"))
    model = "per_lesson" if f.get("fee_model") == "per_lesson" else "monthly"
    eff = f.get("effective_from") or (this_month() + "-01")
    # close the previous open fee of the SAME model for this student+class
    prev = query(
        """SELECT id FROM fees WHERE student_id=? AND class_id=? AND fee_model=?
             AND (effective_to IS NULL OR effective_to='')
             ORDER BY effective_from DESC LIMIT 1""", (sid, class_id, model), one=True)
    if prev:
        execute("UPDATE fees SET effective_to = date(?, '-1 day') WHERE id = ?", (eff, prev["id"]))
    execute(
        """INSERT INTO fees (student_id, class_id, fee_model, amount_cents, discount_cents,
                             effective_from, remarks)
           VALUES (?,?,?,?,?,?,?)""",
        (sid, class_id, model, to_cents(f.get("amount")), to_cents(f.get("discount")),
         eff, f.get("remarks", "").strip()))
    log("fee_add", f"student {sid} class {class_id}")
    flash(t("saved"), "ok")
    return redirect(url_for("students.detail", sid=sid, tab="finance"))


@bp.route("/<int:sid>/fees/<int:fid>/delete", methods=["POST"])
@login_required
def delete_fee(sid, fid):
    execute("DELETE FROM fees WHERE id = ? AND student_id = ?", (fid, sid))
    flash(t("deleted"), "ok")
    return redirect(url_for("students.detail", sid=sid, tab="finance"))


# ── families ───────────────────────────────────────────────────────
@bp.route("/families")
@login_required
def families_index():
    fams = query(
        """SELECT f.*, COUNT(s.id) AS n FROM families f
             LEFT JOIN students s ON s.family_id = f.id
            GROUP BY f.id ORDER BY f.name""")
    members = {
        f["id"]: query("SELECT id, full_name, status FROM students WHERE family_id = ? ORDER BY full_name",
                       (f["id"],))
        for f in fams
    }
    unassigned = query(
        "SELECT id, full_name FROM students WHERE family_id IS NULL AND status != 'left' ORDER BY full_name")
    return render_template("students/families.html", families=fams, members=members,
                           unassigned=unassigned)


@bp.route("/families/new", methods=["POST"])
@login_required
def family_new():
    name = smartcase(request.form.get("name"))
    if name:
        fid = execute("INSERT INTO families (name) VALUES (?)", (name,))
        for sid in request.form.getlist("student_ids"):
            execute("UPDATE students SET family_id = ? WHERE id = ?", (fid, parse_int(sid)))
        flash(t("saved"), "ok")
    return redirect(url_for("students.families_index"))


@bp.route("/families/<int:fid>/edit", methods=["POST"])
@login_required
def family_edit(fid):
    name = smartcase(request.form.get("name"))
    if name:
        execute("UPDATE families SET name = ? WHERE id = ?", (name, fid))
    for sid in request.form.getlist("add_ids"):
        execute("UPDATE students SET family_id = ? WHERE id = ?", (fid, parse_int(sid)))
    for sid in request.form.getlist("remove_ids"):
        execute("UPDATE students SET family_id = NULL WHERE id = ? AND family_id = ?", (parse_int(sid), fid))
    flash(t("saved"), "ok")
    return redirect(url_for("students.families_index"))


@bp.route("/families/<int:fid>/delete", methods=["POST"])
@login_required
def family_delete(fid):
    execute("UPDATE students SET family_id = NULL WHERE family_id = ?", (fid,))
    execute("DELETE FROM families WHERE id = ?", (fid,))
    flash(t("deleted"), "ok")
    return redirect(url_for("students.families_index"))

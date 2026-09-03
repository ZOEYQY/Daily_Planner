"""Fee, forecast, billing and statistics engine.

Money is integer sen everywhere in here. Two ideas kept strictly apart:

  * billed_expected_for()   — what the student actually owes this month, based on
                              recorded attendance. Drives the payment records.
  * forecast_expected_for() — a projection for month-start planning, based on the
                              class weekly schedule when attendance isn't in yet.
"""
import json
import calendar
import datetime as dt

from .db import query, execute, log

BILLABLE_PRESENT = ("present", "replacement")

# order matters — this is the option order in the attendance dropdowns
STATUS_KEYS = ["present", "absent", "student_leave", "teacher_leave", "holiday", "trial"]
# statuses where the student was expected (denominator of the attendance rate).
# 'replacement' kept for old data — a student who came for a make-up counts as present.
APPLICABLE = ("present", "absent", "student_leave", "replacement")
# a student didn't turn up -> ask about a make-up
NO_SHOW = ("absent", "student_leave")
# selecting one of these opens the make-up popup (预计 + 最后决定)
MAKEUP_STATUSES = ("absent", "student_leave", "teacher_leave", "holiday")


# ── money ───────────────────────────────────────────────────────────
def to_cents(value):
    """Accept '120', '120.50', 120 -> integer sen. Blank -> 0."""
    if value is None:
        return 0
    s = str(value).strip().replace(",", "")
    if not s:
        return 0
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return 0


def rm(cents):
    cents = int(cents or 0)
    if cents % 100 == 0:
        return f"{cents // 100:,}"
    return f"{cents / 100:,.2f}"


# ── dates ───────────────────────────────────────────────────────────
def month_bounds(ym):
    y, m = (int(x) for x in ym.split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{ym}-01", f"{ym}-{last:02d}"


def this_month():
    return dt.date.today().strftime("%Y-%m")


def add_months(ym, delta):
    y, m = (int(x) for x in ym.split("-"))
    idx = (y * 12 + (m - 1)) + delta
    return f"{idx // 12}-{idx % 12 + 1:02d}"


def scheduled_dates(weekday, ym):
    """All dates in month `ym` that fall on `weekday` (0=Mon..6=Sun)."""
    if weekday is None or weekday == "":
        return []
    weekday = int(weekday)
    y, m = (int(x) for x in ym.split("-"))
    last = calendar.monthrange(y, m)[1]
    out = []
    for d in range(1, last + 1):
        date = dt.date(y, m, d)
        if date.weekday() == weekday:
            out.append(date.isoformat())
    return out


def class_weekdays(class_id):
    return [r["weekday"] for r in
            query("SELECT weekday FROM class_schedule WHERE class_id = ? ORDER BY weekday", (class_id,))]


def class_scheduled_dates(class_id, ym):
    """Every date in `ym` this class is scheduled to run (union of all its weekdays)."""
    out = set()
    for wd in class_weekdays(class_id):
        out.update(scheduled_dates(wd, ym))
    return sorted(out)


# ── fee resolution ──────────────────────────────────────────────────
def resolve_fee(student_id, class_id, on_date):
    """The fee arrangement in effect for this student+class on `on_date`.

    Falls back to the class default. Returns a dict.
    """
    row = query(
        """SELECT * FROM fees
             WHERE student_id = ? AND class_id = ?
               AND effective_from <= ?
               AND (effective_to IS NULL OR effective_to = '' OR effective_to >= ?)
             ORDER BY effective_from DESC, id DESC LIMIT 1""",
        (student_id, class_id, on_date, on_date),
        one=True,
    )
    if row:
        return {
            "fee_model": row["fee_model"],
            "amount_cents": row["amount_cents"],
            "discount_cents": row["discount_cents"] or 0,
            "source": "own",
        }
    cls = query("SELECT fee_model, default_fee_cents FROM classes WHERE id = ?",
                (class_id,), one=True)
    if not cls:
        return {"fee_model": "monthly", "amount_cents": 0, "discount_cents": 0, "source": "none"}
    return {
        "fee_model": cls["fee_model"],
        "amount_cents": cls["default_fee_cents"] or 0,
        "discount_cents": 0,
        "source": "class",
    }


def _charge_absence():
    s = query("SELECT charge_absence FROM settings WHERE id = 1", one=True)
    return bool(s and s["charge_absence"])


def billable_lesson_count(student_id, class_id, ym):
    first, last = month_bounds(ym)
    rows = query(
        "SELECT status FROM attendance WHERE student_id = ? AND class_id = ? AND date BETWEEN ? AND ?",
        (student_id, class_id, first, last),
    )
    charge_absent = _charge_absence()
    n = 0
    for r in rows:
        if r["status"] in BILLABLE_PRESENT:
            n += 1
        elif r["status"] in ("absent", "student_leave") and charge_absent:
            n += 1
    return n


def _class_kind(class_id):
    c = query("SELECT kind FROM classes WHERE id = ?", (class_id,), one=True)
    return (c["kind"] if c and c["kind"] else "")


def student_attended(student_id, class_id, ym):
    """Did this student turn up to at least one lesson of this class this month?"""
    first, last = month_bounds(ym)
    return query(
        """SELECT 1 FROM attendance
             WHERE student_id=? AND class_id=? AND date BETWEEN ? AND ?
               AND status IN ('present', 'replacement') LIMIT 1""",
        (student_id, class_id, first, last), one=True) is not None


def class_ran(class_id, ym):
    """Did this class run at least one session this month (anyone present)?"""
    first, last = month_bounds(ym)
    return query(
        """SELECT 1 FROM attendance
             WHERE class_id=? AND date BETWEEN ? AND ?
               AND status IN ('present', 'replacement') LIMIT 1""",
        (class_id, first, last), one=True) is not None


def enrollment_overlaps_month(start_date, end_date, ym):
    first, last = month_bounds(ym)
    if start_date and start_date > last:
        return False
    if end_date and end_date < first:
        return False
    return True


# ── expected (actual, drives payments) ──────────────────────────────
# The fee is ONE fixed amount per month ("整个课一个学费"). Whether it is
# charged depends on the class type:
#   1v1   -> charged only if the student actually attended >= 1 lesson
#   小班/其他 -> charged if the class ran >= 1 session that month (slot was held),
#            regardless of this student's own attendance
def billed_expected_for(student_id, class_id, ym, student_status=None):
    """What the student owes for this class this month, from recorded attendance."""
    if student_status is None:
        s = query("SELECT status FROM students WHERE id = ?", (student_id,), one=True)
        student_status = s["status"] if s else "active"
    if student_status == "trial":
        return 0

    _, last = month_bounds(ym)
    fee = resolve_fee(student_id, class_id, last)
    if _class_kind(class_id) == "1v1":
        charged = student_attended(student_id, class_id, ym)
    else:
        charged = class_ran(class_id, ym)
    if not charged:
        return 0
    return max(0, fee["amount_cents"] - fee["discount_cents"])


def forecast_expected_for(student_id, class_id, ym, student_status=None):
    """Month-start projection: assume the month runs normally -> the full fee."""
    if student_status is None:
        s = query("SELECT status FROM students WHERE id = ?", (student_id,), one=True)
        student_status = s["status"] if s else "active"
    if student_status == "trial":
        return 0
    _, last = month_bounds(ym)
    fee = resolve_fee(student_id, class_id, last)
    return max(0, fee["amount_cents"] - fee["discount_cents"])


# ── payment records ─────────────────────────────────────────────────
def recompute_status(expected, paid, month, has_payment_date):
    if expected <= 0 and paid <= 0:
        return "pending"
    if paid <= 0:
        if month < this_month():
            return "overdue"
        return "pending"
    if paid >= expected:
        return "paid"
    return "partial"


def sync_month(ym):
    """Create / refresh a payment record for every active enrollment overlapping
    `ym`. Preserves recorded payments; only auto rows have `expected` refreshed.
    No-ops when the month is closed."""
    if is_closed(ym):
        return 0
    enrolls = query(
        """SELECT e.id AS enr_id, e.student_id, e.class_id, e.start_date, e.end_date,
                  s.status AS student_status
             FROM enrollments e
             JOIN students s ON s.id = e.student_id""")
    touched = 0
    for e in enrolls:
        if not enrollment_overlaps_month(e["start_date"], e["end_date"], ym):
            continue
        if e["student_status"] in ("left", "inactive"):
            # only bill if the enrollment was actually active in-month; keep simple: skip
            existing = query(
                "SELECT id FROM payments WHERE student_id=? AND class_id=? AND month=?",
                (e["student_id"], e["class_id"], ym), one=True)
            if not existing:
                continue
        expected = billed_expected_for(e["student_id"], e["class_id"], ym, e["student_status"])
        row = query(
            "SELECT * FROM payments WHERE student_id=? AND class_id=? AND month=?",
            (e["student_id"], e["class_id"], ym), one=True)
        if row is None:
            paid = 0
            status = recompute_status(expected, paid, ym, False)
            execute(
                """INSERT INTO payments (student_id, class_id, month, expected_cents,
                                         paid_cents, status, auto_expected)
                   VALUES (?, ?, ?, ?, 0, ?, 1)""",
                (e["student_id"], e["class_id"], ym, expected, status))
            touched += 1
        elif row["auto_expected"]:
            status = recompute_status(expected, row["paid_cents"], ym, bool(row["payment_date"]))
            execute(
                "UPDATE payments SET expected_cents=?, status=?, updated_at=datetime('now') WHERE id=?",
                (expected, status, row["id"]))
            touched += 1
    return touched


# ── monthly aggregates ──────────────────────────────────────────────
def month_totals(ym):
    r = query(
        """SELECT COALESCE(SUM(expected_cents),0) AS expected,
                  COALESCE(SUM(paid_cents),0)     AS paid
             FROM payments WHERE month = ?""", (ym,), one=True)
    expected = r["expected"]
    collected = r["paid"]
    outstanding = max(0, expected - collected)
    rate = (collected / expected * 100) if expected else 0.0
    return {"expected": expected, "collected": collected,
            "outstanding": outstanding, "rate": rate}


def forecast_total(ym):
    """Estimated income: sum forecast over active enrollments of non-trial students."""
    enrolls = query(
        """SELECT e.student_id, e.class_id, e.start_date, e.end_date, s.status
             FROM enrollments e JOIN students s ON s.id = e.student_id
             WHERE e.status = 'active' AND s.status IN ('active')""")
    total = 0
    for e in enrolls:
        if not enrollment_overlaps_month(e["start_date"], e["end_date"], ym):
            continue
        total += forecast_expected_for(e["student_id"], e["class_id"], ym, e["status"])
    return total


def show_collected(ym):
    """Fees are collected at month-end — only surface 'collected' in the last week
    of the current month (always shown for past / closed months)."""
    if ym < this_month() or is_closed(ym):
        return True
    if ym > this_month():
        return False
    _, last = month_bounds(ym)
    last_day = int(last[-2:])
    return dt.date.today().day >= last_day - 6


def class_stats(class_id, ym):
    """The at-a-glance numbers for one class in one month."""
    first, last = month_bounds(ym)
    n_students = query(
        """SELECT COUNT(*) n FROM enrollments
             WHERE class_id = ? AND status = 'active'
               AND (start_date IS NULL OR start_date <= ?)
               AND (end_date IS NULL OR end_date = '' OR end_date >= ?)""",
        (class_id, last, first), one=True)["n"]

    scheduled_lessons = len(class_scheduled_dates(class_id, ym))
    held_lessons = query(
        "SELECT COUNT(DISTINCT date) n FROM attendance WHERE class_id = ? AND date BETWEEN ? AND ?",
        (class_id, first, last), one=True)["n"]

    projected = 0
    for e in query("""SELECT student_id, status FROM enrollments
                        WHERE class_id = ? AND status = 'active'""", (class_id,)):
        projected += forecast_expected_for(e["student_id"], class_id, ym)

    fin = class_month_finance(class_id, ym)
    reveal = show_collected(ym)
    return {
        "n_students": n_students,
        "projected_fee": projected,
        "scheduled_lessons": scheduled_lessons,
        "held_lessons": held_lessons,
        "due": fin["expected"],
        "outstanding": max(0, fin["expected"] - fin["collected"]),
        "collected": fin["collected"],
        "show_collected": reveal,
        "rows": fin["rows"],
        "rate": fin["rate"],
    }


def class_month_finance(class_id, ym):
    rows = query(
        """SELECT p.*, s.full_name FROM payments p
             JOIN students s ON s.id = p.student_id
             WHERE p.class_id = ? AND p.month = ?
             ORDER BY s.full_name""", (class_id, ym))
    expected = sum(r["expected_cents"] for r in rows)
    collected = sum(r["paid_cents"] for r in rows)
    return {
        "rows": rows,
        "expected": expected,
        "collected": collected,
        "outstanding": max(0, expected - collected),
        "rate": (collected / expected * 100) if expected else 0.0,
    }


# ── family billing ──────────────────────────────────────────────────
def families_finance(ym, min_members=2):
    """One entry per family that has >= min_members students billed this month."""
    rows = query(
        """SELECT f.id, f.name,
                  COUNT(DISTINCT p.student_id)        AS members,
                  COALESCE(SUM(p.expected_cents), 0)  AS expected,
                  COALESCE(SUM(p.paid_cents), 0)      AS paid,
                  GROUP_CONCAT(DISTINCT s.full_name)  AS names
             FROM families f
             JOIN students s ON s.family_id = f.id
             JOIN payments p ON p.student_id = s.id AND p.month = ?
            GROUP BY f.id
           HAVING members >= ?
            ORDER BY f.name""", (ym, min_members))
    out = []
    for r in rows:
        expected, paid = r["expected"], r["paid"]
        out.append({
            "id": r["id"], "name": r["name"], "members": r["members"],
            "names": (r["names"] or "").split(","),
            "expected": expected, "paid": paid,
            "outstanding": max(0, expected - paid),
            "rate": (paid / expected * 100) if expected else 0.0,
        })
    return out


def family_month_rows(family_id, ym):
    return query(
        """SELECT p.*, s.full_name, c.name AS class_name
             FROM payments p
             JOIN students s ON s.id = p.student_id
             JOIN classes  c ON c.id = p.class_id
            WHERE s.family_id = ? AND p.month = ?
            ORDER BY p.status='paid', s.full_name, c.name""", (family_id, ym))


def apply_family_payment(family_id, ym, amount_cents, date, method, reference, remarks=""):
    """Spread one lump sum across the family's outstanding rows for the month,
    smallest-outstanding students... no — in student/class order. Leftover is added
    to the last row as an overpayment so total recorded == amount handed over."""
    rows = query(
        """SELECT p.* FROM payments p JOIN students s ON s.id = p.student_id
            WHERE s.family_id = ? AND p.month = ?
            ORDER BY s.full_name, p.id""", (family_id, ym))
    if not rows:
        return 0
    left = amount_cents
    touched = 0
    last_outstanding = None
    for r in rows:
        need = r["expected_cents"] - r["paid_cents"]
        if need <= 0:
            continue
        last_outstanding = r
        if left <= 0:
            continue
        pay = min(need, left)
        new_paid = r["paid_cents"] + pay
        left -= pay
        status = recompute_status(r["expected_cents"], new_paid, ym, True)
        execute(
            """UPDATE payments SET paid_cents=?, status=?, payment_date=?, method=?,
                 reference=?, remarks=?, updated_at=datetime('now') WHERE id=?""",
            (new_paid, status, date or None, method, reference, remarks, r["id"]))
        touched += 1
    if left > 0:
        tail = last_outstanding or rows[-1]
        execute(
            """UPDATE payments SET paid_cents = paid_cents + ?, status='paid',
                 payment_date=?, method=?, reference=?, updated_at=datetime('now') WHERE id=?""",
            (left, date or None, method, reference, tail["id"]))
        if last_outstanding is None:
            touched += 1
    log("family_payment", f"family {family_id} {ym}: RM {amount_cents / 100:.2f}")
    return touched


# ── attendance statistics ───────────────────────────────────────────
def attendance_stats(class_id=None, student_id=None, date_from=None, date_to=None):
    where = ["1=1"]
    args = []
    if class_id:
        where.append("class_id = ?"); args.append(class_id)
    if student_id:
        where.append("student_id = ?"); args.append(student_id)
    if date_from:
        where.append("date >= ?"); args.append(date_from)
    if date_to:
        where.append("date <= ?"); args.append(date_to)
    rows = query(f"SELECT status, COUNT(*) n FROM attendance WHERE {' AND '.join(where)} GROUP BY status", args)
    counts = {k: 0 for k in STATUS_KEYS + ["replacement"]}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + r["n"]
    applicable = sum(counts.get(k, 0) for k in APPLICABLE)
    present = counts["present"] + counts.get("replacement", 0)
    counts["applicable"] = applicable
    counts["rate"] = (present / applicable * 100) if applicable else 0.0
    counts["total"] = sum(counts[k] for k in STATUS_KEYS) + counts.get("replacement", 0)
    return counts


# ── dashboard ───────────────────────────────────────────────────────
def dashboard_metrics(ym):
    first, last = month_bounds(ym)
    today = dt.date.today().isoformat()
    today_wd = dt.date.today().weekday()

    students = query("SELECT status, joined_date, left_date FROM students")
    total_students = len(students)
    active = sum(1 for s in students if s["status"] == "active")
    trial = sum(1 for s in students if s["status"] == "trial")
    new_month = sum(1 for s in students if s["joined_date"] and first <= s["joined_date"] <= last)
    leaving = sum(1 for s in students if s["left_date"] and first <= s["left_date"] <= last)

    classes = query("SELECT id, status FROM classes")
    total_classes = len(classes)
    active_classes = sum(1 for c in classes if c["status"] == "active")
    today_ids = {r["class_id"] for r in
                 query("SELECT class_id FROM class_schedule WHERE weekday = ?", (today_wd,))}
    todays = [c for c in classes if c["status"] == "active" and c["id"] in today_ids]
    students_today = 0
    if todays:
        ids = ",".join(str(c["id"]) for c in todays)
        r = query(
            f"""SELECT COUNT(*) n FROM enrollments
                  WHERE status='active' AND class_id IN ({ids})
                    AND (start_date IS NULL OR start_date <= ?)
                    AND (end_date IS NULL OR end_date >= ?)""", (today, today), one=True)
        students_today = r["n"]

    att = attendance_stats(date_from=first, date_to=last)
    fin = month_totals(ym)
    forecast = forecast_total(ym)
    snap = query("SELECT * FROM monthly_finance WHERE month = ?", (ym,), one=True)

    return {
        "total_students": total_students, "active_students": active,
        "trial_students": trial, "new_this_month": new_month, "leaving_this_month": leaving,
        "total_classes": total_classes, "active_classes": active_classes,
        "todays_classes": len(todays), "students_today": students_today,
        "attendance": att,
        "finance": fin, "forecast": forecast,
        "closed": snap is not None,
        "actual_income": snap["collected_cents"] if snap else fin["collected"],
    }


# ── monthly closing ─────────────────────────────────────────────────
def is_closed(ym):
    return query("SELECT 1 FROM monthly_finance WHERE month = ?", (ym,), one=True) is not None


def close_month(ym):
    first, last = month_bounds(ym)
    fin = month_totals(ym)
    students = query("SELECT COUNT(*) n FROM students WHERE status IN ('active','trial')", one=True)["n"]
    classes = query("SELECT COUNT(*) n FROM classes WHERE status='active'", one=True)["n"]
    att = attendance_stats(date_from=first, date_to=last)
    snap = json.dumps({"finance": fin, "attendance": att})
    execute(
        """INSERT INTO monthly_finance
             (month, expected_cents, collected_cents, outstanding_cents,
              total_students, total_classes, attendance_rate, snapshot_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(month) DO UPDATE SET
             expected_cents=excluded.expected_cents,
             collected_cents=excluded.collected_cents,
             outstanding_cents=excluded.outstanding_cents,
             total_students=excluded.total_students,
             total_classes=excluded.total_classes,
             attendance_rate=excluded.attendance_rate,
             snapshot_json=excluded.snapshot_json,
             closed_at=datetime('now')""",
        (ym, fin["expected"], fin["collected"], fin["outstanding"],
         students, classes, att["rate"], snap))
    log("close_month", ym)


def reopen_month(ym):
    execute("DELETE FROM monthly_finance WHERE month = ?", (ym,))
    log("reopen_month", ym)

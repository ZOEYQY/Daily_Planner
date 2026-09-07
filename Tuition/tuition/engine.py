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
# payment methods offered when recording a payment (finance + student pages)
PAYMENT_METHODS = ("cash", "bank", "duitnow", "other")
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
    # Monthly rows only — a per-lesson override row (fee_model='per_lesson',
    # used solely by the Bills page via resolve_lesson_fee) must never be read
    # here as if it were the fixed monthly fee.
    row = query(
        """SELECT * FROM fees
             WHERE student_id = ? AND class_id = ?
               AND (fee_model = 'monthly' OR fee_model IS NULL OR fee_model = '')
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


# ── billing modes (per-lesson: fee = amount × lessons that ran) ─────
BILL_MODES = ("student_attend", "class_ran", "class_flat", "agent_headcount")
# modes billed as ONE whole-class bill (class_bills table), not per student
CLASS_LEVEL_MODES = ("class_flat", "agent_headcount")


def class_bill_mode(class_id):
    c = query("SELECT bill_mode, kind FROM classes WHERE id = ?", (class_id,), one=True)
    if not c:
        return "student_attend"
    if c["kind"] == "1v1":
        return "student_attend"
    return c["bill_mode"] if c["bill_mode"] in BILL_MODES else "student_attend"


def class_enrolled_count(class_id, ym):
    """Active enrollments overlapping the month — the N in agent_headcount."""
    first, last = month_bounds(ym)
    return query(
        """SELECT COUNT(*) n FROM enrollments
             WHERE class_id = ? AND status = 'active'
               AND (start_date IS NULL OR start_date <= ?)
               AND (end_date IS NULL OR end_date = '' OR end_date >= ?)""",
        (class_id, last, first), one=True)["n"]


def student_attended_count(student_id, class_id, ym, since=None, until=None):
    """Distinct dates this student was present/replacement this month."""
    first, last = month_bounds(ym)
    lo = max(first, since) if since else first
    hi = min(last, until) if until else last
    if lo > hi:
        return 0
    r = query(
        """SELECT COUNT(DISTINCT date) n FROM attendance
             WHERE student_id = ? AND class_id = ? AND status IN ('present', 'replacement')
               AND date BETWEEN ? AND ?""",
        (student_id, class_id, lo, hi), one=True)
    return r["n"] if r else 0


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
# Per-lesson billing: fee = per-lesson amount × lessons that ran. Which
# lessons count depends on classes.bill_mode (see class_bill_mode):
#   student_attend  -> the student's own per-lesson rate × their own present dates
#   class_ran       -> the student's own per-lesson rate × dates the class ran
#   class_flat / agent_headcount -> a WHOLE-CLASS bill, not per student (0 here,
#                       see class_bill_expected + the class_bills table)
def _student_lesson_count(student_id, class_id, ym, mode, since, until):
    if mode == "student_attend":
        return student_attended_count(student_id, class_id, ym, since, until)
    return held_lesson_count(class_id, ym, since, until)  # class_ran


def billed_expected_for(student_id, class_id, ym, student_status=None):
    """What the student owes for this class this month, from recorded attendance."""
    if student_status is None:
        s = query("SELECT status FROM students WHERE id = ?", (student_id,), one=True)
        student_status = s["status"] if s else "active"
    if student_status == "trial":
        return 0
    mode = class_bill_mode(class_id)
    if mode in CLASS_LEVEL_MODES:
        return 0

    _, last = month_bounds(ym)
    enr = query(
        """SELECT start_date, end_date FROM enrollments
             WHERE student_id = ? AND class_id = ? ORDER BY status='ended', id DESC LIMIT 1""",
        (student_id, class_id), one=True)
    since = enr["start_date"] if enr else None
    until = enr["end_date"] if enr and enr["end_date"] else None
    count = _student_lesson_count(student_id, class_id, ym, mode, since, until)
    return resolve_lesson_fee(student_id, class_id, last) * count


def forecast_expected_for(student_id, class_id, ym, student_status=None):
    """Month-start projection: assume every scheduled lesson runs."""
    if student_status is None:
        s = query("SELECT status FROM students WHERE id = ?", (student_id,), one=True)
        student_status = s["status"] if s else "active"
    if student_status == "trial":
        return 0
    if class_bill_mode(class_id) in CLASS_LEVEL_MODES:
        return 0
    _, last = month_bounds(ym)
    lessons = len(class_scheduled_dates(class_id, ym)) or 1
    return resolve_lesson_fee(student_id, class_id, last) * lessons


def class_bill_expected(class_id, ym, actual=True):
    """The whole-class bill for a class_flat / agent_headcount class.
    `actual` = from recorded attendance; otherwise a schedule-based forecast."""
    c = query("""SELECT bill_mode, lesson_fee_cents, base_fee_cents, base_head_count, per_head_cents
                   FROM classes WHERE id = ?""", (class_id,), one=True)
    if not c or c["bill_mode"] not in CLASS_LEVEL_MODES:
        return 0
    lessons = held_lesson_count(class_id, ym) if actual else (len(class_scheduled_dates(class_id, ym)) or 1)
    if lessons == 0:
        return 0
    if c["bill_mode"] == "class_flat":
        per_lesson = c["lesson_fee_cents"] or 0
    else:  # agent_headcount — base covers the first base_head_count students,
           # per_head applies only to each student beyond that
        n = class_enrolled_count(class_id, ym)
        extra = max(0, n - (c["base_head_count"] or 0))
        per_lesson = (c["base_fee_cents"] or 0) + (c["per_head_cents"] or 0) * extra
    return per_lesson * lessons


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


def _derive_flat_family(class_id):
    """The family a flat-class-fee class should bill to when none was picked:
    the family shared by its active students (only when they all share one)."""
    rows = query(
        """SELECT DISTINCT s.family_id FROM enrollments e
             JOIN students s ON s.id = e.student_id
            WHERE e.class_id = ? AND e.status = 'active' AND s.family_id IS NOT NULL""",
        (class_id,))
    return rows[0]["family_id"] if len(rows) == 1 else None


def sync_month(ym):
    """Refresh the month's bills. Per-student classes get one `payments` row per
    active enrollment; class-level classes (class_flat / agent_headcount) get one
    `class_bills` row. Preserves recorded payments; only auto rows have `expected`
    refreshed. No-ops when the month is closed."""
    if is_closed(ym):
        return 0
    touched = 0

    # A flat-class-fee class always bills a family; when none was picked, adopt
    # the family of its students so the bill isn't stranded (self-heals here so
    # every finance/bills page benefits without a separate step).
    for c in query(
            "SELECT id FROM classes WHERE status='active' AND bill_mode='class_flat' AND billed_family_id IS NULL"):
        fam = _derive_flat_family(c["id"])
        if fam:
            execute("UPDATE classes SET billed_family_id=? WHERE id=?", (fam, c["id"]))

    enrolls = query(
        """SELECT e.student_id, e.class_id, e.start_date, e.end_date, s.status AS student_status
             FROM enrollments e JOIN students s ON s.id = e.student_id""")
    for e in enrolls:
        if not enrollment_overlaps_month(e["start_date"], e["end_date"], ym):
            continue
        row = query(
            "SELECT * FROM payments WHERE student_id=? AND class_id=? AND month=?",
            (e["student_id"], e["class_id"], ym), one=True)
        # class-level mode -> no per-student row; drop any stale auto one
        if class_bill_mode(e["class_id"]) in CLASS_LEVEL_MODES:
            if row and row["auto_expected"] and not row["paid_cents"]:
                execute("DELETE FROM payments WHERE id=?", (row["id"],))
                touched += 1
            continue
        if e["student_status"] in ("left", "inactive") and not row:
            continue
        expected = billed_expected_for(e["student_id"], e["class_id"], ym, e["student_status"])
        if row is None:
            status = recompute_status(expected, 0, ym, False)
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

    # class-level bills, one per active class in a class-level mode
    for c in query("SELECT id FROM classes WHERE status = 'active'"):
        cid = c["id"]
        cb = query("SELECT * FROM class_bills WHERE class_id=? AND month=?", (cid, ym), one=True)
        if class_bill_mode(cid) not in CLASS_LEVEL_MODES:
            if cb and cb["auto_expected"] and not cb["paid_cents"]:
                execute("DELETE FROM class_bills WHERE id=?", (cb["id"],))
                touched += 1
            continue
        expected = class_bill_expected(cid, ym, actual=True)
        if cb is None:
            status = recompute_status(expected, 0, ym, False)
            execute(
                """INSERT INTO class_bills (class_id, month, expected_cents, paid_cents, status, auto_expected)
                   VALUES (?, ?, ?, 0, ?, 1)""",
                (cid, ym, expected, status))
            touched += 1
        elif cb["auto_expected"]:
            status = recompute_status(expected, cb["paid_cents"], ym, bool(cb["payment_date"]))
            execute(
                "UPDATE class_bills SET expected_cents=?, status=?, updated_at=datetime('now') WHERE id=?",
                (expected, status, cb["id"]))
            touched += 1
    return touched


# ── monthly aggregates ──────────────────────────────────────────────
def month_totals(ym):
    """Expected / collected for the month across BOTH per-student `payments` and
    whole-class `class_bills`."""
    p = query(
        """SELECT COALESCE(SUM(expected_cents),0) AS e, COALESCE(SUM(paid_cents),0) AS p
             FROM payments WHERE month = ?""", (ym,), one=True)
    b = query(
        """SELECT COALESCE(SUM(expected_cents),0) AS e, COALESCE(SUM(paid_cents),0) AS p
             FROM class_bills WHERE month = ?""", (ym,), one=True)
    expected = p["e"] + b["e"]
    collected = p["p"] + b["p"]
    return {"expected": expected, "collected": collected,
            "outstanding": max(0, expected - collected),
            "rate": (collected / expected * 100) if expected else 0.0}


def forecast_total(ym):
    """Estimated income: forecast over active enrollments of non-trial students,
    plus the schedule-based forecast for every class-level-mode class."""
    enrolls = query(
        """SELECT e.student_id, e.class_id, e.start_date, e.end_date, s.status
             FROM enrollments e JOIN students s ON s.id = e.student_id
             WHERE e.status = 'active' AND s.status IN ('active')""")
    total = 0
    for e in enrolls:
        if not enrollment_overlaps_month(e["start_date"], e["end_date"], ym):
            continue
        total += forecast_expected_for(e["student_id"], e["class_id"], ym, e["status"])
    for c in query("SELECT id FROM classes WHERE status = 'active'"):
        if class_bill_mode(c["id"]) in CLASS_LEVEL_MODES:
            total += class_bill_expected(c["id"], ym, actual=False)
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

    if class_bill_mode(class_id) in CLASS_LEVEL_MODES:
        projected = class_bill_expected(class_id, ym, actual=False)
    else:
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


def class_bill_row(class_id, ym):
    return query("SELECT * FROM class_bills WHERE class_id=? AND month=?", (class_id, ym), one=True)


def class_month_finance(class_id, ym):
    """Per-student payment rows for a per-student class, OR the single class_bills
    row for a class-level class. `class_bill` is set in the latter case."""
    if class_bill_mode(class_id) in CLASS_LEVEL_MODES:
        cb = class_bill_row(class_id, ym)
        expected = cb["expected_cents"] if cb else 0
        collected = cb["paid_cents"] if cb else 0
        return {"rows": [], "class_bill": cb, "expected": expected, "collected": collected,
                "outstanding": max(0, expected - collected),
                "rate": (collected / expected * 100) if expected else 0.0}
    rows = query(
        """SELECT p.*, s.full_name FROM payments p
             JOIN students s ON s.id = p.student_id
             WHERE p.class_id = ? AND p.month = ?
             ORDER BY s.full_name""", (class_id, ym))
    expected = sum(r["expected_cents"] for r in rows)
    collected = sum(r["paid_cents"] for r in rows)
    return {
        "rows": rows, "class_bill": None,
        "expected": expected,
        "collected": collected,
        "outstanding": max(0, expected - collected),
        "rate": (collected / expected * 100) if expected else 0.0,
    }


# ── family billing ──────────────────────────────────────────────────
def families_finance(ym, min_members=2):
    """One entry per family that either has >= min_members students billed via
    per-student `payments`, or is the `billed_family_id` of a class_flat class."""
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
            ORDER BY f.name""", (ym,))
    fam = {}
    for r in rows:
        fam[r["id"]] = {
            "id": r["id"], "name": r["name"], "members": r["members"],
            "names": (r["names"] or "").split(","),
            "expected": r["expected"], "paid": r["paid"], "keep": r["members"] >= min_members,
        }
    # class_flat classes billed to a family
    for cb in query(
        """SELECT c.billed_family_id AS fid, f.name AS fname,
                  cb.expected_cents AS e, cb.paid_cents AS p
             FROM class_bills cb JOIN classes c ON c.id = cb.class_id
             JOIN families f ON f.id = c.billed_family_id
            WHERE cb.month = ? AND c.bill_mode = 'class_flat'""", (ym,)):
        entry = fam.setdefault(cb["fid"], {
            "id": cb["fid"], "name": cb["fname"], "members": 0, "names": [],
            "expected": 0, "paid": 0, "keep": False,
        })
        entry["expected"] += cb["e"]
        entry["paid"] += cb["p"]
        entry["keep"] = True
    out = []
    for e in sorted(fam.values(), key=lambda x: x["name"]):
        if not e["keep"]:
            continue
        exp, paid = e["expected"], e["paid"]
        out.append({
            "id": e["id"], "name": e["name"], "members": e["members"],
            "names": [n for n in e["names"] if n],
            "expected": exp, "paid": paid,
            "outstanding": max(0, exp - paid),
            "rate": (paid / exp * 100) if exp else 0.0,
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
    """Spread one lump sum across the family's outstanding bills for the month —
    per-student `payments` rows plus any class_flat `class_bills` billed to this
    family — in name order. Leftover is added to the last row as overpayment so
    the total recorded == the amount handed over."""
    rows = [
        {"t": "payments", "id": r["id"], "exp": r["expected_cents"], "paid": r["paid_cents"]}
        for r in query(
            """SELECT p.id, p.expected_cents, p.paid_cents FROM payments p
                 JOIN students s ON s.id = p.student_id
                WHERE s.family_id = ? AND p.month = ? ORDER BY s.full_name, p.id""",
            (family_id, ym))
    ] + [
        {"t": "class_bills", "id": r["id"], "exp": r["expected_cents"], "paid": r["paid_cents"]}
        for r in query(
            """SELECT cb.id, cb.expected_cents, cb.paid_cents FROM class_bills cb
                 JOIN classes c ON c.id = cb.class_id
                WHERE c.billed_family_id = ? AND c.bill_mode = 'class_flat' AND cb.month = ?""",
            (family_id, ym))
    ]
    if not rows:
        return 0
    left = amount_cents
    touched = 0
    last_out = None
    for r in rows:
        need = r["exp"] - r["paid"]
        if need <= 0:
            continue
        last_out = r
        if left <= 0:
            continue
        pay = min(need, left)
        left -= pay
        new_paid = r["paid"] + pay
        status = recompute_status(r["exp"], new_paid, ym, True)
        execute(
            f"""UPDATE {r['t']} SET paid_cents=?, status=?, payment_date=?, method=?,
                  reference=?, remarks=?, updated_at=datetime('now') WHERE id=?""",
            (new_paid, status, date or None, method, reference, remarks, r["id"]))
        touched += 1
    if left > 0:
        tail = last_out or rows[-1]
        execute(
            f"""UPDATE {tail['t']} SET paid_cents = paid_cents + ?, status='paid',
                  payment_date=?, method=?, reference=?, updated_at=datetime('now') WHERE id=?""",
            (left, date or None, method, reference, tail["id"]))
        if last_out is None:
            touched += 1
    log("family_payment", f"family {family_id} {ym}: RM {amount_cents / 100:.2f}")
    return touched


# ── monthly bills (parent-facing fee messages) ─────────────────────
# Same numbers as the Finance page (per-lesson billing), just formatted as a
# copy-paste message. A student_bill covers the per-student modes; class_flat
# goes into the family_bill; agent_headcount gets its own agent_bill.

def resolve_lesson_fee(student_id, class_id, on_date):
    """Per-lesson rate in effect: the student's own dated `fees` row with
    fee_model='per_lesson', else the class's lesson_fee_cents. Returns sen."""
    row = query(
        """SELECT amount_cents, discount_cents FROM fees
             WHERE student_id = ? AND class_id = ? AND fee_model = 'per_lesson'
               AND effective_from <= ?
               AND (effective_to IS NULL OR effective_to = '' OR effective_to >= ?)
             ORDER BY effective_from DESC, id DESC LIMIT 1""",
        (student_id, class_id, on_date, on_date), one=True)
    if row:
        return max(0, row["amount_cents"] - (row["discount_cents"] or 0))
    cls = query("SELECT lesson_fee_cents FROM classes WHERE id = ?", (class_id,), one=True)
    return (cls["lesson_fee_cents"] or 0) if cls else 0


def held_lesson_count(class_id, ym, since=None, until=None):
    """Distinct dates this class ran (>=1 present/replacement) within `ym`,
    clipped to [since, until] when an enrollment window is given."""
    first, last = month_bounds(ym)
    lo = max(first, since) if since else first
    hi = min(last, until) if until else last
    if lo > hi:
        return 0
    r = query(
        """SELECT COUNT(DISTINCT date) n FROM attendance
             WHERE class_id = ? AND status IN ('present', 'replacement')
               AND date BETWEEN ? AND ?""",
        (class_id, lo, hi), one=True)
    return r["n"] if r else 0


def student_bill(student_id, ym):
    """{student, lines:[{class_id, class_name, lesson_fee, count, subtotal}], total}.
    Per-student modes only — a class in a class-level mode (class_flat /
    agent_headcount) is billed elsewhere and skipped here."""
    s = query("SELECT id, full_name, status FROM students WHERE id = ?", (student_id,), one=True)
    if not s or s["status"] == "trial":
        return {"student": s, "lines": [], "total": 0}
    _, last = month_bounds(ym)
    enrolls = query(
        """SELECT e.class_id, e.start_date, e.end_date, c.name AS class_name
             FROM enrollments e JOIN classes c ON c.id = e.class_id
            WHERE e.student_id = ? ORDER BY c.name""", (student_id,))
    lines, total = [], 0
    for e in enrolls:
        if not enrollment_overlaps_month(e["start_date"], e["end_date"], ym):
            continue
        mode = class_bill_mode(e["class_id"])
        if mode in CLASS_LEVEL_MODES:
            continue
        count = _student_lesson_count(student_id, e["class_id"], ym, mode,
                                      e["start_date"], e["end_date"] or None)
        if count == 0:
            continue
        fee = resolve_lesson_fee(student_id, e["class_id"], last)
        subtotal = fee * count
        lines.append({"class_id": e["class_id"], "class_name": e["class_name"],
                      "lesson_fee": fee, "count": count, "subtotal": subtotal})
        total += subtotal
    return {"student": s, "lines": lines, "total": total}


def _class_flat_line(class_id, ym):
    c = query("SELECT name, lesson_fee_cents FROM classes WHERE id = ?", (class_id,), one=True)
    count = held_lesson_count(class_id, ym)
    if not c or count == 0:
        return None
    fee = c["lesson_fee_cents"] or 0
    return {"class_id": class_id, "class_name": c["name"],
            "lesson_fee": fee, "count": count, "subtotal": fee * count}


def family_bill(family_id, ym):
    fam = query("SELECT id, name FROM families WHERE id = ?", (family_id,), one=True)
    if not fam:
        return None
    members = query("SELECT id FROM students WHERE family_id = ? ORDER BY full_name",
                    (family_id,))
    bills = [b for b in (student_bill(m["id"], ym) for m in members) if b["lines"]]
    flat_lines = [ln for ln in (
        _class_flat_line(c["id"], ym) for c in query(
            "SELECT id FROM classes WHERE billed_family_id = ? AND bill_mode = 'class_flat'",
            (family_id,))
    ) if ln]
    total = sum(b["total"] for b in bills) + sum(ln["subtotal"] for ln in flat_lines)
    return {"family": fam, "bills": bills, "flat_lines": flat_lines, "total": total}


def agent_bill(class_id, ym):
    """One bill for an agent_headcount class, addressed to the agent."""
    c = query("""SELECT name, agent_name, agent_phone, base_fee_cents, base_head_count, per_head_cents
                   FROM classes WHERE id = ? AND bill_mode = 'agent_headcount'""",
              (class_id,), one=True)
    if not c:
        return None
    count = held_lesson_count(class_id, ym)
    if count == 0:
        return None
    n = class_enrolled_count(class_id, ym)
    base_heads = c["base_head_count"] or 0
    extra = max(0, n - base_heads)
    per_lesson = (c["base_fee_cents"] or 0) + (c["per_head_cents"] or 0) * extra
    return {
        "class_id": class_id, "class_name": c["name"],
        "agent_name": c["agent_name"] or "", "agent_phone": c["agent_phone"] or "",
        "base": c["base_fee_cents"] or 0, "per_head": c["per_head_cents"] or 0,
        "students": n, "base_heads": base_heads, "extra": extra,
        "count": count, "per_lesson": per_lesson, "total": per_lesson * count,
    }


def agent_bills(ym):
    """One combined bill per agent, covering every agent_headcount class they
    run this month. Each line is just class · lessons · per-class rate · total
    (the base/headcount maths stays on the Finance side — the agent only needs
    the final numbers). Shape mirrors student_bill so render_bill_text reuses
    _bill_line_block. Classes with no agent name are left out (flagged elsewhere)."""
    by_agent = {}
    for c in query(
            """SELECT id, agent_name FROM classes
                WHERE bill_mode = 'agent_headcount' AND status = 'active'
                ORDER BY agent_name, name"""):
        name = (c["agent_name"] or "").strip()
        if not name:
            continue
        ab = agent_bill(c["id"], ym)
        if not ab:
            continue
        g = by_agent.setdefault(name, {"agent_name": name, "lines": [], "total": 0})
        g["lines"].append({
            "class_id": ab["class_id"], "class_name": ab["class_name"],
            "lesson_fee": ab["per_lesson"], "count": ab["count"], "subtotal": ab["total"],
        })
        g["total"] += ab["total"]
    return list(by_agent.values())


_BILL_DIVIDER = "───────────────"

# Parent-facing bill text is sent as a single language (the teacher picks EN or
# 华语 per batch on the Bills page) rather than the bilingual "中文 | English"
# every line used to carry — see render_bill_text's `lang` arg.
_BILL_TEXT = {
    "en": {
        "fee": "Fee: RM{fee} / Class",
        "count": "Number of Classes: {count}",
        "line_total": "Total: RM{total}",
        "grand_total": "💰 Total: RM{total}",
        "payment": "💳 Payment Methods",
        "reminder": ("🔔 Please remember to send me a photo/screenshot of the "
                     "payment once it has been made. 🤗"),
        "thanks": "Thank you for your kind cooperation! 💗",
    },
    "zh": {
        "fee": "收费：RM{fee} / 堂",
        "count": "上课次数：{count}",
        "line_total": "上课总费用：RM{total}",
        "grand_total": "💰 总额：RM{total}",
        "payment": "💳 付款方式",
        "reminder": "🔔 付款后，请记得发送付款截图给我哦！🤗",
        "thanks": "谢谢您的配合！💗",
    },
}


def _bill_lang(lang):
    return "zh" if lang == "zh" else "en"


def _bill_month_title(ym, lang="en"):
    from .i18n import MONTHS
    y, m = ym.split("-")
    if _bill_lang(lang) == "zh":
        return f"{y}年{int(m)}月补习学费"
    return f"Tuition Fee ({MONTHS['en'][int(m) - 1]} {y})"


def _bill_line_block(line, lang="en", head=None):
    s = _BILL_TEXT[_bill_lang(lang)]
    prefix = f"📖 {head}\n" if head else ""
    return (f"{prefix}"
            f"{s['fee'].format(fee=rm(line['lesson_fee']))}\n"
            f"{s['count'].format(count=line['count'])}\n"
            f"{s['line_total'].format(total=rm(line['subtotal']))}")


def render_bill_text(bill, ym, payment_info, lang="en"):
    """`bill` is a student_bill, a family_bill ('family' key), or a combined
    agent bill ('agent_name' key). Rendered in a single language — "en" or "zh"
    (anything else falls back to English) — chosen by the teacher on the Bills
    page, independent of the app's own UI language."""
    lang = _bill_lang(lang)
    s = _BILL_TEXT[lang]
    if "agent_name" in bill:  # combined agent bill — plain per-class lines
        body = "\n\n".join(_bill_line_block(ln, lang, head=ln["class_name"]) for ln in bill["lines"])
    elif "family" in bill:
        blocks = [
            _bill_line_block(ln, lang, head=f"{b['student']['full_name']} · {ln['class_name']}")
            for b in bill["bills"] for ln in b["lines"]
        ] + [_bill_line_block(ln, lang, head=ln["class_name"]) for ln in bill.get("flat_lines", [])]
        body = "\n\n".join(blocks)
    else:
        multi = len(bill["lines"]) > 1
        body = "\n\n".join(
            _bill_line_block(ln, lang, head=ln["class_name"] if multi else None)
            for ln in bill["lines"])
    return (
        f"📚 {_bill_month_title(ym, lang)}\n\n"
        f"{body}\n\n"
        f"{s['grand_total'].format(total=rm(bill['total']))}\n"
        f"{_BILL_DIVIDER}\n"
        f"{s['payment']}\n\n"
        f"{(payment_info or '').strip()}\n"
        f"{_BILL_DIVIDER}\n"
        f"{s['reminder']}\n\n"
        f"{s['thanks']}"
    )


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

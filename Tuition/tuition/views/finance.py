from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, send_from_directory)

from ..auth import login_required
from ..db import query, execute, log
from ..i18n import t
from .. import receipts
from ..engine import (this_month, add_months, to_cents, sync_month, month_totals,
                      forecast_total, recompute_status, is_closed, close_month,
                      reopen_month, families_finance, apply_family_payment, PAYMENT_METHODS)

bp = Blueprint("finance", __name__)


def _drop_receipt(path, except_pid=None):
    """Null the column, and delete the file only if no other payment still uses it."""
    if not path:
        return
    others = query(
        "SELECT COUNT(*) n FROM payments WHERE receipt_path = ? AND id != ?",
        (path, except_pid or 0), one=True)["n"]
    if others == 0:
        receipts.delete(path)

METHODS = PAYMENT_METHODS


@bp.route("/")
@login_required
def index():
    ym = request.args.get("month") or this_month()
    tab = request.args.get("tab") if request.args.get("tab") in ("overview", "collect") else "overview"
    class_id = request.args.get("class", type=int)
    status = request.args.get("status") or ""

    if not is_closed(ym):
        sync_month(ym)

    totals = month_totals(ym)
    forecast = forecast_total(ym)
    closed = is_closed(ym)
    snap = query("SELECT * FROM monthly_finance WHERE month = ?", (ym,), one=True)

    # per-class breakdown (per-student payments + any whole-class bill)
    classes = query(
        """SELECT c.id, c.name,
                  COALESCE(SUM(p.expected_cents),0)
                    + COALESCE((SELECT expected_cents FROM class_bills
                                 WHERE class_id = c.id AND month = ?), 0) AS expected,
                  COALESCE(SUM(p.paid_cents),0)
                    + COALESCE((SELECT paid_cents FROM class_bills
                                 WHERE class_id = c.id AND month = ?), 0) AS collected
             FROM classes c
             LEFT JOIN payments p ON p.class_id = c.id AND p.month = ?
            GROUP BY c.id ORDER BY c.name""", (ym, ym, ym))

    where = ["p.month = ?"]
    args = [ym]
    if class_id:
        where.append("p.class_id = ?"); args.append(class_id)
    if status in ("pending", "partial", "paid", "overdue"):
        where.append("p.status = ?"); args.append(status)
    rows = query(
        f"""SELECT p.*, s.full_name, c.name AS class_name
              FROM payments p
              JOIN students s ON s.id = p.student_id
              JOIN classes  c ON c.id = p.class_id
             WHERE {' AND '.join(where)}
             ORDER BY p.status='paid', s.full_name""", args)

    all_classes = query("SELECT id, name FROM classes ORDER BY name")
    families = families_finance(ym)

    cb_where = ["cb.month = ?"]
    cb_args = [ym]
    if class_id:
        cb_where.append("cb.class_id = ?"); cb_args.append(class_id)
    if status in ("pending", "partial", "paid", "overdue"):
        cb_where.append("cb.status = ?"); cb_args.append(status)
    class_bills = query(
        f"""SELECT cb.*, c.name AS class_name, c.bill_mode, c.agent_name,
                   f.name AS family_name
              FROM class_bills cb
              JOIN classes c ON c.id = cb.class_id
              LEFT JOIN families f ON f.id = c.billed_family_id
             WHERE {' AND '.join(cb_where)}
             ORDER BY cb.status='paid', c.name""", cb_args)

    return render_template(
        "finance/index.html", ym=ym, tab=tab, totals=totals, forecast=forecast,
        classes=classes, rows=rows, all_classes=all_classes, families=families,
        class_bills=class_bills,
        class_id=class_id, status=status, closed=closed, snap=snap,
        methods=METHODS,
        prev_month=add_months(ym, -1), next_month=add_months(ym, 1))


@bp.route("/generate", methods=["POST"])
@login_required
def generate():
    ym = request.form.get("month") or this_month()
    if is_closed(ym):
        flash(t("month_closed"), "error")
    else:
        n = sync_month(ym)
        flash(f"{t('saved')} ({n})", "ok")
    return redirect(url_for("finance.index", month=ym))


@bp.route("/pay/<int:pid>", methods=["POST"])
@login_required
def pay(pid):
    p = query("SELECT * FROM payments WHERE id = ?", (pid,), one=True)
    if not p:
        abort(404)
    if is_closed(p["month"]):
        flash(t("month_closed"), "error")
        return redirect(url_for("finance.index", month=p["month"]))
    f = request.form
    paid = to_cents(f.get("amount"))
    expected = p["expected_cents"]
    auto = p["auto_expected"]
    if (f.get("expected") or "").strip() != "":
        expected = to_cents(f.get("expected"))
        auto = 0
    status = recompute_status(expected, paid, p["month"], bool(f.get("payment_date")))
    execute(
        """UPDATE payments SET paid_cents=?, expected_cents=?, auto_expected=?, status=?,
              payment_date=?, method=?, reference=?, remarks=?, updated_at=datetime('now')
            WHERE id=?""",
        (paid, expected, auto, status, f.get("payment_date") or None,
         f.get("method") if f.get("method") in METHODS else None,
         f.get("reference", "").strip(), f.get("remarks", "").strip(), pid))

    # receipt image (uploaded file or pasted from clipboard)
    upload = request.files.get("receipt")
    if f.get("remove_receipt") and p["receipt_path"]:
        _drop_receipt(p["receipt_path"], except_pid=pid)
        execute("UPDATE payments SET receipt_path = NULL WHERE id = ?", (pid,))
    elif upload and upload.filename:
        _drop_receipt(p["receipt_path"], except_pid=pid)
        name = receipts.save(upload, prefix=f"pmt{pid}")
        execute("UPDATE payments SET receipt_path = ? WHERE id = ?", (name, pid))

    log("payment", f"row {pid}: RM {paid/100:.2f}")
    flash(t("saved"), "ok")
    dest = f.get("back") or url_for("finance.index", month=p["month"])
    return redirect(dest)


@bp.route("/class-pay/<int:bid>", methods=["POST"])
@login_required
def class_pay(bid):
    b = query("SELECT * FROM class_bills WHERE id = ?", (bid,), one=True)
    if not b:
        abort(404)
    if is_closed(b["month"]):
        flash(t("month_closed"), "error")
        return redirect(url_for("finance.index", month=b["month"]))
    f = request.form
    paid = to_cents(f.get("amount"))
    expected = b["expected_cents"]
    auto = b["auto_expected"]
    if (f.get("expected") or "").strip() != "":
        expected = to_cents(f.get("expected"))
        auto = 0
    status = recompute_status(expected, paid, b["month"], bool(f.get("payment_date")))
    execute(
        """UPDATE class_bills SET paid_cents=?, expected_cents=?, auto_expected=?, status=?,
              payment_date=?, method=?, reference=?, remarks=?, updated_at=datetime('now')
            WHERE id=?""",
        (paid, expected, auto, status, f.get("payment_date") or None,
         f.get("method") if f.get("method") in METHODS else None,
         f.get("reference", "").strip(), f.get("remarks", "").strip(), bid))
    upload = request.files.get("receipt")
    if f.get("remove_receipt") and b["receipt_path"]:
        receipts.delete(b["receipt_path"])
        execute("UPDATE class_bills SET receipt_path = NULL WHERE id = ?", (bid,))
    elif upload and upload.filename:
        if b["receipt_path"]:
            receipts.delete(b["receipt_path"])
        name = receipts.save(upload, prefix=f"cb{bid}")
        execute("UPDATE class_bills SET receipt_path = ? WHERE id = ?", (name, bid))
    log("class_payment", f"bill {bid}: RM {paid/100:.2f}")
    flash(t("saved"), "ok")
    return redirect(f.get("back") or url_for("finance.index", month=b["month"]))


@bp.route("/receipt/<name>")
@login_required
def receipt(name):
    return send_from_directory(receipts.dir_path(), name)


@bp.route("/family/<int:fid>/pay", methods=["POST"])
@login_required
def family_pay(fid):
    ym = request.form.get("month") or this_month()
    if is_closed(ym):
        flash(t("month_closed"), "error")
        return redirect(url_for("finance.index", month=ym))
    f = request.form
    n = apply_family_payment(
        fid, ym, to_cents(f.get("amount")), f.get("payment_date") or None,
        f.get("method") if f.get("method") in METHODS else None,
        f.get("reference", "").strip(), f.get("remarks", "").strip())

    upload = request.files.get("receipt")
    if upload and upload.filename:
        name = receipts.save(upload, prefix=f"fam{fid}")
        rows = query(
            """SELECT p.id, p.receipt_path FROM payments p JOIN students s ON s.id = p.student_id
                WHERE s.family_id = ? AND p.month = ? AND p.paid_cents > 0""", (fid, ym))
        for r in rows:
            _drop_receipt(r["receipt_path"], except_pid=r["id"])
            execute("UPDATE payments SET receipt_path = ? WHERE id = ?", (name, r["id"]))

    flash(f"{t('saved')} ({n})", "ok")
    return redirect(request.form.get("back") or url_for("finance.index", month=ym))


@bp.route("/pay/<int:pid>/clear", methods=["POST"])
@login_required
def clear_pay(pid):
    p = query("SELECT * FROM payments WHERE id = ?", (pid,), one=True)
    if not p:
        abort(404)
    if is_closed(p["month"]):
        flash(t("month_closed"), "error")
        return redirect(url_for("finance.index", month=p["month"]))
    status = recompute_status(p["expected_cents"], 0, p["month"], False)
    execute(
        """UPDATE payments SET paid_cents=0, status=?, payment_date=NULL, method=NULL,
              reference='', updated_at=datetime('now') WHERE id=?""", (status, pid))
    flash(t("saved"), "ok")
    return redirect(request.form.get("back") or url_for("finance.index", month=p["month"]))


@bp.route("/close", methods=["POST"])
@login_required
def close():
    ym = request.form.get("month") or this_month()
    sync_month(ym)
    close_month(ym)
    flash(t("saved"), "ok")
    return redirect(url_for("finance.index", month=ym))


@bp.route("/reopen", methods=["POST"])
@login_required
def reopen():
    ym = request.form.get("month") or this_month()
    reopen_month(ym)
    flash(t("saved"), "ok")
    return redirect(url_for("finance.index", month=ym))

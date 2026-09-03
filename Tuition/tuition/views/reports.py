from flask import Blueprint, render_template, request

from ..auth import login_required
from ..db import query
from ..engine import (this_month, add_months, month_bounds, month_totals,
                      forecast_total, attendance_stats, sync_month)

bp = Blueprint("reports", __name__)


@bp.route("/")
@login_required
def index():
    ym = request.args.get("month") or this_month()
    first, last = month_bounds(ym)
    sync_month(ym)

    students = query("SELECT status, joined_date, left_date FROM students")
    student_rpt = {
        "total": len(students),
        "active": sum(1 for s in students if s["status"] == "active"),
        "trial": sum(1 for s in students if s["status"] == "trial"),
        "inactive": sum(1 for s in students if s["status"] == "inactive"),
        "left": sum(1 for s in students if s["status"] == "left"),
        "new": sum(1 for s in students if s["joined_date"] and first <= s["joined_date"] <= last),
        "left_month": sum(1 for s in students if s["left_date"] and first <= s["left_date"] <= last),
    }

    overall_att = attendance_stats(date_from=first, date_to=last)
    class_att = []
    for c in query("SELECT id, name FROM classes ORDER BY name"):
        st = attendance_stats(class_id=c["id"], date_from=first, date_to=last)
        if st["total"]:
            class_att.append({"name": c["name"], "st": st})

    totals = month_totals(ym)
    forecast = forecast_total(ym)

    class_rev = query(
        """SELECT c.name,
                  COALESCE(SUM(p.expected_cents),0) expected,
                  COALESCE(SUM(p.paid_cents),0) collected
             FROM classes c
             LEFT JOIN payments p ON p.class_id = c.id AND p.month = ?
            GROUP BY c.id ORDER BY collected DESC""", (ym,))

    # 12-month revenue trend (closed snapshots, else live)
    trend = []
    m = ym
    for _ in range(12):
        snap = query("SELECT * FROM monthly_finance WHERE month = ?", (m,), one=True)
        if snap:
            trend.append({"month": m, "expected": snap["expected_cents"],
                          "collected": snap["collected_cents"], "closed": True})
        else:
            tt = month_totals(m)
            trend.append({"month": m, "expected": tt["expected"],
                          "collected": tt["collected"], "closed": False})
        m = add_months(m, -1)
    trend.reverse()
    trend_max = max([x["expected"] for x in trend] + [1])

    return render_template(
        "reports/index.html", ym=ym, prev_month=add_months(ym, -1),
        next_month=add_months(ym, 1), sr=student_rpt, overall_att=overall_att,
        class_att=class_att, totals=totals, forecast=forecast, class_rev=class_rev,
        trend=trend, trend_max=trend_max)

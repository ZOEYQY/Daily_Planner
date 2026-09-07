"""Monthly Bills — parent-facing bilingual fee messages, ready to copy-paste.

Same numbers as the Finance page (per-lesson billing), just formatted for the
teacher to send to each parent / family / agent.
"""
from flask import Blueprint, render_template, request

from ..auth import login_required
from ..db import query
from ..engine import (this_month, add_months, student_bill, family_bill,
                      agent_bills, render_bill_text, held_lesson_count,
                      sync_month, is_closed)

bp = Blueprint("bills", __name__)


@bp.route("/")
@login_required
def index():
    ym = request.args.get("month") or this_month()
    if not is_closed(ym):
        sync_month(ym)  # also self-heals a flat class's billed family
    pay_info = (query("SELECT payment_info FROM settings WHERE id = 1", one=True)
                or {"payment_info": ""})["payment_info"]

    def card(name, bill, sid=None):
        return {"name": name, "total": bill["total"], "sid": sid,
                "text": render_bill_text(bill, ym, pay_info)}

    billed_classes = set()  # class ids that landed on some card this month

    # Individual cards are only for students with NO family — a student in a
    # family is folded into that family's combined card below (siblings billed
    # together on one message).
    students = query(
        """SELECT DISTINCT s.id, s.full_name FROM students s
             JOIN enrollments e ON e.student_id = s.id
            WHERE s.status != 'trial' AND s.family_id IS NULL
            ORDER BY s.full_name""")
    student_cards = []
    for s in students:
        b = student_bill(s["id"], ym)
        if b["lines"]:
            billed_classes.update(ln["class_id"] for ln in b["lines"])
            student_cards.append(card(s["full_name"], b, sid=s["id"]))

    fam_ids = query(
        """SELECT DISTINCT f.id, f.name FROM families f
             LEFT JOIN students s ON s.family_id = f.id
             LEFT JOIN classes c ON c.billed_family_id = f.id AND c.bill_mode = 'class_flat'
            WHERE s.id IS NOT NULL OR c.id IS NOT NULL
            ORDER BY f.name""")
    family_cards = []
    for row in fam_ids:
        fb = family_bill(row["id"], ym)
        if fb and (fb["bills"] or fb["flat_lines"]):
            billed_classes.update(ln["class_id"] for ln in fb["flat_lines"])
            for bb in fb["bills"]:
                billed_classes.update(ln["class_id"] for ln in bb["lines"])
            family_cards.append(card(fb["family"]["name"], fb))

    agent_cards = []
    for ab in agent_bills(ym):  # one combined bill per agent
        billed_classes.update(ln["class_id"] for ln in ab["lines"])
        agent_cards.append(card(ab["agent_name"], ab))

    # Whole-class billing modes that produced no bill this month — surface why,
    # so a misconfigured class (no billed family, no agent) or one that simply
    # hasn't run yet doesn't silently disappear from the month's bills.
    unbilled = []
    for c in query(
            """SELECT id, name, bill_mode, billed_family_id, agent_name FROM classes
                WHERE status = 'active' AND bill_mode IN ('class_flat', 'agent_headcount')
                ORDER BY name"""):
        if c["id"] in billed_classes:
            continue
        if c["bill_mode"] == "class_flat" and not c["billed_family_id"]:
            reason = "no_billed_family"
        elif c["bill_mode"] == "agent_headcount" and not (c["agent_name"] or "").strip():
            reason = "no_agent"
        elif held_lesson_count(c["id"], ym) == 0:
            reason = "no_lessons"
        else:
            reason = "other"
        unbilled.append({"id": c["id"], "name": c["name"],
                         "bill_mode": c["bill_mode"], "reason": reason})

    return render_template(
        "bills/index.html", ym=ym,
        student_cards=student_cards, family_cards=family_cards, agent_cards=agent_cards,
        unbilled=unbilled,
        prev_month=add_months(ym, -1), next_month=add_months(ym, 1))

import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash

from ..auth import login_required
from ..db import query, execute, log
from ..i18n import t, LANGS
from ..util import smartcase

bp = Blueprint("settings", __name__)


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    s = query("SELECT * FROM settings WHERE id = 1", one=True)
    if request.method == "POST":
        f = request.form
        lang = f.get("language") if f.get("language") in LANGS else s["language"]
        subjects = [x.strip() for x in f.get("result_subjects", "").split(",") if x.strip()]
        execute(
            """UPDATE settings SET teacher_name=?, language=?, currency=?, charge_absence=?,
                  result_subjects=?, payment_info=?, updated_at=datetime('now')
                WHERE id=1""",
            (smartcase(f.get("teacher_name")) or s["teacher_name"], lang,
             f.get("currency", "").strip() or "RM",
             1 if f.get("charge_absence") else 0,
             json.dumps(subjects or ["Bahasa Melayu"]),
             f.get("payment_info", "").strip()))
        session["lang"] = lang
        log("settings_update", "")
        flash(t("saved"), "ok")
        return redirect(url_for("settings.index"))

    try:
        subjects = ", ".join(json.loads(s["result_subjects"]))
    except (ValueError, TypeError):
        subjects = ""
    return render_template("settings/index.html", s=s, subjects=subjects)


@bp.route("/password", methods=["POST"])
@login_required
def change_password():
    s = query("SELECT password_hash FROM settings WHERE id = 1", one=True)
    f = request.form
    if not check_password_hash(s["password_hash"], f.get("current", "")):
        flash(t("wrong_password"), "error")
    elif len(f.get("new", "")) < 4:
        flash(t("password") + " ≥ 4", "error")
    elif f.get("new") != f.get("confirm"):
        flash(t("password_mismatch"), "error")
    else:
        execute("UPDATE settings SET password_hash=?, updated_at=datetime('now') WHERE id=1",
                (generate_password_hash(f.get("new")),))
        flash(t("saved"), "ok")
    return redirect(url_for("settings.index"))

"""Single-teacher auth. The account lives in the settings row."""
import functools
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, g)
from werkzeug.security import generate_password_hash, check_password_hash

from .db import query, execute
from .i18n import t, LANGS
from .util import smartcase

bp = Blueprint("auth", __name__)


def account_exists():
    row = query("SELECT password_hash FROM settings WHERE id = 1", one=True)
    return bool(row and row["password_hash"])


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not account_exists():
        return redirect(url_for("auth.setup"))
    if request.method == "POST":
        row = query("SELECT password_hash FROM settings WHERE id = 1", one=True)
        if check_password_hash(row["password_hash"], request.form.get("password", "")):
            session.permanent = True
            session["logged_in"] = True
            return redirect(url_for("attendance.today"))
        flash(t("wrong_password"), "error")
    return render_template("auth/login.html")


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if account_exists():
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        name = smartcase(request.form.get("teacher_name")) or "Teacher"
        pw = request.form.get("password", "")
        pw2 = request.form.get("confirm_password", "")
        if len(pw) < 4:
            flash(t("password") + " ≥ 4", "error")
        elif pw != pw2:
            flash(t("password_mismatch"), "error")
        else:
            execute(
                "UPDATE settings SET teacher_name = ?, password_hash = ?, updated_at = datetime('now') WHERE id = 1",
                (name, generate_password_hash(pw)))
            session.permanent = True
            session["logged_in"] = True
            return redirect(url_for("attendance.today"))
    return render_template("auth/setup.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/lang/<code>")
def set_lang(code):
    if code in LANGS:
        session["lang"] = code
    return redirect(request.referrer or url_for("attendance.today"))

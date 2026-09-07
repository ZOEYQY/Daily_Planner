"""Tuition Management System — Flask app factory.

Stack: Flask + stdlib sqlite3, server-rendered Jinja, no build step.
Data (database, secret key, backups) lives OUTSIDE the repo so it is never
touched by git operations or cleanup:

    Windows : %LOCALAPPDATA%\\TuitionSystem\\
    other   : ~/.tuition-system/
    override: TUITION_DATA_DIR env var
"""
import os
import datetime as dt

from flask import Flask, g, session, redirect, url_for, request

from . import db as _db
from .i18n import t, weekday_name, weekday_short, month_name, month_label, LANGS


def resolve_data_dir():
    override = os.environ.get("TUITION_DATA_DIR")
    if override:
        return os.path.abspath(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "TuitionSystem")
    return os.path.join(os.path.expanduser("~"), ".tuition-system")


def _secret_key(data_dir):
    path = os.path.join(data_dir, "secret_key")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    key = os.urandom(32).hex()
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    return key


def create_app(data_dir=None):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    data_dir = data_dir or resolve_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    app.config["DATA_DIR"] = data_dir
    app.config["DB_PATH"] = os.path.join(data_dir, "tuition.db")
    app.secret_key = _secret_key(data_dir)
    app.permanent_session_lifetime = dt.timedelta(days=30)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB receipt cap

    _db.init_db(app)
    _db.backup_db(app)
    app.teardown_appcontext(_db.close_db)

    # ── request lifecycle ──────────────────────────────────────────
    @app.before_request
    def _load_lang():
        if "lang" in session and session["lang"] in LANGS:
            g.lang = session["lang"]
        else:
            row = _db.query("SELECT language FROM settings WHERE id = 1", one=True)
            g.lang = row["language"] if row and row["language"] in LANGS else "en"

    # ── jinja helpers ──────────────────────────────────────────────
    from .engine import rm, STATUS_KEYS
    app.jinja_env.globals.update(
        t=t, weekday_name=weekday_name, weekday_short=weekday_short, month_name=month_name,
        month_label=month_label, LANGS=LANGS, rm=rm, cur_lang=lambda: g.get("lang", "en"),
        STATUS_KEYS=STATUS_KEYS, today=lambda: dt.date.today().isoformat(),
    )

    @app.context_processor
    def _inject():
        teacher = None
        if session.get("logged_in"):
            row = _db.query("SELECT teacher_name FROM settings WHERE id = 1", one=True)
            teacher = row["teacher_name"] if row else None
        return {"teacher_name": teacher, "active_nav": request.endpoint or ""}

    # ── blueprints ─────────────────────────────────────────────────
    from .auth import bp as auth_bp
    from .views.students import bp as students_bp
    from .views.classes import bp as classes_bp
    from .views.attendance import bp as attendance_bp
    from .views.finance import bp as finance_bp
    from .views.bills import bp as bills_bp
    from .views.reports import bp as reports_bp
    from .views.settings import bp as settings_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(students_bp, url_prefix="/students")
    app.register_blueprint(classes_bp, url_prefix="/classes")
    app.register_blueprint(attendance_bp, url_prefix="/attendance")
    app.register_blueprint(finance_bp, url_prefix="/finance")
    app.register_blueprint(bills_bp, url_prefix="/bills")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(settings_bp, url_prefix="/settings")

    @app.route("/")
    def _home():
        if not session.get("logged_in"):
            return redirect(url_for("auth.login"))
        return redirect(url_for("attendance.today"))

    return app

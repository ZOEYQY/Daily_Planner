"""
Standalone entry point for the Finance module.

Run with:
    python app.py

This makes Finance a fully self-contained app: no parent project, no
database, no login — everything is read from and written to local JSON
files in Finance/data/, and receipt images are stored in
Finance/static/receipts/.
"""
import os
from flask import Flask, redirect, url_for
from finance_routes import finance_bp

app = Flask(__name__)
# Needed for the profile-switcher session cookie. Set FLASK_SECRET_KEY in
# Finance/.env for anything beyond local/personal use; this fallback keeps
# sessions stable across restarts without requiring setup first.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "daily-planner-dev-secret-change-me")
app.register_blueprint(finance_bp)


@app.route("/")
def index():
    return redirect(url_for("finance.finance_home"))


if __name__ == "__main__":
    app.run(debug=True, port=5050)

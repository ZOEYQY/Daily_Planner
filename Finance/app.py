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
try:
    from . import database, receipt_storage
except ImportError:
    import database
    import receipt_storage
from finance_routes import finance_bp

app = Flask(__name__)
# Needed for the profile-switcher session cookie. Set FLASK_SECRET_KEY in
# Finance/.env for anything beyond local/personal use; this fallback keeps
# sessions stable across restarts without requiring setup first.
if os.environ.get("RENDER"):
    database.require_postgres()
    receipt_storage.require_cloudinary()
    app.secret_key = os.environ.get("FLASK_SECRET_KEY")
    if not app.secret_key or len(app.secret_key) < 32 or app.secret_key == "replace-with-a-long-random-secret":
        raise RuntimeError("Set FLASK_SECRET_KEY on Render to a stable random value of at least 32 characters.")
else:
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "daily-planner-dev-secret-change-me")
app.register_blueprint(finance_bp)


@app.route("/")
def index():
    return redirect(url_for("finance.finance_home"))


if __name__ == "__main__":
    app.run(debug=True, port=5050)

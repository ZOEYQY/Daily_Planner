"""
Standalone entry point for the Finance module.

Run with:
    python app.py

This makes Finance a fully self-contained app: no parent project, no
database, no login — everything is read from and written to local JSON
files in Finance/data/, and receipt images are stored in
Finance/static/receipts/.
"""
from flask import Flask, redirect, url_for
from finance_routes import finance_bp

app = Flask(__name__)
app.register_blueprint(finance_bp)


@app.route("/")
def index():
    return redirect(url_for("finance.finance_home"))


if __name__ == "__main__":
    app.run(debug=True, port=5050)

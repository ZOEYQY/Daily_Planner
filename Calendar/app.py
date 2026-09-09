"""
Standalone entry point for the Calendar module.

Run with:
    cd Calendar
    python app.py      ->  http://127.0.0.1:5051

The calendar itself is a fully client-side app: index.html plus the static
assets in Calendar/src/ and Calendar/styles/ (state in the browser's
localStorage). The one exception is the Habit check-in (打卡) view, which is
server-backed so it can sync across devices — see habits.py; its SQLite file
(../.data/habits.db) is the SAME store the combined app.py uses.
"""
import os

from flask import Flask, send_from_directory, abort

import habits as habits_module

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

habits_module.init(os.path.join(BASE_DIR, "..", ".data", "habits.db"))
app.register_blueprint(habits_module.bp, url_prefix="/api")


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/<path:filename>")
def asset(filename):
    full = os.path.normpath(os.path.join(BASE_DIR, filename))
    if not full.startswith(BASE_DIR) or not os.path.isfile(full):
        abort(404)
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    print("\n  Calendar  ->  http://127.0.0.1:5051\n")
    app.run(debug=True, port=5051)

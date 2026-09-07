"""
Standalone entry point for the Calendar module.

Run with:
    cd Calendar
    python app.py      ->  http://127.0.0.1:5051

The calendar is a fully client-side app: index.html plus the static
assets in Calendar/src/ and Calendar/styles/. This tiny Flask server
just serves those files (state lives in the browser's localStorage).
No parent project, no database, no login.
"""
import os

from flask import Flask, send_from_directory, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)


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

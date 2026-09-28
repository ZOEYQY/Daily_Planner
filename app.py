"""
Daily Planner — one standalone entry point that combines Finance + Calendar.

    python app.py      ->  http://127.0.0.1:5050

Home page (/) links to:
  * Finance   (/finance/)   — the Flask finance app in Finance/
  * Calendar  (/calendar/)  — the client-side calendar app in Calendar/

Each module can also be run on its own:
    cd Finance  && python app.py     (port 5050)
    cd Calendar && python app.py     (port 5051)

Tuition is a separate project and is intentionally not part of this app.
"""
import os
import secrets
from datetime import timedelta

from flask import Flask, redirect, url_for, send_from_directory, abort, request, session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CALENDAR_DIR = os.path.join(BASE_DIR, "Calendar")

from Finance.finance_routes import finance_bp, authed_profile
from Calendar import habits as calendar_habits

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "Finance", "templates"),
    static_folder=os.path.join(BASE_DIR, "Finance", "static"),
)
# Signs the login cookie. Set FLASK_SECRET_KEY for anything beyond local use;
# on Render (which sets RENDER) a missing key falls back to a random one rather
# than the public dev value, which would let anyone forge a login.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or (
    secrets.token_hex(32) if os.environ.get("RENDER") else "daily-planner-dev-secret-change-me")
app.permanent_session_lifetime = timedelta(days=30)
app.register_blueprint(finance_bp, url_prefix="/finance")

# Habit check-in (打卡) — SQLite in .data/ (gitignored), shared by every device
# that reaches this server, with each profile seeing only its own habits.
# The calendar's events/tasks stay in the browser; only habits are
# server-backed. Mounted where the calendar page can fetch it with "./api/...".
calendar_habits.init(os.path.join(BASE_DIR, ".data", "habits.db"))
app.register_blueprint(calendar_habits.bp, url_prefix="/calendar/api")


# ── login: every page needs a profile logged in with its password ───
# Finance's own before_request does this for /finance/*; this covers the rest
# (home, calendar, habits API). The login screen is /finance/profiles.
@app.before_request
def _require_profile():
    if request.blueprint == "finance":
        return None
    if request.endpoint == "static":
        # Receipt images are served per-profile by /finance/receipts/ —
        # never straight out of the static folder.
        if (request.view_args or {}).get("filename", "").startswith("receipts/"):
            abort(404)
        return None
    if authed_profile():
        return None
    if request.method == "GET" and request.accept_mimetypes.accept_html:
        return redirect(url_for("finance.profiles_page", next=request.full_path.rstrip("?")))
    abort(401)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("finance.profiles_page"))


# ── home / portal ───────────────────────────────────────────────────
HOME_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daily Planner</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; min-height:100vh; display:flex; flex-direction:column;
    align-items:center; justify-content:center; gap:28px; background:#f6f7f9;
    font:16px/1.5 -apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
    color:#232733; }
  h1 { margin:0; font-size:1.7rem; letter-spacing:-.02em; }
  .grid { display:flex; gap:20px; flex-wrap:wrap; justify-content:center; }
  a.card { display:flex; flex-direction:column; align-items:center; justify-content:center;
    gap:10px; width:200px; height:150px; border-radius:16px; background:#fff;
    border:1px solid #e7e9ee; box-shadow:0 1px 2px rgba(20,25,40,.04),0 8px 24px rgba(20,25,40,.05);
    text-decoration:none; color:inherit; font-weight:650; transition:.12s; }
  a.card:hover { border-color:#2f4b7c; transform:translateY(-2px);
    box-shadow:0 2px 6px rgba(20,25,40,.06),0 14px 34px rgba(20,25,40,.08); }
  a.card .ico { font-size:2.4rem; }
</style></head><body>
  <h1>Daily Planner</h1>
  <div class="grid">
    <a class="card" href="/finance/"><span class="ico">$</span>Finance</a>
    <a class="card" href="/calendar/"><span class="ico">&#128197;</span>Calendar</a>
  </div>
</body></html>"""


@app.route("/")
def home():
    return HOME_HTML


@app.route("/finance/")
def finance_root():
    return redirect(url_for("finance.finance_home"))


# ── top bar shown on every Finance / Calendar page ──────────────────
SWITCHER = """
<style>
html{scroll-padding-top:44px}
body{padding-top:40px !important}
/* full-screen fixed overlays (the calendar's modals) are viewport-anchored and
   ignore the body padding-top above, so they'd tuck their top edge under this
   bar — start them below it instead */
.modal-overlay,.popup-overlay{top:40px !important}
#dp-switch{position:fixed;top:0;left:0;right:0;height:40px;z-index:2147483647;
  display:flex;align-items:center;gap:4px;padding:0 12px;background:#151821;color:#fff;
  box-shadow:0 2px 10px rgba(0,0,0,.25);
  font:13px/1 -apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif}
#dp-switch .brand{font-weight:800;opacity:.65;margin-right:6px}
#dp-switch a{text-decoration:none;color:#cdd6e6;font-weight:700;padding:7px 13px;border-radius:8px}
#dp-switch a:hover{background:#262b38;color:#fff}
#dp-switch a.here{background:#3a6ea5;color:#fff}
@media print{#dp-switch{display:none}body{padding-top:0 !important}}
</style>
<div id="dp-switch">
  <span class="brand">Daily Planner</span>
  <a href="/" title="Home">&#127968;</a>
  <a href="/finance/" class="__F__">$ Finance</a>
  <a href="/calendar/" class="__C__">&#128197; Calendar</a>
  <a href="/finance/profiles" style="margin-left:auto" title="My profile">&#128100;</a>
  <a href="/logout">Log out</a>
</div>
"""


def _with_switcher(html, current):
    snippet = SWITCHER.replace("__F__", "here" if current == "finance" else "") \
                      .replace("__C__", "here" if current == "calendar" else "")
    if "</body>" in html and 'id="dp-switch"' not in html:
        return html.replace("</body>", snippet + "</body>", 1)
    return html + snippet


@app.after_request
def _inject_finance(resp):
    if (request.path.startswith("/finance")
            and resp.mimetype == "text/html" and resp.status_code == 200):
        resp.direct_passthrough = False
        resp.set_data(_with_switcher(resp.get_data(as_text=True), "finance"))
    return resp


# ── calendar (static files in Calendar/) ────────────────────────────
# One-time data migration: the calendar keeps its account + events in the
# browser's localStorage, which is scoped per address — so data created at
# file:// or :5051 does not show up here at :5050/calendar/. Drop a backup
# bundle (the JSON that Calendar/transfer.html exports) at Calendar/_migrate.json
# and it gets merged into localStorage exactly once per browser (a marker key
# guards re-runs): the account(s) in the bundle are added (union by email) and
# it signs you straight into the bundle's active account; each data blob is
# written only if that browser doesn't already have one. Delete the file once
# everyone who needs it has loaded the page once.
_MIGRATE_PATH = os.path.join(CALENDAR_DIR, "_migrate.json")


def _calendar_migrate_script():
    try:
        with open(_MIGRATE_PATH, "r", encoding="utf-8") as fh:
            bundle = fh.read()
    except OSError:
        return ""
    import json
    try:
        keys = json.loads(bundle).get("keys", {})
    except ValueError:
        return ""
    if not keys:
        return ""
    return (
        "<script>(function(){"
        "var MARK='monoCalendar._migrated.v1';"
        "try{"
        "if(localStorage.getItem(MARK))return;"
        "var inc=" + json.dumps(keys) + ";"
        "var AK='monoCalendar.auth.v1';"
        "var cur=null,ia=null;"
        "try{cur=JSON.parse(localStorage.getItem(AK)||'null');}catch(e){}"
        "try{ia=JSON.parse(inc[AK]||'null');}catch(e){}"
        "if(ia){"
        "if(!cur||!Array.isArray(cur.users)||!cur.users.length){localStorage.setItem(AK,inc[AK]);}"
        "else{var seen={};cur.users.forEach(function(u){seen[u.email]=1;});"
        "ia.users.forEach(function(u){if(!seen[u.email])cur.users.push(u);});"
        "cur.currentUserId=ia.currentUserId||cur.currentUserId;"
        "localStorage.setItem(AK,JSON.stringify(cur));}}"
        "for(var n in inc){if(n===AK)continue;"
        "if(localStorage.getItem(n)==null)localStorage.setItem(n,inc[n]);}"
        "localStorage.setItem(MARK,new Date().toISOString());"
        "console.log('calendar: migrated from _migrate.json');"
        "}catch(e){console.warn('calendar migrate failed',e);}})();</script>"
    )


@app.route("/calendar/")
def calendar_index():
    with open(os.path.join(CALENDAR_DIR, "index.html"), "r", encoding="utf-8") as fh:
        html = fh.read()
    seed = _calendar_migrate_script()
    if seed:
        html = html.replace("<head>", "<head>\n" + seed, 1)
    return _with_switcher(html, "calendar")


@app.route("/calendar/<path:filename>")
def calendar_asset(filename):
    full = os.path.normpath(os.path.join(CALENDAR_DIR, filename))
    if not full.startswith(CALENDAR_DIR):
        abort(404)
    if not os.path.isfile(full):
        abort(404)
    return send_from_directory(CALENDAR_DIR, filename)


if __name__ == "__main__":
    print("\n  Daily Planner  ->  http://127.0.0.1:5050"
          "\n    Finance   http://127.0.0.1:5050/finance/"
          "\n    Calendar  http://127.0.0.1:5050/calendar/\n")
    app.run(debug=True, port=5050)

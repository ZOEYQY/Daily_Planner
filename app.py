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
import io
import json
import zipfile
from urllib.parse import quote
import os
from html import escape as html_escape
from datetime import timedelta

from flask import Flask, redirect, url_for, send_from_directory, abort, request, session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CALENDAR_DIR = os.path.join(BASE_DIR, "Calendar")

from Finance import database as finance_database
from Finance import receipt_storage
from Finance.finance_routes import finance_bp, authed_profile
from Calendar import habits as calendar_habits
from Calendar import schedule_feed
from Finance.finance_helpers import load_profiles, find_profile

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "Finance", "templates"),
    static_folder=os.path.join(BASE_DIR, "Finance", "static"),
)
# Signs the login cookie. Set FLASK_SECRET_KEY for anything beyond local use;
# on Render (which sets RENDER) a missing key falls back to a random one rather
# than the public dev value, which would let anyone forge a login.
if os.environ.get("RENDER"):
    finance_database.require_postgres()
    receipt_storage.require_cloudinary()
    app.secret_key = os.environ.get("FLASK_SECRET_KEY")
    if not app.secret_key or len(app.secret_key) < 32 or app.secret_key == "replace-with-a-long-random-secret":
        raise RuntimeError("Set FLASK_SECRET_KEY on Render to a stable random value of at least 32 characters.")
else:
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or "daily-planner-dev-secret-change-me"
app.permanent_session_lifetime = timedelta(days=30)
app.register_blueprint(finance_bp, url_prefix="/finance")

# Habit check-in and Calendar state use the shared PostgreSQL database on
# Render; local development without DATABASE_URL retains Habit SQLite. Mounted
# where the calendar page can fetch it with "./api/...".
calendar_habits.init(os.path.join(BASE_DIR, ".data", "habits.db"))
app.register_blueprint(calendar_habits.bp, url_prefix="/calendar/api")


# ── login: every page needs a profile logged in with its password ───
# Finance's own before_request does this for /finance/*; this covers the rest
# (home, calendar, habits API). The login screen is /finance/profiles.
@app.before_request
def _require_profile():
    if request.blueprint == "finance":
        return None
    # The phone browser fetches the manifest, icons and service worker without
    # the login cookie, and the offline page must show when nothing else can.
    if request.endpoint in _PWA_PUBLIC_ENDPOINTS:
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
  a.out { color:#8a8f9c; font-size:.9rem; }
  @media (max-width:700px){
    body { justify-content:flex-start; padding:48px 20px 0; gap:22px; box-sizing:border-box; }
    .grid { width:100%; flex-direction:column; gap:14px; }
    a.card { width:auto; height:auto; flex-direction:row; justify-content:flex-start;
      padding:22px; gap:16px; font-size:1.15rem; }
    a.card .ico { font-size:1.9rem; width:40px; text-align:center; }
  }
</style></head><body>
  <h1>Daily Planner</h1>
  <div class="grid">
    <a class="card" href="/calendar/"><span class="ico">&#128197;</span>Calendar</a>
    <a class="card" href="/finance/"><span class="ico">$</span>Finance</a>
    <a class="card" href="/finance/add"><span class="ico">&#10133;</span>Add expense / income</a>
    <a class="card" href="/calendar/widget"><span class="ico">&#128242;</span>Home-screen widget</a>
  </div>
  <a class="out" href="/logout">Log out</a>
</body></html>"""


@app.route("/")
def home():
    return _with_switcher(HOME_HTML, "home")


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
#dp-switch a.dp-profile{display:flex;align-items:center;gap:8px;padding:4px 10px 4px 4px}
#dp-switch .dp-avatar{width:26px;height:26px;border-radius:50%;background:#fff;color:#151821;
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:12px}
#dp-switch .ti{margin-right:5px}
#dp-switch .dp-profile .ti{margin:0}
@media (min-width:701px){#dp-switch .dp-mobile{display:none}}
@media print{#dp-switch{display:none}body{padding-top:0 !important}}
/* Phone: the bar moves to the bottom as big thumb-sized tabs, like an app. */
@media (max-width:700px){
  html{scroll-padding-top:0}
  :root{--dp-tabbar:60px}
  body{padding-top:env(safe-area-inset-top) !important;
    padding-bottom:calc(60px + env(safe-area-inset-bottom)) !important}
  .modal-overlay,.popup-overlay{top:env(safe-area-inset-top) !important}
  #dp-switch{top:auto;bottom:0;height:auto;padding:0 0 env(safe-area-inset-bottom);gap:0;
    background:rgba(21,24,33,.97);box-shadow:0 -2px 12px rgba(0,0,0,.18);
    -webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px)}
  #dp-switch .brand,#dp-switch .dp-out{display:none}
  #dp-switch a,#dp-switch a.dp-profile{flex:1 1 0;min-width:0;height:60px;margin:0 !important;padding:0;
    border-radius:0;display:flex;flex-direction:column;align-items:center;justify-content:center;
    gap:3px;font-size:11px;font-weight:700;color:#9aa3b5;-webkit-tap-highlight-color:transparent}
  #dp-switch a:hover{background:none;color:#9aa3b5}
  #dp-switch a.here{background:none;color:#fff}
  #dp-switch a.here .ti{background:#3a6ea5}
  #dp-switch .ti{margin:0;min-width:44px;height:28px;padding:0 10px;border-radius:14px;box-sizing:border-box;
    display:flex;align-items:center;justify-content:center;font-size:17px;line-height:1}
  #dp-switch .dp-avatar{width:22px;height:22px;font-size:11px}
  #dp-switch .dp-name{max-width:72px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
}
</style>
<div id="dp-switch">
  <span class="brand">Daily Planner</span>
  <a href="/" class="__H__" title="Home"><span class="ti">&#127968;</span><span class="dp-mobile">Home</span></a>
  <a href="/calendar/" class="__C__"><span class="ti">&#128197;</span><span>Calendar</span></a>
  <a href="/finance/" class="__F__"><span class="ti">$</span><span>Finance</span></a>
  <a href="/finance/profiles" class="dp-profile" style="margin-left:auto" title="My profile">__PROFILE__</a>
  <a href="/logout" class="dp-out">Log out</a>
</div>
"""


def _profile_badge():
    """The logged-in profile's initial in a round avatar plus its name, or a
    plain person icon when nobody is logged in (e.g. the login page)."""
    profile = authed_profile()
    name = ((profile or {}).get("name") or "").strip()
    if not name:
        return '<span class="ti">&#128100;</span><span class="dp-name dp-mobile">Me</span>'
    return (f'<span class="ti"><span class="dp-avatar">{html_escape(name[0].upper())}</span></span>'
            f'<span class="dp-name">{html_escape(name)}</span>')


def _with_switcher(html, current):
    snippet = SWITCHER.replace("__F__", "here" if current == "finance" else "") \
                      .replace("__C__", "here" if current == "calendar" else "") \
                      .replace("__H__", "here" if current == "home" else "") \
                      .replace("__PROFILE__", _profile_badge())
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


# ── phone app (installable web app / PWA) ───────────────────────────
# Open the site on a phone and choose "Add to Home Screen" (iPhone: Safari's
# Share button; Android: Chrome's ⋮ menu or the install prompt). It then opens
# full-screen from its own icon like a normal app. The pieces:
#   * /manifest.webmanifest  name, icon, colours, start page, shortcuts
#   * /sw.js                 service worker — needed for "install"; it only
#                            keeps the offline page and app files (scripts,
#                            styles, icons), never your pages or data
#   * /offline               shown when the phone has no connection
#   * pwa/*.png              icons (made by scripts/make_pwa_icons.py)
PWA_DIR = os.path.join(BASE_DIR, "pwa")
PWA_THEME = "#151821"
PWA_VERSION = "1"  # bump to make phones drop their saved app files

PWA_MANIFEST = {
    "name": "Daily Planner",
    "short_name": "Planner",
    "id": "/",
    "start_url": "/calendar/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#f9f9f7",
    "theme_color": PWA_THEME,
    "icons": [
        {"src": "/pwa/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/pwa/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "/pwa/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
    # Long-press the home-screen icon (Android) for these.
    "shortcuts": [
        {"name": "Calendar", "url": "/calendar/", "icons": [{"src": "/pwa/icon-192.png", "sizes": "192x192"}]},
        {"name": "Add expense / income", "short_name": "Add expense", "url": "/finance/add",
         "icons": [{"src": "/pwa/icon-192.png", "sizes": "192x192"}]},
        {"name": "Finance", "url": "/finance/", "icons": [{"src": "/pwa/icon-192.png", "sizes": "192x192"}]},
    ],
}

PWA_HEAD = f"""
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="{PWA_THEME}">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Planner">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="apple-touch-icon" href="/pwa/apple-touch-icon.png">
<script>if("serviceWorker" in navigator)addEventListener("load",function(){{navigator.serviceWorker.register("/sw.js").catch(function(){{}})}});</script>
"""
PWA_VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'

SERVICE_WORKER_JS = """
const CACHE = "daily-planner-v__VERSION__";
const OFFLINE_URL = "/offline";
const PRECACHE = [OFFLINE_URL, "/pwa/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// App files (scripts, styles, icons): always try the network first so a new
// version shows up straight away; the saved copy is only used when offline.
function isAppFile(url) {
  return url.origin === self.location.origin &&
    /\\.(js|css|png|svg|ico|webp|woff2?)$/.test(url.pathname) &&
    !url.pathname.startsWith("/finance/receipts/") &&
    !url.pathname.startsWith("/static/receipts/");
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // Pages always come live from the server (your data is never saved here).
  // With no connection, show the offline page instead of the browser's error.
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  if (isAppFile(url)) {
    e.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => caches.match(req))
    );
  }
});
"""

OFFLINE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#151821">
<title>Offline · Daily Planner</title>
<style>
  body { margin:0; min-height:100vh; display:flex; flex-direction:column; align-items:center;
    justify-content:center; gap:14px; padding:24px; box-sizing:border-box; text-align:center;
    background:#f6f7f9; color:#232733;
    font:16px/1.5 -apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif; }
  img { width:72px; height:72px; border-radius:16px; }
  h1 { margin:0; font-size:1.3rem; }
  p { margin:0; color:#667; max-width:320px; }
  button { margin-top:8px; padding:12px 28px; font:inherit; font-weight:700; color:#fff;
    background:#151821; border:0; border-radius:12px; }
</style></head><body>
  <img src="/pwa/icon-192.png" alt="">
  <h1>No internet connection</h1>
  <p>Daily Planner needs the internet to load your calendar and money. Check Wi-Fi or mobile data, then try again.</p>
  <button onclick="location.reload()">Try again</button>
  <script>addEventListener("online", function () { location.reload(); });</script>
</body></html>"""


@app.route("/manifest.webmanifest")
def pwa_manifest():
    resp = app.response_class(json.dumps(PWA_MANIFEST, ensure_ascii=False),
                              mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/sw.js")
def pwa_service_worker():
    resp = app.response_class(SERVICE_WORKER_JS.replace("__VERSION__", PWA_VERSION),
                              mimetype="application/javascript")
    # Browsers must re-check the worker itself, or app updates would stall.
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/offline")
def pwa_offline():
    return OFFLINE_HTML


_PWA_ICONS = {"icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"}


@app.route("/pwa/<name>")
def pwa_icon(name):
    if name not in _PWA_ICONS:
        abort(404)
    resp = send_from_directory(PWA_DIR, name)
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


# iOS asks for this at the site root whatever the <link> tag says.
@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def pwa_apple_icon():
    return pwa_icon("apple-touch-icon.png")


_PWA_PUBLIC_ENDPOINTS = {"pwa_manifest", "pwa_service_worker", "pwa_offline", "pwa_icon", "pwa_apple_icon",
                         # the home-screen widget can't log in — the URL's secret token is the key
                         "calendar_widget_data", "calendar_widget_zip", "calendar_widget_code"}


def _with_pwa_head(html):
    """Adds the phone-app tags to a page's <head>, plus a viewport tag if the
    page has none (most Finance templates don't, so phones drew them as a
    shrunken desktop page)."""
    if 'rel="manifest"' in html:
        return html
    tags = PWA_HEAD
    if 'name="viewport"' not in html:
        tags = PWA_VIEWPORT + tags
    else:
        # Let pages use the whole screen around the notch / home bar.
        for old in ('content="width=device-width, initial-scale=1.0"',
                    'content="width=device-width, initial-scale=1"'):
            if old in html:
                html = html.replace(old, 'content="width=device-width, initial-scale=1, viewport-fit=cover"', 1)
                break
    i = html.lower().find("</head>")
    if i != -1:
        return html[:i] + tags + html[i:]
    return tags + html


@app.after_request
def _inject_pwa(resp):
    if (resp.mimetype == "text/html" and resp.status_code == 200
            and request.endpoint != "pwa_offline"):
        resp.direct_passthrough = False
        resp.set_data(_with_pwa_head(resp.get_data(as_text=True)))
    return resp


# ── iPhone home-screen widget (via the "Scripting" app) ────────────
# Apple doesn't let a web app put a widget on the home screen, so the
# Scripting app runs one (scripting/DailyPlanner in this repo, installed
# straight from GitHub). The widget can't log in: it reads
# /calendar/widget/<profile>/<token>.json, where the token is tied to the
# profile and the server's secret key. /calendar/widget (logged in) shows the
# owner that link to paste into the app once — it is never stored in the repo.
SCRIPTING_DIR = os.path.join(BASE_DIR, "scripting", "DailyPlanner")


WIDGET_CODE_FILES = ("widget.tsx", "app_intents.tsx", "index.tsx", "script.json")


@app.route("/calendar/widget/code/<name>")
def calendar_widget_code(name):
    # The widget's files (no personal data in them), downloaded by the installer.
    if name not in WIDGET_CODE_FILES:
        abort(404)
    with open(os.path.join(SCRIPTING_DIR, name), "r", encoding="utf-8") as fh:
        resp = app.response_class(fh.read(), mimetype="text/plain; charset=utf-8")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/calendar/widget/DailyPlanner.zip")
def calendar_widget_zip():
    # The widget's code (no personal data in it) as a zip the Scripting app can import.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(os.listdir(SCRIPTING_DIR)):
            path = os.path.join(SCRIPTING_DIR, name)
            if os.path.isfile(path):
                zf.write(path, name)
    resp = app.response_class(buf.getvalue(), mimetype="application/zip")
    resp.headers["Content-Disposition"] = "attachment; filename=DailyPlanner.zip"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _site_base():
    https = os.environ.get("RENDER") or request.headers.get("X-Forwarded-Proto") == "https" or request.is_secure
    return f"{'https' if https else 'http'}://{request.host}"


@app.route("/calendar/widget/<profile_id>/<token>.json")
def calendar_widget_data(profile_id, token):
    if not schedule_feed.token_ok(app.secret_key, profile_id, token):
        abort(404)
    profile = find_profile(load_profiles(), profile_id)
    if not profile:
        abort(404)
    state = calendar_habits.load_calendar_state(profile_id)
    # A week back and a month ahead, so the widget's ◀ ▶ buttons have days to show.
    data = schedule_feed.build_widget_data(state, name=profile.get("name") or "", days_before=7, days_after=30)
    resp = app.response_class(json.dumps(data, ensure_ascii=False), mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    return resp


WIDGET_PAGE_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>Home-screen widget · Daily Planner</title>
<style>
  body { margin:0; background:#f6f7f9; color:#232733;
    font:16px/1.6 -apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif; }
  main { max-width:560px; margin:0 auto; padding:28px 18px 40px; }
  h1 { font-size:1.35rem; margin:0 0 6px; }
  p.lead { margin:0 0 20px; color:#5b6170; }
  .card { background:#fff; border:1px solid #e7e9ee; border-radius:16px; padding:18px; margin-bottom:14px; }
  .card h2 { font-size:1.05rem; margin:0 0 8px; }
  .btn { display:block; width:100%; box-sizing:border-box; text-align:center; background:#151821; color:#fff;
    text-decoration:none; font:inherit; font-weight:700; padding:14px; border:0; border-radius:12px; margin-top:8px; }
  .btn.light { background:#fff; color:#151821; border:1px solid #151821; }
  ol { margin:6px 0 0; padding-left:22px; } li { margin:4px 0; }
  input.link { width:100%; box-sizing:border-box; margin-top:10px; font:12px monospace;
    border:1px solid #d6d9e0; border-radius:10px; padding:8px; background:#f9fafb; color:#555; }
  textarea { width:100%; box-sizing:border-box; height:90px; margin-top:10px; font:11px monospace;
    border:1px solid #d6d9e0; border-radius:10px; padding:8px; background:#f9fafb; color:#555; }
  .keys { display:grid; grid-template-columns:auto 1fr; gap:4px 12px; margin:6px 0 0; }
  .keys b { text-align:center; }
  .note { font-size:.88rem; color:#6b7180; }
</style></head><body><main>
  <h1>&#128242; 主屏幕小组件</h1>
  <p class="lead">在 iPhone 主屏幕直接看你的 Day 视图——跟 Calendar 里的一样。一个<b>大号</b>放上面、一个<b>中号</b>接在下面，整页就是你的时间表。</p>

  <div class="card">
    <h2>1. 安装 Scripting（免费）</h2>
    <a class="btn light" href="https://apps.apple.com/app/id6479691128">在 App Store 打开 Scripting</a>
  </div>

  <div class="card">
    <h2>2. 在 Scripting 新建脚本</h2>
    <ol>
      <li>打开 Scripting，按右上角 <b>＋</b>，名字打 <b>Daily Planner</b>（已经建过就用原本那个）</li>
      <li>打开它，按左下角 <b>📁 文件夹</b> 按钮</li>
    </ol>
  </div>

  <div class="card">
    <h2>3. 加两个文件（各贴一次）</h2>
    <p><b>第一个：widget.tsx</b></p>
    <button class="btn" id="copyw">复制第一段</button>
    <ol>
      <li>在文件夹画面按左下角 <b>📄＋ 新建文件</b>，名字打 <b>widget.tsx</b></li>
      <li>打开它，里面有字就全部删掉，长按 → <b>粘贴</b> → 关掉</li>
    </ol>
    <p style="margin-top:14px"><b>第二个：app_intents.tsx</b></p>
    <button class="btn" id="copya">复制第二段</button>
    <ol>
      <li>再按 <b>📄＋ 新建文件</b>，名字打 <b>app_intents.tsx</b></li>
      <li>打开它，里面有字就全部删掉，长按 → <b>粘贴</b> → 关掉</li>
    </ol>
    <p class="note">名字要一模一样（小写、有 .tsx）。做完文件夹里会有 widget.tsx 和 app_intents.tsx。</p>
    <textarea id="codew" readonly style="display:none">__WIDGET_CODE__</textarea>
    <textarea id="codea" readonly style="display:none">__INTENTS_CODE__</textarea>
  </div>

  <div class="card">
    <h2>4. 放到主屏幕</h2>
    <ol>
      <li>主屏幕空白处 <b>长按</b> → 左上角 <b>编辑</b> →「添加小组件」→ 找 <b>Scripting</b></li>
      <li>选 <b>大号</b> → 添加；再加一个 <b>中号</b>，放在大号的<b>正下方</b></li>
      <li>长按每个小组件 →「编辑小组件」→ Script 选 <b>Daily Planner</b></li>
    </ol>
    <p class="note">建议把这一页主屏幕只放这两个小组件，看起来就是一整页 Day 视图。</p>
  </div>

  <div class="card">
    <h2>小组件上的按钮</h2>
    <div class="keys">
      <b>▲ ▼</b><span>看更早 / 更晚的时间（大号和中号一起动）</span>
      <b>◀ ▶</b><span>前一天 / 后一天</span>
      <b>日期</b><span>点一下回到今天、现在的时间</span>
      <b>＋</b><span>打开网站的 Add（新增活动）</span>
      <b>活动</b><span>点活动方块，在网站打开那一天</span>
    </div>
    <p class="note">30 分钟没按，它会自己回到今天、现在的时间。</p>
  </div>

  <p class="note">&#8226; 小组件大约每 15 分钟自己更新一次（iPhone 决定准确时间），按按钮会马上更新。<br>
  &#8226; 第一段代码里有你专属的链接，等于看你时间表的钥匙，不要发给别人。</p>
</main>
<script>
function copyButton(btnId, inputId, doneText) {
  document.getElementById(btnId).addEventListener("click", function () {
    var inp = document.getElementById(inputId), btn = this;
    function done() { btn.textContent = doneText; }
    function fallback() { inp.style.display = "block"; inp.select(); inp.setSelectionRange(0, 999999); document.execCommand("copy"); inp.style.display = "none"; done(); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(inp.value).then(done, fallback);
    } else { fallback(); }
  });
}
copyButton("copyw", "codew", "已复制第一段 ✓  去贴进 widget.tsx");
copyButton("copya", "codea", "已复制第二段 ✓  去贴进 app_intents.tsx");
</script></body></html>"""


@app.route("/calendar/widget")
def calendar_widget_page():
    profile = authed_profile()
    token = schedule_feed.feed_token(app.secret_key, profile["id"])
    base = _site_base()
    feed_url = f"{base}/calendar/widget/{profile['id']}/{token}.json"
    with open(os.path.join(SCRIPTING_DIR, "widget.tsx"), "r", encoding="utf-8") as fh:
        widget_code = fh.read().replace("__FEED_URL__", feed_url)
    with open(os.path.join(SCRIPTING_DIR, "app_intents.tsx"), "r", encoding="utf-8") as fh:
        intents_code = fh.read()
    html = (WIDGET_PAGE_HTML.replace("__WIDGET_CODE__", html_escape(widget_code))
                            .replace("__INTENTS_CODE__", html_escape(intents_code)))
    return _with_switcher(html, "calendar")


# ── calendar (static files in Calendar/) ────────────────────────────
# Optional one-time bootstrap for legacy Calendar browser exports. New Calendar
# reads and writes go to PostgreSQL; this script only seeds the old localStorage
# key so the authenticated page can import it when that Profile has no row yet.
# A marker prevents reruns. Prefer Calendar/transfer.html for per-Profile imports.
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
    profile = authed_profile()
    identity = json.dumps({"id": profile["id"], "name": profile["name"]})
    identity = identity.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = html.replace(
        "</head>",
        f"<script>window.DAILY_PLANNER_PROFILE={identity};</script></head>",
        1,
    )
    seed = _calendar_migrate_script()
    if seed:
        html = html.replace("<head>", "<head>\n" + seed, 1)
    return _with_switcher(html, "calendar")


# Only the Calendar's front-end files are served. Python sources, caches and
# local data files such as _migrate.json (which can hold personal data) are not.
_CALENDAR_ASSET_TYPES = {".html", ".js", ".css", ".svg", ".png", ".ico", ".webp", ".jpg", ".jpeg", ".woff", ".woff2"}


@app.route("/calendar/<path:filename>")
def calendar_asset(filename):
    full = os.path.normpath(os.path.join(CALENDAR_DIR, filename))
    if os.path.commonpath([full, CALENDAR_DIR]) != CALENDAR_DIR:
        abort(404)
    parts = os.path.relpath(full, CALENDAR_DIR).split(os.sep)
    if any(part.startswith((".", "_")) for part in parts):
        abort(404)
    if os.path.splitext(full)[1].lower() not in _CALENDAR_ASSET_TYPES or not os.path.isfile(full):
        abort(404)
    return send_from_directory(CALENDAR_DIR, filename)


if __name__ == "__main__":
    print("\n  Daily Planner  ->  http://127.0.0.1:5050"
          "\n    Finance   http://127.0.0.1:5050/finance/"
          "\n    Calendar  http://127.0.0.1:5050/calendar/\n")
    app.run(debug=True, port=5050)

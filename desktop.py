"""
Daily Planner desktop launcher.

Opens the Daily Planner website in its own window. Closing the window does not
quit: it shrinks to a small button in the bottom-right corner, and clicking
that button opens the window again. Hover the button and press x to quit;
drag the button to move it (the spot is remembered).

    .venv\\Scripts\\pythonw.exe desktop.py             open now
    .venv\\Scripts\\pythonw.exe desktop.py --startup   open, then shrink to the
                                                      corner after a few seconds
                                                      (used when Windows starts)

The website address is kept in desktop.local.json (git-ignored). On the first
run the window asks for it. Run scripts\\install_desktop_startup.ps1 once to
start this automatically when you log in to Windows.
"""
import ctypes
import json
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, ".data", "desktop.log")

# pythonw has no console, so errors would vanish: write them to a log file.
if sys.stdout is None or sys.stderr is None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    sys.stdout = sys.stderr = open(LOG_PATH, "a", encoding="utf-8", buffering=1)

import webview  # noqa: E402  (after the log redirect, so its errors are logged too)

CONFIG_PATH = os.path.join(BASE_DIR, "desktop.local.json")
# Keeps the login cookie between runs (git-ignored via .data/).
STORAGE_DIR = os.path.join(BASE_DIR, ".data", "desktop-webview")
# Only one copy runs; a second launch just asks the first to open its window.
# Uses named Windows objects, not a network port, so nothing can block it.
MUTEX_NAME = r"Local\DailyPlannerDesktop"
SHOW_EVENT_NAME = r"Local\DailyPlannerDesktopShow"
STARTUP_HIDE_SECONDS = 4
# Windows will not make a window narrower than about 118px, so the corner
# button is a short bar rather than a square.
BUBBLE_W, BUBBLE_H = 124, 44


def load_url():
    url = os.environ.get("DAILY_PLANNER_URL", "").strip()
    if url:
        return url
    return (load_config().get("url") or "").strip()


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(**changes):
    data = load_config()
    data.update(changes)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


SETUP_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
  background:#f6f7f9;font:16px/1.5 "Segoe UI","Microsoft YaHei",sans-serif;color:#232733}
.box{background:#fff;padding:32px;border-radius:16px;width:440px;
  box-shadow:0 8px 24px rgba(20,25,40,.08)}
h2{margin:0 0 6px}p{margin:0 0 16px;color:#667}
input{width:100%;box-sizing:border-box;padding:10px 12px;font-size:15px;
  border:1px solid #ccd;border-radius:8px}
button{margin-top:14px;padding:10px 18px;font-size:15px;border:0;border-radius:8px;
  background:#3a6ea5;color:#fff;cursor:pointer}
</style></head><body><div class="box">
<h2>Daily Planner</h2>
<p>请输入 Daily Planner 的网址 (Render)</p>
<input id="u" placeholder="https://xxxx.onrender.com" autofocus>
<button onclick="go()">保存并打开</button>
</div><script>
function go(){var u=document.getElementById('u').value.trim();
  if(!/^https?:\\/\\//.test(u))u='https://'+u;
  pywebview.api.set_url(u);}
document.getElementById('u').addEventListener('keydown',function(e){if(e.key==='Enter')go();});
</script></body></html>"""

BUBBLE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;overflow:hidden;background:#151821;user-select:none;
  font:600 14px "Segoe UI","Microsoft YaHei",sans-serif;color:#fff}
/* Fixed to the button's real size: with display scaling the page can be
   laid out wider than the window, which pushed centred content off the edge. */
#bar,#ask{position:absolute;left:0;top:0;width:__W__px;height:__H__px;display:flex;align-items:center;justify-content:center;
  gap:6px;cursor:pointer;transition:background .12s;touch-action:none}
#bar span{font-size:20px}
#bar:hover{background:#3a6ea5}
#bar.drag{background:#2a4f78;cursor:grabbing}
#quit{position:absolute;top:2px;left:__QX__px;width:16px;height:16px;border-radius:50%;
  background:#c0392b;color:#fff;font:bold 11px/16px sans-serif;text-align:center;
  cursor:pointer;display:none}
body:hover #quit{display:block}
#ask{display:none;align-items:center;justify-content:center;
  gap:4px;background:#151821;font-size:12px}
#ask b{padding:2px 6px;border-radius:5px;cursor:pointer}
#yes{background:#c0392b}#no{background:#3a4152}
body.asking #ask{display:flex}body.asking #quit{display:none !important}
</style></head><body title="Daily Planner — 点一下打开，按住拖动">
<div id="bar"><span>&#128197;</span>Planner</div>
<div id="quit" title="关闭 Daily Planner">&times;</div>
<div id="ask">关闭?<b id="yes">是</b><b id="no">否</b></div>
<script>
var bar=document.getElementById('bar'), body=document.body;
var down=null, dragging=false, origin=null, pending=null, busy=false, askTimer=null;

// Moves go to Python one at a time, always with the latest position.
function flush(){
  if(busy||!origin||!pending)return;
  var d=pending; pending=null; busy=true;
  pywebview.api.move_bubble(origin[0]+d[0], origin[1]+d[1], d[2])
    .then(function(){busy=false;flush();}, function(){busy=false;flush();});
}
bar.addEventListener('pointerdown',function(e){
  if(e.button!==0)return;
  bar.setPointerCapture(e.pointerId);
  down={x:e.screenX,y:e.screenY}; dragging=false; origin=null; pending=null;
  pywebview.api.bubble_pos().then(function(p){origin=p;flush();});
});
bar.addEventListener('pointermove',function(e){
  if(!down)return;
  var dx=e.screenX-down.x, dy=e.screenY-down.y;
  if(!dragging&&Math.abs(dx)+Math.abs(dy)<5)return;  // small wobble = still a click
  dragging=true; bar.classList.add('drag');
  pending=[dx,dy,false]; flush();
});
bar.addEventListener('pointerup',function(e){
  if(!down)return;
  var dx=e.screenX-down.x, dy=e.screenY-down.y, was=dragging;
  down=null; dragging=false; bar.classList.remove('drag');
  if(was){pending=[dx,dy,true];flush();}   // last move also saves the spot
  else pywebview.api.expand();
});
document.getElementById('quit').addEventListener('click',function(e){
  e.stopPropagation(); body.classList.add('asking');
  clearTimeout(askTimer); askTimer=setTimeout(function(){body.classList.remove('asking');},5000);
});
document.getElementById('yes').addEventListener('click',function(){pywebview.api.quit();});
document.getElementById('no').addEventListener('click',function(){body.classList.remove('asking');});
</script>
</body></html>""".replace("__W__", str(BUBBLE_W)).replace("__H__", str(BUBBLE_H)) \
    .replace("__QX__", str(BUBBLE_W - 18))


class Api:
    """Called from the pages via pywebview.api.*. Attributes start with an
    underscore so pywebview does not try to expose the window objects."""

    def __init__(self):
        self._main = None
        self._bubble = None
        self._quitting = False

    def expand(self):
        self._bubble.hide()
        self._main.show()
        self._main.restore()

    def shrink(self):
        self._main.hide()
        self._bubble.show()

    def set_url(self, url):
        save_config(url=url)
        self._main.load_url(url)

    def bubble_pos(self):
        return [self._bubble.x, self._bubble.y]

    def move_bubble(self, x, y, save=False):
        x, y = clamp_bubble(int(x), int(y))
        self._bubble.move(x, y)
        if save:
            save_config(bubble=[x, y])

    def quit(self):
        self._quitting = True
        self._bubble.destroy()
        self._main.destroy()


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
ERROR_ALREADY_EXISTS = 183
INFINITE = 0xFFFFFFFF


def listen_for_second_launch(api, show_event):
    """A second launch sets the show event: open the window."""
    while True:
        kernel32.WaitForSingleObject(show_event, INFINITE)
        try:
            api.expand()
        except Exception:
            traceback.print_exc()


def clamp_bubble(x, y):
    """Keep the corner button fully on screen."""
    screen = webview.screens[0]
    x = max(0, min(x, screen.width - BUBBLE_W))
    y = max(0, min(y, screen.height - BUBBLE_H))
    return x, y


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def main():
    startup = "--startup" in sys.argv
    log(f"start {' '.join(sys.argv[1:])}")

    # Kept in a variable so the mutex lives as long as this process.
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)  # noqa: F841
    already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    show_event = kernel32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
    if already_running:
        log("already running: asked it to open its window")
        kernel32.SetEvent(show_event)
        return

    url = load_url()
    api = Api()
    screen = webview.screens[0]
    width = min(1280, screen.width - 80)
    height = min(820, screen.height - 100)
    main_win = webview.create_window(
        "Daily Planner",
        url=url or None,
        html=None if url else SETUP_HTML,
        js_api=api,
        width=width,
        height=height,
        x=(screen.width - width) // 2,
        y=max(0, (screen.height - height) // 2 - 20),
        min_size=(600, 400),
    )

    saved = load_config().get("bubble")
    if isinstance(saved, list) and len(saved) == 2:
        bubble_x, bubble_y = clamp_bubble(int(saved[0]), int(saved[1]))
    else:
        bubble_x = screen.width - BUBBLE_W - 16
        bubble_y = screen.height - BUBBLE_H - 64  # above the taskbar
    bubble = webview.create_window(
        "Daily Planner mini",
        html=BUBBLE_HTML,
        js_api=api,
        width=BUBBLE_W,
        height=BUBBLE_H,
        min_size=(BUBBLE_W, BUBBLE_H),
        x=bubble_x,
        y=bubble_y,
        frameless=True,
        easy_drag=False,
        on_top=True,
        resizable=False,
        hidden=True,
    )
    api._main = main_win
    api._bubble = bubble

    def on_closing():
        if api._quitting:
            return True
        # The X button shrinks to the corner instead of quitting.
        threading.Thread(target=api.shrink, daemon=True).start()
        return False

    main_win.events.closing += on_closing
    # Alt+F4 on the corner button should not leave nothing to click.
    bubble.events.closing += lambda: api._quitting

    def on_start():
        threading.Thread(target=listen_for_second_launch, args=(api, show_event), daemon=True).start()
        if startup and url:
            time.sleep(STARTUP_HIDE_SECONDS)
            api.shrink()

    os.makedirs(STORAGE_DIR, exist_ok=True)
    webview.start(on_start, private_mode=False, storage_path=STORAGE_DIR)
    log("quit")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise

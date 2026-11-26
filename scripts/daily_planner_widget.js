// Daily Planner — iPhone home-screen widget (runs in the free "Scriptable" app).
//
// Don't copy this file by hand: open Daily Planner → Home → "Home-screen widget"
// and press Copy. That copy has your own private link filled in below.
//
// Shows what's left today (with your category colours), then the next days.
// Tap the widget to open Daily Planner. Works on the home screen (small /
// medium / large) and the lock screen. Keeps the last schedule it loaded, so it
// still shows something without internet.

const FEED_URL = "__FEED_URL__";
const OPEN_URL = "__OPEN_URL__";

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const BG = Color.dynamic(new Color("#ffffff"), new Color("#1c1c1e"));
const INK = Color.dynamic(new Color("#0b0b0b"), new Color("#f2f2f7"));
const MUTED = Color.dynamic(new Color("#8a8a8e"), new Color("#98989f"));
const ACCENT = new Color("#3a6ea5");

const family = config.widgetFamily || "medium";
// How many rows fit: an item costs 1, a later day's heading 0.7.
const ROOM = { small: 3, medium: 3, large: 8, extraLarge: 8 };

function isoOf(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function minutes(hhmm) {
  if (!hhmm) return null;
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}
function dayLabel(iso, todayISO) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  const [ty, tm, td] = todayISO.split("-").map(Number);
  const diff = Math.round((date - new Date(ty, tm - 1, td)) / 86400000);
  const name = diff === 0 ? "今天" : diff === 1 ? "明天" : diff === 2 ? "后天" : "";
  return `${name ? name + " · " : ""}${WEEKDAYS[date.getDay()]} ${d}/${m}`;
}
function timeText(item) {
  if (item.allDay) return item.kind === "task" ? "待办" : "全天";
  return item.end ? `${item.start}–${item.end}` : item.start;
}

async function loadData() {
  const fm = FileManager.local();
  const cache = fm.joinPath(fm.documentsDirectory(), "daily-planner-widget.json");
  try {
    const req = new Request(FEED_URL);
    req.timeoutInterval = 12;
    const data = await req.loadJSON();
    if (!data || !Array.isArray(data.days)) throw new Error("bad data");
    fm.writeString(cache, JSON.stringify(data));
    return { data, offline: false };
  } catch (e) {
    if (fm.fileExists(cache)) return { data: JSON.parse(fm.readString(cache)), offline: true };
    return { data: null, offline: true };
  }
}

// What's still to come: today's items that haven't ended yet, then later days.
function upcoming(data, now) {
  const todayISO = isoOf(now);
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const groups = [];
  for (const day of data.days) {
    if (day.date < todayISO) continue;
    let items = day.items;
    if (day.date === todayISO) {
      items = items.filter((it) => it.allDay || (minutes(it.end || it.start) ?? 0) > nowMin);
      items = items.map((it) => ({
        ...it,
        now: !it.allDay && minutes(it.start) <= nowMin && nowMin < (minutes(it.end) ?? minutes(it.start) + 60),
      }));
    }
    groups.push({ date: day.date, items });
  }
  return { todayISO, groups };
}

function addItemRow(parent, item, compact) {
  const row = parent.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();
  const bar = row.addStack();
  bar.size = new Size(4, compact ? 26 : 30);
  bar.cornerRadius = 2;
  bar.backgroundColor = new Color(item.color || "#898781");
  row.addSpacer(7);
  const col = row.addStack();
  col.layoutVertically();
  const title = col.addText((item.kind === "task" ? (item.done ? "✅ " : "☐ ") : item.kind === "special" ? "🎉 " : "") + item.title);
  title.font = Font.semiboldSystemFont(compact ? 12 : 13);
  title.textColor = INK;
  title.lineLimit = 1;
  if (item.done) title.textOpacity = 0.5;
  const meta = col.addStack();
  meta.layoutHorizontally();
  const t = meta.addText(timeText(item) + (item.place && !compact ? `  ·  ${item.place}` : ""));
  t.font = Font.systemFont(compact ? 10 : 11);
  t.textColor = MUTED;
  t.lineLimit = 1;
  if (item.now) {
    meta.addSpacer(5);
    const n = meta.addText("进行中");
    n.font = Font.boldSystemFont(compact ? 10 : 11);
    n.textColor = ACCENT;
  }
  row.addSpacer();
}

function buildHomeWidget(data, offline, now) {
  const w = new ListWidget();
  w.backgroundColor = BG;
  w.url = OPEN_URL;
  w.setPadding(12, 14, 12, 14);
  w.refreshAfterDate = new Date(now.getTime() + 15 * 60 * 1000);
  const compact = family === "small";
  const room = ROOM[family] || 3;

  if (!data) {
    const t = w.addText("Daily Planner\n连不上网络，稍后再试");
    t.font = Font.systemFont(12);
    t.textColor = MUTED;
    return w;
  }

  const { todayISO, groups } = upcoming(data, now);
  let used = 0;
  let first = true;
  for (const g of groups) {
    const isToday = g.date === todayISO;
    if (!g.items.length && !isToday) continue;
    if (!first) used += 0.7;
    if (used + 1 > room) break;
    if (!first) w.addSpacer(compact ? 4 : 6);
    const head = w.addText(dayLabel(g.date, todayISO));
    head.font = Font.boldSystemFont(compact ? 11 : 12);
    head.textColor = isToday ? ACCENT : MUTED;
    w.addSpacer(compact ? 3 : 4);
    first = false;
    if (!g.items.length) {
      const none = w.addText("今天没有其他安排了 🎉");
      none.font = Font.systemFont(compact ? 11 : 12);
      none.textColor = MUTED;
      continue;
    }
    for (const item of g.items) {
      if (used + 1 > room) break;
      addItemRow(w, item, compact);
      w.addSpacer(compact ? 3 : 4);
      used += 1;
    }
  }
  w.addSpacer();
  if (offline) {
    const o = w.addText("离线 · 显示上次的资料");
    o.font = Font.systemFont(9);
    o.textColor = MUTED;
  }
  return w;
}

// Lock screen: the next thing coming up.
function buildLockWidget(data, now) {
  const w = new ListWidget();
  w.url = OPEN_URL;
  w.refreshAfterDate = new Date(now.getTime() + 15 * 60 * 1000);
  if (!data) {
    w.addText("Daily Planner");
    return w;
  }
  const { todayISO, groups } = upcoming(data, now);
  const next = [];
  for (const g of groups) for (const it of g.items) next.push({ ...it, date: g.date });
  const todayLeft = (groups.find((g) => g.date === todayISO) || { items: [] }).items.length;

  if (family === "accessoryCircular") {
    w.addAccessoryWidgetBackground = true;
    const n = w.addText(String(todayLeft));
    n.font = Font.boldSystemFont(22);
    n.centerAlignText();
    const l = w.addText("今天");
    l.font = Font.systemFont(10);
    l.centerAlignText();
    return w;
  }
  // Prefer the next thing with a time (a class, a meeting) over all-day items.
  const timedFirst = [...next.filter((x) => !x.allDay), ...next.filter((x) => x.allDay)];
  if (!timedFirst.length) {
    w.addText("接下来没有安排");
    return w;
  }
  next.splice(0, next.length, ...timedFirst);
  const it = next[0];
  const when = (it.date === todayISO ? "" : dayLabel(it.date, todayISO).split(" · ")[0] + " ") + timeText(it);
  if (family === "accessoryInline") {
    w.addText(`${when} ${it.title}`);
    return w;
  }
  const t = w.addText(it.title);
  t.font = Font.boldSystemFont(14);
  t.lineLimit = 1;
  const s = w.addText(when + (it.place ? ` · ${it.place}` : ""));
  s.font = Font.systemFont(12);
  s.lineLimit = 1;
  if (next[1]) {
    const n2 = w.addText(`接着：${next[1].title}`);
    n2.font = Font.systemFont(11);
    n2.lineLimit = 1;
    n2.textOpacity = 0.7;
  }
  return w;
}

const now = new Date();
const { data, offline } = await loadData();
const widget = family.startsWith("accessory") ? buildLockWidget(data, now) : buildHomeWidget(data, offline, now);

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentLarge(); // preview when run inside Scriptable
}
Script.complete();

// Daily Planner — iPhone home-screen widget (runs in the free "Scriptable" app).
//
// Don't copy this file by hand: open Daily Planner → Home → "Home-screen widget"
// and press Copy. That copy has your own private link filled in below.
//
// The home-screen widget is a picture of today's Day view, drawn the same way
// the app draws it (Calendar/src/render/dayGridView.js + styles/calendar.css):
// hour rows with "8 AM" labels, the "THU 26" heading, the DAY tray, and each
// event as a block in its colour with the lighter title panel in the middle.
// It shows the hours around now (or the next thing coming up). Tap it to open
// Daily Planner. The lock-screen widget shows the next item as text. Keeps the
// last schedule it loaded, so it still shows something without internet.

const FEED_URL = "__FEED_URL__";
const OPEN_URL = "__OPEN_URL__";

// ── the app's look (Calendar/styles/tokens.css) ─────────────────────
const C = {
  surface: "#ffffff",
  surfaceAlt: "#fcfcfb",
  border: "#e5e4dd",
  ink: "#0b0b0b",
  muted: "#898781",
  active: "#0b0b0b",
  fallback: "#898781",
};
const WEEKDAY_SHORT = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];

// Canvas size per widget size (points). iOS scales the picture to the
// widget, and every iPhone's widgets have about these proportions.
const CANVAS = { small: [170, 170], medium: [364, 170], large: [364, 382], extraLarge: [715, 382] };
// Hours shown at once (the app shows 56px per hour; a widget is smaller).
const HOURS_SHOWN = { small: 4, medium: 4, large: 9, extraLarge: 9 };

const family = config.widgetFamily || "large";

// ── helpers ─────────────────────────────────────────────────────────
function isoOf(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function minutes(hhmm) {
  if (!hhmm) return null;
  const [h, m] = hhmm.split(":").map(Number);
  return Number.isFinite(h) && Number.isFinite(m) ? h * 60 + m : null;
}
// Same as formatTime / formatHourLabel in Calendar/src/dateUtils.js.
function formatTime(hhmm) {
  const t = minutes(hhmm);
  if (t == null) return "";
  const h = Math.floor(t / 60), m = t % 60;
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, "0")} ${h >= 12 ? "PM" : "AM"}`;
}
function formatHourLabel(h) {
  return `${h % 12 === 0 ? 12 : h % 12} ${h >= 12 ? "PM" : "AM"}`;
}
// Same as inkOn in Calendar/src/utils.js: dark text on light colours.
function hexRGB(hex) {
  let h = String(hex || "").replace("#", "").trim();
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}
function inkOn(hex) {
  const rgb = hexRGB(hex);
  if (!rgb) return "#1b1b1b";
  return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255 > 0.62 ? "#1b1b1b" : "#ffffff";
}
// CSS color-mix(in srgb, a p%, b).
function mix(a, p, b) {
  const x = hexRGB(a) || hexRGB(C.fallback), y = hexRGB(b);
  const c = x.map((v, i) => Math.round(v * p + y[i] * (1 - p)));
  return "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");
}
const col = (hex, alpha) => new Color(hex, alpha == null ? 1 : alpha);

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

// Side-by-side columns for overlapping blocks — layoutDayEvents in
// Calendar/src/timeLayout.js.
function layoutDay(items) {
  const sorted = [...items].sort((a, b) => minutes(a.start) - minutes(b.start));
  const out = [];
  let cluster = [], clusterEnd = -Infinity;
  const flush = () => {
    if (!cluster.length) return;
    const ends = [], placed = [];
    for (const it of cluster) {
      const s = minutes(it.start), e = Math.max(minutes(it.end) ?? s + 60, s + 15);
      let c = ends.findIndex((t) => t <= s);
      if (c === -1) { c = ends.length; ends.push(e); } else ends[c] = e;
      placed.push({ it, c, s, e });
    }
    placed.forEach((p) => out.push({ item: p.it, col: p.c, cols: ends.length, startMin: p.s, endMin: p.e }));
    cluster = []; clusterEnd = -Infinity;
  };
  for (const it of sorted) {
    const s = minutes(it.start), e = Math.max(minutes(it.end) ?? s + 60, s + 15);
    if (cluster.length && s >= clusterEnd) flush();
    cluster.push(it);
    clusterEnd = Math.max(clusterEnd, e);
  }
  flush();
  return out;
}

// Scriptable can't measure text, so widths are estimated per character.
function textWidth(str, size, bold) {
  let w = 0;
  for (const ch of str) {
    if (/[⺀-鿿가-힯＀-￯]/.test(ch)) w += size;
    else if (/[A-Z0-9]/.test(ch)) w += size * (bold ? 0.68 : 0.62);
    else if (ch === " ") w += size * 0.28;
    else w += size * (bold ? 0.56 : 0.52);
  }
  return w;
}
// Greedy wrap, breaking inside a word only when the word alone is too wide
// (like overflow-wrap: break-word on the app's title).
function wrap(str, size, bold, width) {
  const lines = [];
  let line = "";
  const push = () => { if (line) lines.push(line); line = ""; };
  for (const word of str.split(/(\s+)/)) {
    if (!word) continue;
    const cand = line + word;
    if (textWidth(cand.trim(), size, bold) <= width) { line = cand; continue; }
    if (/^\s+$/.test(word)) { push(); continue; }
    push();
    let piece = "";
    for (const ch of word) {
      if (textWidth(piece + ch, size, bold) > width && piece) { lines.push(piece); piece = ""; }
      piece += ch;
    }
    line = piece;
  }
  push();
  return lines.map((l) => l.trim());
}

// ── drawing ─────────────────────────────────────────────────────────
function roundRect(ctx, x, y, w, h, r, fill, stroke, lineWidth) {
  if (w <= 0 || h <= 0) return;
  const p = new Path();
  p.addRoundedRect(new Rect(x, y, w, h), r, r);
  if (fill) { ctx.addPath(p); ctx.setFillColor(col(fill)); ctx.fillPath(); }
  if (stroke) { ctx.addPath(p); ctx.setStrokeColor(col(stroke)); ctx.setLineWidth(lineWidth || 1); ctx.strokePath(); }
}
function text(ctx, str, x, y, w, h, size, color, opts = {}) {
  ctx.setFont(opts.bold ? Font.boldSystemFont(size) : opts.semibold ? Font.semiboldSystemFont(size) : Font.systemFont(size));
  ctx.setTextColor(col(color, opts.alpha));
  if (opts.align === "left") ctx.setTextAlignedLeft();
  else if (opts.align === "right") ctx.setTextAlignedRight();
  else ctx.setTextAlignedCenter();
  ctx.drawTextInRect(str, new Rect(x, y, w, h));
}
function hline(ctx, x1, x2, y, color) {
  ctx.setFillColor(col(color));
  ctx.fillRect(new Rect(x1, y, x2 - x1, 0.75));
}

// Which hours to show: from just before now, or from the next/ongoing block
// if that is later — so the widget shows what matters right now.
function windowStart(timed, nowMin, hours) {
  const live = timed
    .filter((it) => (minutes(it.end) ?? minutes(it.start) + 60) > nowMin)
    .sort((a, b) => minutes(a.start) - minutes(b.start))[0];
  let start = Math.floor(nowMin / 60) - 1;
  if (live) {
    const s = Math.floor(minutes(live.start) / 60);
    start = s > start + hours - 2 ? s : Math.min(start, s);
  }
  return Math.max(0, Math.min(24 - hours, start));
}

function drawEventBlock(ctx, b, x, y, w, h, scale) {
  const it = b.item;
  const color = it.color || C.fallback;
  if (it.kind === "task") {
    // .timegrid-task-block: pale tint, coloured border, checkbox + title row.
    roundRect(ctx, x, y, w, h, 4, mix(color, 0.14, C.surface), mix(color, 0.55, C.border), 1.25);
    const pad = 5 * scale, box = 10 * scale;
    const rowY = y + Math.min(5 * scale, h / 2 - box / 2);
    roundRect(ctx, x + pad, rowY + 1, box, box, 3, it.done ? color : C.surface, color, 1.25);
    const size = Math.max(8, 11 * scale);
    text(ctx, it.title, x + pad + box + 4, rowY - 1, w - pad * 2 - box - 4, size * 1.4, size, C.ink,
      { semibold: true, align: "left", alpha: it.done ? 0.5 : 1 });
    if (h >= 34 * scale) {
      text(ctx, `${formatTime(it.start)} – ${formatTime(it.end)}`, x + pad, rowY + size * 1.4, w - pad * 2,
        size * 1.3, Math.max(7, 9.5 * scale), C.ink, { alpha: 0.8, align: "left" });
    }
    return;
  }
  // .timegrid-event: solid block; .timegrid-event-header-panel: lighter tint
  // panel (85% colour + 15% white) with the title and time, centred.
  const ink = inkOn(color);
  roundRect(ctx, x, y, w, h, 4, color);
  const compact = h < 26 * scale;
  const narrow = w < 70;
  const margin = narrow ? 1 : 6 * scale;
  const innerW = w - margin * 2 - (narrow ? 2 : 10 * scale);
  const timeStr = `${formatTime(it.start)} – ${formatTime(it.end)}`;
  const avail = h - 8 * scale;
  let size = Math.max(narrow ? 7 : 8, Math.round(14 * scale));
  const minSize = narrow ? 7 : 8;
  let lines, timeH;
  for (;;) {
    lines = wrap(it.title, size, true, innerW);
    const timeSize = Math.max(7, Math.round(size * 0.82));
    timeH = !compact && !narrow ? timeSize * 1.25 : 0;
    if (lines.length * size * 1.22 + timeH + 6 <= avail || size <= minSize) break;
    size -= 1;
  }
  const maxLines = Math.max(1, Math.floor((avail - timeH - 6) / (size * 1.22)));
  if (lines.length > maxLines) {
    lines = lines.slice(0, maxLines);
    lines[maxLines - 1] = lines[maxLines - 1].replace(/.$/, "…");
  }
  const titleH = lines.length * size * 1.22;
  const panelH = Math.min(h - 4, titleH + timeH + (compact ? 2 : 6 * scale));
  const panelY = y + (h - panelH) / 2;
  if (!compact) roundRect(ctx, x + margin, panelY, w - margin * 2, panelH, 5, mix(color, 0.85, "#ffffff"));
  let ty = panelY + (panelH - titleH - timeH) / 2;
  for (const line of lines) {
    text(ctx, line, x + margin, ty - size * 0.08, w - margin * 2, size * 1.3, size, ink, { bold: true });
    ty += size * 1.22;
  }
  if (timeH) {
    text(ctx, timeStr, x + margin, ty, w - margin * 2, timeH + 2, Math.max(7, Math.round(size * 0.82)), ink,
      { semibold: true, alpha: 0.92 });
  }
}

function buildDayWidget(data, offline, now) {
  const w = new ListWidget();
  w.url = OPEN_URL;
  w.setPadding(0, 0, 0, 0);
  w.backgroundColor = col(C.surface);
  w.refreshAfterDate = new Date(now.getTime() + 15 * 60 * 1000);

  const [W, H] = CANVAS[family] || CANVAS.large;
  const scale = family === "small" ? 0.82 : family === "medium" ? 0.86 : 0.92;
  const ctx = new DrawContext();
  ctx.size = new Size(W, H);
  ctx.opaque = false;
  ctx.respectScreenScale = true;

  const todayISO = isoOf(now);
  const day = (data && data.days.find((d) => d.date === todayISO)) || { items: [] };
  const timed = day.items.filter((it) => !it.allDay && minutes(it.start) != null);
  const untimed = day.items.filter((it) => it.allDay || minutes(it.start) == null);
  const nowMin = now.getHours() * 60 + now.getMinutes();

  const gutter = family === "small" ? 30 : 40;
  const headH = 30;
  const trayH = untimed.length ? (family === "small" ? 20 : 22) : 0;
  const top = headH + trayH;

  // card
  ctx.setFillColor(col(C.surface));
  ctx.fillRect(new Rect(0, 0, W, H));

  // head row: weekday + day number in a black circle (today), like .timegrid-head-col
  ctx.setFillColor(col(C.surfaceAlt));
  ctx.fillRect(new Rect(0, 0, W, headH));
  const cx = gutter + (W - gutter) / 2;
  text(ctx, WEEKDAY_SHORT[now.getDay()], gutter, 2, W - gutter, 11, 8.5, C.muted, { bold: true });
  ctx.setFillColor(col(C.active));
  ctx.fillEllipse(new Rect(cx - 8.5, 12.5, 17, 17));
  text(ctx, String(now.getDate()), cx - 12, 14.2, 24, 14, 10, "#ffffff", { semibold: true });
  hline(ctx, 0, W, headH - 0.75, C.border);
  ctx.setFillColor(col(C.border));
  ctx.fillRect(new Rect(gutter - 0.75, 0, 0.75, H));

  // DAY tray: untimed items as pills; a special day (birthday, 三天班, ...)
  // stretches across the rest of the row in its solid colour, as in the app.
  if (trayH) {
    text(ctx, "DAY", 0, top - trayH / 2 - 5, gutter, 10, 7.5, C.muted, { bold: true });
    const size = 9;
    const labelOf = (it) => (it.kind === "task" && it.done ? "✓ " : "") + it.title;
    const plain = untimed.filter((it) => it.kind !== "special");
    const specials = untimed.filter((it) => it.kind === "special");
    const plainW = plain.reduce((s, it) => s + Math.min(textWidth(labelOf(it), size, false) + 14, 120) + 4, 0);
    const specialW = specials.length ? Math.max(40, (W - gutter - 8 - plainW) / specials.length - 4) : 0;
    let x = gutter + 4;
    for (const it of [...specials, ...plain]) {
      const label = labelOf(it);
      const special = it.kind === "special";
      const want = special ? specialW : Math.min(textWidth(label, size, false) + 14, 120);
      const pw = Math.min(want, W - x - 4);
      if (pw < 30) break;
      roundRect(ctx, x, headH + 3, pw, trayH - 6, (trayH - 6) / 2,
        special ? it.color : mix(it.color, 0.12, C.surface), special ? null : mix(it.color, 0.35, C.border), 0.75);
      const shown = wrap(label, size, special, pw - 10)[0] || "";
      text(ctx, shown, x + 5, headH + 3 + (trayH - 6 - size * 1.3) / 2, pw - 10, size * 1.35, size,
        special ? inkOn(it.color) : C.ink, { semibold: true, alpha: it.done ? 0.5 : 1 });
      x += pw + 4;
    }
    hline(ctx, 0, W, top - 0.75, C.border);
  }

  // timeline
  const hours = HOURS_SHOWN[family] || 9;
  const startHour = windowStart(timed, nowMin, hours);
  const hourH = (H - top) / hours;
  const yFor = (min) => top + ((min - startHour * 60) / 60) * hourH;
  for (let i = 0; i < hours; i++) {
    const y = top + i * hourH;
    if (i > 0) hline(ctx, gutter, W, y, C.border);
    const h = startHour + i;
    if (h > 0) {
      text(ctx, formatHourLabel(h), 0, i === 0 ? y + 2 : y - 6, gutter - 2, 11, family === "small" ? 7.5 : 8.5,
        C.muted, { semibold: true });
    }
  }

  const colX = gutter, colW = W - gutter;
  ctx.setFillColor(col(C.surface));
  for (const b of layoutDay(timed)) {
    const y1 = Math.max(yFor(b.startMin), top), y2 = Math.min(yFor(b.endMin), H);
    if (y2 - y1 < 4) continue;
    const bw = colW / b.cols;
    drawEventBlock(ctx, b, colX + b.col * bw + 2, y1 + 0.5, bw - 4, y2 - y1 - 1, scale);
  }

  if (offline) {
    text(ctx, data ? "离线 · 上次的资料" : "连不上网络", W - 110, 3, 106, 10, 7, C.muted, { align: "right" });
  }
  w.backgroundImage = ctx.getImage();
  return w;
}

// ── lock screen: the next thing coming up, as text ──────────────────
function buildLockWidget(data, now) {
  const w = new ListWidget();
  w.url = OPEN_URL;
  w.refreshAfterDate = new Date(now.getTime() + 15 * 60 * 1000);
  if (!data) {
    w.addText("Daily Planner");
    return w;
  }
  const todayISO = isoOf(now);
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const next = [];
  for (const d of data.days) {
    if (d.date < todayISO) continue;
    for (const it of d.items) {
      if (it.allDay) continue;
      if (d.date === todayISO && (minutes(it.end) ?? minutes(it.start) + 60) <= nowMin) continue;
      next.push({ ...it, date: d.date });
    }
  }
  const todayLeft = next.filter((it) => it.date === todayISO).length;
  if (family === "accessoryCircular") {
    w.addAccessoryWidgetBackground = true;
    const n = w.addText(String(todayLeft));
    n.font = Font.boldSystemFont(22);
    n.centerAlignText();
    const l = w.addText("TODAY");
    l.font = Font.systemFont(9);
    l.centerAlignText();
    return w;
  }
  if (!next.length) {
    w.addText("Nothing coming up");
    return w;
  }
  const it = next[0];
  const [, m, d] = it.date.split("-").map(Number);
  const when = (it.date === todayISO ? "" : `${WEEKDAY_SHORT[new Date(it.date + "T00:00").getDay()]} ${d}/${m} `) +
    `${formatTime(it.start)} – ${formatTime(it.end)}`;
  if (family === "accessoryInline") {
    w.addText(`${formatTime(it.start)} ${it.title}`);
    return w;
  }
  const t = w.addText(it.title);
  t.font = Font.boldSystemFont(14);
  t.lineLimit = 1;
  const s = w.addText(when + (it.place ? ` · ${it.place}` : ""));
  s.font = Font.systemFont(12);
  s.lineLimit = 1;
  if (next[1]) {
    const n2 = w.addText(`Next: ${next[1].title}`);
    n2.font = Font.systemFont(11);
    n2.lineLimit = 1;
    n2.textOpacity = 0.7;
  }
  return w;
}

const now = new Date();
const { data, offline } = await loadData();
const widget = family.startsWith("accessory") ? buildLockWidget(data, now) : buildDayWidget(data, offline, now);

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentLarge(); // preview when run inside Scriptable
}
Script.complete();

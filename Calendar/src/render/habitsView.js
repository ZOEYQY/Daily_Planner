import { habitsApi } from "../habitsApi.js";
import { icons } from "../icons.js";
import { esc, inkOn } from "../utils.js";
import { parseISODate, toISODate, addDays, MONTH_LABELS } from "../dateUtils.js";
import { openFormPopup, showToast, showConfirm } from "./notify.js";

const COLOR_PRESETS = ["#3a9163", "#2a78d6", "#c08a3e", "#8069ac", "#c23b3b", "#39bee5", "#e0669a", "#0b0b0b"];

// The habit data lives on the server (see habitsApi.js), not the store — so this
// view owns its own little cache + reload cycle instead of reacting to the store.
let cache = null; // { habits, checkins: {id:[iso...]}, today }
let loadError = null;
let inFlight = false;
let ctx = null; // { root, state, actions } from the last render, for self-rerender
let monthOffset = 0; // 0 = current month, -1 = last month … (shared by all cards)

function rerender() {
  if (ctx) renderHabitsView(ctx.root, ctx.state, ctx.actions);
}

async function load() {
  if (inFlight) return;
  inFlight = true;
  loadError = null;
  try {
    cache = await habitsApi.list();
  } catch (e) {
    loadError = e;
  } finally {
    inFlight = false;
    rerender();
  }
}

export function renderHabitsView(root, state, actions) {
  ctx = { root, state, actions };

  if (!cache && !loadError) {
    load();
    root.innerHTML = `<div class="habits-wrap"><div class="habits-empty">Loading…</div></div>`;
    return;
  }

  if (loadError) {
    root.innerHTML = `
      <div class="habits-wrap">
        <div class="habits-notice">
          <p><strong>打卡 needs the server.</strong></p>
          <p>Open the calendar with <code>python app.py</code> (then <code>127.0.0.1:5050/calendar/</code>) — a plain
             file:// or Live Server page can't reach the habit store.</p>
          <button type="button" class="btn btn-secondary" id="habits-retry">Try again</button>
        </div>
      </div>`;
    root.querySelector("#habits-retry").addEventListener("click", () => {
      loadError = null;
      load();
      rerender();
    });
    return;
  }

  const { habits, checkins, today } = cache;

  root.innerHTML = `
    <div class="habits-wrap">
      <div class="habits-head">
        <div class="habits-title">打卡</div>
        <button type="button" class="btn btn-primary" id="habit-add">${icons.plus}<span>New habit</span></button>
      </div>

      ${
        habits.length === 0
          ? `<div class="habits-empty">No habits yet. Add one — “Drink water”, “Sleep by 11”, “Read 20 min”…<br>Tap the big ring each day to keep the streak going.</div>`
          : `
        <div class="habit-monthnav">
          <button type="button" class="habit-mn-btn" id="mn-prev" aria-label="Previous month">${icons.chevronLeft}</button>
          <span class="habit-mn-label">${monthTitle(today, monthOffset)}</span>
          <button type="button" class="habit-mn-btn" id="mn-next" aria-label="Next month" ${monthOffset >= 0 ? "disabled" : ""}>${icons.chevronRight}</button>
        </div>
        <div class="habit-list">${habits.map((h) => habitCard(h, checkins[String(h.id)] || [], today)).join("")}</div>`
      }
    </div>
  `;

  root.querySelector("#habit-add").addEventListener("click", () => openHabitForm(null));
  root.querySelector("#mn-prev")?.addEventListener("click", () => { monthOffset -= 1; rerender(); });
  root.querySelector("#mn-next")?.addEventListener("click", () => { monthOffset = Math.min(0, monthOffset + 1); rerender(); });

  root.querySelectorAll(".habit-card").forEach((card) => {
    const id = Number(card.dataset.id);
    // The big ring is the only thing that writes — tap it to check in / undo
    // TODAY. The month grid is display-only (no back-filling: a streak means you
    // actually showed up).
    card.querySelector(".habit-ring").addEventListener("click", () => toggle(id, cache.today));
    card.querySelector(".habit-edit-btn").addEventListener("click", () => {
      openHabitForm(cache.habits.find((h) => h.id === id));
    });
  });
}

// ---- data ops (optimistic) ----
async function toggle(habitId, date) {
  const key = String(habitId);
  const list = cache.checkins[key] || (cache.checkins[key] = []);
  const i = list.indexOf(date);
  const wasOn = i !== -1;
  if (wasOn) list.splice(i, 1);
  else list.push(date);
  rerender();
  try {
    const { on } = await habitsApi.toggleCheckin(habitId, date);
    // reconcile if the server disagrees (shouldn't, but keep them in sync)
    const cur = cache.checkins[key];
    const has = cur.includes(date);
    if (on && !has) cur.push(date);
    if (!on && has) cur.splice(cur.indexOf(date), 1);
    rerender();
  } catch (e) {
    if (wasOn) list.push(date);
    else list.splice(list.indexOf(date), 1);
    rerender();
    showToast("Couldn't save — is the server running?", { variant: "danger" });
  }
}

function openHabitForm(habit) {
  const editing = !!habit;
  const color = habit?.color || COLOR_PRESETS[0];
  openFormPopup({
    title: editing ? "Edit Habit" : "New Habit",
    submitLabel: editing ? "Save" : "Add",
    bodyHTML: `
      <div class="field"><label>Name</label>
        <input type="text" id="habit-name" placeholder="e.g. Drink water" value="${esc(habit?.name || "")}" /></div>
      <div class="field"><label>Emoji (optional)</label>
        <input type="text" id="habit-emoji" maxlength="4" placeholder="💧" value="${esc(habit?.emoji || "")}" style="max-width:90px;" /></div>
      <div class="field"><label>Colour</label>
        <div class="habit-color-row" id="habit-colors">
          ${COLOR_PRESETS.map(
            (c) => `<button type="button" class="habit-swatch ${c === color ? "is-on" : ""}" data-color="${c}" style="--sw:${c}"></button>`
          ).join("")}
          <input type="color" id="habit-color-custom" value="${esc(color)}" aria-label="Custom colour" />
        </div>
      </div>
      ${editing ? `<button type="button" class="btn btn-danger-ghost" id="habit-delete" style="width:100%;margin-top:2px;">Delete habit</button>` : ""}
    `,
    onMount: (panel) => {
      panel.querySelector("#habit-name").focus();
      let sel = color;
      const custom = panel.querySelector("#habit-color-custom");
      const paint = () => panel.querySelectorAll(".habit-swatch").forEach((s) => s.classList.toggle("is-on", s.dataset.color === sel));
      panel.querySelectorAll(".habit-swatch").forEach((s) => {
        s.addEventListener("click", () => { sel = s.dataset.color; custom.value = sel; paint(); });
      });
      custom.addEventListener("input", () => { sel = custom.value; paint(); });
      panel._getColor = () => sel;
      panel.querySelector("#habit-delete")?.addEventListener("click", async () => {
        const ok = await showConfirm({
          title: "Delete this habit?",
          message: `${esc(habit.name)} — its check-in history goes too.`,
          confirmLabel: "Delete",
          danger: true,
        });
        if (!ok) return;
        cache.habits = cache.habits.filter((h) => h.id !== habit.id);
        delete cache.checkins[String(habit.id)];
        panel.closest(".popup-overlay")?.remove();
        rerender();
        try { await habitsApi.remove(habit.id); } catch (e) { showToast("Delete failed — reload to resync", { variant: "danger" }); }
      });
    },
    onSubmit: async ({ panel, close }) => {
      const name = panel.querySelector("#habit-name").value.trim();
      if (!name) { panel.querySelector("#habit-name").focus(); return; }
      const data = { name, emoji: panel.querySelector("#habit-emoji").value.trim(), color: panel._getColor() };
      close();
      try {
        if (editing) {
          Object.assign(cache.habits.find((h) => h.id === habit.id), data);
          rerender();
          await habitsApi.update(habit.id, data);
        } else {
          const { id } = await habitsApi.create(data);
          cache.habits.push({ id, sort: cache.habits.length, ...data });
          cache.checkins[String(id)] = [];
          rerender();
        }
      } catch (e) {
        showToast("Couldn't save the habit", { variant: "danger" });
        load();
      }
    },
  });
}

// ---- rendering helpers ----
function habitCard(habit, dates, todayISO) {
  const set = new Set(dates);
  const doneToday = set.has(todayISO);
  const streak = currentStreak(set, todayISO);
  const best = bestStreak(dates);
  const ink = inkOn(habit.color);
  const t = parseISODate(todayISO);
  const viewYM = ((y, m) => `${y}-${String(m + 1).padStart(2, "0")}`)(
    new Date(t.getFullYear(), t.getMonth() + monthOffset, 1).getFullYear(),
    new Date(t.getFullYear(), t.getMonth() + monthOffset, 1).getMonth()
  );
  const viewCount = dates.filter((d) => d.slice(0, 7) === viewYM).length;
  const week = thisWeekDots(set, todayISO);

  return `
    <div class="habit-card" data-id="${habit.id}" style="--habit:${habit.color};--habit-ink:${ink}">
      <div class="habit-card-top">
        <span class="habit-emoji">${esc(habit.emoji || "◎")}</span>
        <span class="habit-name">${esc(habit.name)}</span>
        <button type="button" class="habit-edit-btn" aria-label="Edit habit">${icons.settings}</button>
      </div>

      <div class="habit-hero">
        <button type="button" class="habit-ring ${doneToday ? "is-done" : ""}" aria-pressed="${doneToday}" aria-label="Check in for today">
          <span class="habit-ring-label">${doneToday ? icons.check : "打卡"}</span>
        </button>
        <div class="habit-hero-side">
          <div class="habit-streak-big">${streak}${streak > 0 ? " <span>🔥</span>" : ""}</div>
          <div class="habit-streak-cap">${streak === 1 ? "day" : "days"} streak</div>
          <div class="habit-week" title="This week">
            ${week.map((d) => `<span class="hw-dot ${d.on ? "is-on" : ""} ${d.isToday ? "is-today" : ""} ${d.future ? "is-future" : ""}"></span>`).join("")}
          </div>
        </div>
      </div>

      <div class="habit-month">${monthCalHTML(set, todayISO, monthOffset)}</div>

      <div class="habit-stats">
        <span>${MONTH_LABELS[Number(viewYM.slice(5)) - 1].slice(0, 3)} <b>${viewCount}</b></span>
        <span>Best <b>${best}</b></span>
        <span>Total <b>${dates.length}</b></span>
      </div>
    </div>
  `;
}

function monthCalHTML(set, todayISO, offset) {
  const t = parseISODate(todayISO);
  const view = new Date(t.getFullYear(), t.getMonth() + offset, 1);
  const y = view.getFullYear();
  const m = view.getMonth();
  const pad = new Date(y, m, 1).getDay();
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const wd = ["S", "M", "T", "W", "T", "F", "S"].map((w) => `<span class="hc-wd">${w}</span>`).join("");
  let cells = "";
  for (let i = 0; i < pad; i++) cells += `<span class="hc-cell is-blank"></span>`;
  for (let d = 1; d <= daysInMonth; d++) {
    const iso = toISODate(new Date(y, m, d));
    const on = set.has(iso);
    const isToday = iso === todayISO;
    const future = iso > todayISO;
    cells += `<span class="hc-cell ${on ? "is-on" : ""} ${isToday ? "is-today" : ""} ${future ? "is-future" : ""}">${d}</span>`;
  }
  return `<div class="hc-grid">${wd}${cells}</div>`;
}

function thisWeekDots(set, todayISO) {
  const t = parseISODate(todayISO);
  const sunday = addDays(t, -t.getDay());
  return Array.from({ length: 7 }, (_, i) => {
    const d = addDays(sunday, i);
    const iso = toISODate(d);
    return { iso, on: set.has(iso), isToday: iso === todayISO, future: d > t };
  });
}

function monthTitle(todayISO, offset) {
  const t = parseISODate(todayISO);
  const v = new Date(t.getFullYear(), t.getMonth() + offset, 1);
  return `${MONTH_LABELS[v.getMonth()]} ${v.getFullYear()}`;
}

function currentStreak(set, todayISO) {
  let d = parseISODate(todayISO);
  if (!set.has(toISODate(d))) d = addDays(d, -1); // today not yet done — streak still alive from yesterday
  let n = 0;
  while (set.has(toISODate(d))) { n++; d = addDays(d, -1); }
  return n;
}

function bestStreak(dates) {
  const sorted = [...new Set(dates)].sort();
  let best = 0, run = 0, prev = null;
  for (const iso of sorted) {
    run = prev && toISODate(addDays(parseISODate(prev), 1)) === iso ? run + 1 : 1;
    if (run > best) best = run;
    prev = iso;
  }
  return best;
}

// Called by main.js when leaving the view / switching user, so a stale cache
// from another account doesn't flash before the reload.
export function resetHabitsView() {
  cache = null;
  loadError = null;
  monthOffset = 0;
}

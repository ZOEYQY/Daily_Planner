import { getMonthGridDays, parseISODate, toISODate, isSameDay, today, orderedWeekdayLabels } from "../dateUtils.js";
import { eventsOnDate, scheduledTasksOnDate, specialDaysOnDate, resolveOccurrence } from "../selectors.js";
import { isOverdue } from "../rescheduleTracking.js";
import { esc } from "../utils.js";
import { requireAuth } from "../authGate.js";
import { handleOccurrenceClick } from "./recurrenceUI.js";

const MAX_VISIBLE = 3;

export function renderMonthView(root, state, actions, currentUser) {
  const cursor = parseISODate(state.cursorDate);
  const days = getMonthGridDays(cursor, state.weekStartsOn);
  const t = today();
  const todayISO = toISODate(t);

  root.innerHTML = `
    <div class="month-grid">
      <div class="month-weekdays">
        ${orderedWeekdayLabels(state.weekStartsOn).map((w) => `<div>${w}</div>`).join("")}
      </div>
      <div class="month-weeks">
        ${chunk(days, 7)
          .map(
            (week) => `<div class="month-row">${week.map((d) => dayCell(d, cursor, t, state, todayISO)).join("")}</div>`
          )
          .join("")}
      </div>
    </div>
  `;

  root.querySelectorAll(".month-cell").forEach((cell) => {
    cell.addEventListener("click", () => {
      requireAuth(currentUser, actions, () => actions.openModal({ type: "add", date: cell.dataset.date }));
    });
  });

  root.querySelectorAll(".task-chip-checkbox").forEach((box) => {
    box.addEventListener("click", (e) => {
      e.stopPropagation();
      requireAuth(currentUser, actions, () => actions.toggleTaskOccurrence(box.dataset.id, box.dataset.occurrence || null));
    });
  });

  root.querySelectorAll(".event-chip").forEach((chip) => {
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      requireAuth(currentUser, actions, () => {
        const kind = chip.dataset.kind;
        const occurrenceKey = chip.dataset.occurrence || null;
        const source = kind === "task" ? state.tasks : kind === "specialDay" ? state.specialDays || [] : state.events;
        const master = source.find((x) => x.id === chip.dataset.id);
        if (!master) return;
        const dateField = kind === "task" ? "dueDate" : "date";
        const item = occurrenceKey ? resolveOccurrence(master, occurrenceKey, dateField) : master;
        handleOccurrenceClick(item, kind, occurrenceKey, actions);
      });
    });
  });
}

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

function dayCell(date, cursor, t, state, todayISO) {
  const iso = toISODate(date);
  const isOutside = date.getMonth() !== cursor.getMonth();
  const isToday = isSameDay(date, t);
  const dayTasks = scheduledTasksOnDate(state, iso);
  // Month cells only surface one-off events — a class that repeats every week
  // would otherwise stamp the same name into every cell and bury the things
  // that actually make a given day special (a duty, a one-time meeting).
  // resolveOccurrence sets isRecurring on every resolved item (see selectors.js).
  // Tasks and special days still show regardless of repeat.
  const dayEvents = eventsOnDate(state, iso).filter((e) => !e.isRecurring);
  const daySpecialDays = specialDaysOnDate(state, iso);
  const items = [
    ...daySpecialDays.map((x) => ({ ...x, kind: "specialDay" })),
    ...dayTasks.map((x) => ({ ...x, kind: "task" })),
    ...dayEvents.map((x) => ({ ...x, kind: "event" })),
  ];
  const visible = items.slice(0, MAX_VISIBLE);
  const overflow = items.length - visible.length;

  return `
    <div class="month-cell ${isOutside ? "is-outside" : ""} ${isToday ? "is-today" : ""}" data-date="${iso}">
      <div class="cell-daynum">${date.getDate()}</div>
      <div class="cell-events">
        ${visible.map((item) => (item.kind === "task" ? taskChip(item, todayISO) : item.kind === "specialDay" ? specialDayChip(item) : eventChip(item))).join("")}
        ${overflow > 0 ? `<div class="cell-more">+${overflow} more</div>` : ""}
      </div>
    </div>
  `;
}

function eventChip(e) {
  return `
    <div class="event-chip" style="--chip-color:${e.color}" data-id="${e.id}" data-kind="event" data-occurrence="${e.occurrenceDate || ""}">
      <span class="chip-bar"></span>
      <span class="chip-label">${esc(e.title)}</span>
    </div>`;
}

// Deliberately not .event-chip's small pill+accent-bar look — a special day
// (birthday, exam, anniversary) is meant to stand out as its own kind of
// marker, not blend in as "one more event", so it gets a solid full-width
// banner instead.
function specialDayChip(d) {
  // Keeps the .event-chip class purely so the shared click-to-open wiring in
  // renderMonthView (root.querySelectorAll(".event-chip")) still picks it up
  // — .special-day-chip's own CSS overrides all of .event-chip's visual
  // styling to get the solid-banner look instead.
  return `
    <div class="event-chip special-day-chip" style="--chip-color:${d.color}" data-id="${d.id}" data-kind="specialDay" data-occurrence="${d.occurrenceDate || ""}" title="${esc(d.title)}">
      ${esc(d.title)}
    </div>`;
}

function taskChip(t, todayISO) {
  const overdue = isOverdue(t, todayISO);
  const count = t.rescheduleCount || 0;
  const postponedTodo = t.isTodo && count > 0;
  const severity = overdue
    ? "is-overdue"
    : postponedTodo
      ? "is-todo-postponed"
      : count >= 3 ? "is-resched-3" : count === 2 ? "is-resched-2" : count === 1 ? "is-resched-1" : "";
  const prefix = overdue ? "⚠ " : count > 0 ? (count >= 3 || t.overdueReschedule ? "⚠ " : "↻ ") : "";
  return `
    <div class="event-chip task-chip ${t.done ? "is-done" : ""} ${severity}" style="--chip-color:${t.color}" data-id="${t.id}" data-kind="task" data-occurrence="${t.occurrenceDate || ""}">
      <button type="button" class="task-chip-checkbox" data-id="${t.id}" data-occurrence="${t.occurrenceDate || ""}" aria-label="Toggle done"></button>
      <span class="chip-label">${prefix}${esc(t.title)}</span>
    </div>`;
}

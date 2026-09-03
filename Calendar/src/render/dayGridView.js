import {
  parseISODate, toISODate, addDays, startOfWeek, isSameDay, today, WEEKDAY_LABELS,
  formatHourLabel, formatTime, formatFullDate, minutesFromMidnight, minutesToHHMM, daysBetweenISO,
} from "../dateUtils.js";
import {
  eventsOnDate, scheduledTasksOnDate, specialDaysOnDate, getWeekUnscheduledTasks, getWeekUnscheduledEvents,
  getDayUnscheduledTasks, getDayUnscheduledEvents, resolveOccurrence, isRepeating, isTodoUrgent,
} from "../selectors.js";
import { icons } from "../icons.js";
import { esc } from "../utils.js";
import { fieldLabels, FIXED_FIELD_DEFS } from "../extraFields.js";
import { layoutDayEvents, HOUR_ROW_PX, minutesToTop } from "../timeLayout.js";
import { startPointerInteraction, snapMinutes, createAutoScroller } from "../dragUtils.js";
import { isOverdue, computeReschedulePatch } from "../rescheduleTracking.js";
import { handleOccurrenceClick } from "./recurrenceUI.js";
import { showToast, openFormPopup } from "./notify.js";
import { timeInputHTML, wireTimeInput } from "./timeInput.js";

const HOURS = Array.from({ length: 24 }, (_, h) => h);
// Fallback only — the actual starting hour is user-configurable via
// Settings > Calendar > Starts At (state.dayStartHour, see its use below).
const SCROLL_TO_HOUR = 7;
const DAY_MINUTES = 24 * 60;
const MIN_DURATION = 15;

// "Starts At" (Settings > Calendar, state.dayStartHour) reorders the whole
// grid rather than just scrolling to a position — the chosen hour becomes the
// very first row, with the hours before it wrapped around to the bottom (e.g.
// starting at 7 AM puts 7 AM...11 PM on top, then 12 AM...6 AM underneath).
// wrapMinutes/toDisplayMinutes/toActualMinutes translate between a real clock
// time and where it lands in that reordered layout — used for POSITIONS
// (an absolute point in time, e.g. a block's top) — a DURATION/height is a
// span, not a point, and is never offset. orderedHours does the same
// reordering for the hour-label row. A block whose actual time span crosses
// the wrap point (e.g. 6 AM-8 AM when starting at 7 AM) is an inherent limit
// of any wraparound single-day view, not handled specially — it just renders
// from its (possibly discontinuous-looking) start.
function wrapMinutes(min) {
  return ((min % DAY_MINUTES) + DAY_MINUTES) % DAY_MINUTES;
}
function toDisplayMinutes(actualMin, offsetMin) {
  return wrapMinutes(actualMin - offsetMin);
}
function toActualMinutes(displayMin, offsetMin) {
  return wrapMinutes(displayMin + offsetMin);
}
function orderedHours(startHour) {
  return Array.from({ length: 24 }, (_, i) => (startHour + i) % 24);
}
// Default visible count before a tray needs its "Show all" toggle (see
// weekTrayExpanded/expandedDayTrayDates below) — both trays still render
// every item underneath regardless, just visually capped by CSS
// overflow-y:auto (see .week-tray/.day-tray-col) so scrolling within the tray
// reaches anything past this count even without expanding it.
const WEEK_TRAY_VISIBLE = 5;
const DAY_TRAY_VISIBLE = 5;

// Which trays are currently expanded past their default ~5-item height — a
// single flag for the Week tray, a per-date set for the Day tray (each day
// column expands independently). Transient UI state, not persisted: reset on
// reload same as which Add/Edit tab was open, which category panels were
// expanded, etc.
let weekTrayExpanded = false;
let expandedDayTrayDates = new Set();

// Where Ctrl+V pastes — the timeline date/time under the pointer right now, kept
// up to date by a mousemove listener re-attached on every render (see the end of
// renderDayGridView). Module-level so main.js's keydown handler can read it
// without this view needing to expose any of its render-scoped internals.
let hoverTarget = null;

export function getHoverTarget() {
  return hoverTarget;
}

// The dayStartHour this view last scrolled to on its own — lets a Settings
// change to "Starts At" force an immediate rescroll even though the timegrid
// scroll element already exists and would otherwise just keep its current
// scrollTop across the re-render (see prevScrollTop below, which exists so
// unrelated updates like opening a modal don't reset a scroll position you
// set by hand). Starts null so the very first render still falls through to
// state.dayStartHour normally.
let appliedDayStartHour = null;

function commitItemPatch(actions, item, patch) {
  if (item.kind === "task") actions.updateTask(item.id, patch);
  else actions.updateEvent(item.id, patch);
}

// Drags/resizes only ever change a couple of fields (time, date) but
// updateTaskOccurrence/updateEventOccurrence *replaces* the whole exception object —
// so this rebuilds the complete thing from the occurrence's current effective values
// (already resolved onto `item`) plus whatever just changed, instead of only writing
// the changed fields and silently discarding any prior override for this occurrence.
function occurrenceException(item, dateField, canonicalKey, changes = {}) {
  const merged = { ...item, ...changes };
  const exception = {
    title: merged.title,
    categoryId: merged.categoryId,
    colorId: merged.colorId,
    notes: merged.notes,
    startTime: merged.startTime,
    endTime: merged.endTime,
  };
  if (dateField === "dueDate") exception.scheduled = merged.scheduled;
  if (merged[dateField] && merged[dateField] !== canonicalKey) exception.movedTo = merged[dateField];
  return exception;
}

// `kind` is passed explicitly (not read off `item.kind`) because the raw task/event
// records this resolves from never carry a `kind` field themselves — only the
// timeline-rendering step decorates a *copy* with one (see dayColumn/itemBlock).
function commitOccurrencePatch(actions, kind, item, dateField, changes) {
  const exception = occurrenceException(item, dateField, item.occurrenceDate, changes);
  if (kind === "task") actions.updateTaskOccurrence(item.id, item.occurrenceDate, exception);
  else actions.updateEventOccurrence(item.id, item.occurrenceDate, exception);
}

function rescheduleSeverityClass(count) {
  if (count >= 3) return "is-resched-3";
  if (count === 2) return "is-resched-2";
  if (count === 1) return "is-resched-1";
  return "";
}

function rescheduleHistoryText(task) {
  const chain = [...(task.rescheduleHistory || []), { date: task.dueDate, startTime: task.startTime }];
  return chain
    .map((h) => `${formatFullDate(parseISODate(h.date))}${h.startTime ? ` · ${formatTime(h.startTime)}` : ""}`)
    .join("\n↓\n");
}

/** Inline badge markup for a task's reschedule/overdue awareness state — empty string if there's nothing to show. */
function rescheduleBadgeHTML(task, todayISO) {
  const overdue = isOverdue(task, todayISO);
  const count = task.rescheduleCount || 0;
  if (!overdue && count === 0) return "";

  if (overdue) {
    return `<div class="reschedule-badge is-overdue">⚠ Overdue</div>`;
  }

  const original = task.rescheduleHistory?.[0]?.date || task.dueDate;
  const delay = task.dueDate ? daysBetweenISO(original, task.dueDate) : 0;
  const icon = count >= 3 || task.overdueReschedule ? "⚠" : "↻";

  return `
    <div class="reschedule-badge" title="${esc(rescheduleHistoryText(task))}">
      ${icon} Rescheduled ${count}×${delay > 0 ? ` · +${delay} day${delay === 1 ? "" : "s"} delayed` : ""}
    </div>
  `;
}

function quickAddTask(actions, state, dueDate) {
  const created = actions.addTask({
    title: "",
    categoryId: state.categories[0]?.id || "",
    colorId: state.categories[0]?.colors[0]?.id || "",
    dueDate,
    startTime: "",
    endTime: "",
    scheduled: false,
  });
  actions.openModal({ type: "edit", itemType: "task", id: created.id, isDraft: true });
}

// A to-do gets its own small popup instead of the full Add/Edit modal —
// deliberately lighter (title, an optional deadline, an optional Detail
// note; no category/color/repeat) since that's the whole point of it being a
// "completely separate" quick-capture flow. isTodo:true is what
// getDayUnscheduledTasks (selectors.js) checks to roll an incomplete one
// forward onto today's Day tray column instead of leaving it stranded on
// whatever day it was created, the way a plain task stays put until you
// manually reschedule it. The same popup doubles as the edit flow — a to-do
// chip's second click (already selected, see the tray chip onClick below)
// opens this with existingItem set, instead of the full Add/Edit modal.
//
// fixedDate: when opened from a specific Day tray column, that day is
// already known, so only a deadline *time* is asked for. Opened from the
// Week tray, or when editing (existingItem set — the date might need
// changing too), there's no implicit day, so both a deadline date and time
// are asked for — mirroring what was actually requested rather than reusing
// one layout for both.
function openTodoQuickAdd(actions, state, fixedDate, existingItem = null) {
  const requireDeadline = !!state.todoDeadlineRequired;
  const showDetail = state.todoShowDetail !== false;
  const optionalSuffix = requireDeadline ? "" : " (optional)";
  const askDate = !fixedDate || !!existingItem;

  openFormPopup({
    title: existingItem ? "Edit To-Do" : "Add To-Do",
    submitLabel: existingItem ? "Save" : "Add",
    bodyHTML: `
      <div class="field"><label>Title</label><input type="text" id="todo-title" placeholder="e.g. Submit assignment" value="${esc(existingItem?.title || "")}" /></div>
      ${askDate ? `<div class="field"><label>Deadline Date${optionalSuffix}</label><input type="date" id="todo-date" value="${esc(existingItem?.dueDate || "")}" /></div>` : ""}
      <div class="field"><label>Deadline Time${askDate ? " (optional)" : optionalSuffix}</label>${timeInputHTML("todo-time", existingItem?.startTime || "", state)}</div>
      ${showDetail ? `<div class="field"><label>Detail</label><textarea id="todo-detail" rows="3" placeholder="Optional notes">${esc(existingItem?.notes || "")}</textarea></div>` : ""}
    `,
    onMount: (panel) => {
      panel.querySelector("#todo-title").focus();
      wireTimeInput(panel, "todo-time");
    },
    onSubmit: ({ panel, close }) => {
      const titleInput = panel.querySelector("#todo-title");
      const title = titleInput.value.trim();
      if (!title) {
        titleInput.focus();
        showToast("To-do needs a title before it can be added", { variant: "danger" });
        return;
      }
      const dateInput = panel.querySelector("#todo-date");
      const timeInput = panel.querySelector("#todo-time");
      const date = askDate ? dateInput.value || "" : fixedDate;
      const time = timeInput.value || "";
      if (requireDeadline) {
        // Day tray create: the date's already fixed, so the required deadline
        // is the time. Otherwise (Week tray, or editing) the date itself is
        // the required deadline (time stays optional, same as everywhere
        // else a task's date/time works).
        if (!askDate && !time) {
          timeInput.focus();
          showToast("This to-do needs a deadline time", { variant: "danger" });
          return;
        }
        if (askDate && !date) {
          dateInput.focus();
          showToast("This to-do needs a deadline date", { variant: "danger" });
          return;
        }
      }
      const detailInput = panel.querySelector("#todo-detail");
      close();
      const patch = {
        title,
        dueDate: date,
        startTime: date ? time : "",
        endTime: "",
        scheduled: false,
        isTodo: true,
        notes: detailInput ? detailInput.value.trim() : "",
      };
      if (existingItem) {
        actions.updateTask(existingItem.id, patch);
        showToast("To-do updated");
      } else {
        actions.addTask({
          categoryId: state.categories[0]?.id || "",
          colorId: state.categories[0]?.colors[0]?.id || "",
          ...patch,
        });
        showToast("To-do added");
      }
    },
  });
}

export function renderDayGridView(root, state, actions, currentUser) {
  // Guest always has zero categories (its data never persists — see state.js) and
  // can't create anything anyway, so it gets a sign-in prompt instead of the grid.
  // A signed-in user with zero categories still gets the real, interactive grid —
  // creating a task/event opens the Add/Edit modal's inline "+ Add Category" flow
  // right there, so there's no need to force a trip to Settings first.
  if (state.categories.length === 0 && !currentUser) {
    root.innerHTML = `<div class="timegrid-empty-state">Sign in to create tasks and events.</div>`;
    return;
  }

  const cursor = parseISODate(state.cursorDate);
  const t = today();
  const todayISO = toISODate(t);
  const startHour = state.dayStartHour ?? SCROLL_TO_HOUR;

  let days;
  if (state.view === "week") {
    const start = startOfWeek(cursor, state.weekStartsOn);
    days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  } else if (state.view === "day") {
    days = [cursor];
  } else {
    days = [...state.customDates].sort().map(parseISODate);
  }

  const weekItems = [
    ...getWeekUnscheduledTasks(state).map((t) => ({ item: t, kind: "task" })),
    ...getWeekUnscheduledEvents(state).map((e) => ({ item: e, kind: "event" })),
  ];

  // Every store update (including just opening a modal) re-renders this whole view
  // from scratch, which would otherwise snap the scroll position back to 7am each
  // time — jarring if you were, say, looking at 7pm and the "Add" popup that just
  // opened appears to jump the calendar behind it back to the morning. Carry the
  // previous scroll position forward across re-renders; only default to 7am when
  // there's no prior grid to read a position from (first render of this view).
  const prevScrollEl = root.querySelector("#timegrid-scroll");
  const prevScrollTop = prevScrollEl ? prevScrollEl.scrollTop : null;

  root.innerHTML = `
    <div class="timegrid">
      <div class="timegrid-headrow">
        <div class="timegrid-gutter-spacer corner-unhide-cell">
          ${
            state.showWeekTray
              ? `<button type="button" class="corner-unhide-btn ${state.weekTrayCollapsed ? "" : "is-active"}" id="week-tray-toggle-btn" title="${state.weekTrayCollapsed ? "Show" : "Hide"} Week tray">W</button>`
              : ""
          }
          ${
            state.showDayTray
              ? `<button type="button" class="corner-unhide-btn ${state.dayTrayCollapsed ? "" : "is-active"}" id="day-tray-toggle-btn" title="${state.dayTrayCollapsed ? "Show" : "Hide"} Day tray">D</button>`
              : ""
          }
        </div>
        <div class="timegrid-headcols" style="grid-template-columns: repeat(${days.length}, 1fr);">
          ${days
            .map(
              (d) => `
            <div class="timegrid-head-col ${isSameDay(d, t) ? "is-today" : ""}">
              <div class="daygrid-head-weekday">${WEEKDAY_LABELS[d.getDay()]}</div>
              <div class="daygrid-head-num">${d.getDate()}</div>
            </div>`
            )
            .join("")}
        </div>
      </div>

      ${
        state.showWeekTray
          ? `<div class="week-tray ${weekTrayExpanded ? "is-expanded" : ""}" style="${state.weekTrayCollapsed ? "display:none;" : ""}">
        <div class="week-tray-title">Week</div>
        <div class="week-tray-chips">
          ${weekItems.map(({ item, kind }) => weekTrayChip(item, todayISO, kind, state)).join("")}
        </div>
        ${
          weekItems.length > WEEK_TRAY_VISIBLE
            ? `<button type="button" class="tray-show-all-btn" id="week-tray-show-all-btn">${weekTrayExpanded ? "Show less" : `Show all (${weekItems.length})`}</button>`
            : ""
        }
        <button type="button" class="tray-add-btn tray-add-specialday-btn tray-add-btn-floating" id="week-add-specialday-btn" aria-label="Add a special day" title="Add a special day — birthday, exam, anniversary…">🎉</button>
        <button type="button" class="tray-add-btn tray-add-todo-btn tray-add-btn-floating" id="week-add-todo-btn" aria-label="Add a to-do for sometime this week" title="Add a to-do — rolls forward to today until done">${icons.checkSmall}</button>
        <button type="button" class="tray-add-btn tray-add-btn-floating" id="week-add-btn" aria-label="Add a task for sometime this week">${icons.plusSmall}</button>
      </div>`
          : ""
      }

      ${
        state.showDayTray
          ? `<div class="day-tray-row" style="${state.dayTrayCollapsed ? "display:none;" : ""}">
        <div class="timegrid-gutter-spacer day-tray-gutter-label">Day</div>
        <div class="day-tray-cols" style="grid-template-columns: repeat(${days.length}, 1fr);">
          ${days.map((d) => dayTrayCol(d, state, todayISO)).join("")}
        </div>
      </div>`
          : ""
      }

      <div class="timegrid-scroll" id="timegrid-scroll">
        <div class="timegrid-gutter" style="height:${24 * HOUR_ROW_PX}px;">
          ${orderedHours(startHour)
            .map(
              (h, i) =>
                `<div class="timegrid-hour-label ${i === 0 ? "is-first" : ""}" style="top:${i * HOUR_ROW_PX}px;">${h === 0 ? "" : formatHourLabel(h)}</div>`
            )
            .join("")}
        </div>
        <div class="timegrid-body" style="grid-template-columns: repeat(${days.length}, 1fr); height:${24 * HOUR_ROW_PX}px;">
          ${days.map((d) => dayColumn(d, state, todayISO)).join("")}
        </div>
      </div>
    </div>
  `;

  const scrollEl = root.querySelector("#timegrid-scroll");
  const weekTrayEl = root.querySelector(".week-tray");
  // The grid itself is reordered around startHour now (see orderedHours
  // above), so its row is always at the very top by construction — a
  // Settings change to "Starts At" just needs to reset the scroll to 0
  // (the old scroll position pointed at whatever used to be there before the
  // reorder, which is meaningless now). Any other re-render (opening a
  // modal, ticking a checkbox, etc.) leaves your manual scroll alone.
  const startHourChanged = appliedDayStartHour !== null && appliedDayStartHour !== startHour;
  scrollEl.scrollTop = prevScrollTop !== null && !startHourChanged ? prevScrollTop : 0;
  appliedDayStartHour = startHour;

  fitEventHeaderTitles(root);

  // Returns the real clock-time minute under the pointer — contentY/HOUR_ROW_PX
  // is a *display* minute (0 = the grid's very top, i.e. startHour, not
  // midnight), so every caller downstream (drag/resize/create) can keep
  // working in ordinary "minutes since midnight" without knowing the grid is
  // reordered at all — the wraparound is fully resolved right here.
  function minuteFromClientY(clientY) {
    const scrollRect = scrollEl.getBoundingClientRect();
    const contentY = clientY - scrollRect.top + scrollEl.scrollTop;
    const displayMin = (contentY / HOUR_ROW_PX) * 60;
    return toActualMinutes(displayMin, startHour * 60);
  }

  // The reverse of minuteFromClientY, for POSITIONING (never for a duration —
  // see the comment on orderedHours near the top of the file).
  function topForMinute(actualMin) {
    return minutesToTop(toDisplayMinutes(actualMin, startHour * 60));
  }

  function columnsSnapshot() {
    return Array.from(root.querySelectorAll(".timegrid-col")).map((col) => ({
      date: col.dataset.date,
      rect: col.getBoundingClientRect(),
    }));
  }

  function dayTrayColsSnapshot() {
    return Array.from(root.querySelectorAll(".day-tray-col")).map((col) => ({
      date: col.dataset.date,
      rect: col.getBoundingClientRect(),
    }));
  }

  function dateFromClientX(clientX, columns) {
    for (const c of columns) {
      if (clientX >= c.rect.left && clientX < c.rect.right) return c.date;
    }
    return clientX < columns[0].rect.left ? columns[0].date : columns[columns.length - 1].date;
  }

  // Keeps hoverTarget current for Ctrl+V (see getHoverTarget above) — re-attached
  // fresh on every render since root.innerHTML wipes the previous listener along
  // with everything else.
  const timegridBody = root.querySelector(".timegrid-body");
  timegridBody?.addEventListener("mousemove", (e) => {
    const columns = columnsSnapshot();
    if (columns.length === 0) return;
    const date = dateFromClientX(e.clientX, columns);
    let startMin = snapMinutes(minuteFromClientY(e.clientY), 15);
    startMin = Math.max(0, Math.min(DAY_MINUTES - 60, startMin));
    hoverTarget = { date, startMin };
  });
  timegridBody?.addEventListener("mouseleave", () => {
    hoverTarget = null;
  });

  function resolveDropTarget(cx, cy, ctx) {
    const wt = ctx.weekTrayRect;
    if (wt && cy >= wt.top && cy <= wt.bottom && cx >= wt.left && cx <= wt.right) {
      return { zone: "week" };
    }
    for (const c of ctx.dayTrayCols) {
      if (cx >= c.rect.left && cx < c.rect.right && cy >= c.rect.top && cy <= c.rect.bottom) {
        return { zone: "day", date: c.date };
      }
    }
    for (const c of ctx.columns) {
      if (cx >= c.rect.left && cx < c.rect.right) {
        let startMin = snapMinutes(minuteFromClientY(cy), 15);
        startMin = Math.max(0, Math.min(DAY_MINUTES - 60, startMin));
        return { zone: "timeline", date: c.date, startMin };
      }
    }
    return { zone: "none" };
  }

  // commitOccurrencePatch only ever writes an *exception* — for a repeating item
  // that's correct (exceptions are how one occurrence diverges from its pattern),
  // but for a non-repeating item it's a silent no-op: occurrencesOnDate's
  // non-repeating branch never looks at `exceptions`/`movedTo` at all, it only
  // ever checks the master's own date field. So the gate here has to be
  // isRepeating(item), never occurrenceKey/item.occurrenceDate truthiness —
  // resolveOccurrence sets occurrenceDate on every resolved item, repeating or
  // not, so that would always take the exception branch and silently do nothing
  // for the common (non-repeating) case. This was the bug behind "dragging a Day
  // tray task/event onto the calendar does nothing."
  function applyTaskDrop(task, target) {
    if (target.zone === "none") return;
    const repeating = isRepeating(task);

    if (target.zone === "week") {
      if (repeating) {
        showToast("Recurring tasks can't be fully unscheduled — delete this occurrence instead", { variant: "danger" });
        return;
      }
      actions.updateTask(task.id, { dueDate: "", startTime: "", endTime: "", scheduled: false });
      return;
    }

    if (target.zone === "day") {
      if (repeating) commitOccurrencePatch(actions, "task", task, "dueDate", { dueDate: target.date, startTime: "", endTime: "", scheduled: false });
      else actions.updateTask(task.id, computeReschedulePatch(task, target.date, "", "", { scheduled: false }));
      return;
    }

    const startTime = minutesToHHMM(target.startMin);
    const endTime = minutesToHHMM(target.startMin + 60);
    if (repeating) commitOccurrencePatch(actions, "task", task, "dueDate", { dueDate: target.date, startTime, endTime, scheduled: true });
    else actions.updateTask(task.id, computeReschedulePatch(task, target.date, startTime, endTime, { scheduled: true }));
  }

  // Dragging an unscheduled event chip (Week or Day tray) somewhere else — mirrors
  // applyTaskDrop exactly, minus the reschedule-tracking fields events don't have.
  // Gated on isRepeating(), not occurrence-key truthiness — see the comment above
  // applyTaskDrop for why.
  function applyEventDrop(event, target) {
    if (target.zone === "none") return;
    const repeating = isRepeating(event);

    if (target.zone === "week") {
      if (repeating) {
        showToast("Recurring events can't be fully unscheduled — delete this occurrence instead", { variant: "danger" });
        return;
      }
      actions.updateEvent(event.id, { date: "", startTime: "", endTime: "" });
      return;
    }

    if (target.zone === "day") {
      if (repeating) commitOccurrencePatch(actions, "event", event, "date", { date: target.date, startTime: "", endTime: "" });
      else actions.updateEvent(event.id, { date: target.date, startTime: "", endTime: "" });
      return;
    }

    const startTime = minutesToHHMM(target.startMin);
    const endTime = minutesToHHMM(target.startMin + 60);
    if (repeating) commitOccurrencePatch(actions, "event", event, "date", { date: target.date, startTime, endTime });
    else actions.updateEvent(event.id, { date: target.date, startTime, endTime });
  }

  function createFromRange(date, startMin, endMin) {
    actions.setSelectedItem(null);
    const type = state.pendingCreate?.type || "event";
    const category = state.categories[0];
    const categoryId = category?.id || "";
    const colorId = category?.colors[0]?.id || "";
    const startTime = minutesToHHMM(startMin);
    const endTime = minutesToHHMM(Math.max(endMin, startMin + MIN_DURATION));
    actions.setPendingCreate(null);
    if (type === "task") {
      const created = actions.addTask({
        title: "",
        categoryId,
        colorId,
        dueDate: date,
        startTime,
        endTime,
        scheduled: true,
      });
      actions.openModal({ type: "edit", itemType: "task", id: created.id, isDraft: true });
    } else {
      const created = actions.addEvent({ title: "", categoryId, colorId, date, startTime, endTime });
      actions.openModal({ type: "edit", itemType: "event", id: created.id, isDraft: true });
    }
  }

  function updateCreatePreview(ctx, clientY) {
    if (!ctx.previewEl) return;
    const curMin = snapMinutes(minuteFromClientY(clientY), 15);
    const lo = Math.max(0, Math.min(ctx.startMin, curMin));
    const hi = Math.min(DAY_MINUTES, Math.max(ctx.startMin, curMin, lo + 15));
    ctx.lo = lo;
    ctx.hi = hi;
    ctx.previewEl.style.top = `${topForMinute(lo)}px`;
    ctx.previewEl.style.height = `${minutesToTop(hi - lo)}px`;
  }

  // ---- click / drag-to-create on an empty hour cell ----
  root.querySelectorAll(".timegrid-hourcell").forEach((cell) => {
    cell.addEventListener("pointerdown", (e) => {
      startPointerInteraction(e, {
        onStart: () => {
          const col = cell.closest(".timegrid-col");
          const startMin = snapMinutes(minuteFromClientY(e.clientY), 15);
          const ctx = { date: col.dataset.date, col, startMin, previewEl: null, lo: startMin, hi: startMin + 60 };
          ctx.autoScroll = createAutoScroller(scrollEl, (clientY) => updateCreatePreview(ctx, clientY));
          return ctx;
        },
        onMove: (ev, { ctx }) => {
          if (!ctx.previewEl) {
            const el = document.createElement("div");
            el.className = "timegrid-drag-preview";
            ctx.col.appendChild(el);
            ctx.previewEl = el;
          }
          updateCreatePreview(ctx, ev.clientY);
          ctx.autoScroll.update(ev.clientY);
        },
        onEnd: (ev, { ctx }) => {
          ctx.autoScroll.stop();
          if (ctx.previewEl) ctx.previewEl.remove();
          createFromRange(ctx.date, ctx.lo, ctx.hi);
        },
        // Same select-first-then-act pattern as an existing card: the first click
        // just marks this slot (a dashed preview box, see dayColumn/pendingSlot);
        // clicking the *same* slot again is what actually creates something
        // there. Clicking a different slot (or a card) just moves the mark.
        onClick: (ev, { ctx }) => {
          ctx.autoScroll.stop();
          const pending = state.selectedItem;
          const isThisSlot = pending?.kind === "slot" && pending.date === ctx.date && pending.startMin === ctx.startMin;
          if (isThisSlot) {
            actions.setSelectedItem(null);
            createFromRange(ctx.date, ctx.startMin, ctx.startMin + 60);
          } else {
            actions.setSelectedItem({ kind: "slot", date: ctx.date, startMin: ctx.startMin });
          }
        },
      });
    });
  });

  // ---- drag an existing event/task block to reschedule it, or resize its edges ----
  root.querySelectorAll(".timegrid-event, .timegrid-task-block").forEach((card) => {
    const kind = card.dataset.kind;
    const id = card.dataset.id;
    const occurrenceKey = card.dataset.occurrence || null;
    const dateField = kind === "task" ? "dueDate" : "date";
    const findItem = () => {
      const master = (kind === "task" ? state.tasks : state.events).find((x) => x.id === id);
      if (!master) return null;
      return occurrenceKey ? resolveOccurrence(master, occurrenceKey, dateField) : master;
    };

    card.querySelectorAll(".resize-handle").forEach((handle) => {
      const edge = handle.dataset.edge;

      // dy is a viewport-px delta from drag start and never accounts for
      // scrolling on its own — add how far the container itself has scrolled
      // since drag start so a held-still pointer during auto-scroll still
      // keeps extending the resize (see createAutoScroller's tick callback).
      function applyResize(ctx, dy) {
        const effectiveDy = dy + (scrollEl.scrollTop - ctx.startScrollTop);
        const deltaMin = (effectiveDy / HOUR_ROW_PX) * 60;
        if (edge === "top") {
          let newStart = snapMinutes(ctx.startMin + deltaMin, 15);
          newStart = Math.max(0, Math.min(ctx.endMin - MIN_DURATION, newStart));
          card.style.top = `${topForMinute(newStart)}px`;
          card.style.height = `${Math.max(minutesToTop(ctx.endMin - newStart), 20)}px`;
          ctx.liveStart = newStart;
        } else {
          let newEnd = snapMinutes(ctx.endMin + deltaMin, 15);
          newEnd = Math.min(DAY_MINUTES, Math.max(ctx.startMin + MIN_DURATION, newEnd));
          card.style.height = `${Math.max(minutesToTop(newEnd - ctx.startMin), 20)}px`;
          ctx.liveEnd = newEnd;
        }
      }

      handle.addEventListener("pointerdown", (e) => {
        e.stopPropagation();
        startPointerInteraction(e, {
          onStart: () => {
            const item = findItem();
            if (!item) return null;
            const ctx = {
              item,
              startMin: minutesFromMidnight(item.startTime),
              endMin: minutesFromMidnight(item.endTime),
              startScrollTop: scrollEl.scrollTop,
              lastDy: 0,
            };
            ctx.autoScroll = createAutoScroller(scrollEl, () => applyResize(ctx, ctx.lastDy));
            return ctx;
          },
          onMove: (ev, { dy, ctx }) => {
            if (!ctx) return;
            ctx.lastDy = dy;
            applyResize(ctx, dy);
            ctx.autoScroll.update(ev.clientY);
          },
          onEnd: (ev, { ctx }) => {
            if (!ctx) return;
            ctx.autoScroll.stop();
            const newStart = edge === "top" ? ctx.liveStart ?? ctx.startMin : ctx.startMin;
            const newEnd = edge === "bottom" ? ctx.liveEnd ?? ctx.endMin : ctx.endMin;
            if (newStart === ctx.startMin && newEnd === ctx.endMin) return;
            const timePatch = { startTime: minutesToHHMM(newStart), endTime: minutesToHHMM(newEnd) };
            if (occurrenceKey) commitOccurrencePatch(actions, kind, ctx.item, dateField, timePatch);
            else commitItemPatch(actions, { kind, id }, timePatch);
          },
        });
      });
    });

    card.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".resize-handle") || e.target.closest(".timegrid-task-checkbox") || e.target.closest(".timegrid-event-info")) return;
      startPointerInteraction(e, {
        onStart: () => {
          const item = findItem();
          if (!item) return null;
          const duration = minutesFromMidnight(item.endTime) - minutesFromMidnight(item.startTime);
          const ctx = {
            item,
            duration,
            origRect: card.getBoundingClientRect(),
            columns: columnsSnapshot(),
            dayTrayCols: dayTrayColsSnapshot(),
            weekTrayRect: weekTrayEl ? weekTrayEl.getBoundingClientRect() : null,
            picked: false,
          };
          ctx.autoScroll = createAutoScroller(scrollEl);
          return ctx;
        },
        onMove: (ev, { dx, dy, ctx }) => {
          if (!ctx) return;
          if (!ctx.picked) {
            ctx.picked = true;
            card.classList.add("is-dragging");
            card.style.position = "fixed";
            card.style.left = `${ctx.origRect.left}px`;
            card.style.top = `${ctx.origRect.top}px`;
            card.style.width = `${ctx.origRect.width}px`;
            card.style.height = `${ctx.origRect.height}px`;
            card.style.zIndex = "200";
            document.body.appendChild(card);
          }
          card.style.left = `${ctx.origRect.left + dx}px`;
          card.style.top = `${ctx.origRect.top + dy}px`;
          ctx.autoScroll.update(ev.clientY);
        },
        onEnd: (ev, { ctx }) => {
          ctx?.autoScroll.stop();
          if (!ctx || !ctx.picked) return;
          const finalRect = card.getBoundingClientRect();
          const cx = finalRect.left + finalRect.width / 2;
          const cy = finalRect.top + finalRect.height / 2;

          // A scheduled task or event dragged back onto the Week tray loses its date
          // AND time (fully unscheduled). Dragged onto the Day tray instead, it keeps
          // that day's date but loses its time — events have no rescheduleCount/
          // overdue tracking the way tasks do, so they skip computeReschedulePatch
          // and just set the fields directly. Only the Week zone rejects a recurring
          // occurrence (isRepeating(), not occurrenceKey — occurrenceKey is set for
          // every timeline item regardless of whether it actually repeats): it would
          // have no date left to be found by again. The Day zone allows one, same as
          // any other date move, via commitOccurrencePatch.
          if (kind === "task" || kind === "event") {
            const wt = ctx.weekTrayRect;
            const onWeekTray = wt && cy >= wt.top && cy <= wt.bottom && cx >= wt.left && cx <= wt.right;
            const dayCol = ctx.dayTrayCols.find((c) => cx >= c.rect.left && cx < c.rect.right && cy >= c.rect.top && cy <= c.rect.bottom);

            if (onWeekTray) {
              card.remove();
              if (isRepeating(ctx.item)) {
                showToast(`Recurring ${kind === "task" ? "tasks" : "events"} can't be fully unscheduled — delete this occurrence instead`, { variant: "danger" });
              } else if (kind === "task") {
                actions.updateTask(id, { dueDate: "", startTime: "", endTime: "", scheduled: false });
              } else {
                actions.updateEvent(id, { date: "", startTime: "", endTime: "" });
              }
              return;
            }
            if (dayCol) {
              card.remove();
              // isRepeating(ctx.item), not occurrenceKey — occurrenceKey is set for
              // every timeline item regardless of whether it repeats, and
              // commitOccurrencePatch is a silent no-op for a non-repeating one
              // (see the comment above applyTaskDrop).
              const repeating = isRepeating(ctx.item);
              if (kind === "event") {
                if (repeating) commitOccurrencePatch(actions, kind, ctx.item, "date", { date: dayCol.date, startTime: "", endTime: "" });
                else actions.updateEvent(id, { date: dayCol.date, startTime: "", endTime: "" });
              } else if (repeating) {
                commitOccurrencePatch(actions, kind, ctx.item, "dueDate", { dueDate: dayCol.date, startTime: "", endTime: "", scheduled: false });
              } else {
                actions.updateTask(id, computeReschedulePatch(ctx.item, dayCol.date, "", "", { scheduled: false }));
              }
              return;
            }
          }

          const date = dateFromClientX(cx, ctx.columns);
          let startMin = snapMinutes(minuteFromClientY(finalRect.top), 15);
          startMin = Math.max(0, Math.min(DAY_MINUTES - ctx.duration, startMin));
          card.remove();
          const startTime = minutesToHHMM(startMin);
          const endTime = minutesToHHMM(startMin + ctx.duration);
          // Same isRepeating() vs occurrenceKey distinction as above.
          const repeating = isRepeating(ctx.item);

          if (kind === "event") {
            if (repeating) commitOccurrencePatch(actions, kind, ctx.item, "date", { date, startTime, endTime });
            else actions.updateEvent(id, { date, startTime, endTime });
          } else if (repeating) {
            commitOccurrencePatch(actions, kind, ctx.item, "dueDate", { dueDate: date, startTime, endTime, scheduled: true });
          } else {
            actions.updateTask(id, computeReschedulePatch(ctx.item, date, startTime, endTime, { scheduled: true }));
          }
        },
        onClick: () => {
          const item = findItem();
          if (!item) return;
          const already = isSelected(state, kind, id, occurrenceKey);
          if (already) {
            actions.setSelectedItem(null);
            handleOccurrenceClick(item, kind, occurrenceKey, actions);
          } else {
            actions.setSelectedItem({ kind, id, occurrenceDate: occurrenceKey || null });
          }
        },
      });
    });
  });

  root.querySelectorAll(".timegrid-task-checkbox").forEach((box) => {
    box.addEventListener("click", (e) => {
      e.stopPropagation();
      actions.toggleTaskOccurrence(box.dataset.id, box.dataset.occurrence || null);
    });
  });

  // ---- Timed to-do blocks: check off in place, or click through to the parent card ----
  root.querySelectorAll(".timegrid-todo-block").forEach((block) => {
    const checkbox = block.querySelector(".timegrid-todo-checkbox");
    checkbox.addEventListener("click", (e) => {
      e.stopPropagation();
      actions.toggleTodoItemDone(block.dataset.parentType, block.dataset.parentId, block.dataset.todoId);
    });
    block.addEventListener("click", (e) => {
      if (e.target === checkbox) return;
      const parentType = block.dataset.parentType;
      const master = (parentType === "task" ? state.tasks : state.events).find((x) => x.id === block.dataset.parentId);
      if (!master) return;
      const occurrenceKey = block.dataset.parentOccurrence || null;
      const dateField = parentType === "task" ? "dueDate" : "date";
      const item = occurrenceKey ? resolveOccurrence(master, occurrenceKey, dateField) : master;
      handleOccurrenceClick(item, parentType, occurrenceKey, actions);
    });
  });

  // ---- Special Day tray chips: click-to-edit only, no drag (no time to reschedule to) ----
  root.querySelectorAll(".special-day-tray-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const master = (state.specialDays || []).find((x) => x.id === chip.dataset.id);
      if (!master) return;
      const occurrenceKey = chip.dataset.occurrence || null;
      const item = occurrenceKey ? resolveOccurrence(master, occurrenceKey, "date") : master;
      handleOccurrenceClick(item, "specialDay", occurrenceKey, actions);
    });
  });

  // ---- Week / Day tray chips: click to edit, drag to reschedule between week / day / timeline ----
  root.querySelectorAll(".unscheduled-chip, .day-tray-chip:not(.special-day-tray-chip)").forEach((chip) => {
    const kind = chip.dataset.kind || "task";
    const dateField = kind === "task" ? "dueDate" : "date";
    chip.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".timegrid-task-checkbox")) return;
      startPointerInteraction(e, {
        onStart: () => {
          const master = (kind === "task" ? state.tasks : state.events).find((x) => x.id === chip.dataset.id);
          if (!master) return null;
          const occurrenceKey = chip.dataset.occurrence || null;
          const item = occurrenceKey ? resolveOccurrence(master, occurrenceKey, dateField) : master;
          const ctx = {
            item,
            origRect: chip.getBoundingClientRect(),
            columns: columnsSnapshot(),
            dayTrayCols: dayTrayColsSnapshot(),
            weekTrayRect: weekTrayEl ? weekTrayEl.getBoundingClientRect() : null,
            picked: false,
          };
          ctx.autoScroll = createAutoScroller(scrollEl);
          return ctx;
        },
        onMove: (ev, { dx, dy, ctx }) => {
          if (!ctx) return;
          if (!ctx.picked) {
            ctx.picked = true;
            chip.classList.add("is-dragging");
            chip.style.position = "fixed";
            chip.style.left = `${ctx.origRect.left}px`;
            chip.style.top = `${ctx.origRect.top}px`;
            chip.style.width = `${ctx.origRect.width}px`;
            chip.style.zIndex = "200";
            document.body.appendChild(chip);
          }
          chip.style.left = `${ctx.origRect.left + dx}px`;
          chip.style.top = `${ctx.origRect.top + dy}px`;
          ctx.autoScroll.update(ev.clientY);
        },
        onEnd: (ev, { ctx }) => {
          ctx?.autoScroll.stop();
          if (!ctx || !ctx.picked) return;
          const finalRect = chip.getBoundingClientRect();
          const cx = finalRect.left + finalRect.width / 2;
          const cy = finalRect.top + finalRect.height / 2;
          chip.remove();
          const target = resolveDropTarget(cx, cy, ctx);
          if (kind === "task") applyTaskDrop(ctx.item, target);
          else applyEventDrop(ctx.item, target);
        },
        onClick: (ev, { ctx }) => {
          if (!ctx) return;
          // A to-do gets the same click-to-select-first pattern as a timeline
          // card (see itemBlock's onClick) instead of opening straight away —
          // first click reveals its deadline/detail in place (see
          // todoDetailHTML/dayTrayChip's is-selected), a second click (now
          // already selected) opens it for editing via its own lightweight
          // popup rather than the full Add/Edit modal. A plain task/event
          // chip is unaffected — still opens directly on a single click.
          if (ctx.item.isTodo) {
            const already = isSelected(state, kind, ctx.item.id, ctx.item.occurrenceDate || null);
            if (already) {
              actions.setSelectedItem(null);
              openTodoQuickAdd(actions, state, null, ctx.item);
            } else {
              actions.setSelectedItem({ kind, id: ctx.item.id, occurrenceDate: ctx.item.occurrenceDate || null });
            }
            return;
          }
          handleOccurrenceClick(ctx.item, kind, ctx.item.occurrenceDate || null, actions);
        },
      });
    });
  });

  root.querySelector("#week-add-btn")?.addEventListener("click", () => quickAddTask(actions, state, ""));
  root.querySelectorAll(".day-tray-add-btn").forEach((btn) => {
    btn.addEventListener("click", () => quickAddTask(actions, state, btn.dataset.date));
  });
  // Covers both the Week tray's single button (no data-date, so fixedDate
  // comes through undefined/falsy — the "ask for both date and time" case in
  // openTodoQuickAdd) and each Day tray column's own button (data-date set).
  root.querySelectorAll(".tray-add-todo-btn").forEach((btn) => {
    btn.addEventListener("click", () => openTodoQuickAdd(actions, state, btn.dataset.date));
  });
  // Opens the existing Special Day step of the Add chooser (addChooserModal.js)
  // — a birthday/exam/anniversary marker, deliberately a different shape (no
  // checkbox, no deadline, always date-based) and look (🎉, see the
  // .special-day-tray-chip it creates) from a task or to-do, so it doesn't
  // get mistaken for either. Day tray's button carries that day's date;
  // Week tray's (no data-date) falls back to today inside the modal itself.
  root.querySelectorAll(".tray-add-specialday-btn").forEach((btn) => {
    btn.addEventListener("click", () => actions.openModal({ type: "add-chooser", step: "specialDay", date: btn.dataset.date || undefined }));
  });

  // Toggled by direct DOM manipulation (class + label text) rather than a
  // full re-render — weekTrayExpanded/expandedDayTrayDates are transient
  // module state the store doesn't know about, so there's nothing to trigger
  // one; same pattern as fieldsChecklistOpen in addModal.js.
  root.querySelector("#week-tray-show-all-btn")?.addEventListener("click", (e) => {
    weekTrayExpanded = !weekTrayExpanded;
    root.querySelector(".week-tray")?.classList.toggle("is-expanded", weekTrayExpanded);
    e.currentTarget.textContent = weekTrayExpanded ? "Show less" : `Show all (${weekItems.length})`;
  });
  root.querySelectorAll(".day-tray-show-all-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const iso = btn.dataset.date;
      const expanded = expandedDayTrayDates.has(iso);
      if (expanded) expandedDayTrayDates.delete(iso);
      else expandedDayTrayDates.add(iso);
      btn.closest(".day-tray-col")?.classList.toggle("is-expanded", !expanded);
      e.currentTarget.textContent = expanded ? `Show all (${btn.dataset.total})` : "Show less";
    });
  });

  // Only rendered at all while the feature is on in Settings (see
  // corner-unhide-cell above). Persisted (state.weekTrayCollapsed/
  // dayTrayCollapsed), so hiding a tray this way survives a reload — distinct
  // from showWeekTray/showDayTray, the master on/off switch for the feature.
  root.querySelector("#week-tray-toggle-btn")?.addEventListener("click", () => actions.setWeekTrayCollapsed(!state.weekTrayCollapsed));
  root.querySelector("#day-tray-toggle-btn")?.addEventListener("click", () => actions.setDayTrayCollapsed(!state.dayTrayCollapsed));
}

// Flattens every timed to-do item (item.todoList entries carrying their own .time)
// off this day's tasks/events into small standalone blocks for the hourly grid —
// untimed items only ever show inside the parent card's plain checklist.
function todoBlocksForDay(events, tasks) {
  const blocks = [];
  const collect = (items, parentType) => {
    items.forEach((item) => {
      (item.todoList || []).forEach((t) => {
        if (!t.time) return;
        blocks.push({
          kind: "todoItem",
          parentType,
          parentId: item.id,
          parentOccurrence: item.occurrenceDate || "",
          todoId: t.id,
          title: t.text,
          done: t.done,
          startTime: t.time,
          endTime: minutesToHHMM(minutesFromMidnight(t.time) + 15),
        });
      });
    });
  };
  collect(events, "event");
  collect(tasks, "task");
  return blocks;
}

function dayColumn(date, state, todayISO) {
  const iso = toISODate(date);
  const events = eventsOnDate(state, iso).map((e) => ({ ...e, kind: "event" }));
  const tasks = scheduledTasksOnDate(state, iso).map((t) => ({ ...t, kind: "task" }));
  const todoBlocks = todoBlocksForDay(events, tasks);
  const placed = layoutDayEvents([...events, ...tasks, ...todoBlocks]);

  // First click on an empty hour cell marks it rather than creating right away
  // (see the onClick handler above) — this dashed box is that mark; clicking
  // the same spot again is what actually creates something.
  const sel = state.selectedItem;
  const pendingSlot = sel?.kind === "slot" && sel.date === iso ? sel : null;
  const offsetMin = (state.dayStartHour ?? SCROLL_TO_HOUR) * 60;

  return `
    <div class="timegrid-col" data-date="${iso}">
      ${HOURS.map((h) => `<div class="timegrid-hourcell" style="top:${h * HOUR_ROW_PX}px; height:${HOUR_ROW_PX}px;"></div>`).join("")}
      ${placed.map((p) => itemBlock(p, todayISO, state)).join("")}
      ${
        pendingSlot
          ? `<div class="timegrid-pending-slot" style="top:${minutesToTop(toDisplayMinutes(pendingSlot.startMin, offsetMin))}px; height:${minutesToTop(60)}px;">${icons.plusSmall}<span>Click again to add</span></div>`
          : ""
      }
    </div>
  `;
}

function dayTrayCol(date, state, todayISO) {
  const iso = toISODate(date);
  const items = [
    ...getDayUnscheduledTasks(state, iso).map((t) => ({ item: t, kind: "task" })),
    ...getDayUnscheduledEvents(state, iso).map((e) => ({ item: e, kind: "event" })),
  ];
  const specialDays = specialDaysOnDate(state, iso);
  const total = specialDays.length + items.length;
  const expanded = expandedDayTrayDates.has(iso);

  return `
    <div class="day-tray-col ${expanded ? "is-expanded" : ""}" data-date="${iso}">
      ${specialDays.map((d) => specialDayTrayChip(d)).join("")}
      ${items.map(({ item, kind }) => dayTrayChip(item, todayISO, kind, state)).join("")}
      ${
        total > DAY_TRAY_VISIBLE
          ? `<button type="button" class="tray-show-all-btn day-tray-show-all-btn" data-date="${iso}" data-total="${total}">${expanded ? "Show less" : `Show all (${total})`}</button>`
          : ""
      }
      <button type="button" class="tray-add-btn tray-add-specialday-btn tray-add-btn-floating" data-date="${iso}" aria-label="Add a special day" title="Add a special day — birthday, exam, anniversary…">🎉</button>
      <button type="button" class="tray-add-btn tray-add-todo-btn tray-add-btn-floating" data-date="${iso}" aria-label="Add a to-do for this day" title="Add a to-do — rolls forward to today until done">${icons.checkSmall}</button>
      <button type="button" class="tray-add-btn day-tray-add-btn tray-add-btn-floating" data-date="${iso}" aria-label="Add a task for this day">${icons.plusSmall}</button>
    </div>
  `;
}

// All-day markers (birthdays, holidays, ...) live in the day tray — the app's
// existing "no specific time" strip — rather than the hourly grid. No drag
// support (there's no time to reschedule to); click opens the edit step.
// .special-day-tray-chip gives it the same solid-fill look as its month-view
// counterpart (specialDayChip in monthView.js) rather than the same muted
// pill a task/to-do chip gets — it kept getting mistaken for one of those
// with only a small 🎉 prefix as the difference.
function specialDayTrayChip(d) {
  return `
    <div class="day-tray-chip special-day-tray-chip" style="--chip-color:${d.color}" data-id="${d.id}" data-occurrence="${d.occurrenceDate || ""}" title="${esc(d.title)}">
      <span class="unscheduled-chip-label">${esc(d.title)}</span>
    </div>
  `;
}

// Tasks get their done-checkbox and reschedule badge; events have neither (no
// completion state, no reschedule tracking), so those are gated on kind here
// rather than needing a whole separate chip renderer.
function weekTrayChip(item, todayISO, kind = "task", state) {
  const isTask = kind === "task";
  const severity = isTask ? rescheduleSeverityClass(item.rescheduleCount || 0) : "";
  const prefix = isTask && item.rescheduleCount ? (item.rescheduleCount >= 3 || item.overdueReschedule ? "⚠ " : "↻ ") : "";
  const dragHint = isTask ? "Drag onto a day or the timeline" : "Drag onto the timeline to schedule";
  // Same click-to-select/click-again-to-open pattern as dayTrayChip — only
  // for to-dos, see the reasoning there.
  const selected = item.isTodo && isSelected(state, kind, item.id, item.occurrenceDate || null);
  return `
    <div class="unscheduled-chip ${isTask && item.done ? "is-done" : ""} ${severity} ${selected ? "is-selected" : ""}" style="--chip-color:${item.color}" data-id="${item.id}" data-kind="${kind}" data-occurrence="${item.occurrenceDate || ""}" title="${esc(item.title)} · ${dragHint}${isTask && item.rescheduleCount ? `\n${esc(rescheduleHistoryText(item))}` : ""}">
      ${isTask ? `<button type="button" class="timegrid-task-checkbox" data-id="${item.id}" data-occurrence="${item.occurrenceDate || ""}" aria-label="Toggle done"></button>` : ""}
      <div class="day-tray-chip-body">
        <span class="unscheduled-chip-label">${prefix}${esc(item.title)}</span>
        ${selected ? todoDetailHTML(item) : ""}
      </div>
    </div>
  `;
}

// A to-do's deadline time and Detail note aren't shown anywhere else in the
// tray view, unlike a scheduled task's time on the timeline — surface them
// once the chip is selected (click once), rather than always-on, so a plain
// unselected to-do still just shows its title like any other tray chip (see
// dayTrayChip/weekTrayChip's default "just show title" look). Nothing to
// show (no deadline, no detail) renders nothing, same as extraInfoHTML.
function todoDetailHTML(item) {
  const rows = [];
  if (item.startTime) rows.push(`<div class="todo-chip-detail-row"><span class="todo-chip-detail-label">Deadline</span> ${esc(formatTime(item.startTime))}</div>`);
  if (item.notes) rows.push(`<div class="todo-chip-detail-row"><span class="todo-chip-detail-label">Detail</span> ${esc(item.notes)}</div>`);
  if (rows.length === 0) return "";
  return `<div class="todo-chip-detail">${rows.join("")}</div>`;
}

// Overdue/reschedule tracking, done-checkbox — all task-only concepts (events
// have no dueDate/done fields to check), so those are gated on kind here rather
// than needing a whole separate chip renderer, same approach as weekTrayChip.
function dayTrayChip(item, todayISO, kind = "task", state) {
  const isTask = kind === "task";
  const overdue = isTask && isOverdue(item, todayISO);
  const urgent = isTask && !overdue && isTodoUrgent(item, state?.todoUrgentThresholdHours ?? 24, todayISO);
  const severity = isTask ? rescheduleSeverityClass(item.rescheduleCount || 0) : "";
  const prefix = overdue ? "⚠ " : isTask && item.rescheduleCount ? (item.rescheduleCount >= 3 || item.overdueReschedule ? "⚠ " : "↻ ") : "";
  const historyLine = isTask && (overdue || item.rescheduleCount) ? `\n${esc(rescheduleHistoryText(item))}` : "";
  // Only a to-do supports the click-to-select/click-again-to-open pattern (see
  // this chip's onClick wiring below) — a plain task/event chip still opens
  // directly on a single click, same as always.
  const selected = item.isTodo && isSelected(state, kind, item.id, item.occurrenceDate || null);
  return `
    <div class="day-tray-chip ${isTask && item.done ? "is-done" : ""} ${overdue ? "is-overdue" : urgent ? "is-todo-urgent" : severity} ${selected ? "is-selected" : ""}" style="--chip-color:${item.color}" data-id="${item.id}" data-kind="${kind}" data-occurrence="${item.occurrenceDate || ""}" title="${esc(item.title)} · Drag onto the timeline to set a time${historyLine}">
      ${isTask ? `<button type="button" class="timegrid-task-checkbox" data-id="${item.id}" data-occurrence="${item.occurrenceDate || ""}" aria-label="Toggle done"></button>` : ""}
      <div class="day-tray-chip-body">
        <span class="unscheduled-chip-label">${prefix}${esc(item.title)}</span>
        ${selected ? todoDetailHTML(item) : ""}
      </div>
    </div>
  `;
}

// Shows whatever Extra Fields info an item actually has filled in, right on its
// timeline block, instead of that info only ever being visible after opening the
// Edit modal. The block's own height is fixed by its start/end time, so this
// section scrolls independently (see .timegrid-event-info in calendar.css)
// rather than being clipped when there's more info than fits.
function extraInfoHTML(item, state) {
  const labels = fieldLabels(state);
  const rows = [];
  FIXED_FIELD_DEFS.forEach((f) => {
    if (item[f.key]) rows.push({ label: f.label, value: item[f.key] });
  });
  Object.entries(item.customFields || {}).forEach(([key, value]) => {
    if (value) rows.push({ label: labels[key] || "Detail", value });
  });
  if (item.notes) rows.push({ label: "Notes", value: item.notes });
  const todoItems = (item.todoList || []).filter((t) => t.text);

  // No panel is rendered when there's nothing to show — an event with no Extra
  // Fields filled in just gets its header panel and otherwise-empty card, rather
  // than an empty inset panel forced in to take up space.
  if (rows.length === 0 && todoItems.length === 0) return "";

  return `
    <div class="timegrid-event-info">
      ${rows
        .map(
          (r) => `
        <div class="timegrid-event-info-row"><span class="timegrid-event-info-label">${esc(r.label)}</span> ${esc(r.value)}</div>`
        )
        .join("")}
      ${
        todoItems.length
          ? `<div class="timegrid-event-info-todo">${todoItems
              .map((t) => `<div class="timegrid-event-info-todo-row ${t.done ? "is-done" : ""}">${esc(t.text)}</div>`)
              .join("")}</div>`
          : ""
      }
    </div>
  `;
}

// A card must be selected before a click opens it (see the main pointerdown
// handler below) — this just decides whether to paint the selection outline.
function isSelected(state, kind, id, occurrenceDate) {
  const sel = state.selectedItem;
  return !!sel && sel.kind === kind && sel.id === id && (sel.occurrenceDate || null) === (occurrenceDate || null);
}

// A card's `top` always lands exactly at its own start minute (needed for the
// grid to line up with the hour labels), so two back-to-back cards — one
// ending right as the next begins — would otherwise render with touching
// edges and no visible seam between them. Trimming a couple px off the
// bottom (not the top, so the grid alignment above is untouched) leaves a
// small gap wherever the next card follows immediately, without shifting
// anything's actual start position.
const CARD_BOTTOM_GAP = 2;

function itemBlock({ event: item, col, cols, startMin, endMin }, todayISO, state) {
  const offsetMin = (state.dayStartHour ?? SCROLL_TO_HOUR) * 60;
  const top = minutesToTop(toDisplayMinutes(startMin, offsetMin));
  const height = Math.max(minutesToTop(endMin - startMin) - CARD_BOTTOM_GAP, 20);
  const widthPct = 100 / cols;
  const leftPct = col * widthPct;
  const compact = height < 40;
  const isTask = item.kind === "task";
  const resizeHandles = `<div class="resize-handle" data-edge="top"></div><div class="resize-handle" data-edge="bottom"></div>`;

  // No resize/drag — there's nothing to reschedule to but the item's own
  // .time field, edited back on its parent card (see wireTodoList in addModal.js).
  if (item.kind === "todoItem") {
    return `
      <div class="timegrid-todo-block ${item.done ? "is-done" : ""}"
           style="top:${top}px; height:${height}px; left:calc(${leftPct}% + 2px); width:calc(${widthPct}% - 4px);"
           data-kind="todoItem" data-parent-type="${item.parentType}" data-parent-id="${item.parentId}" data-parent-occurrence="${item.parentOccurrence}" data-todo-id="${item.todoId}" title="${esc(item.title)}">
        <input type="checkbox" class="timegrid-todo-checkbox" ${item.done ? "checked" : ""} />
        <span class="timegrid-todo-label">${esc(item.title)}</span>
      </div>
    `;
  }

  const selected = isSelected(state, isTask ? "task" : "event", item.id, item.occurrenceDate || null);

  // Selecting a short card grows it (min-height:160px, see .is-selected in
  // calendar.css) so its full info panel has room to show. Left at a plain
  // `top`, that growth only extends downward from the card's original top
  // edge — it visually "pops up" anchored at a corner unrelated to where you
  // clicked. Growing it symmetrically around the original card's own vertical
  // center instead keeps the click point the visual anchor. Clamped to 0 so
  // it can't grow above the grid's start (hour 0).
  const SELECTED_MIN_HEIGHT = 160; // keep in sync with .is-selected's min-height in calendar.css
  const displayHeight = selected ? Math.max(height, SELECTED_MIN_HEIGHT) : height;
  const displayTop = selected ? Math.max(0, top - (displayHeight - height) / 2) : top;

  // Same idea horizontally: a card sharing the day column with overlapping
  // siblings (cols > 1) only owns a slice of the column's width (widthPct).
  // Selecting it grows that to the column's full width, centered on the
  // slice's own center rather than snapped to the column's left edge — for a
  // card that started at, say, the right half of a 2-way split, centering on
  // a full-width box means it overflows past the day column's edge into the
  // neighboring column's space. That's fine: .timegrid-col has no
  // overflow:hidden, and the z-index above already lifts it over neighbors.
  const displayWidthPct = selected ? 100 : widthPct;
  const displayLeftPct = selected ? leftPct + widthPct / 2 - 50 : leftPct;

  if (isTask) {
    const severity = rescheduleSeverityClass(item.rescheduleCount || 0);
    return `
      <div class="timegrid-task-block ${item.done ? "is-done" : ""} ${compact ? "is-compact" : ""} ${severity} ${selected ? "is-selected" : ""}"
           style="top:${displayTop}px; height:${displayHeight}px; left:calc(${displayLeftPct}% + 2px); width:calc(${displayWidthPct}% - 4px); --chip-color:${item.color};"
           data-id="${item.id}" data-kind="task" data-occurrence="${item.occurrenceDate || ""}" title="${esc(item.title)}">
        ${resizeHandles}
        <div class="timegrid-task-block-row">
          <button type="button" class="timegrid-task-checkbox" data-id="${item.id}" data-occurrence="${item.occurrenceDate || ""}" aria-label="Toggle done"></button>
          <div class="timegrid-event-title">${esc(item.title)}</div>
        </div>
        ${!compact ? `<div class="timegrid-event-time">${formatTime(item.startTime)} – ${formatTime(item.endTime)}</div>` : ""}
        ${!compact ? rescheduleBadgeHTML(item, todayISO) : ""}
        ${!compact ? extraInfoHTML(item, state) : ""}
      </div>
    `;
  }

  // Non-compact events get their title+time inside a "header panel" (a light
  // tint of --event-color — see .timegrid-event-panel in calendar.css) rather
  // than sitting directly on the card; compact events (<40px, no room for panel
  // padding) keep the plain bare title as before.
  const header = compact
    ? `<div class="timegrid-event-title">${esc(item.title)}</div>`
    : `
      <div class="timegrid-event-panel timegrid-event-header-panel">
        <div class="timegrid-event-title">${esc(item.title)}</div>
        <div class="timegrid-event-time">${formatTime(item.startTime)} – ${formatTime(item.endTime)}</div>
      </div>`;

  // The header panel alone (padding + title + time, see calendar.css) already
  // takes ~58px of a non-compact card; a second info panel needs another ~27px
  // to show even one line without being squeezed flat by flex-shrink (leaving
  // a dead gap between the two — see the "不上不下" fix history). Below that,
  // just skip the info panel entirely rather than show a cut-off sliver of it —
  // the header panel alone still centers cleanly (see .timegrid-event's
  // justify-content). Selected cards get their guaranteed min-height counted
  // here too, since that's what actually determines their rendered height.
  const MIN_HEIGHT_FOR_EVENT_INFO = 90;
  const info = !compact ? extraInfoHTML(item, state) : "";
  const showInfo = info && displayHeight >= MIN_HEIGHT_FOR_EVENT_INFO;

  return `
    <div class="timegrid-event ${compact ? "is-compact" : ""} ${selected ? "is-selected" : ""}"
         style="top:${displayTop}px; height:${displayHeight}px; left:calc(${displayLeftPct}% + 2px); width:calc(${displayWidthPct}% - 4px); background-color:${item.color}; --event-color:${item.color};"
         data-id="${item.id}" data-kind="event" data-occurrence="${item.occurrenceDate || ""}" title="${esc(item.title)}">
      ${resizeHandles}
      ${header}
      ${showInfo ? info : ""}
    </div>
  `;
}

// Shrinks el's font a step at a time until container fits within available
// height (or bottoms out at minFont and gives up). Returns whether it fits.
function shrinkFontToFit(el, container, available, maxFont, minFont) {
  el.style.fontSize = "";
  let size = maxFont;
  while (container.scrollHeight > available && size > minFont) {
    size -= 1;
    el.style.fontSize = `${size}px`;
  }
  return container.scrollHeight <= available;
}

// The title wraps to as many lines as it needs (see .timegrid-event-header-panel
// .timegrid-event-title in calendar.css) rather than truncating, shrinking its
// font first if a short/narrow card doesn't have room for that at full size
// (getting clipped by the card's own overflow:hidden otherwise). Only once
// shrinking bottoms out and it *still* doesn't fit — a long multi-word title
// in a genuinely narrow column — does it fall back to just the first word
// (plus an ellipsis to signal there's more), which then gets its own chance to
// size back up since it's so much shorter. Checking actual fit (rather than a
// flat "more than N lines" rule) is what makes this behave right on a
// selected card too: is-selected's min-height:160px (calendar.css) often
// gives a narrow-but-tall card plenty of room to show the full title wrapped,
// even though the same card unselected did not. Needs a real DOM measurement
// pass, so it runs after the innerHTML render rather than as a plain CSS rule.
const EVENT_TITLE_MAX_FONT = 14; // matches .timegrid-event-header-panel .timegrid-event-title
const EVENT_TITLE_MIN_FONT = 10;
function fitEventHeaderTitles(root) {
  root.querySelectorAll(".timegrid-event-header-panel").forEach((panel) => {
    const card = panel.closest(".timegrid-event");
    const title = panel.querySelector(".timegrid-event-title");
    if (!card || !title) return;
    const fullTitle = title.textContent;
    const available = card.clientHeight - 12; // card's own 6px top + 6px bottom padding
    if (shrinkFontToFit(title, panel, available, EVENT_TITLE_MAX_FONT, EVENT_TITLE_MIN_FONT)) return;
    const words = fullTitle.trim().split(/\s+/);
    if (words.length <= 1) return; // nothing shorter to fall back to
    title.textContent = `${words[0]}…`;
    shrinkFontToFit(title, panel, available, EVENT_TITLE_MAX_FONT, EVENT_TITLE_MIN_FONT);
  });
}

import {
  parseISODate, toISODate, addDays, startOfWeek, isSameDay, today, WEEKDAY_LABELS,
  formatHourLabel, formatTime, formatFullDate, minutesFromMidnight, minutesToHHMM, daysBetweenISO,
} from "../dateUtils.js";
import {
  eventsOnDate, scheduledTasksOnDate, specialDaysOnDate, getWeekUnscheduledTasks, getDayUnscheduledTasks, resolveOccurrence, isRepeating,
} from "../selectors.js";
import { icons } from "../icons.js";
import { esc } from "../utils.js";
import { fieldLabels, FIXED_FIELD_DEFS } from "../extraFields.js";
import { layoutDayEvents, HOUR_ROW_PX, minutesToTop } from "../timeLayout.js";
import { startPointerInteraction, snapMinutes, createAutoScroller } from "../dragUtils.js";
import { isOverdue, computeReschedulePatch } from "../rescheduleTracking.js";
import { handleOccurrenceClick } from "./recurrenceUI.js";
import { showToast } from "./notify.js";

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const SCROLL_TO_HOUR = 7;
const DAY_MINUTES = 24 * 60;
const MIN_DURATION = 15;
const WEEK_TRAY_VISIBLE = 6;
const DAY_TRAY_VISIBLE = 2;

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
    title: "New Task",
    categoryId: state.categories[0]?.id || "",
    colorId: state.categories[0]?.colors[0]?.id || "",
    dueDate,
    startTime: "",
    endTime: "",
    scheduled: false,
  });
  actions.openModal({ type: "edit", itemType: "task", id: created.id, isDraft: true });
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

  let days;
  if (state.view === "week") {
    const start = startOfWeek(cursor);
    days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  } else if (state.view === "day") {
    days = [cursor];
  } else {
    days = [...state.customDates].sort().map(parseISODate);
  }

  const weekTasks = getWeekUnscheduledTasks(state);

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
              ? `<button type="button" class="corner-unhide-btn is-active" id="week-tray-toggle-btn" title="Hide Week tray">W</button>`
              : ""
          }
          ${
            state.showDayTray
              ? `<button type="button" class="corner-unhide-btn is-active" id="day-tray-toggle-btn" title="Hide Day tray">D</button>`
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
          ? `<div class="week-tray">
        <div class="week-tray-title">Week</div>
        <div class="week-tray-chips">
          ${weekTasks
            .slice(0, WEEK_TRAY_VISIBLE)
            .map((task) => weekTrayChip(task, todayISO))
            .join("")}
          ${weekTasks.length > WEEK_TRAY_VISIBLE ? `<div class="unscheduled-more">+${weekTasks.length - WEEK_TRAY_VISIBLE} more</div>` : ""}
        </div>
        <button type="button" class="tray-add-btn tray-add-btn-floating" id="week-add-btn" aria-label="Add a task for sometime this week">${icons.plusSmall}</button>
      </div>`
          : ""
      }

      ${
        state.showDayTray
          ? `<div class="day-tray-row">
        <div class="timegrid-gutter-spacer day-tray-gutter-label">Day</div>
        <div class="day-tray-cols" style="grid-template-columns: repeat(${days.length}, 1fr);">
          ${days.map((d) => dayTrayCol(d, state, todayISO)).join("")}
        </div>
      </div>`
          : ""
      }

      <div class="timegrid-scroll" id="timegrid-scroll">
        <div class="timegrid-gutter" style="height:${24 * HOUR_ROW_PX}px;">
          ${HOURS.map((h) => `<div class="timegrid-hour-label" style="top:${h * HOUR_ROW_PX}px;">${h === 0 ? "" : formatHourLabel(h)}</div>`).join("")}
        </div>
        <div class="timegrid-body" style="grid-template-columns: repeat(${days.length}, 1fr); height:${24 * HOUR_ROW_PX}px;">
          ${days.map((d) => dayColumn(d, state, todayISO)).join("")}
        </div>
      </div>
    </div>
  `;

  const scrollEl = root.querySelector("#timegrid-scroll");
  const weekTrayEl = root.querySelector(".week-tray");
  scrollEl.scrollTop = prevScrollTop !== null ? prevScrollTop : SCROLL_TO_HOUR * HOUR_ROW_PX;

  function minuteFromClientY(clientY) {
    const scrollRect = scrollEl.getBoundingClientRect();
    const contentY = clientY - scrollRect.top + scrollEl.scrollTop;
    return (contentY / HOUR_ROW_PX) * 60;
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

  function applyTaskDrop(task, target) {
    if (target.zone === "none") return;
    const isOccurrence = !!task.occurrenceDate;

    if (target.zone === "week") {
      if (isOccurrence) {
        showToast("Recurring tasks can't be fully unscheduled — delete this occurrence instead", { variant: "danger" });
        return;
      }
      actions.updateTask(task.id, { dueDate: "", startTime: "", endTime: "", scheduled: false });
      return;
    }

    if (target.zone === "day") {
      if (isOccurrence) commitOccurrencePatch(actions, "task", task, "dueDate", { dueDate: target.date, startTime: "", endTime: "", scheduled: false });
      else actions.updateTask(task.id, computeReschedulePatch(task, target.date, "", "", { scheduled: false }));
      return;
    }

    const startTime = minutesToHHMM(target.startMin);
    const endTime = minutesToHHMM(target.startMin + 60);
    if (isOccurrence) commitOccurrencePatch(actions, "task", task, "dueDate", { dueDate: target.date, startTime, endTime, scheduled: true });
    else actions.updateTask(task.id, computeReschedulePatch(task, target.date, startTime, endTime, { scheduled: true }));
  }

  function createFromRange(date, startMin, endMin) {
    const type = state.pendingCreate?.type || "event";
    const category = state.categories[0];
    const categoryId = category?.id || "";
    const colorId = category?.colors[0]?.id || "";
    const startTime = minutesToHHMM(startMin);
    const endTime = minutesToHHMM(Math.max(endMin, startMin + MIN_DURATION));
    actions.setPendingCreate(null);
    if (type === "task") {
      const created = actions.addTask({
        title: "New Task",
        categoryId,
        colorId,
        dueDate: date,
        startTime,
        endTime,
        scheduled: true,
      });
      actions.openModal({ type: "edit", itemType: "task", id: created.id, isDraft: true });
    } else {
      const created = actions.addEvent({ title: "New Event", categoryId, colorId, date, startTime, endTime });
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
    ctx.previewEl.style.top = `${minutesToTop(lo)}px`;
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
        onClick: (ev, { ctx }) => {
          ctx.autoScroll.stop();
          createFromRange(ctx.date, ctx.startMin, ctx.startMin + 60);
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
          card.style.top = `${minutesToTop(newStart)}px`;
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

          // A scheduled task dragged back onto the Week/Day tray loses its confirmed
          // time (Week: fully unscheduled, Day: keeps the date, drops the time) —
          // events have no such unscheduled state, so this only applies to tasks. A
          // recurring occurrence can't go to the Week tray specifically — it would
          // have no date left to be found by again, so that drop is rejected instead.
          if (kind === "task") {
            const wt = ctx.weekTrayRect;
            if (wt && cy >= wt.top && cy <= wt.bottom && cx >= wt.left && cx <= wt.right) {
              card.remove();
              if (isRepeating(ctx.item)) {
                showToast("Recurring tasks can't be fully unscheduled — delete this occurrence instead", { variant: "danger" });
              } else {
                actions.updateTask(id, { dueDate: "", startTime: "", endTime: "", scheduled: false });
              }
              return;
            }
            const dayCol = ctx.dayTrayCols.find((c) => cx >= c.rect.left && cx < c.rect.right && cy >= c.rect.top && cy <= c.rect.bottom);
            if (dayCol) {
              card.remove();
              if (occurrenceKey) commitOccurrencePatch(actions, kind, ctx.item, "dueDate", { dueDate: dayCol.date, startTime: "", endTime: "", scheduled: false });
              else actions.updateTask(id, computeReschedulePatch(ctx.item, dayCol.date, "", "", { scheduled: false }));
              return;
            }
          }

          const date = dateFromClientX(cx, ctx.columns);
          let startMin = snapMinutes(minuteFromClientY(finalRect.top), 15);
          startMin = Math.max(0, Math.min(DAY_MINUTES - ctx.duration, startMin));
          card.remove();
          const startTime = minutesToHHMM(startMin);
          const endTime = minutesToHHMM(startMin + ctx.duration);

          if (kind === "event") {
            if (occurrenceKey) commitOccurrencePatch(actions, kind, ctx.item, "date", { date, startTime, endTime });
            else actions.updateEvent(id, { date, startTime, endTime });
          } else if (occurrenceKey) {
            commitOccurrencePatch(actions, kind, ctx.item, "dueDate", { dueDate: date, startTime, endTime, scheduled: true });
          } else {
            actions.updateTask(id, computeReschedulePatch(ctx.item, date, startTime, endTime, { scheduled: true }));
          }
        },
        onClick: () => {
          const item = findItem();
          if (item) handleOccurrenceClick(item, kind, occurrenceKey, actions);
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
    chip.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".timegrid-task-checkbox")) return;
      startPointerInteraction(e, {
        onStart: () => {
          const master = state.tasks.find((x) => x.id === chip.dataset.id);
          if (!master) return null;
          const occurrenceKey = chip.dataset.occurrence || null;
          const task = occurrenceKey ? resolveOccurrence(master, occurrenceKey, "dueDate") : master;
          const ctx = {
            task,
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
          applyTaskDrop(ctx.task, target);
        },
        onClick: (ev, { ctx }) => {
          if (!ctx) return;
          handleOccurrenceClick(ctx.task, "task", ctx.task.occurrenceDate || null, actions);
        },
      });
    });
  });

  root.querySelector("#week-add-btn")?.addEventListener("click", () => quickAddTask(actions, state, ""));
  root.querySelectorAll(".day-tray-add-btn").forEach((btn) => {
    btn.addEventListener("click", () => quickAddTask(actions, state, btn.dataset.date));
  });

  // Only rendered while its tray is on (see corner-unhide-cell above), so this is a
  // one-way "hide" — once off, Settings' Visibility checkboxes are the only way
  // back, not this corner button (see settingsPanel.js).
  root.querySelector("#week-tray-toggle-btn")?.addEventListener("click", () => actions.setShowWeekTray(false));
  root.querySelector("#day-tray-toggle-btn")?.addEventListener("click", () => actions.setShowDayTray(false));
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

  return `
    <div class="timegrid-col" data-date="${iso}">
      ${HOURS.map((h) => `<div class="timegrid-hourcell" style="top:${h * HOUR_ROW_PX}px; height:${HOUR_ROW_PX}px;"></div>`).join("")}
      ${placed.map((p) => itemBlock(p, todayISO, state)).join("")}
    </div>
  `;
}

function dayTrayCol(date, state, todayISO) {
  const iso = toISODate(date);
  const tasks = getDayUnscheduledTasks(state, iso);
  const specialDays = specialDaysOnDate(state, iso);

  // Special Days share the tray's fixed-height row with unscheduled tasks, so
  // they compete for the same small slot budget rather than adding to it
  // unbounded — otherwise a day with several markers would overflow the row.
  const specialSlots = Math.min(specialDays.length, DAY_TRAY_VISIBLE);
  const visibleSpecial = specialDays.slice(0, specialSlots);
  const visibleTasks = tasks.slice(0, DAY_TRAY_VISIBLE - specialSlots);
  const overflow = specialDays.length - visibleSpecial.length + (tasks.length - visibleTasks.length);

  return `
    <div class="day-tray-col" data-date="${iso}">
      ${visibleSpecial.map((d) => specialDayTrayChip(d)).join("")}
      ${visibleTasks.map((t) => dayTrayChip(t, todayISO)).join("")}
      ${overflow > 0 ? `<div class="unscheduled-more">+${overflow} more</div>` : ""}
      <button type="button" class="tray-add-btn day-tray-add-btn tray-add-btn-floating" data-date="${iso}" aria-label="Add a task for this day">${icons.plusSmall}</button>
    </div>
  `;
}

// All-day markers (birthdays, holidays, ...) live in the day tray — the app's
// existing "no specific time" strip — rather than the hourly grid. No drag
// support (there's no time to reschedule to); click opens the edit step.
function specialDayTrayChip(d) {
  return `
    <div class="day-tray-chip special-day-tray-chip" style="--chip-color:${d.color}" data-id="${d.id}" data-occurrence="${d.occurrenceDate || ""}" title="${esc(d.title)}">
      <span class="unscheduled-chip-label">🎉 ${esc(d.title)}</span>
    </div>
  `;
}

function weekTrayChip(t, todayISO) {
  const severity = rescheduleSeverityClass(t.rescheduleCount || 0);
  return `
    <div class="unscheduled-chip ${t.done ? "is-done" : ""} ${severity}" style="--chip-color:${t.color}" data-id="${t.id}" data-occurrence="${t.occurrenceDate || ""}" title="${esc(t.title)} · Drag onto a day or the timeline${t.rescheduleCount ? `\n${esc(rescheduleHistoryText(t))}` : ""}">
      <button type="button" class="timegrid-task-checkbox" data-id="${t.id}" data-occurrence="${t.occurrenceDate || ""}" aria-label="Toggle done"></button>
      <span class="unscheduled-chip-label">${t.rescheduleCount ? (t.rescheduleCount >= 3 || t.overdueReschedule ? "⚠ " : "↻ ") : ""}${esc(t.title)}</span>
    </div>
  `;
}

function dayTrayChip(t, todayISO) {
  const overdue = isOverdue(t, todayISO);
  const severity = rescheduleSeverityClass(t.rescheduleCount || 0);
  const prefix = overdue ? "⚠ " : t.rescheduleCount ? (t.rescheduleCount >= 3 || t.overdueReschedule ? "⚠ " : "↻ ") : "";
  const historyLine = overdue || t.rescheduleCount ? `\n${esc(rescheduleHistoryText(t))}` : "";
  return `
    <div class="day-tray-chip ${t.done ? "is-done" : ""} ${overdue ? "is-overdue" : severity}" style="--chip-color:${t.color}" data-id="${t.id}" data-occurrence="${t.occurrenceDate || ""}" title="${esc(t.title)} · Drag onto the timeline to set a time${historyLine}">
      <button type="button" class="timegrid-task-checkbox" data-id="${t.id}" data-occurrence="${t.occurrenceDate || ""}" aria-label="Toggle done"></button>
      <span class="unscheduled-chip-label">${prefix}${esc(t.title)}</span>
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
    if (value) rows.push({ label: labels[key] || "Field", value });
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

function itemBlock({ event: item, col, cols, startMin, endMin }, todayISO, state) {
  const top = minutesToTop(startMin);
  const height = Math.max(minutesToTop(endMin - startMin), 20);
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

  if (isTask) {
    const severity = rescheduleSeverityClass(item.rescheduleCount || 0);
    return `
      <div class="timegrid-task-block ${item.done ? "is-done" : ""} ${compact ? "is-compact" : ""} ${severity}"
           style="top:${top}px; height:${height}px; left:calc(${leftPct}% + 2px); width:calc(${widthPct}% - 4px); --chip-color:${item.color};"
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

  return `
    <div class="timegrid-event ${compact ? "is-compact" : ""}"
         style="top:${top}px; height:${height}px; left:calc(${leftPct}% + 2px); width:calc(${widthPct}% - 4px); background-color:${item.color}; --event-color:${item.color};"
         data-id="${item.id}" data-kind="event" data-occurrence="${item.occurrenceDate || ""}" title="${esc(item.title)}">
      ${resizeHandles}
      ${header}
      ${!compact ? extraInfoHTML(item, state) : ""}
    </div>
  `;
}

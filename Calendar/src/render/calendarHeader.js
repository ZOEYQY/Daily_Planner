import { icons } from "../icons.js";
import { esc } from "../utils.js";
import {
  parseISODate, toISODate, addDays, addMonths, startOfMonth, startOfWeek,
  formatMonthYear, formatWeekRange, formatFullDate, today,
} from "../dateUtils.js";
import { isTodoPanelOpen } from "../selectors.js";

const VIEWS = [
  { id: "month", label: "Month" },
  { id: "week", label: "Week" },
  { id: "day", label: "Day" },
  { id: "custom", label: "Custom" },
];

function shortDateLabel(iso) {
  const d = parseISODate(iso);
  return formatFullDate(d).split(", ")[1];
}

function periodLabel(state) {
  const cursor = parseISODate(state.cursorDate);
  if (state.view === "month") return formatMonthYear(cursor);
  if (state.view === "week") {
    // The To-Do side panel shows a 5-day window starting at the cursor date
    // itself, not the calendar week's Monday/Sunday (see isTodoPanelOpen) —
    // the label follows suit rather than naming a "week" that isn't fully shown.
    return isTodoPanelOpen(state) ? formatWeekRange(cursor, 4) : formatWeekRange(startOfWeek(cursor, state.weekStartsOn));
  }
  if (state.view === "day") return formatFullDate(cursor);
  return [...state.customDates].sort().map(shortDateLabel).join(", ");
}

function step(state, dir) {
  const cursor = parseISODate(state.cursorDate);
  if (state.view === "month") return toISODate(addMonths(startOfMonth(cursor), dir));
  // Panel mode's 5-day window pages by 5 days too, so paging never skips a
  // day (a plain 7-day step would leave 2 days out of every window) — see
  // isTodoPanelOpen.
  if (state.view === "week") return toISODate(addDays(cursor, dir * (isTodoPanelOpen(state) ? 5 : 7)));
  return toISODate(addDays(cursor, dir));
}

// Filtering applies everywhere (month chips, week/day timeline, week/day
// trays) via getVisibleEvents/getVisibleTasks/getVisibleSpecialDays in
// selectors.js — this row just drives state.categoryFilterActive/
// categoryFilterIds, it doesn't do any filtering itself. Multi-select: MMU
// and CLSC can both be active at once, showing either's items. "All" turns
// the filter off (show everything); "None" turns it on with nothing selected
// (show nothing) — a third state distinct from just deselecting every
// category pill one at a time, for a quick reset before picking a couple.
function categoryFilterHTML(state) {
  const categories = state.categories.filter((c) => !c.archived);
  if (categories.length === 0) return "";
  // Filtering off, or every category individually toggled back on (e.g.
  // clicking MMU off from "All" then clicking it on again) — both mean
  // "everything shows", so the All pill lights up either way rather than
  // only recognizing the exact state filtering-off produces.
  const isAll = !state.categoryFilterActive || categories.every((c) => state.categoryFilterIds.includes(c.id));
  const isNone = state.categoryFilterActive && state.categoryFilterIds.length === 0;
  return `
    <div class="cal-category-filter">
      <button type="button" class="cal-cat-pill ${isAll ? "is-active" : ""}" id="cat-filter-all">All</button>
      <button type="button" class="cal-cat-pill ${isNone ? "is-active" : ""}" id="cat-filter-none">None</button>
      ${categories
        .map(
          (c) => `
        <button type="button" class="cal-cat-pill ${isAll || state.categoryFilterIds.includes(c.id) ? "is-active" : ""}" data-cat-id="${c.id}">${esc(c.name)}</button>`
        )
        .join("")}
    </div>
  `;
}

export function renderCalendarHeader(root, state, actions) {
  root.className = "cal-header-wrap";
  const isCustom = state.view === "custom";

  root.innerHTML = `
    <div class="cal-header">
      <div class="cal-header-left">
        ${
          isCustom
            ? `<button class="cal-today-btn" id="btn-customize">${icons.settings}<span>Customize</span></button>`
            : `<div class="cal-nav">
                <button class="cal-nav-btn" id="nav-prev" aria-label="Previous">${icons.chevronLeft}</button>
                <button class="cal-nav-btn" id="nav-next" aria-label="Next">${icons.chevronRight}</button>
              </div>
              <button class="cal-today-btn" id="nav-today">Today</button>`
        }
        <div class="cal-period-label">${periodLabel(state)}</div>
      </div>
      <div class="view-switch">
        ${VIEWS.map(
          (v) => `<button class="view-switch-btn ${state.view === v.id ? "active" : ""}" data-view="${v.id}">${v.label}</button>`
        ).join("")}
      </div>
    </div>
    ${categoryFilterHTML(state)}
  `;

  if (isCustom) {
    root.querySelector("#btn-customize").addEventListener("click", () => actions.openModal({ type: "customize" }));
  } else {
    root.querySelector("#nav-prev").addEventListener("click", () => actions.setCursorDate(step(state, -1)));
    root.querySelector("#nav-next").addEventListener("click", () => actions.setCursorDate(step(state, 1)));
    root.querySelector("#nav-today").addEventListener("click", () => actions.setCursorDate(toISODate(today())));
  }

  root.querySelectorAll(".view-switch-btn").forEach((btn) => {
    btn.addEventListener("click", () => actions.setView(btn.dataset.view));
  });

  root.querySelector("#cat-filter-all")?.addEventListener("click", () => actions.setCategoryFilterAll());
  root.querySelector("#cat-filter-none")?.addEventListener("click", () => actions.setCategoryFilterNone());
  root.querySelectorAll(".cal-cat-pill[data-cat-id]").forEach((btn) => {
    btn.addEventListener("click", () => actions.toggleCategoryFilterId(btn.dataset.catId));
  });
}

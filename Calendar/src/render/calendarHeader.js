import { icons } from "../icons.js";
import {
  parseISODate, toISODate, addDays, addMonths, startOfMonth, startOfWeek,
  formatMonthYear, formatWeekRange, formatFullDate, today,
} from "../dateUtils.js";

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
  if (state.view === "week") return formatWeekRange(startOfWeek(cursor));
  if (state.view === "day") return formatFullDate(cursor);
  return [...state.customDates].sort().map(shortDateLabel).join(", ");
}

function step(state, dir) {
  const cursor = parseISODate(state.cursorDate);
  if (state.view === "month") return toISODate(addMonths(startOfMonth(cursor), dir));
  if (state.view === "week") return toISODate(addDays(cursor, dir * 7));
  return toISODate(addDays(cursor, dir));
}

export function renderCalendarHeader(root, state, actions) {
  root.className = "cal-header";
  const isCustom = state.view === "custom";

  root.innerHTML = `
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
}

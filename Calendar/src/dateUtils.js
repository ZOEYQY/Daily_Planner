export const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
export const MONTH_LABELS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function pad2(n) {
  return String(n).padStart(2, "0");
}

export function toISODate(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

export function parseISODate(str) {
  const [y, m, d] = str.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function isSameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function addDays(date, n) {
  const d = new Date(date);
  d.setDate(d.getDate() + n);
  return d;
}

export function addMonths(date, n) {
  const d = new Date(date);
  d.setDate(1);
  d.setMonth(d.getMonth() + n);
  return d;
}

export function startOfWeek(date) {
  const d = startOfDay(date);
  d.setDate(d.getDate() - d.getDay());
  return d;
}

export function startOfMonth(date) {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

export function endOfMonth(date) {
  return new Date(date.getFullYear(), date.getMonth() + 1, 0);
}

/** Returns an array of 42 Date objects covering the full 6-week grid for a month. */
export function getMonthGridDays(date) {
  const gridStart = startOfWeek(startOfMonth(date));
  const days = [];
  for (let i = 0; i < 42; i++) {
    days.push(addDays(gridStart, i));
  }
  return days;
}

export function formatMonthYear(date) {
  return `${MONTH_LABELS[date.getMonth()]} ${date.getFullYear()}`;
}

export function formatWeekRange(weekStart) {
  const weekEnd = addDays(weekStart, 6);
  const sameMonth = weekStart.getMonth() === weekEnd.getMonth();
  const startStr = `${MONTH_LABELS[weekStart.getMonth()].slice(0, 3)} ${weekStart.getDate()}`;
  const endStr = sameMonth
    ? `${weekEnd.getDate()}`
    : `${MONTH_LABELS[weekEnd.getMonth()].slice(0, 3)} ${weekEnd.getDate()}`;
  return `${startStr} – ${endStr}, ${weekEnd.getFullYear()}`;
}

export function formatFullDate(date) {
  return `${WEEKDAY_LABELS[date.getDay()]}, ${MONTH_LABELS[date.getMonth()].slice(0, 3)} ${date.getDate()}`;
}

export function formatTime(hhmm) {
  if (!hhmm) return "";
  const [h, m] = hhmm.split(":").map(Number);
  const period = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${pad2(m)} ${period}`;
}

export function today() {
  return startOfDay(new Date());
}

export function formatHourLabel(hour) {
  const period = hour >= 12 ? "PM" : "AM";
  const h12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${h12} ${period}`;
}

export function minutesFromMidnight(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

export function minutesToHHMM(totalMinutes) {
  const clamped = Math.max(0, Math.min(24 * 60 - 1, Math.round(totalMinutes)));
  const h = Math.floor(clamped / 60);
  const m = clamped % 60;
  return `${pad2(h)}:${pad2(m)}`;
}

export function daysBetweenISO(fromISO, toISO) {
  const from = parseISODate(fromISO);
  const to = parseISODate(toISO);
  return Math.round((to - from) / (1000 * 60 * 60 * 24));
}

// 1st, 2nd, 3rd... occurrence of this date's weekday within its month — used to match
// "every 2nd Tuesday" style monthly recurrence. A 5th-weekday anchor (the 29th-31st)
// will simply not match in months that lack a 5th such weekday — accepted limitation.
export function nthWeekdayOfMonth(date) {
  return Math.ceil(date.getDate() / 7);
}

// Interval arithmetic for "every N weeks/months/years" recurrence — how many whole
// units separate an item's anchor date from a candidate occurrence date. Used to
// gate rule matches to only every Nth unit instead of every single one.
export function weeksBetween(anchor, date) {
  return Math.round((startOfWeek(date) - startOfWeek(anchor)) / (1000 * 60 * 60 * 24 * 7));
}

export function monthsBetween(anchor, date) {
  return (date.getFullYear() - anchor.getFullYear()) * 12 + (date.getMonth() - anchor.getMonth());
}

export function yearsBetween(anchor, date) {
  return date.getFullYear() - anchor.getFullYear();
}

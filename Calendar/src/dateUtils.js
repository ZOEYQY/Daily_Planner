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

// weekStartsOn: 0 = Sunday (default), 1 = Monday — see state.weekStartsOn,
// user-configurable in Settings. Kept as a parameter (not read from state
// directly) since this is a plain date util with no store access; callers that
// care about the user's preference pass state.weekStartsOn through explicitly.
export function startOfWeek(date, weekStartsOn = 0) {
  const d = startOfDay(date);
  d.setDate(d.getDate() - ((d.getDay() - weekStartsOn + 7) % 7));
  return d;
}

// WEEKDAY_LABELS rotated to start on the given day — for header rows (month
// grid, etc.) that lay out a fixed Sun–Sat sequence and need it reordered to
// match. Contexts that index by an actual date's getDay() (the week/day view's
// per-column head label) don't need this — only fixed positional sequences do.
export function orderedWeekdayLabels(weekStartsOn = 0) {
  return [...WEEKDAY_LABELS.slice(weekStartsOn), ...WEEKDAY_LABELS.slice(0, weekStartsOn)];
}

export function startOfMonth(date) {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

export function endOfMonth(date) {
  return new Date(date.getFullYear(), date.getMonth() + 1, 0);
}

/** Returns an array of 42 Date objects covering the full 6-week grid for a month. */
export function getMonthGridDays(date, weekStartsOn = 0) {
  const gridStart = startOfWeek(startOfMonth(date), weekStartsOn);
  const days = [];
  for (let i = 0; i < 42; i++) {
    days.push(addDays(gridStart, i));
  }
  return days;
}

export function formatMonthYear(date) {
  return `${MONTH_LABELS[date.getMonth()]} ${date.getFullYear()}`;
}

// spanDays: how many days *after* weekStart the range covers — 6 (a full
// 7-day calendar week) by default. The To-Do side panel's 5-day window
// (see dayGridView.js/calendarHeader.js) passes 4 instead.
export function formatWeekRange(weekStart, spanDays = 6) {
  const weekEnd = addDays(weekStart, spanDays);
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
  // Blank rather than "NaN:NaN AM" for a value already corrupted before the
  // minutesFromMidnight/minutesToHHMM fix — this only masks old bad data on
  // display, it doesn't repair it; re-saving the item (e.g. editing its time
  // in the modal) is what actually fixes the stored value.
  if (!Number.isFinite(h) || !Number.isFinite(m)) return "";
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

// "" (an untimed task) used to silently become NaN here — "".split(":") is
// [""], Number("") is 0, but the missing second element makes m undefined, and
// h*60 + undefined is NaN — which then survives all the way through
// minutesToHHMM into a literally-saved "NaN:NaN" startTime/endTime that every
// future drag/resize on that item re-derives from, staying broken forever.
// Falls back to 0 (midnight) for anything that isn't a clean "HH:MM" instead.
export function minutesFromMidnight(hhmm) {
  if (!hhmm) return 0;
  const [h, m] = hhmm.split(":").map(Number);
  return Number.isFinite(h) && Number.isFinite(m) ? h * 60 + m : 0;
}

// Never emits "NaN:NaN" — see minutesFromMidnight above for how that used to
// happen and permanently corrupt whatever item it got saved onto.
export function minutesToHHMM(totalMinutes) {
  const safe = Number.isFinite(totalMinutes) ? totalMinutes : 0;
  const clamped = Math.max(0, Math.min(24 * 60 - 1, Math.round(safe)));
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

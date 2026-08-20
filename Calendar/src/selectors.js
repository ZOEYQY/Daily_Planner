import { parseISODate, nthWeekdayOfMonth, weeksBetween, monthsBetween, yearsBetween } from "./dateUtils.js";

const FALLBACK_CATEGORY = { id: "", name: "Uncategorized", colors: [] };
const FALLBACK_COLOR = { id: "", name: "", value: "#898781", enabledFields: [] };

function categoryFor(state, categoryId) {
  return state.categories.find((c) => c.id === categoryId) || FALLBACK_CATEGORY;
}

function colorFor(category, colorId) {
  return category.colors.find((c) => c.id === colorId) || FALLBACK_COLOR;
}

// Colors belong to a category (each category keeps its own named palette),
// so an item's accent color is resolved via categoryId -> colorId. If either
// no longer exists (category/color renamed away or deleted), this quietly
// falls back to a neutral gray instead of erroring or losing the item.
function withCategory(state, item) {
  const cat = categoryFor(state, item.categoryId);
  const col = colorFor(cat, item.colorId);
  return { ...item, categoryName: cat.name, colorName: col.name, color: col.value };
}

export function matchesSearch(title, query) {
  if (!query.trim()) return true;
  return title.toLowerCase().includes(query.trim().toLowerCase());
}

export function getVisibleEvents(state) {
  return state.events.filter((e) => matchesSearch(e.title, state.searchQuery)).map((e) => withCategory(state, e));
}

export function getVisibleTasks(state) {
  return state.tasks.filter((t) => matchesSearch(t.title, state.searchQuery)).map((t) => withCategory(state, t));
}

export function getVisibleSpecialDays(state) {
  return (state.specialDays || [])
    .filter((d) => matchesSearch(d.title, state.searchQuery))
    .map((d) => withCategory(state, d));
}

// ---------- Recurrence ----------
// repeat is an array of independent rules — [] means "does not repeat". Each rule
// carries its own weekday/ordinal explicitly (not derived from the item's anchor
// date), so a task anchored on a Tuesday can still carry a Friday rule, etc. An
// occurrence matches if ANY rule matches (logical OR), still gated by iso >= anchor.
// Single-occurrence edits/moves/deletes are recorded in `exceptions`, keyed by the
// occurrence's CANONICAL date (where the pattern says it belongs), not where it's
// currently displayed — `movedTo` carries the actual date when a drag/edit relocates
// one occurrence.
//
// [] is truthy in JS — every check for "does this item repeat" MUST go through
// isRepeating() below, never a bare `item.repeat` truthiness check, or a plain
// non-repeating item (repeat: []) will be silently treated as recurring.
export function isRepeating(item) {
  return Array.isArray(item.repeat) && item.repeat.length > 0;
}

function ruleMatches(rule, date, anchor) {
  const interval = rule.interval || 1;
  if (rule.freq === "weekly") {
    return rule.weekdays.includes(date.getDay()) && weeksBetween(anchor, date) % interval === 0;
  }
  if (rule.freq === "monthly") {
    return (
      rule.weekdays.includes(date.getDay()) &&
      nthWeekdayOfMonth(date) === rule.ordinal &&
      monthsBetween(anchor, date) % interval === 0
    );
  }
  if (rule.freq === "yearly") {
    return (
      date.getMonth() === anchor.getMonth() &&
      date.getDate() === anchor.getDate() &&
      yearsBetween(anchor, date) % interval === 0
    );
  }
  return false;
}

export function matchesPattern(item, iso, dateField) {
  const anchor = item[dateField];
  if (!anchor || !isRepeating(item)) return false;
  if (iso < anchor) return false;
  const t = parseISODate(iso);
  const a = parseISODate(anchor);
  return item.repeat.some((rule) => ruleMatches(rule, t, a));
}

// Whether two repeat rules would ever produce the same occurrence date. Each rule
// now carries a *set* of weekdays, so two rules overlap whenever they share ANY
// weekday (subject to matching ordinal for monthly-vs-monthly) — resolved as a
// single whole-rule choice regardless of whether the overlap is full or partial,
// not a per-day split (confirmed with the user: simplicity over precision here).
export function ruleOverlap(a, b) {
  // Yearly rules have no weekdays — both rules on one item always share the same
  // anchor date, so they only ever collide (and only ever fully coincide) when
  // their interval matches too.
  if (a.freq === "yearly" || b.freq === "yearly") {
    if (a.freq !== b.freq) return { relation: "none", sharedWeekdays: [] };
    const sameInterval = (a.interval || 1) === (b.interval || 1);
    return sameInterval ? { relation: "equal", sharedWeekdays: [] } : { relation: "overlap", sharedWeekdays: [] };
  }
  if (a.freq === "monthly" && b.freq === "monthly" && a.ordinal !== b.ordinal) {
    return { relation: "none", sharedWeekdays: [] };
  }
  const shared = a.weekdays.filter((w) => b.weekdays.includes(w));
  if (shared.length === 0) return { relation: "none", sharedWeekdays: [] };
  const sameFreq =
    a.freq === b.freq && (a.freq !== "monthly" || a.ordinal === b.ordinal) && (a.interval || 1) === (b.interval || 1);
  const sameSet = sameFreq && a.weekdays.length === b.weekdays.length && shared.length === a.weekdays.length;
  return { relation: sameSet ? "equal" : "overlap", sharedWeekdays: shared };
}

// Resolves how a specific occurrence (identified by its canonical date) should render:
// applies any standing override, moves the display date if the occurrence was dragged
// elsewhere, and — for tasks — resolves per-occurrence completion instead of the
// master's single `done` flag.
export function resolveOccurrence(item, canonicalKey, dateField) {
  const ex = item.exceptions?.[canonicalKey];
  const overrides = ex && !ex.deleted ? ex : {};
  const repeating = isRepeating(item);
  const done = repeating ? (item.doneDates || []).includes(canonicalKey) : item.done;
  return {
    ...item,
    ...overrides,
    [dateField]: ex?.movedTo ?? canonicalKey,
    done,
    occurrenceDate: canonicalKey,
    isRecurring: repeating,
  };
}

function occurrencesOnDate(items, iso, dateField) {
  const out = [];
  for (const item of items) {
    if (!isRepeating(item)) {
      if (item[dateField] === iso) out.push(resolveOccurrence(item, item[dateField], dateField));
      continue;
    }
    const exceptions = item.exceptions || {};
    if (matchesPattern(item, iso, dateField)) {
      const ex = exceptions[iso];
      const movedAway = ex?.movedTo && ex.movedTo !== iso;
      if (!ex?.deleted && !movedAway) out.push(resolveOccurrence(item, iso, dateField));
    }
    // Occurrences dragged/edited onto this date from elsewhere in the series.
    for (const [canonicalKey, ex] of Object.entries(exceptions)) {
      if (ex.movedTo === iso && canonicalKey !== iso) out.push(resolveOccurrence(item, canonicalKey, dateField));
    }
  }
  return out;
}

// Only events that actually have a time show on the hourly timeline — one
// dragged onto the Day tray (see applyEventDrop in dayGridView.js) keeps its
// date but loses its time, same as a day-tray task, so it drops out of here and
// into getDayUnscheduledEvents below instead.
export function eventsOnDate(state, iso) {
  return occurrencesOnDate(
    getVisibleEvents(state).filter((e) => e.startTime),
    iso,
    "date"
  ).sort((a, b) => a.startTime.localeCompare(b.startTime));
}

export function specialDaysOnDate(state, iso) {
  return occurrencesOnDate(getVisibleSpecialDays(state), iso, "date");
}

export function scheduledTasksOnDate(state, iso) {
  return occurrencesOnDate(
    getVisibleTasks(state).filter((t) => t.scheduled),
    iso,
    "dueDate"
  ).sort((a, b) => a.startTime.localeCompare(b.startTime));
}

// Fully unscheduled: no date, no time — "sometime this week". A task needs a date
// before it can repeat, so this list never contains recurring items by construction.
export function getWeekUnscheduledTasks(state) {
  return getVisibleTasks(state).filter((t) => !t.scheduled && !t.dueDate);
}

// Fully unscheduled: no date, no time — "sometime this week". Same construction
// note as tasks re: an event needs a date before it can repeat, so this list
// never contains recurring items either.
export function getWeekUnscheduledEvents(state) {
  return getVisibleEvents(state).filter((e) => !e.date);
}

// Day-scoped but time-unscheduled: has a date, no time yet — same shape as
// getDayUnscheduledTasks below, since an event dropped on the Day tray behaves
// exactly like a task dropped there (see applyEventDrop in dayGridView.js).
export function getDayUnscheduledEvents(state, iso) {
  return occurrencesOnDate(
    getVisibleEvents(state).filter((e) => !e.startTime && e.date),
    iso,
    "date"
  );
}

// Day-scoped but time-unscheduled: has a date, no time yet (e.g. an exam
// you know the day of but haven't pinned a time for).
export function getDayUnscheduledTasks(state, iso) {
  return occurrencesOnDate(
    getVisibleTasks(state).filter((t) => !t.scheduled && t.dueDate),
    iso,
    "dueDate"
  );
}

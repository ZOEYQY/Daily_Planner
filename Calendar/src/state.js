import { buildSeedData, uid } from "./seed.js";
import { toISODate, addDays, today, parseISODate, nthWeekdayOfMonth } from "./dateUtils.js";
import { makeColor, pickUnusedColor } from "./categoryColor.js";
import { matchesPattern, isRepeating } from "./selectors.js";

// Drops any exception/doneDate entries that no longer match an item's (possibly just
// changed) recurrence pattern, so an actively-edited series doesn't accumulate
// unreachable garbage in localStorage indefinitely. Safe no-op for non-repeating items.
function pruneStaleOccurrenceData(item, dateField) {
  if (!isRepeating(item)) return item;
  const exceptions = {};
  for (const [key, ex] of Object.entries(item.exceptions || {})) {
    if (matchesPattern(item, key, dateField)) exceptions[key] = ex;
  }
  const doneDates = (item.doneDates || []).filter((d) => matchesPattern(item, d, dateField));
  return { ...item, exceptions, doneDates };
}

// A rule's weekday used to be a single number; it's now a `weekdays` array (one
// rule can now cover several weekdays at once). Wraps an old single-weekday rule
// into the equivalent one-entry array; a no-op for anything already migrated.
function migrateRuleWeekdays(rule) {
  const withInterval = rule.interval ? rule : { ...rule, interval: 1 };
  if (Array.isArray(withInterval.weekdays)) return withInterval;
  const { weekday, ...rest } = withInterval;
  return { ...rest, weekdays: weekday != null ? [weekday] : [] };
}

// A previous version of the app stored `repeat` as a single object ({freq}) with the
// weekday/ordinal implicitly derived from the item's own anchor date, instead of
// today's array-of-explicit-rules shape. Freezes that implicit pattern into an
// equivalent explicit rule the first time old data is loaded; idempotent (already-
// migrated array data passes through untouched, aside from the weekdays sweep above).
function migrateRepeatShape(item, dateField) {
  if (item.repeat == null) return { ...item, repeat: [] };
  if (Array.isArray(item.repeat)) return { ...item, repeat: item.repeat.map(migrateRuleWeekdays) };
  const anchor = item[dateField];
  if (!anchor) return { ...item, repeat: [] };
  const a = parseISODate(anchor);
  const rule = { id: uid("rule"), freq: item.repeat.freq, weekdays: [a.getDay()], interval: 1 };
  if (item.repeat.freq === "monthly") rule.ordinal = nthWeekdayOfMonth(a);
  return { ...item, repeat: [rule] };
}

// Guest (no logged-in user) is a view-only demo shell with no data of its own — it
// never reads or writes localStorage at all, so it's always empty and can never
// touch (or be confused with) whatever real data sits at the pre-accounts
// "monoCalendar.v1" key. Only a logged-in user's own per-account key is ever used.
function storageKeyFor(userId) {
  return `monoCalendar.v1.user.${userId}`;
}

function loadPersisted(userId) {
  if (!userId) return null;
  try {
    const raw = localStorage.getItem(storageKeyFor(userId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || !Array.isArray(parsed.categories)) return null;
    // Normalize categories saved by older versions of the app, where a
    // category had no nested `colors` array — without this, resolving a
    // task/event's color crashes the whole render on load. Extra Fields used
    // to be one global list (state.enabledFields) instead of per-category —
    // seed each category still missing its own list from that old global
    // value so nobody's current setup silently reverts to "nothing enabled".
    parsed.categories = parsed.categories.map((c) => ({
      ...c,
      colors: Array.isArray(c.colors)
        ? c.colors.map((col) => ({ ...col, enabledFields: Array.isArray(col.enabledFields) ? col.enabledFields : [] }))
        : [],
      enabledFields: Array.isArray(c.enabledFields) ? c.enabledFields : [...(parsed.enabledFields || [])],
    }));
    if (Array.isArray(parsed.tasks)) parsed.tasks = parsed.tasks.map((t) => migrateRepeatShape(t, "dueDate"));
    if (Array.isArray(parsed.events)) parsed.events = parsed.events.map((e) => migrateRepeatShape(e, "date"));
    parsed.specialDays = Array.isArray(parsed.specialDays) ? parsed.specialDays.map((d) => migrateRepeatShape(d, "date")) : [];
    parsed.customFieldDefs = Array.isArray(parsed.customFieldDefs) ? parsed.customFieldDefs : [];
    return parsed;
  } catch {
    return null;
  }
}

function persist(state, userId) {
  if (!userId) return;
  const { categories, tasks, events, specialDays, customFieldDefs, customDates, showWeekTray, showDayTray, view, cursorDate, modal } =
    state;
  try {
    localStorage.setItem(
      storageKeyFor(userId),
      // `modal` is only ever "add"/"edit"/"settings"/etc. plus plain ids/dates — never
      // raw unsaved keystrokes — so reloading mid-edit reopens the same modal on the
      // same item/date rather than resuming exactly-as-typed unsaved text.
      JSON.stringify({
        categories,
        tasks,
        events,
        specialDays,
        customFieldDefs,
        customDates,
        showWeekTray,
        showDayTray,
        view,
        cursorDate,
        modal,
      })
    );
  } catch {
    /* storage unavailable — app still works in-memory */
  }
}

function defaultCustomDates() {
  const t = today();
  return [0, 1, 2].map((n) => toISODate(addDays(t, n)));
}

function initialState(userId = null) {
  const persisted = loadPersisted(userId);
  const base = persisted || buildSeedData();
  return {
    categories: base.categories,
    tasks: base.tasks,
    events: base.events,
    specialDays: base.specialDays || [],
    customFieldDefs: base.customFieldDefs || [],
    customDates: persisted?.customDates?.length ? persisted.customDates : defaultCustomDates(),
    showWeekTray: persisted?.showWeekTray ?? true,
    showDayTray: persisted?.showDayTray ?? true,
    view: persisted?.view || "month",
    cursorDate: persisted?.cursorDate || toISODate(today()),
    searchQuery: "",
    searchOpen: false,
    modal: persisted?.modal || null,
    pendingCreate: null,
  };
}

class Store {
  constructor() {
    this.userId = null; // null = guest; set via switchUser once authStore knows who's logged in
    this.state = initialState(this.userId);
    this.listeners = new Set();
  }

  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  set(patch, opts = {}) {
    this.state = { ...this.state, ...(typeof patch === "function" ? patch(this.state) : patch) };
    if (!opts.silent) persist(this.state, this.userId);
    this.listeners.forEach((fn) => fn(this.state));
  }

  // Swaps the entire calendar dataset to the given user's (or guest's, if null) own
  // storage — a load, not a mutation, so it bypasses set()/persist() entirely.
  switchUser(userId) {
    this.userId = userId;
    this.state = initialState(userId);
    this.listeners.forEach((fn) => fn(this.state));
  }

  // ---- tasks ----
  // patch: { title, categoryId, colorId, dueDate, startTime, endTime, scheduled, notes }
  // Unscheduled tasks have scheduled:false and empty dueDate/startTime/endTime.
  // colorId references one of that task's category's own colors (see selectors.js).
  // repeat: [] | [{id, freq:"weekly"|"monthly"|"yearly", weekdays:number[], ordinal?, interval}, ...] —
  // an array of independent rules (occurrence matches if ANY rule matches), each
  // anchored on this item's own date field and gated to every Nth unit via `interval`
  // (default 1); see selectors.js for how occurrences/exceptions/doneDates are
  // resolved and expanded, and isRepeating().
  // link/place/people/thingsToBring/topic/todoList are always present regardless of
  // whether currently enabled in Settings' "Extra Fields" — see settingsPanel.js.
  addTask(patch) {
    const task = {
      id: uid("tsk"),
      title: "",
      done: false,
      categoryId: "",
      colorId: "",
      dueDate: "",
      startTime: "",
      endTime: "",
      scheduled: false,
      notes: "",
      rescheduleCount: 0,
      rescheduleHistory: [],
      overdueReschedule: false,
      repeat: [],
      exceptions: {},
      doneDates: [],
      link: "",
      place: "",
      people: "",
      thingsToBring: "",
      topic: "",
      todoList: [],
      // Values for user-defined custom Extra Fields (see src/extraFields.js),
      // keyed by field id — kept separate from the fixed named properties above.
      customFields: {},
      // Fields turned on for just this item, beyond its category's own Extra
      // Fields defaults — see addModal.js's effectiveEnabledFields().
      extraFieldsOverride: [],
      ...patch,
    };
    this.set((s) => ({ tasks: [task, ...s.tasks] }));
    return task;
  }

  // Non-repeating tasks only — see toggleTaskOccurrence for repeating ones.
  toggleTask(id) {
    this.set((s) => ({
      tasks: s.tasks.map((t) => (t.id === id ? { ...t, done: !t.done } : t)),
    }));
  }

  removeTask(id) {
    this.set((s) => ({ tasks: s.tasks.filter((t) => t.id !== id) }));
  }

  // Whole-series edit. Prunes now-unreachable exceptions/doneDates when the pattern
  // or anchor date actually changes — see updateTaskOccurrence for single-occurrence edits.
  updateTask(id, patch) {
    this.set((s) => ({
      tasks: s.tasks.map((t) => (t.id === id ? pruneStaleOccurrenceData({ ...t, ...patch }, "dueDate") : t)),
    }));
  }

  // Single-occurrence edit — `exception` fully replaces (never merges into) any prior
  // exception at this canonical date, so a stale `movedTo` from an earlier drag can be
  // cleared by a later edit that doesn't move the date again.
  updateTaskOccurrence(id, canonicalKey, exception) {
    this.set((s) => ({
      tasks: s.tasks.map((t) => (t.id === id ? { ...t, exceptions: { ...t.exceptions, [canonicalKey]: exception } } : t)),
    }));
  }

  deleteTaskOccurrence(id, canonicalKey) {
    this.updateTaskOccurrence(id, canonicalKey, { deleted: true });
  }

  // Toggles completion for one occurrence of a repeating task; delegates to the plain
  // toggleTask for non-repeating tasks (canonicalKey null) so callers can use one method.
  toggleTaskOccurrence(id, canonicalKey) {
    if (!canonicalKey) return this.toggleTask(id);
    this.set((s) => ({
      tasks: s.tasks.map((t) => {
        if (t.id !== id) return t;
        const doneDates = t.doneDates || [];
        const next = doneDates.includes(canonicalKey) ? doneDates.filter((d) => d !== canonicalKey) : [...doneDates, canonicalKey];
        return { ...t, doneDates: next };
      }),
    }));
  }

  // ---- events ----
  addEvent(evt) {
    const event = {
      id: uid("evt"),
      notes: "",
      colorId: "",
      repeat: [],
      exceptions: {},
      link: "",
      place: "",
      people: "",
      thingsToBring: "",
      topic: "",
      todoList: [],
      customFields: {},
      extraFieldsOverride: [],
      ...evt,
    };
    this.set((s) => ({ events: [...s.events, event] }));
    return event;
  }

  removeEvent(id) {
    this.set((s) => ({ events: s.events.filter((e) => e.id !== id) }));
  }

  updateEvent(id, patch) {
    this.set((s) => ({
      events: s.events.map((e) => (e.id === id ? pruneStaleOccurrenceData({ ...e, ...patch }, "date") : e)),
    }));
  }

  updateEventOccurrence(id, canonicalKey, exception) {
    this.set((s) => ({
      events: s.events.map((e) => (e.id === id ? { ...e, exceptions: { ...e.exceptions, [canonicalKey]: exception } } : e)),
    }));
  }

  deleteEventOccurrence(id, canonicalKey) {
    this.updateEventOccurrence(id, canonicalKey, { deleted: true });
  }

  // ---- special days ----
  // Lightweight all-day markers (birthdays, holidays, weddings, ...) — no
  // startTime/endTime by design. Created via addChooserModal.js's own quick-add
  // step, not the full Task/Event modal. Shares the same repeat-rule engine
  // (repeat/exceptions) as tasks/events — see selectors.js.
  addSpecialDay(patch) {
    const specialDay = {
      id: uid("spd"),
      title: "",
      date: "",
      categoryId: "",
      colorId: "",
      repeat: [],
      exceptions: {},
      notes: "",
      todoList: [],
      customFields: {},
      extraFieldsOverride: [],
      ...patch,
    };
    this.set((s) => ({ specialDays: [...s.specialDays, specialDay] }));
    return specialDay;
  }

  removeSpecialDay(id) {
    this.set((s) => ({ specialDays: s.specialDays.filter((d) => d.id !== id) }));
  }

  updateSpecialDay(id, patch) {
    this.set((s) => ({
      specialDays: s.specialDays.map((d) => (d.id === id ? pruneStaleOccurrenceData({ ...d, ...patch }, "date") : d)),
    }));
  }

  updateSpecialDayOccurrence(id, canonicalKey, exception) {
    this.set((s) => ({
      specialDays: s.specialDays.map((d) => (d.id === id ? { ...d, exceptions: { ...d.exceptions, [canonicalKey]: exception } } : d)),
    }));
  }

  deleteSpecialDayOccurrence(id, canonicalKey) {
    this.updateSpecialDayOccurrence(id, canonicalKey, { deleted: true });
  }

  // ---- to-do items (shared shape across tasks/events/specialDays) ----
  // Toggles one checklist item's done state in place, regardless of which kind of
  // card it lives on — used by the day grid's timed to-do blocks (dayGridView.js)
  // as well as the checklist inside the Add/Edit modal.
  toggleTodoItemDone(itemType, id, todoId) {
    const key = itemType === "task" ? "tasks" : itemType === "event" ? "events" : "specialDays";
    this.set((s) => ({
      [key]: s[key].map((item) =>
        item.id === id
          ? { ...item, todoList: (item.todoList || []).map((t) => (t.id === todoId ? { ...t, done: !t.done } : t)) }
          : item
      ),
    }));
  }

  // ---- custom Extra Field definitions — global, reusable across every category/
  // color/card once created (see src/extraFields.js). Creatable both from Settings
  // and from the small "+" in the Add/Edit modal's own Extra Fields checklist.
  addCustomFieldDef(label) {
    const field = { id: uid("field"), key: uid("field"), label: label.trim() || "Field" };
    this.set((s) => ({ customFieldDefs: [...s.customFieldDefs, field] }));
    return field;
  }

  // ---- view / nav ----
  setView(view) {
    this.set({ view });
  }

  setCursorDate(isoDate) {
    this.set({ cursorDate: isoDate });
  }

  // ---- quick tray hide (clicking the "Week"/"Day" tray label directly, instead of
  // going through Settings' Visibility checkboxes) ----
  setShowWeekTray(visible) {
    this.set({ showWeekTray: visible });
  }

  setShowDayTray(visible) {
    this.set({ showDayTray: visible });
  }

  setCustomDates(isoDates) {
    const unique = [...new Set(isoDates)].sort();
    if (unique.length === 0) return;
    this.set({ customDates: unique });
  }

  // ---- search ----
  setSearchOpen(open) {
    this.set({ searchOpen: open, searchQuery: open ? this.state.searchQuery : "" }, { silent: true });
  }

  setSearchQuery(q) {
    this.set({ searchQuery: q }, { silent: true });
  }

  // ---- modal ----
  // Not silent — persisted so a reload resumes on the same modal (e.g. the Add/Edit
  // form for whatever task/event/date you were on) instead of dumping you back to
  // a bare calendar view.
  openModal(modal) {
    this.set({ modal });
  }

  closeModal() {
    this.set({ modal: null });
  }

  // ---- +Add placement mode: next click/drag on the timeline creates this type ----
  setPendingCreate(pendingCreate) {
    this.set({ pendingCreate }, { silent: true });
  }

  // ---- Settings draft commit ----
  // Categories (with their nested colors and each one's own Extra Fields list) are
  // edited as a draft in the Settings UI and only ever reach the store here,
  // atomically, when the user hits Save. Tray visibility is a plain instant toggle
  // (setShowWeekTray/setShowDayTray below) rather than part of this draft — same as
  // the W/D corner buttons on the calendar itself, so both controls agree. Tasks/
  // events are never touched — if a category or color they reference disappears,
  // they just resolve to a neutral fallback at render time (see selectors.js)
  // instead of being deleted.
  applySettingsDraft({ categories }) {
    this.set({ categories });
  }

  // ---- categories/colors: direct add, for the Add/Edit modal's inline "+ Add"
  // affordances. Settings' own category editing goes through the draft/
  // applySettingsDraft path instead (see settingsPanel.js) — this path is for
  // quick additions made without opening Settings.
  addCategory(name) {
    const category = { id: uid("cat"), name: name.trim() || "Untitled", colors: [makeColor("General", pickUnusedColor([]))], enabledFields: [] };
    this.set((s) => ({ categories: [...s.categories, category] }));
    return category;
  }

  addColorToCategory(categoryId, { name, value }) {
    const color = makeColor(name, value);
    this.set((s) => ({
      categories: s.categories.map((c) => (c.id === categoryId ? { ...c, colors: [...c.colors, color] } : c)),
    }));
    return color;
  }

  // ---- derived ----
  getCategory(id) {
    return this.state.categories.find((c) => c.id === id);
  }
}

export const store = new Store();

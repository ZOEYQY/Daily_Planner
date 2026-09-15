import { buildSeedData, uid } from "./seed.js";
import { toISODate, addDays, today, parseISODate, nthWeekdayOfMonth } from "./dateUtils.js";
import { makeColor, pickUnusedColor } from "./categoryColor.js";
import { matchesPattern, isRepeating } from "./selectors.js";

// Ctrl+Z/Ctrl+Y (see main.js) undo/redo across everything a user actually
// creates or configures — tasks, events, categories, custom fields, the
// Settings toggles. Deliberately excludes pure navigation/UI state (view,
// cursorDate, modal, search, which tray is collapsed, selection) — undoing
// "which month you're looking at" would be disorienting, not useful, the way
// undoing a delete or an edit is.
const UNDOABLE_KEYS = [
  "categories", "tasks", "events", "specialDays", "customFieldDefs", "customDates",
  "showWeekTray", "showDayTray", "showTodoTargetTab", "weekStartsOn", "dayStartHour",
  "todoDeadlineRequired", "todoShowDetail", "todoUrgentThresholdHours", "timePickerStyle",
  "todoDisplayMode", "todoSortMode", "todoOrder", "todoNag",
];
const HISTORY_LIMIT = 50;

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

// A per-occurrence exception snapshots the item's shared metadata (title,
// category, color, notes) alongside its own positional overrides — a drag/resize
// bakes in the master's current values (see occurrenceException in dayGridView.js),
// and a "this occurrence only" edit stores whatever the form held. So a later
// whole-series edit that changes one of those shared fields would leave every
// such occurrence stuck showing the pre-edit value ("换了名字，日历上还是旧名字").
// This pushes a genuinely-changed shared field down into the surviving
// exceptions so "edit all occurrences" really does reach all of them. Only
// fields whose value actually changed are propagated — a series edit that just
// moves the time leaves an individually-renamed occurrence's title alone.
// Positional overrides (startTime/endTime/movedTo/scheduled) are never touched.
const OCCURRENCE_SHARED_FIELDS = ["title", "categoryId", "colorId", "notes"];
function syncExceptionsToSeries(nextItem, prevItem, patch) {
  if (!isRepeating(nextItem) || !nextItem.exceptions) return nextItem;
  const changed = OCCURRENCE_SHARED_FIELDS.filter((k) => k in patch && patch[k] !== prevItem[k]);
  if (changed.length === 0) return nextItem;
  const exceptions = {};
  for (const [key, ex] of Object.entries(nextItem.exceptions)) {
    if (ex && !ex.deleted && OCCURRENCE_SHARED_FIELDS.some((k) => k in ex)) {
      const next = { ...ex };
      for (const k of changed) next[k] = patch[k];
      exceptions[key] = next;
    } else {
      exceptions[key] = ex;
    }
  }
  return { ...nextItem, exceptions };
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

// A dateUtils.js bug (now fixed — see minutesFromMidnight/minutesToHHMM) used to
// let a drag/resize on an untimed item save the literal string "NaN:NaN" as its
// startTime/endTime, which then self-perpetuated: every future drag re-derived
// its math from that same broken value. One-time repair on load for anyone who
// already has one — clearing it to "" is enough to make it display blank
// instead of "NaN:NaN" and (for an event) fall back to the Day tray's
// date-but-no-time state rather than sitting broken on the timeline.
function fixCorruptedTimes(item) {
  if (item.startTime !== "NaN:NaN" && item.endTime !== "NaN:NaN") return item;
  return {
    ...item,
    startTime: item.startTime === "NaN:NaN" ? "" : item.startTime,
    endTime: item.endTime === "NaN:NaN" ? "" : item.endTime,
  };
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
    if (Array.isArray(parsed.tasks)) parsed.tasks = parsed.tasks.map((t) => fixCorruptedTimes(migrateRepeatShape(t, "dueDate")));
    if (Array.isArray(parsed.events)) parsed.events = parsed.events.map((e) => fixCorruptedTimes(migrateRepeatShape(e, "date")));
    parsed.specialDays = Array.isArray(parsed.specialDays) ? parsed.specialDays.map((d) => migrateRepeatShape(d, "date")) : [];
    parsed.customFieldDefs = Array.isArray(parsed.customFieldDefs) ? parsed.customFieldDefs : [];
    return parsed;
  } catch {
    return null;
  }
}

function persist(state, userId) {
  if (!userId) return;
  const {
    categories, tasks, events, specialDays, customFieldDefs, customDates,
    showWeekTray, showDayTray, showTodoTargetTab, weekTrayCollapsed, dayTrayCollapsed,
    weekStartsOn, dayStartHour, todoDeadlineRequired, todoShowDetail, todoUrgentThresholdHours,
    todoDisplayMode, todoSortMode, todoOrder, todoPanelCollapsed, todoPanelDate,
    todoNag, todoNagLastShown,
    timePickerStyle, categoryFilterActive, categoryFilterIds, view, cursorDate, modal,
  } = state;
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
        showTodoTargetTab,
        weekTrayCollapsed,
        dayTrayCollapsed,
        weekStartsOn,
        dayStartHour,
        todoDeadlineRequired,
        todoShowDetail,
        todoUrgentThresholdHours,
        todoDisplayMode,
        todoSortMode,
        todoOrder,
        todoPanelCollapsed,
        todoPanelDate,
        todoNag,
        todoNagLastShown,
        timePickerStyle,
        categoryFilterActive,
        categoryFilterIds,
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
    showTodoTargetTab: persisted?.showTodoTargetTab ?? true,
    // Whether the W/D corner button has hidden the tray for now — distinct from
    // showWeekTray/showDayTray (the master on/off switch) — see dayGridView.js.
    weekTrayCollapsed: persisted?.weekTrayCollapsed ?? false,
    dayTrayCollapsed: persisted?.dayTrayCollapsed ?? false,
    // 0 = Sunday, 1 = Monday — see startOfWeek/orderedWeekdayLabels in dateUtils.js.
    weekStartsOn: persisted?.weekStartsOn ?? 0,
    // Hour (0-23) scrolled to the top of the Week/Day/Custom timeline on first
    // load of that view — see SCROLL_TO_HOUR's use in dayGridView.js. The grid
    // itself always covers all 24 hours; this only picks the default scroll
    // position, so earlier/later events are still just a scroll away.
    dayStartHour: persisted?.dayStartHour ?? 7,
    // Settings for the standalone To-Do quick-add popup (see openTodoQuickAdd
    // in dayGridView.js) — distinct from the full Add/Edit modal's own fields.
    todoDeadlineRequired: persisted?.todoDeadlineRequired ?? false,
    todoShowDetail: persisted?.todoShowDetail ?? true,
    // Hours-before-deadline at which an open to-do's chip switches to its
    // urgent/red styling (see isTodoUrgent in selectors.js) — only applies
    // while the deadline hasn't already passed by a full day (that's the
    // existing "Overdue" styling's job instead, see isOverdue).
    todoUrgentThresholdHours: persisted?.todoUrgentThresholdHours ?? 24,
    // "trays" (default) = to-dos surface in the Week/Day trays, same as always.
    // "panel" = Week view shows a dedicated 5-day-wide To-Do side panel instead
    // (see the todo-side-panel rendering in dayGridView.js) — to-dos stop being
    // duplicated in the trays while it's active. A Settings toggle, so it's
    // undoable like the rest of this section (see UNDOABLE_KEYS above).
    todoDisplayMode: persisted?.todoDisplayMode || "trays",
    // "deadline" (default) sorts the To-Do side panel / tray to-dos by their
    // deadline time; "manual" uses todoOrder instead, letting the user drag
    // to-dos into a hand-picked priority order (drag handles only show in the
    // side panel while this is "manual"). A Settings toggle, undoable.
    todoSortMode: persisted?.todoSortMode || "deadline",
    // Hand-picked to-do priority order (task ids, front = highest priority) —
    // only consulted while todoSortMode is "manual". Ids missing from here
    // (e.g. a to-do created since the last reorder) sort last, keeping their
    // natural order. Undoable alongside todoSortMode.
    todoOrder: persisted?.todoOrder ?? [],
    // Session hide/show for the To-Do side panel itself — its "Hide" (✕) button
    // collapses it to a small right-edge chevron button, which clicks it back
    // open (see todoSidePanelHTML in dayGridView.js). Distinct from todoDisplayMode
    // (the master on/off switch). Persisted but deliberately not undoable, same
    // as weekTrayCollapsed/dayTrayCollapsed below.
    todoPanelCollapsed: persisted?.todoPanelCollapsed ?? false,
    // Which day the To-Do side panel is scoped to — the date column the user
    // last clicked in Week view, defaulting to today. Snapped back into the
    // visible 5-day window on render (see dayGridView.js). A view preference
    // like cursorDate: persisted, not undoable.
    todoPanelDate: persisted?.todoPanelDate || toISODate(today()),
    // "Nag" mode for overdue to-dos (Settings › To-Do). On by default. When on,
    // the To-Do panel floats overdue items to the top worst-first, paints them
    // with an escalating red that gets louder the longer they're late (see
    // overdueSeverity in rescheduleTracking.js, .nag-sev-* in calendar.css),
    // shows a days-late counter on each, a summary line in the panel header, and
    // a once-a-day reminder toast. Undoable like the rest of this section.
    todoNag: persisted?.todoNag ?? true,
    // ISO date the daily nag toast last fired — so it shows at most once per day
    // (see maybeShowTodoNag in main.js). A view preference, not undoable.
    todoNagLastShown: persisted?.todoNagLastShown || "",
    // "native" (browser's own time picker), "text" (type e.g. "2:30 PM"), or
    // "clock" (tap-to-select dial popup) — see timeInput.js, used by every
    // time field in the app (Add/Edit modal's Start/End, the To-Do deadline).
    timePickerStyle: persisted?.timePickerStyle || "native",
    view: persisted?.view || "month",
    cursorDate: persisted?.cursorDate || toISODate(today()),
    searchQuery: "",
    searchOpen: false,
    // categoryFilterActive false = "All": show everything, categoryFilterIds
    // ignored. Active + empty ids = "None": show nothing. Active + some ids =
    // show only items in one of those categories (multi-select — clicking
    // MMU then CLSC shows both). Applies across every view (month chips,
    // week/day timeline, week/day trays) — see matchesCategoryFilter in
    // selectors.js. Persisted (survives reload) like weekStartsOn/dayStartHour,
    // but — like view/cursorDate — deliberately left out of undo/redo: it's
    // what you're currently looking at, not data you created or configured.
    categoryFilterActive: persisted?.categoryFilterActive ?? false,
    categoryFilterIds: persisted?.categoryFilterIds ?? [],
    modal: persisted?.modal || null,
    pendingCreate: null,
    // Click-to-select-first-then-click-to-open on a timeline card (see
    // dayGridView.js) — { kind, id, occurrenceDate } or null. Transient UI
    // state: not persisted, not part of undo/redo.
    selectedItem: null,
  };
}

class Store {
  constructor() {
    this.userId = null; // null = guest; set via switchUser once authStore knows who's logged in
    this.state = initialState(this.userId);
    this.listeners = new Set();
    this.undoStack = [];
    this.redoStack = [];
  }

  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  snapshotUndoable() {
    const snap = {};
    for (const key of UNDOABLE_KEYS) snap[key] = this.state[key];
    return snap;
  }

  set(patch, opts = {}) {
    const resolved = typeof patch === "function" ? patch(this.state) : patch;
    // skipHistory: true is how undo()/redo() themselves apply a snapshot without
    // that application becoming a new undoable step (which would make undo
    // immediately push its own reversal back onto the stack).
    if (!opts.skipHistory && UNDOABLE_KEYS.some((key) => key in resolved)) {
      this.undoStack.push(this.snapshotUndoable());
      if (this.undoStack.length > HISTORY_LIMIT) this.undoStack.shift();
      this.redoStack = [];
    }
    this.state = { ...this.state, ...resolved };
    if (!opts.silent) persist(this.state, this.userId);
    this.listeners.forEach((fn) => fn(this.state));
  }

  canUndo() {
    return this.undoStack.length > 0;
  }

  canRedo() {
    return this.redoStack.length > 0;
  }

  undo() {
    if (this.undoStack.length === 0) return;
    const prev = this.undoStack.pop();
    this.redoStack.push(this.snapshotUndoable());
    this.set(prev, { skipHistory: true });
  }

  redo() {
    if (this.redoStack.length === 0) return;
    const next = this.redoStack.pop();
    this.undoStack.push(this.snapshotUndoable());
    this.set(next, { skipHistory: true });
  }

  // Swaps the entire calendar dataset to the given user's (or guest's, if null) own
  // storage — a load, not a mutation, so it bypasses set()/persist() entirely.
  // History is per-account (undoing across a user switch would silently touch the
  // wrong person's data), so it resets here too.
  switchUser(userId) {
    this.userId = userId;
    this.state = initialState(userId);
    this.undoStack = [];
    this.redoStack = [];
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
      // Plain checkbox goals — see targetListHTML in addModal.js.
      targets: [],
      // Free-text, filled in after the event happens (progress made, things to
      // note, a summary) — see the Record tab in addModal.js.
      recordNotes: "",
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
      tasks: s.tasks.map((t) =>
        t.id === id ? syncExceptionsToSeries(pruneStaleOccurrenceData({ ...t, ...patch }, "dueDate"), t, patch) : t
      ),
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
  // toggleTask for non-repeating tasks. Gated on isRepeating(), not just canonicalKey
  // being present — resolveOccurrence sets occurrenceDate on *any* resolved item
  // (see selectors.js), repeating or not (e.g. getDayUnscheduledTasks's rolled-forward
  // isTodo items), so a truthy canonicalKey alone doesn't mean "this is repeating".
  // Writing to doneDates for a non-repeating task would be silently ignored on
  // read anyway (resolveOccurrence only consults doneDates when isRepeating), making
  // the checkbox look unresponsive — same bug class as the drag/exception fix elsewhere.
  toggleTaskOccurrence(id, canonicalKey) {
    const task = this.state.tasks.find((t) => t.id === id);
    if (!canonicalKey || !task || !isRepeating(task)) return this.toggleTask(id);
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
      targets: [],
      recordNotes: "",
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
      events: s.events.map((e) =>
        e.id === id ? syncExceptionsToSeries(pruneStaleOccurrenceData({ ...e, ...patch }, "date"), e, patch) : e
      ),
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

  // Session hide/show via the calendar's own W/D corner button — persisted, so
  // it survives a reload, but distinct from showWeekTray/showDayTray above
  // (the master switch: off means no button and no tray at all).
  setWeekTrayCollapsed(collapsed) {
    this.set({ weekTrayCollapsed: collapsed });
  }

  setDayTrayCollapsed(collapsed) {
    this.set({ dayTrayCollapsed: collapsed });
  }

  // Master on/off switch for the Add/Edit modal's To-Do & Target tab — off, and
  // the tab (and its "+" affordances) just don't render, same as the tray
  // features above. Existing todoList/targets data on any item is untouched.
  setShowTodoTargetTab(visible) {
    this.set({ showTodoTargetTab: visible });
  }

  setTodoDeadlineRequired(required) {
    this.set({ todoDeadlineRequired: required });
  }

  setTodoShowDetail(show) {
    this.set({ todoShowDetail: show });
  }

  setTodoUrgentThresholdHours(hours) {
    this.set({ todoUrgentThresholdHours: hours });
  }

  setTodoDisplayMode(mode) {
    this.set({ todoDisplayMode: mode });
  }

  // On/off for overdue-to-do nagging — see todoNag in initialState().
  setTodoNag(on) {
    this.set({ todoNag: on });
  }

  // Records that today's nag reminder has been shown (main.js) so it fires at
  // most once a day. Not in UNDOABLE_KEYS, so it persists without touching undo
  // history; the one extra render it triggers is a no-op for the nag check.
  markTodoNagShown(isoDate) {
    this.set({ todoNagLastShown: isoDate });
  }

  // "deadline" | "manual" — see todoSortMode in initialState().
  setTodoSortMode(mode) {
    this.set({ todoSortMode: mode });
  }

  // Full replacement of the hand-picked to-do order (task ids). Called by the
  // side panel's drag-to-reorder — see todoOrder in initialState(). Dragging a
  // to-do into a new position *is* the gesture for "I want manual order now",
  // so this flips todoSortMode to "manual" too (the Settings toggle switches
  // back to "deadline", which ignores todoOrder without discarding it).
  setTodoOrder(ids) {
    this.set({ todoOrder: [...ids], todoSortMode: "manual" });
  }

  // Session hide/show for the To-Do side panel — see todoPanelCollapsed's
  // comment in initialState() for how this differs from setTodoDisplayMode.
  setTodoPanelCollapsed(collapsed) {
    this.set({ todoPanelCollapsed: collapsed });
  }

  // Which day the day-scoped To-Do panel shows — set by clicking a date
  // column header in Week view. A view preference, not undoable (like cursorDate).
  setTodoPanelDate(isoDate) {
    this.set({ todoPanelDate: isoDate });
  }

  setTimePickerStyle(style) {
    this.set({ timePickerStyle: style });
  }

  // 0 = Sunday, 1 = Monday — see startOfWeek/orderedWeekdayLabels in dateUtils.js.
  setWeekStartsOn(day) {
    this.set({ weekStartsOn: day });
  }

  // 0-23 — see dayStartHour's use as the default scroll position in dayGridView.js.
  setDayStartHour(hour) {
    this.set({ dayStartHour: hour });
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

  // ---- category filter ----
  // "All" — turns the filter off entirely; categoryFilterIds is left as-is
  // (irrelevant while inactive) rather than cleared, so toggling a category
  // back on afterward doesn't lose whatever selection was built up before.
  setCategoryFilterAll() {
    this.set({ categoryFilterActive: false });
  }

  // "None" — filter on, nothing selected (distinct from "All": every view
  // shows zero items instead of everything).
  setCategoryFilterNone() {
    this.set({ categoryFilterActive: true, categoryFilterIds: [] });
  }

  // Toggles one category in/out of the active set. "All" shows every pill
  // active (see calendarHeader.js), so clicking one there means "everything
  // is implicitly selected — deselect just this one", not "start a fresh
  // selection with only this one" — matching a legend-style toggle (click to
  // hide just that category, the rest stay showing) rather than a radio pick.
  toggleCategoryFilterId(id) {
    const allIds = this.state.categories.filter((c) => !c.archived).map((c) => c.id);
    const current = this.state.categoryFilterActive ? this.state.categoryFilterIds : allIds;
    const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
    this.set({ categoryFilterActive: true, categoryFilterIds: next });
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

  // { kind, id, occurrenceDate } or null — see dayGridView.js's click-to-select.
  setSelectedItem(selectedItem) {
    this.set({ selectedItem }, { silent: true });
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

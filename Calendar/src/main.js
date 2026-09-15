import { store } from "./state.js";
import { authStore } from "./authStore.js";
import { renderTopbar } from "./render/topbar.js";
import { renderCalendarHeader } from "./render/calendarHeader.js";
import { renderMonthView } from "./render/monthView.js";
import { renderDayGridView, getHoverTarget } from "./render/dayGridView.js";
import { renderHabitsView } from "./render/habitsView.js";
import { renderAddModal, closeAddOrEditModal } from "./render/addModal.js";
import { renderSettingsPanel, resetSettingsDraft } from "./render/settingsPanel.js";
import { renderCustomizeModal } from "./render/customizeModal.js";
import { renderAddChooserModal } from "./render/addChooserModal.js";
import { renderAuthModal } from "./render/authViews.js";
import { renderProfileModal } from "./render/profileModal.js";
import { showToast } from "./render/notify.js";
import { resolveOccurrence, isRepeating, getDayTodos } from "./selectors.js";
import { daysOverdue } from "./rescheduleTracking.js";
import { minutesFromMidnight, minutesToHHMM, toISODate, today } from "./dateUtils.js";
import { esc } from "./utils.js";

const els = {
  topbar: document.getElementById("topbar"),
  calHeader: document.getElementById("calendar-header"),
  calBody: document.getElementById("calendar-body"),
  modalRoot: document.getElementById("modal-root"),
  placementBanner: document.getElementById("placement-banner"),
};

const actions = {
  setSearchOpen: (v) => store.setSearchOpen(v),
  setSearchQuery: (v) => store.setSearchQuery(v),
  setCategoryFilterAll: () => store.setCategoryFilterAll(),
  setCategoryFilterNone: () => store.setCategoryFilterNone(),
  toggleCategoryFilterId: (id) => store.toggleCategoryFilterId(id),
  openModal: (m) => store.openModal(m),
  closeModal: () => store.closeModal(),
  setView: (v) => store.setView(v),
  setCursorDate: (d) => store.setCursorDate(d),
  toggleTask: (id) => store.toggleTask(id),
  removeTask: (id) => store.removeTask(id),
  addTask: (patch) => store.addTask(patch),
  updateTask: (id, patch) => store.updateTask(id, patch),
  updateTaskOccurrence: (id, canonicalKey, exception) => store.updateTaskOccurrence(id, canonicalKey, exception),
  deleteTaskOccurrence: (id, canonicalKey) => store.deleteTaskOccurrence(id, canonicalKey),
  toggleTaskOccurrence: (id, canonicalKey) => store.toggleTaskOccurrence(id, canonicalKey),
  addEvent: (evt) => store.addEvent(evt),
  removeEvent: (id) => store.removeEvent(id),
  updateEvent: (id, patch) => store.updateEvent(id, patch),
  updateEventOccurrence: (id, canonicalKey, exception) => store.updateEventOccurrence(id, canonicalKey, exception),
  deleteEventOccurrence: (id, canonicalKey) => store.deleteEventOccurrence(id, canonicalKey),
  addSpecialDay: (patch) => store.addSpecialDay(patch),
  removeSpecialDay: (id) => store.removeSpecialDay(id),
  updateSpecialDay: (id, patch) => store.updateSpecialDay(id, patch),
  updateSpecialDayOccurrence: (id, canonicalKey, exception) => store.updateSpecialDayOccurrence(id, canonicalKey, exception),
  deleteSpecialDayOccurrence: (id, canonicalKey) => store.deleteSpecialDayOccurrence(id, canonicalKey),
  toggleTodoItemDone: (itemType, id, todoId) => store.toggleTodoItemDone(itemType, id, todoId),
  addCustomFieldDef: (label) => store.addCustomFieldDef(label),
  applySettingsDraft: (draft) => store.applySettingsDraft(draft),
  addCategory: (name) => store.addCategory(name),
  addColorToCategory: (categoryId, color) => store.addColorToCategory(categoryId, color),
  setShowWeekTray: (v) => store.setShowWeekTray(v),
  setShowDayTray: (v) => store.setShowDayTray(v),
  setWeekTrayCollapsed: (v) => store.setWeekTrayCollapsed(v),
  setDayTrayCollapsed: (v) => store.setDayTrayCollapsed(v),
  setShowTodoTargetTab: (v) => store.setShowTodoTargetTab(v),
  setWeekStartsOn: (v) => store.setWeekStartsOn(v),
  setDayStartHour: (v) => store.setDayStartHour(v),
  setTodoDeadlineRequired: (v) => store.setTodoDeadlineRequired(v),
  setTodoShowDetail: (v) => store.setTodoShowDetail(v),
  setTodoUrgentThresholdHours: (v) => store.setTodoUrgentThresholdHours(v),
  setTodoDisplayMode: (v) => store.setTodoDisplayMode(v),
  setTodoNag: (v) => store.setTodoNag(v),
  setTodoSortMode: (v) => store.setTodoSortMode(v),
  setTodoOrder: (ids) => store.setTodoOrder(ids),
  setTodoPanelCollapsed: (v) => store.setTodoPanelCollapsed(v),
  setTodoPanelDate: (v) => store.setTodoPanelDate(v),
  setTimePickerStyle: (v) => store.setTimePickerStyle(v),
  setCustomDates: (dates) => store.setCustomDates(dates),
  setPendingCreate: (v) => store.setPendingCreate(v),
  setSelectedItem: (item) => store.setSelectedItem(item),
};

// Mock, front-end-only auth — see src/authStore.js. Kept as a separate actions
// object (rather than folded into `actions`) since it wraps a completely separate
// store with no calendar-data concerns of its own.
const authActions = {
  signUp: (fields) => authStore.signUp(fields),
  verifyEmail: (userId, code) => authStore.verifyEmail(userId, code),
  resendCode: (userId) => authStore.resendCode(userId),
  logIn: (fields) => authStore.logIn(fields),
  logOut: () => authStore.logOut(),
  updateProfile: (userId, patch) => authStore.updateProfile(userId, patch),
};

function render(state) {
  const currentUser = authStore.getCurrentUser();
  renderTopbar(els.topbar, state, actions, currentUser);
  renderCalendarHeader(els.calHeader, state, actions);

  if (state.view === "habits") {
    renderHabitsView(els.calBody, state, actions);
  } else if (state.view === "month") {
    renderMonthView(els.calBody, state, actions, currentUser);
  } else {
    renderDayGridView(els.calBody, state, actions, currentUser);
  }

  if (state.modal?.type === "add" || state.modal?.type === "edit") {
    renderAddModal(els.modalRoot, state, actions);
  } else if (state.modal?.type === "settings") {
    renderSettingsPanel(els.modalRoot, state, actions);
  } else if (state.modal?.type === "customize") {
    renderCustomizeModal(els.modalRoot, state, actions);
  } else if (state.modal?.type === "add-chooser") {
    renderAddChooserModal(els.modalRoot, state, actions);
  } else if (state.modal?.type === "auth") {
    renderAuthModal(els.modalRoot, state, authStore.state, actions, authActions);
  } else if (state.modal?.type === "profile") {
    renderProfileModal(els.modalRoot, authStore.state, authActions, actions);
  } else {
    els.modalRoot.innerHTML = "";
  }

  if (state.modal?.type !== "settings") {
    resetSettingsDraft();
  }

  if (state.pendingCreate && state.view !== "month") {
    const label = state.pendingCreate.type === "task" ? "Task" : "Event";
    els.placementBanner.innerHTML = `<span>Click or drag on the timeline to place your ${label}</span><button id="placement-cancel">Cancel</button>`;
    els.placementBanner.style.display = "flex";
    els.placementBanner.querySelector("#placement-cancel").addEventListener("click", () => actions.setPendingCreate(null));
  } else if (state.pendingCreate && state.view === "month") {
    els.placementBanner.innerHTML = `<span>Switch to Week, Day, or Custom view to place your ${state.pendingCreate.type === "task" ? "Task" : "Event"}</span><button id="placement-cancel">Cancel</button>`;
    els.placementBanner.style.display = "flex";
    els.placementBanner.querySelector("#placement-cancel").addEventListener("click", () => actions.setPendingCreate(null));
  } else {
    els.placementBanner.style.display = "none";
  }

  maybeShowTodoNag(state);
}

// Once-a-day reminder of the to-dos you're behind on (Settings › To-Do › Nag).
// Called from render(); self-limits via nagPending + the persisted
// todoNagLastShown stamp so it fires at most once per calendar day. The actual
// toast + stamp are deferred to a microtask so they never re-enter render().
let nagPending = true;
function maybeShowTodoNag(state) {
  if (!nagPending || state.todoNag === false) return;
  const todayISO = toISODate(today());
  if (state.todoNagLastShown === todayISO) {
    nagPending = false;
    return;
  }
  nagPending = false;

  const late = getDayTodos(state, todayISO)
    .map((t) => ({ t, d: daysOverdue(t, todayISO) }))
    .filter((x) => x.d > 0)
    .sort((a, b) => b.d - a.d);

  queueMicrotask(() => {
    store.markTodoNagShown(todayISO);
    if (!late.length) return;
    const worst = late[0];
    const more = late.length - 1;
    const msg =
      `You're behind on ${late.length} to-do${late.length > 1 ? "s" : ""}. ` +
      `Oldest: “${esc(worst.t.title)}” — ${worst.d}d late` +
      (more > 0 ? ` · +${more} more` : "");
    showToast(msg, { variant: "danger" });
  });
}

// ---- Selected-card clipboard (Ctrl+C/Ctrl+V/Delete) — see dayGridView.js's
// click-to-select-first-then-click-to-open and getHoverTarget. Held in memory
// only, not the store: copying isn't itself an undoable action, only what you
// go on to do with it (paste, delete) is.
let clipboardItem = null; // { kind, data } | null

function selectedMaster() {
  const sel = store.state.selectedItem;
  if (!sel) return null;
  const list = sel.kind === "task" ? store.state.tasks : store.state.events;
  const master = list.find((x) => x.id === sel.id);
  return master ? { master, sel } : null;
}

function resolvedSelectedItem() {
  const found = selectedMaster();
  if (!found) return null;
  const { master, sel } = found;
  const dateField = sel.kind === "task" ? "dueDate" : "date";
  return sel.occurrenceDate ? resolveOccurrence(master, sel.occurrenceDate, dateField) : master;
}

function copySelected() {
  const item = resolvedSelectedItem();
  if (!item) return;
  const kind = store.state.selectedItem.kind;
  // Strip identity/series/completion-tracking fields — a paste is always a
  // fresh, non-repeating, not-yet-done copy, same rule the Duplicate button in
  // the Add/Edit modal follows.
  const { id, exceptions, repeat, occurrenceDate, isRecurring, done, doneDates, rescheduleCount, rescheduleHistory, overdueReschedule, ...rest } =
    structuredClone(item);
  clipboardItem = { kind, data: rest };
  showToast(`${kind === "task" ? "Task" : "Event"} copied`);
}

function pasteAtHover() {
  if (!clipboardItem) return;
  const target = getHoverTarget();
  if (!target) {
    showToast("Hover over the calendar to choose where to paste", { variant: "danger" });
    return;
  }
  const { kind, data } = clipboardItem;
  const duration = Math.max(15, minutesFromMidnight(data.endTime || "10:00") - minutesFromMidnight(data.startTime || "09:00"));
  const startTime = minutesToHHMM(target.startMin);
  const endTime = minutesToHHMM(Math.min(24 * 60, target.startMin + duration));
  const patch = { ...data, repeat: [], startTime, endTime };
  const created =
    kind === "task"
      ? store.addTask({ ...patch, dueDate: target.date, scheduled: true })
      : store.addEvent({ ...patch, date: target.date });
  store.setSelectedItem({ kind, id: created.id, occurrenceDate: null });
  showToast(`${kind === "task" ? "Task" : "Event"} pasted`);
}

// A selected occurrence of a repeating item deletes just that occurrence (what
// you clicked); anything else — a plain item, or the series itself with no
// specific occurrence selected — deletes outright. No confirmation dialog, the
// way the Delete button in the modal has one: Ctrl+Z is the safety net instead.
function deleteSelected() {
  const found = selectedMaster();
  if (!found) return;
  const { master, sel } = found;
  if (sel.occurrenceDate && isRepeating(master)) {
    if (sel.kind === "task") store.deleteTaskOccurrence(sel.id, sel.occurrenceDate);
    else store.deleteEventOccurrence(sel.id, sel.occurrenceDate);
  } else if (sel.kind === "task") {
    store.removeTask(sel.id);
  } else {
    store.removeEvent(sel.id);
  }
  store.setSelectedItem(null);
  showToast(`${sel.kind === "task" ? "Task" : "Event"} deleted`, { variant: "danger" });
}

function isEditableFocus() {
  const el = document.activeElement;
  if (!el) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (store.state.modal) closeAddOrEditModal(store.state, actions);
    else if (store.state.pendingCreate) store.setPendingCreate(null);
    else if (store.state.selectedItem) store.setSelectedItem(null);
    return;
  }

  const ctrlOrCmd = e.ctrlKey || e.metaKey;

  // Undo/redo — outside text fields only, so native browser text-undo inside an
  // input/textarea isn't hijacked by the app-level history. Works regardless of
  // whether a modal happens to be open, per "the whole app should be undoable".
  if (ctrlOrCmd && !isEditableFocus()) {
    const key = e.key.toLowerCase();
    if (key === "z" && !e.shiftKey) {
      e.preventDefault();
      store.undo();
      return;
    }
    if (key === "y" || (key === "z" && e.shiftKey)) {
      e.preventDefault();
      store.redo();
      return;
    }
  }

  // Card copy/paste/delete — selection only exists on the bare calendar view,
  // so these are gated on no modal being open, on top of the same
  // not-in-a-text-field guard undo/redo uses.
  if (store.state.modal || isEditableFocus()) return;

  if (ctrlOrCmd && e.key.toLowerCase() === "c") {
    if (store.state.selectedItem) {
      e.preventDefault();
      copySelected();
    }
  } else if (ctrlOrCmd && e.key.toLowerCase() === "v") {
    if (clipboardItem) {
      e.preventDefault();
      pasteAtHover();
    }
  } else if (e.key === "Delete" || e.key === "Backspace") {
    if (store.state.selectedItem) {
      e.preventDefault();
      deleteSelected();
    }
  }
});

// A returning logged-in user's data should load immediately, not start on guest
// data — this must happen before the subscriptions below are wired up.
if (authStore.state.currentUserId) store.switchUser(authStore.state.currentUserId);

store.subscribe(render);
authStore.subscribe((authState) => {
  if (authState.currentUserId !== store.userId) {
    nagPending = true; // re-check the overdue nag against the new account's to-dos
    store.switchUser(authState.currentUserId); // triggers render via store's own listeners
  } else {
    render(store.state); // same user — e.g. rename, resend code — nothing for `store` to react to
  }
});
render(store.state);

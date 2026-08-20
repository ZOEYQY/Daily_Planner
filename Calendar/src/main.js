import { store } from "./state.js";
import { authStore } from "./authStore.js";
import { renderTopbar } from "./render/topbar.js";
import { renderCalendarHeader } from "./render/calendarHeader.js";
import { renderMonthView } from "./render/monthView.js";
import { renderDayGridView } from "./render/dayGridView.js";
import { renderAddModal, closeAddOrEditModal } from "./render/addModal.js";
import { renderSettingsPanel, resetSettingsDraft } from "./render/settingsPanel.js";
import { renderCustomizeModal } from "./render/customizeModal.js";
import { renderAddChooserModal } from "./render/addChooserModal.js";
import { renderAuthModal } from "./render/authViews.js";
import { renderProfileModal } from "./render/profileModal.js";

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
  setCustomDates: (dates) => store.setCustomDates(dates),
  setPendingCreate: (v) => store.setPendingCreate(v),
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

  if (state.view === "month") {
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
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (store.state.modal) closeAddOrEditModal(store.state, actions);
    else if (store.state.pendingCreate) store.setPendingCreate(null);
  }
});

// A returning logged-in user's data should load immediately, not start on guest
// data — this must happen before the subscriptions below are wired up.
if (authStore.state.currentUserId) store.switchUser(authStore.state.currentUserId);

store.subscribe(render);
authStore.subscribe((authState) => {
  if (authState.currentUserId !== store.userId) {
    store.switchUser(authState.currentUserId); // triggers render via store's own listeners
  } else {
    render(store.state); // same user — e.g. rename, resend code — nothing for `store` to react to
  }
});
render(store.state);

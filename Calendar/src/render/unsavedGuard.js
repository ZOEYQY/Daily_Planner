import { showConfirm } from "./notify.js";

// Warns before an accidental close (clicking outside the form, or Escape)
// throws away edits that were never saved. When a form modal first renders,
// main.js records a fingerprint of everything the user can change in it; on
// an accidental close the fingerprint is taken again and compared. Only the
// Add/Edit and Add-chooser forms are tracked (see TRACKED_MODALS).

const TRACKED_MODALS = new Set(["add", "edit", "add-chooser"]);

let baselineModal = null; // the state.modal object the baseline belongs to
let baseline = null;

function modalRoot() {
  return document.getElementById("modal-root");
}

// Everything editable in the open modal: field values, checkboxes, picked
// pills/swatches/weekdays, repeat-rule chips and checklist rows. Tabs and
// expand/collapse toggles are left out, since switching them changes nothing.
function formFingerprint(root) {
  const panel = root?.querySelector(".modal-panel");
  if (!panel) return null;
  const parts = [];
  panel.querySelectorAll("input, textarea, select").forEach((el, i) => {
    const name = el.id || el.dataset.fieldKey || el.name || `#${i}`;
    const value = el.type === "checkbox" || el.type === "radio" ? String(el.checked) : el.value;
    parts.push(`${name}=${value}`);
  });
  panel.querySelectorAll(".selected, .is-selected").forEach((el) => {
    if (el.closest(".add-modal-tab")) return;
    parts.push(`sel:${el.dataset.id || el.dataset.key || el.dataset.colorId || el.dataset.day || el.textContent.trim()}`);
  });
  panel.querySelectorAll(".repeat-rule-chip, .todo-item-row, .target-item-row").forEach((el) => {
    parts.push(`row:${el.textContent.trim()}`);
  });
  return parts.join("\n");
}

// Called after every modal render. A new modal (a different state.modal
// object) gets a fresh baseline; re-renders of the same modal keep the first
// one, so text restored after a re-render still counts as an edit.
export function trackModalBaseline(modal) {
  if (!modal || !TRACKED_MODALS.has(modal.type)) {
    baselineModal = null;
    baseline = null;
    return;
  }
  if (modal === baselineModal) return;
  baselineModal = modal;
  baseline = formFingerprint(modalRoot());
}

export function hasUnsavedChanges() {
  if (baseline === null) return false;
  const now = formFingerprint(modalRoot());
  return now !== null && now !== baseline;
}

// Resolves true when it's fine to close: nothing changed, or the user chose
// to discard their changes.
export function confirmDiscardIfUnsaved() {
  if (!hasUnsavedChanges()) return Promise.resolve(true);
  return showConfirm({
    title: "Discard unsaved changes?",
    message: "You haven't saved yet. Closing now will lose what you entered.",
    confirmLabel: "Discard",
    cancelLabel: "Keep editing",
    danger: true,
  });
}

import { showChoice } from "./notify.js";
import { isRepeating } from "../selectors.js";

// Asked once, when a recurring occurrence is clicked to open it — the answer governs
// the whole modal session (both Save and Delete respect state.modal.occurrenceScope),
// so there's no second prompt inside the modal itself. Drags never go through here
// (see dayGridView.js) — only explicit clicks-to-edit ask, matching how direct-
// manipulation drags are treated everywhere else in the app.
export function promptOccurrenceScope(item) {
  return showChoice({
    title: `"${item.title}" repeats`,
    message: "What should this apply to?",
    choices: [
      { label: "This occurrence only", value: "occurrence" },
      { label: "All occurrences", value: "series" },
    ],
  });
}

// Special Days are created/edited through their own lightweight quick-add step
// (addChooserModal.js) rather than the full Task/Event modal — see state.js.
function openEditModal(item, itemType, extra, actions) {
  if (itemType === "specialDay") {
    actions.openModal({ type: "add-chooser", step: "specialDay", id: item.id, ...extra });
  } else {
    actions.openModal({ type: "edit", itemType, id: item.id, ...extra });
  }
}

// Opens the Add/Edit modal for a clicked occurrence, asking this-vs-series first when
// the item actually repeats; non-repeating items open exactly as before, no prompt.
export function handleOccurrenceClick(item, itemType, occurrenceDate, actions) {
  if (!isRepeating(item)) {
    openEditModal(item, itemType, {}, actions);
    return;
  }
  promptOccurrenceScope(item).then((scope) => {
    if (!scope) return;
    openEditModal(item, itemType, { occurrenceDate, occurrenceScope: scope }, actions);
  });
}

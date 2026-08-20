import { icons } from "../icons.js";
import { toISODate, addDays, parseISODate } from "../dateUtils.js";

let draftDates = null;

function ensureDraft(state) {
  if (!draftDates) draftDates = [...state.customDates];
  return draftDates;
}

export function renderCustomizeModal(root, state, actions) {
  const dates = ensureDraft(state);

  root.innerHTML = `
    <div class="modal-overlay" id="overlay">
      <div class="modal-panel" role="dialog" aria-modal="true">
        <div class="modal-header">
          <h2>Customize Days</h2>
          <button class="modal-close" id="close-btn">${icons.close}</button>
        </div>
        <div class="modal-body">
          <p style="font-size:12.5px; color:var(--color-ink-secondary); line-height:1.5; margin:-4px 0 0;">
            Pick any days to show side by side — consecutive for a short week, or spread apart to compare (e.g. this Monday vs. next Monday).
          </p>
          <div class="custom-date-list" id="date-list">
            ${dates.map((d, i) => dateRow(d, i, dates.length)).join("")}
          </div>
          <button type="button" class="suggest-toggle-btn" id="add-date-btn">${icons.plusSmall}<span>Add another day</span></button>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" id="cancel-btn">Cancel</button>
          <button class="btn btn-primary" id="save-btn">Save</button>
        </div>
      </div>
    </div>
  `;

  root.querySelector("#overlay").addEventListener("click", (e) => {
    if (e.target.id === "overlay") closeWithoutSaving(actions);
  });
  root.querySelector("#close-btn").addEventListener("click", () => closeWithoutSaving(actions));
  root.querySelector("#cancel-btn").addEventListener("click", () => closeWithoutSaving(actions));

  root.querySelectorAll(".custom-date-row input[type='date']").forEach((input, i) => {
    input.addEventListener("change", (e) => {
      if (e.target.value) draftDates[i] = e.target.value;
      renderCustomizeModal(root, state, actions);
    });
  });

  root.querySelectorAll(".custom-date-row .remove-btn").forEach((btn, i) => {
    btn.addEventListener("click", () => {
      if (draftDates.length <= 1) return;
      draftDates.splice(i, 1);
      renderCustomizeModal(root, state, actions);
    });
  });

  root.querySelector("#add-date-btn").addEventListener("click", () => {
    if (draftDates.length >= 7) return;
    const last = parseISODate(draftDates[draftDates.length - 1]);
    draftDates.push(toISODate(addDays(last, 1)));
    renderCustomizeModal(root, state, actions);
  });

  root.querySelector("#save-btn").addEventListener("click", () => {
    const toSave = [...draftDates];
    draftDates = null;
    actions.setCustomDates(toSave);
    actions.closeModal();
  });
}

function closeWithoutSaving(actions) {
  draftDates = null;
  actions.closeModal();
}

function dateRow(iso, index, total) {
  return `
    <div class="custom-date-row">
      <input type="date" value="${iso}" />
      <button type="button" class="remove-btn" aria-label="Remove day" ${total <= 1 ? "disabled" : ""}>${icons.close}</button>
    </div>
  `;
}

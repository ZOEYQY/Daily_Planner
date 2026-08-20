import { icons } from "../icons.js";
import { esc } from "../utils.js";
import { toISODate, today } from "../dateUtils.js";
import { resolveOccurrence } from "../selectors.js";
import { categoryById, colorSwatchesHTML } from "./addModal.js";
import { openAddCategoryPopup, openAddColorPopup } from "./categoryColorPopups.js";
import { createRepeatRuleUI } from "./repeatRuleUI.js";
import { showConfirm, showToast } from "./notify.js";

function wireClose(root, actions) {
  root.querySelector("#overlay").addEventListener("click", (e) => {
    if (e.target.id === "overlay") actions.closeModal();
  });
  root.querySelector("#close-btn").addEventListener("click", () => actions.closeModal());
}

export function renderAddChooserModal(root, state, actions) {
  const step = state.modal?.step || "type";

  if (step === "type") {
    root.innerHTML = `
      <div class="modal-overlay" id="overlay">
        <div class="modal-panel" role="dialog" aria-modal="true" style="max-width:380px;">
          <div class="modal-header">
            <h2>Add</h2>
            <button class="modal-close" id="close-btn">${icons.close}</button>
          </div>
          <div class="modal-body">
            <div class="chooser-grid">
              <button type="button" class="chooser-option" id="choose-event">
                <span class="chooser-option-title">Event</span>
                <span class="chooser-option-desc">Already arranged — drag on the calendar to place it</span>
              </button>
              <button type="button" class="chooser-option" id="choose-task">
                <span class="chooser-option-title">Task</span>
                <span class="chooser-option-desc">Something to get done — schedule it now or leave it unscheduled</span>
              </button>
              <button type="button" class="chooser-option" id="choose-special-day">
                <span class="chooser-option-title">Special Day</span>
                <span class="chooser-option-desc">A day worth marking — birthday, holiday, open house, wedding…</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    `;
    wireClose(root, actions);
    root.querySelector("#choose-event").addEventListener("click", () => {
      actions.closeModal();
      actions.setPendingCreate({ type: "event" });
    });
    root.querySelector("#choose-task").addEventListener("click", () => {
      actions.openModal({ type: "add-chooser", step: "task" });
    });
    root.querySelector("#choose-special-day").addEventListener("click", () => {
      actions.openModal({ type: "add-chooser", step: "specialDay" });
    });
    return;
  }

  if (step === "specialDay") {
    renderSpecialDayStep(root, state, actions);
    return;
  }

  const defaultCategoryId = state.categories.find((c) => !c.archived)?.id || state.categories[0]?.id || "";
  const defaultCategory = categoryById(state, defaultCategoryId);
  root.innerHTML = `
    <div class="modal-overlay" id="overlay">
      <div class="modal-panel" role="dialog" aria-modal="true" style="max-width:400px;">
        <div class="modal-header">
          <h2>Add Task</h2>
          <button class="modal-close" id="close-btn">${icons.close}</button>
        </div>
        <div class="modal-body">
          <div class="field">
            <label>Title</label>
            <input type="text" id="f-title" placeholder="e.g. Finish reading" />
          </div>
          <div class="field">
            <label>Category</label>
            <div class="category-select" id="f-category">
              ${state.categories
                .filter((c) => !c.archived || c.id === defaultCategoryId)
                .map(
                  (c) => `
                <button type="button" class="category-pill" data-id="${c.id}">${esc(c.name)}</button>`
                )
                .join("")}
              <button type="button" class="category-pill pill-add" id="f-add-category-btn">${icons.plusSmall}<span>Add Category</span></button>
            </div>
          </div>
          <div class="field">
            <label>Color</label>
            <div class="named-color-list" id="f-color">
              ${colorSwatchesHTML(defaultCategory, defaultCategory?.colors.find((c) => !c.archived)?.id || defaultCategory?.colors[0]?.id || "")}
            </div>
          </div>
          <p style="font-size:12px; color:var(--color-muted); line-height:1.5; margin:0;">
            "Add to Unscheduled" keeps it in the tray with no time yet. "Place on Timeline" lets you drag it straight onto the calendar.
          </p>
        </div>
        <div class="modal-footer">
          <button class="btn btn-ghost" id="back-btn">Back</button>
          <div class="modal-footer-spacer"></div>
          <button class="btn btn-secondary" id="add-unscheduled-btn">Add to Unscheduled</button>
          <button class="btn btn-primary" id="place-btn">Place on Timeline</button>
        </div>
      </div>
    </div>
  `;
  wireClose(root, actions);
  root.querySelector("#back-btn").addEventListener("click", () => actions.openModal({ type: "add-chooser", step: "type" }));

  let selectedCategoryId = defaultCategoryId;
  let selectedColorId = defaultCategory?.colors.find((c) => !c.archived)?.id || defaultCategory?.colors[0]?.id || "";
  const colorList = root.querySelector("#f-color");

  const snapshotTitle = () => root.querySelector("#f-title")?.value || "";
  const restoreTitle = (title) => {
    const el = root.querySelector("#f-title");
    if (el) el.value = title;
  };

  const wireColorSwatches = () => {
    colorList.querySelectorAll(".named-color-swatch[data-color-id]").forEach((sw) => {
      sw.addEventListener("click", () => {
        selectedColorId = sw.dataset.colorId;
        colorList.querySelectorAll(".named-color-swatch[data-color-id]").forEach((s) => s.classList.toggle("selected", s === sw));
      });
    });

    colorList.querySelector("#f-add-color-btn").addEventListener("click", () => {
      const category = categoryById(state, selectedCategoryId);
      if (!category) return;
      const title = snapshotTitle();
      openAddColorPopup({
        actions,
        category,
        onAdded: (color) => {
          restoreTitle(title);
          root.querySelector(`.category-pill[data-id="${selectedCategoryId}"]`)?.click();
          root.querySelector(`.named-color-swatch[data-color-id="${color.id}"]`)?.click();
        },
      });
    });
  };
  wireColorSwatches();

  const syncSelection = () => {
    root.querySelectorAll(".category-pill[data-id]").forEach((pill) => {
      pill.classList.toggle("selected", pill.dataset.id === selectedCategoryId);
    });
  };
  syncSelection();
  root.querySelectorAll(".category-pill[data-id]").forEach((pill) => {
    pill.addEventListener("click", () => {
      selectedCategoryId = pill.dataset.id;
      syncSelection();
      const cat = categoryById(state, selectedCategoryId);
      selectedColorId = cat?.colors.find((c) => !c.archived)?.id || cat?.colors[0]?.id || "";
      colorList.innerHTML = colorSwatchesHTML(cat, selectedColorId);
      wireColorSwatches();
    });
  });

  root.querySelector("#f-add-category-btn").addEventListener("click", () => {
    const title = snapshotTitle();
    openAddCategoryPopup({
      actions,
      onAdded: (category) => {
        restoreTitle(title);
        root.querySelector(`.category-pill[data-id="${category.id}"]`)?.click();
      },
    });
  });

  root.querySelector("#add-unscheduled-btn").addEventListener("click", () => {
    const title = root.querySelector("#f-title").value.trim();
    if (!title) {
      root.querySelector("#f-title").focus();
      return;
    }
    actions.addTask({ title, categoryId: selectedCategoryId, colorId: selectedColorId, scheduled: false });
    actions.closeModal();
  });

  root.querySelector("#place-btn").addEventListener("click", () => {
    actions.closeModal();
    actions.setPendingCreate({ type: "task" });
  });
}

// Special Day: a lightweight all-day marker (birthday, holiday, open house,
// wedding...) — no start/end time by design. Deliberately simpler than the full
// Task/Event modal (addModal.js): title, date, category/color, repeat, notes only.
// Both create and edit go through this one step — state.modal.id distinguishes them.
function renderSpecialDayStep(root, state, actions) {
  const editId = state.modal?.id;
  const master = editId ? (state.specialDays || []).find((d) => d.id === editId) : null;
  const isOccurrenceScope = editId && state.modal.occurrenceScope === "occurrence";
  const editingItem = master && isOccurrenceScope ? resolveOccurrence(master, state.modal.occurrenceDate, "date") : master;
  const isEdit = !!editingItem;

  const defaultDate = isEdit ? editingItem.date : state.modal?.date || toISODate(today());
  const defaultCategoryId = isEdit ? editingItem.categoryId : state.categories.find((c) => !c.archived)?.id || state.categories[0]?.id || "";
  const defaultCategory = categoryById(state, defaultCategoryId);
  const defaultColorId = isEdit
    ? editingItem.colorId || ""
    : defaultCategory?.colors.find((c) => !c.archived)?.id || defaultCategory?.colors[0]?.id || "";
  let repeatRules = isEdit ? editingItem.repeat || [] : [];

  root.innerHTML = `
    <div class="modal-overlay" id="overlay">
      <div class="modal-panel" role="dialog" aria-modal="true" style="max-width:400px;">
        <div class="modal-header">
          <h2>${isEdit ? "Edit Special Day" : "Add Special Day"}</h2>
          <button class="modal-close" id="close-btn">${icons.close}</button>
        </div>
        <div class="modal-body">
          <div class="field">
            <label>Title</label>
            <input type="text" id="f-title" placeholder="e.g. Ethan's Birthday" value="${esc(editingItem?.title || "")}" />
          </div>
          <div class="field">
            <label>Date</label>
            <input type="date" id="f-date" value="${defaultDate}" />
          </div>
          <div class="field">
            <label>Category</label>
            <div class="category-select" id="f-category">
              ${state.categories
                .filter((c) => !c.archived || c.id === defaultCategoryId)
                .map((c) => `<button type="button" class="category-pill" data-id="${c.id}">${esc(c.name)}</button>`)
                .join("")}
              <button type="button" class="category-pill pill-add" id="f-add-category-btn">${icons.plusSmall}<span>Add Category</span></button>
            </div>
          </div>
          <div class="field">
            <label>Color</label>
            <div class="named-color-list" id="f-color">${colorSwatchesHTML(defaultCategory, defaultColorId)}</div>
          </div>
          ${
            isOccurrenceScope
              ? ""
              : `<div class="field" id="f-repeat-wrap">
                  <label>Repeat</label>
                  <div class="repeat-rule-list" id="f-repeat-list"></div>
                </div>`
          }
          <div class="field">
            <label>Notes (optional)</label>
            <textarea id="f-notes" placeholder="Add notes…">${esc(editingItem?.notes || "")}</textarea>
          </div>
        </div>
        <div class="modal-footer">
          ${isEdit ? `<button class="btn btn-danger-ghost" id="delete-btn">Delete</button>` : `<button class="btn btn-ghost" id="back-btn">Back</button>`}
          <div class="modal-footer-spacer"></div>
          <button class="btn btn-secondary" id="cancel-btn">Cancel</button>
          <button class="btn btn-primary" id="save-btn">${isEdit ? "Save Changes" : "Add"}</button>
        </div>
      </div>
    </div>
  `;
  wireClose(root, actions);
  root.querySelector("#cancel-btn").addEventListener("click", () => actions.closeModal());
  root.querySelector("#back-btn")?.addEventListener("click", () => actions.openModal({ type: "add-chooser", step: "type" }));

  const dateInput = root.querySelector("#f-date");
  const repeatListEl = root.querySelector("#f-repeat-list");
  if (repeatListEl) {
    createRepeatRuleUI({
      container: repeatListEl,
      getRules: () => repeatRules,
      setRules: (next) => {
        repeatRules = next;
      },
      getAnchorDate: () => dateInput.value,
    });
  }

  let selectedCategoryId = defaultCategoryId;
  let selectedColorId = defaultColorId;
  const colorList = root.querySelector("#f-color");

  const snapshotTitle = () => root.querySelector("#f-title")?.value || "";
  const restoreTitle = (title) => {
    const el = root.querySelector("#f-title");
    if (el) el.value = title;
  };

  const wireColorSwatches = () => {
    colorList.querySelectorAll(".named-color-swatch[data-color-id]").forEach((sw) => {
      sw.addEventListener("click", () => {
        selectedColorId = sw.dataset.colorId;
        colorList.querySelectorAll(".named-color-swatch[data-color-id]").forEach((s) => s.classList.toggle("selected", s === sw));
      });
    });
    colorList.querySelector("#f-add-color-btn").addEventListener("click", () => {
      const category = categoryById(state, selectedCategoryId);
      if (!category) return;
      const title = snapshotTitle();
      openAddColorPopup({
        actions,
        category,
        onAdded: (color) => {
          restoreTitle(title);
          root.querySelector(`.category-pill[data-id="${selectedCategoryId}"]`)?.click();
          root.querySelector(`.named-color-swatch[data-color-id="${color.id}"]`)?.click();
        },
      });
    });
  };
  wireColorSwatches();

  const syncSelection = () => {
    root.querySelectorAll(".category-pill[data-id]").forEach((pill) => {
      pill.classList.toggle("selected", pill.dataset.id === selectedCategoryId);
    });
  };
  syncSelection();
  root.querySelectorAll(".category-pill[data-id]").forEach((pill) => {
    pill.addEventListener("click", () => {
      selectedCategoryId = pill.dataset.id;
      syncSelection();
      const cat = categoryById(state, selectedCategoryId);
      selectedColorId = cat?.colors.find((c) => !c.archived)?.id || cat?.colors[0]?.id || "";
      colorList.innerHTML = colorSwatchesHTML(cat, selectedColorId);
      wireColorSwatches();
    });
  });

  root.querySelector("#f-add-category-btn").addEventListener("click", () => {
    const title = snapshotTitle();
    openAddCategoryPopup({
      actions,
      onAdded: (category) => {
        restoreTitle(title);
        root.querySelector(`.category-pill[data-id="${category.id}"]`)?.click();
      },
    });
  });

  if (isEdit) {
    root.querySelector("#delete-btn").addEventListener("click", () => {
      showConfirm({
        title: `Delete "${esc(editingItem.title)}"?`,
        message: isOccurrenceScope ? "This only removes this one occurrence — the rest of the series stays." : undefined,
        confirmLabel: "Delete",
        danger: true,
      }).then((ok) => {
        if (!ok) return;
        if (isOccurrenceScope) actions.deleteSpecialDayOccurrence(editingItem.id, state.modal.occurrenceDate);
        else actions.removeSpecialDay(editingItem.id);
        actions.closeModal();
        showToast("Special Day deleted", { variant: "danger" });
      });
    });
  }

  root.querySelector("#save-btn").addEventListener("click", () => {
    const title = root.querySelector("#f-title").value.trim();
    if (!title) {
      root.querySelector("#f-title").focus();
      return;
    }
    const notes = root.querySelector("#f-notes").value.trim();
    const date = dateInput.value || defaultDate;
    const baseFields = { title, categoryId: selectedCategoryId, colorId: selectedColorId, notes };

    if (isOccurrenceScope) {
      const canonicalKey = state.modal.occurrenceDate;
      const exception = { ...baseFields };
      if (date !== canonicalKey) exception.movedTo = date;
      actions.updateSpecialDayOccurrence(editingItem.id, canonicalKey, exception);
    } else if (isEdit) {
      actions.updateSpecialDay(editingItem.id, { ...baseFields, date, repeat: repeatRules });
    } else {
      actions.addSpecialDay({ ...baseFields, date, repeat: repeatRules });
    }
    actions.closeModal();
  });
}

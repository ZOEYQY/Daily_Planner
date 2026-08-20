import { icons } from "../icons.js";
import { esc } from "../utils.js";
import { toISODate, today } from "../dateUtils.js";
import { computeReschedulePatch } from "../rescheduleTracking.js";
import { resolveOccurrence } from "../selectors.js";
import { showConfirm, showToast, openFormPopup } from "./notify.js";
import { openAddCategoryPopup, openAddColorPopup } from "./categoryColorPopups.js";
import { createRepeatRuleUI } from "./repeatRuleUI.js";
import { allFieldDefs, allFieldKeys, fieldLabels, TODO_LIST_KEY } from "../extraFields.js";
import { uid } from "../seed.js";

let localType = "task";

// Adding a custom field commits to the store, which triggers a full app re-render —
// renderAddModal runs again from scratch with entirely new closures, so the *old*
// call's local variables (state, editingItem, the checklist DOM it already patched)
// are gone by the time that store update's listeners finish running. Recording the
// key here (module-level, survives across renders same as `localType`) lets the
// *new* render pick it up naturally while computing its own fresh checklist, instead
// of one render trying to reach into another render's stale DOM/closures.
let pendingCustomFieldKeys = [];

// Whether the Extra Fields checklist is expanded — collapsed by default so the
// form doesn't show every possible toggle up front; the "+" button next to the
// "Extra Fields" label opens/closes it. Module-level for the same reason as
// pendingCustomFieldKeys: a custom-field add triggers a full remount, and the
// checklist should stay open across that so the user sees what they just added.
let fieldsChecklistOpen = false;

// Fields shown for an item = its category's own defaults, its color's own defaults,
// plus any this-item-only additions the user turned on beyond that
// (item.extraFieldsOverride) — see settingsPanel.js's per-category/per-color
// checklists and extraFields.js for the shared field-definition list.
export function effectiveEnabledFields(category, color, item) {
  return [
    ...new Set([...(category?.enabledFields || []), ...(color?.enabledFields || []), ...(item?.extraFieldsOverride || []), ...pendingCustomFieldKeys]),
  ];
}

function plusOneHour(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  const next = (h + 1) % 24;
  return `${String(next).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

// When editing a single occurrence of a recurring item (state.modal.occurrenceScope
// === "occurrence"), resolves to that occurrence's effective data (any standing
// override + moved date already applied) rather than the raw master record — see
// selectors.js for how occurrences/exceptions are resolved.
function findEditingItem(state) {
  const { itemType, id, occurrenceScope, occurrenceDate } = state.modal || {};
  const master = itemType === "task" ? state.tasks.find((t) => t.id === id) : itemType === "event" ? state.events.find((e) => e.id === id) : null;
  if (!master) return null;
  if (occurrenceScope === "occurrence" && occurrenceDate) {
    return resolveOccurrence(master, occurrenceDate, itemType === "task" ? "dueDate" : "date");
  }
  return master;
}

// Dragging/clicking to create on the timeline commits the task/event to the store
// immediately, then opens this modal in edit mode (state.modal.isDraft: true) so it
// can be filled in — but that meant closing without ever hitting Save silently left
// a stray "New Task"/"New Event" behind. Both this modal's own close controls and
// the app's global Escape handler (main.js) route through here so the behavior is
// consistent everywhere the modal can be dismissed.
export function closeAddOrEditModal(state, actions) {
  const modal = state.modal;
  if (modal?.type === "edit" && modal.isDraft) {
    const item = findEditingItem(state);
    showConfirm({
      title: `Discard this ${modal.itemType === "task" ? "task" : "event"}?`,
      message: "It hasn't been saved yet — closing now won't keep it.",
      confirmLabel: "Discard",
      danger: true,
    }).then((ok) => {
      if (!ok) return;
      if (item) {
        if (modal.itemType === "task") actions.removeTask(item.id);
        else actions.removeEvent(item.id);
      }
      pendingCustomFieldKeys = [];
      fieldsChecklistOpen = false;
      actions.closeModal();
    });
    return;
  }
  pendingCustomFieldKeys = [];
  fieldsChecklistOpen = false;
  actions.closeModal();
}

export function categoryById(state, id) {
  return state.categories.find((c) => c.id === id) || null;
}

// Colors belong to whichever category is currently selected — switching category
// swaps the whole palette, since a color is really a named subject inside that category.
// A "+ Add Color" pill always trails the list (even once colors exist) so a new one
// can be added without leaving this form. Archived colors are hidden from the picker
// unless they're the one already assigned — a deleted-but-kept color must still show
// up as the current selection when editing an item that used it.
export function colorSwatchesHTML(category, selectedColorId) {
  const colors = (category?.colors || []).filter((c) => !c.archived || c.id === selectedColorId);
  const swatches = colors
    .map(
      (col) => `
    <button type="button" class="named-color-swatch ${col.id === selectedColorId ? "selected" : ""}" style="--swatch-color:${col.value}" data-color-id="${col.id}">
      <span class="named-color-dot"></span>
      <span class="named-color-name">${esc(col.name)}</span>
    </button>`
    )
    .join("");
  return `${swatches}<button type="button" class="named-color-swatch pill-add" id="f-add-color-btn">${icons.plusSmall}<span class="named-color-name">Add Color</span></button>`;
}

// A field pill's whole clickable area IS the toggle — no checkbox input, just a
// solid "selected" pill vs. a dashed "not selected" one (see .default-field-pill
// in components.css, shared with Settings' Default Fields pills for the same look
// everywhere a field gets turned on/off in this app).
function fieldTogglePillHTML(key, label, selected) {
  return `
    <button type="button" class="default-field-pill field-toggle-pill ${selected ? "is-selected" : "is-unselected"}" data-key="${key}">${selected ? "" : icons.plusSmall}<span>${esc(label)}</span></button>`;
}

// The checklist itself only gets re-rendered on category/color switch (so it
// reflects those defaults + this item's overrides) — a pill click just toggles
// its own class/content in place and rebuilds the dependent inputs below it,
// never the whole checklist, so the user's own toggle choices are never
// clobbered mid-edit (see wireFieldToggles/refreshFieldsChecklist).
function extraFieldsChecklistHTML(state, enabledFields) {
  const labels = fieldLabels(state);
  const toggles = allFieldKeys(state)
    .map((key) => fieldTogglePillHTML(key, labels[key], enabledFields.includes(key)))
    .join("");
  return `${toggles}<button type="button" class="field-toggle-pill field-toggle-add" id="f-add-field-btn">${icons.plusSmall}<span>Add New</span></button>`;
}

function extraFieldInputsHTML(state, checkedKeys, item, todoItems) {
  const inputs = allFieldDefs(state)
    .filter((f) => checkedKeys.includes(f.key))
    .map((f) => {
      const value = f.custom ? item?.customFields?.[f.key] || "" : item?.[f.key] || "";
      return `
    <div class="field">
      <label>${esc(f.label)}</label>
      <input type="${f.type}" id="${f.inputId}" data-field-key="${f.key}" data-custom="${f.custom ? "1" : ""}" placeholder="${f.placeholder}" value="${esc(value)}" />
    </div>`;
    })
    .join("");
  const todo = checkedKeys.includes(TODO_LIST_KEY)
    ? `<div class="field">
        <label>To-Do List</label>
        <div id="f-todo-wrap">${todoListHTML(todoItems)}</div>
      </div>`
    : "";
  return inputs + todo;
}

// Each item can optionally carry its own time — items with one render as small
// blocks in the day grid's hourly timeline (see dayGridView.js); untimed items
// only ever show here, in the parent card's plain checklist.
function todoListHTML(items) {
  const rows = items
    .map(
      (item) => `
    <div class="todo-item-row" data-todo-id="${item.id}">
      <input type="checkbox" class="todo-item-check" data-todo-id="${item.id}" ${item.done ? "checked" : ""} />
      <span class="todo-item-text ${item.done ? "is-done" : ""}">${esc(item.text)}</span>
      <input type="time" class="todo-item-time" data-todo-id="${item.id}" value="${item.time || ""}" title="Show at this time in the day grid" />
      <button type="button" class="remove-btn todo-item-remove" data-todo-id="${item.id}" aria-label="Remove item">${icons.close}</button>
    </div>`
    )
    .join("");
  return `
    <div class="todo-list">${rows}</div>
    <div class="todo-add-row">
      <input type="text" id="f-todo-new" placeholder="Add an item…" />
      <input type="time" id="f-todo-new-time" title="Optional time" />
      <button type="button" class="btn btn-ghost" id="f-todo-add-btn">${icons.plusSmall}</button>
    </div>`;
}

function captureFormSnapshot(root) {
  return {
    title: root.querySelector("#f-title")?.value || "",
    notes: root.querySelector("#f-notes")?.value || "",
    date: root.querySelector("#f-date")?.value || "",
    hasTime: root.querySelector("#f-has-time")?.checked ?? null,
    start: root.querySelector("#f-start")?.value || "",
    end: root.querySelector("#f-end")?.value || "",
  };
}

// Restores form input values after actions.addCategory/addColorToCategory triggers a
// full app re-render (adding a category/color commits to the store immediately, since
// this modal has no draft/save step of its own) — otherwise whatever the user had
// already typed would be silently wiped.
function restoreFormSnapshot(root, snap) {
  const titleEl = root.querySelector("#f-title");
  if (titleEl) titleEl.value = snap.title;
  const notesEl = root.querySelector("#f-notes");
  if (notesEl) notesEl.value = snap.notes;
  const dateEl = root.querySelector("#f-date");
  if (dateEl) {
    dateEl.value = snap.date;
    dateEl.dispatchEvent(new Event("input"));
  }
  const startEl = root.querySelector("#f-start");
  if (startEl && snap.start) startEl.value = snap.start;
  const endEl = root.querySelector("#f-end");
  if (endEl && snap.end) endEl.value = snap.end;
  const hasTimeEl = root.querySelector("#f-has-time");
  if (hasTimeEl && snap.hasTime !== null) {
    hasTimeEl.checked = snap.hasTime;
    hasTimeEl.dispatchEvent(new Event("change"));
  }
}

export function renderAddModal(root, state, actions) {
  const isEdit = state.modal?.type === "edit";
  const editingItem = isEdit ? findEditingItem(state) : null;

  if (isEdit && !editingItem) {
    actions.closeModal();
    return;
  }

  if (isEdit) {
    localType = state.modal.itemType;
  }

  const isTaskForm = localType === "task";

  const defaultDate = isEdit
    ? editingItem.dueDate || editingItem.date || ""
    : state.modal?.date || toISODate(today());
  const prefillTime = isEdit ? editingItem.startTime || "" : state.modal?.time || "";
  const prefillEndTime = isEdit ? editingItem.endTime || "" : state.modal?.endTime || "";
  if (!isEdit && prefillTime) localType = "event";
  const defaultCategoryId = isEdit
    ? editingItem.categoryId
    : state.categories.find((c) => !c.archived)?.id || state.categories[0]?.id || "";
  const defaultCategory = categoryById(state, defaultCategoryId);
  const defaultColorId = isEdit
    ? editingItem.colorId || ""
    : defaultCategory?.colors.find((c) => !c.archived)?.id || defaultCategory?.colors[0]?.id || "";
  // For tasks, whether a specific time applies is independent of whether a date is set
  // (a task can be day-scoped with no time — e.g. "know it's Thursday, not sure when yet").
  const hasTimeInitially = isTaskForm ? (isEdit ? !!editingItem.scheduled : !!prefillTime) : true;
  // Editing a single occurrence never exposes Repeat — repetition is a series-level
  // property; it's set (or changed) only when editing/creating at series scope.
  const isOccurrenceScope = isEdit && state.modal.occurrenceScope === "occurrence";
  let repeatRules = isEdit ? editingItem.repeat || [] : [];
  let todoItems = isEdit ? [...(editingItem.todoList || [])] : [];
  const defaultColor = defaultCategory?.colors.find((c) => c.id === defaultColorId) || null;
  const initialEnabledFields = effectiveEnabledFields(defaultCategory, defaultColor, editingItem);

  root.innerHTML = `
    <div class="modal-overlay" id="overlay">
      <div class="modal-panel" role="dialog" aria-modal="true">
        <div class="modal-header">
          <h2>${isEdit ? `Edit ${localType === "task" ? "Task" : "Event"}` : "Add"}</h2>
          <button class="modal-close" id="close-btn">${icons.close}</button>
        </div>
        <div class="modal-body">
          ${
            isEdit
              ? ""
              : `<div class="type-toggle" id="type-toggle">
                  <button data-type="task" class="${localType === "task" ? "active" : ""}">Task</button>
                  <button data-type="event" class="${localType === "event" ? "active" : ""}">Event</button>
                </div>`
          }

          <div class="field">
            <label>Title</label>
            <input type="text" id="f-title" placeholder="${localType === "task" ? "e.g. Finish reading" : "e.g. Mathematics"}" value="${esc(editingItem?.title || "")}" />
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
              ${colorSwatchesHTML(defaultCategory, defaultColorId)}
            </div>
          </div>

          <div class="field" id="f-date-wrap">
            <label>Date${isTaskForm ? " (leave empty to keep unscheduled)" : ""}</label>
            <div style="display:flex; gap:8px; align-items:center;">
              <input type="date" id="f-date" value="${defaultDate}" style="flex:1 1 auto;" />
              ${
                isTaskForm && defaultDate
                  ? `<button type="button" class="btn btn-ghost" id="clear-date-btn" style="flex:0 0 auto; padding:8px 12px;">Unschedule</button>`
                  : ""
              }
            </div>
          </div>

          ${
            isTaskForm
              ? `<label class="checkbox-field" id="f-has-time-wrap" style="${defaultDate ? "" : "display:none;"}">
                  <input type="checkbox" id="f-has-time" ${hasTimeInitially ? "checked" : ""} />
                  <span>Give it a specific time</span>
                </label>`
              : ""
          }

          <div class="field-row" id="f-time-wrap" style="${localType === "event" || (isTaskForm && hasTimeInitially && defaultDate) ? "" : "display:none;"}">
            <div class="field">
              <label>Start</label>
              <input type="time" id="f-start" value="${prefillTime || "09:00"}" />
            </div>
            <div class="field">
              <label>End</label>
              <input type="time" id="f-end" value="${prefillEndTime || (prefillTime ? plusOneHour(prefillTime) : "10:00")}" />
            </div>
          </div>

          ${
            isOccurrenceScope
              ? ""
              : `<div class="field" id="f-repeat-wrap" style="${defaultDate ? "" : "display:none;"}">
                  <label>Repeat</label>
                  <div class="repeat-rule-list" id="f-repeat-list"></div>
                </div>`
          }

          <div class="field">
            <label>Notes (optional)</label>
            <textarea id="f-notes" placeholder="Add notes…">${esc(editingItem?.notes || "")}</textarea>
          </div>

          <div class="field">
            <div class="field-label-row">
              <label>Extra Fields</label>
              <button type="button" class="icon-btn-small ${fieldsChecklistOpen ? "rotated" : ""}" id="f-toggle-fields-btn" aria-label="${fieldsChecklistOpen ? "Hide extra fields" : "Show extra fields"}" aria-expanded="${fieldsChecklistOpen}">${icons.plusSmall}</button>
            </div>
            <div class="default-field-pill-row" id="f-fields-checklist" style="${fieldsChecklistOpen ? "" : "display:none;"}">${extraFieldsChecklistHTML(state, initialEnabledFields)}</div>
          </div>
          <div id="f-extra-fields-inputs">${extraFieldInputsHTML(state, initialEnabledFields, editingItem, todoItems)}</div>
        </div>
        <div class="modal-footer">
          ${isEdit ? `<button class="btn btn-danger-ghost" id="delete-btn">Delete</button><div class="modal-footer-spacer"></div>` : ""}
          <button class="btn btn-secondary" id="cancel-btn">Cancel</button>
          <button class="btn btn-primary" id="save-btn">${isEdit ? "Save Changes" : `Save ${localType === "task" ? "Task" : "Event"}`}</button>
        </div>
      </div>
    </div>
  `;

  const dateInput = root.querySelector("#f-date");
  const timeWrap = root.querySelector("#f-time-wrap");
  const hasTimeWrap = root.querySelector("#f-has-time-wrap");
  const hasTimeCheckbox = root.querySelector("#f-has-time");
  const syncTimeVisibility = () => {
    if (!isTaskForm) return;
    const dateSet = !!dateInput.value;
    if (hasTimeWrap) hasTimeWrap.style.display = dateSet ? "" : "none";
    timeWrap.style.display = dateSet && hasTimeCheckbox?.checked ? "" : "none";
  };
  syncTimeVisibility();
  if (isTaskForm) {
    dateInput.addEventListener("input", syncTimeVisibility);
    hasTimeCheckbox?.addEventListener("change", syncTimeVisibility);
  }

  const repeatWrap = root.querySelector("#f-repeat-wrap");
  const syncRepeatVisibility = () => {
    if (repeatWrap) repeatWrap.style.display = dateInput.value ? "" : "none";
  };
  syncRepeatVisibility();
  dateInput.addEventListener("input", syncRepeatVisibility);

  const repeatListEl = root.querySelector("#f-repeat-list");
  const repeatRuleUI = repeatListEl
    ? createRepeatRuleUI({
        container: repeatListEl,
        getRules: () => repeatRules,
        setRules: (next) => {
          repeatRules = next;
        },
        getAnchorDate: () => dateInput.value,
      })
    : null;

  function renderTodoList() {
    const wrap = root.querySelector("#f-todo-wrap");
    if (!wrap) return;
    wrap.innerHTML = todoListHTML(todoItems);
    wireTodoList();
  }

  function wireTodoList() {
    const wrap = root.querySelector("#f-todo-wrap");
    if (!wrap) return;
    wrap.querySelectorAll(".todo-item-check").forEach((cb) => {
      cb.addEventListener("change", () => {
        const id = cb.dataset.todoId;
        todoItems = todoItems.map((t) => (t.id === id ? { ...t, done: cb.checked } : t));
        renderTodoList();
      });
    });
    wrap.querySelectorAll(".todo-item-time").forEach((input) => {
      input.addEventListener("change", () => {
        const id = input.dataset.todoId;
        todoItems = todoItems.map((t) => (t.id === id ? { ...t, time: input.value || "" } : t));
      });
    });
    wrap.querySelectorAll(".todo-item-remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        todoItems = todoItems.filter((t) => t.id !== btn.dataset.todoId);
        renderTodoList();
      });
    });
    const addBtn = wrap.querySelector("#f-todo-add-btn");
    const newInput = wrap.querySelector("#f-todo-new");
    const newTimeInput = wrap.querySelector("#f-todo-new-time");
    const submitAdd = () => {
      const text = newInput.value.trim();
      if (!text) return;
      todoItems = [...todoItems, { id: uid("todo"), text, done: false, time: newTimeInput.value || "" }];
      renderTodoList();
    };
    addBtn?.addEventListener("click", submitAdd);
    newInput?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        submitAdd();
      }
    });
  }
  function currentCheckedFieldKeys() {
    return Array.from(root.querySelectorAll(".field-toggle-pill.is-selected[data-key]")).map((el) => el.dataset.key);
  }

  // Rebuilds only the inputs/todo-list dependent on which checkboxes are currently
  // checked — never the checklist itself, so this can run on every checkbox change
  // without clobbering the user's own in-progress toggle choices.
  function renderExtraFieldInputs() {
    const wrap = root.querySelector("#f-extra-fields-inputs");
    if (!wrap) return;
    wrap.innerHTML = extraFieldInputsHTML(state, currentCheckedFieldKeys(), editingItem, todoItems);
    wireTodoList();
  }

  function wireAddFieldBtn() {
    root.querySelector("#f-add-field-btn")?.addEventListener("click", () => {
      const snapshot = captureFormSnapshot(root);
      openFormPopup({
        title: "Add Custom Field",
        submitLabel: "Add",
        bodyHTML: `<div class="field"><label>Field Name</label><input type="text" id="new-field-label" placeholder="e.g. Budget" /></div>`,
        onSubmit: ({ panel, close }) => {
          const labelInput = panel.querySelector("#new-field-label");
          const label = labelInput.value.trim();
          if (!label) {
            labelInput.focus();
            return;
          }
          close();
          // addCustomFieldDef triggers a full store update → app re-render, which
          // rebuilds this whole modal from scratch (see the note by
          // pendingCustomFieldKeys above) — record the key first so the *next*
          // render's own fresh checklist comes back with it already checked,
          // rather than trying to patch this render's DOM after the fact.
          const field = actions.addCustomFieldDef(label);
          pendingCustomFieldKeys = [...pendingCustomFieldKeys, field.key];
          fieldsChecklistOpen = true;
          showToast("Field added");
          restoreFormSnapshot(root, snapshot);
        },
      });
    });
  }

  // Replaces just the one clicked pill with its flipped-state markup and rewires
  // only that new node — never the whole list — so repeated clicks can't stack up
  // duplicate listeners on the pills nobody touched.
  function wirePillClick(pill) {
    pill.addEventListener("click", () => {
      const key = pill.dataset.key;
      const label = pill.querySelector("span")?.textContent || "";
      const selected = !pill.classList.contains("is-selected");
      const wrap = document.createElement("div");
      wrap.innerHTML = fieldTogglePillHTML(key, label, selected).trim();
      const newPill = wrap.firstElementChild;
      pill.replaceWith(newPill);
      wirePillClick(newPill);
      renderExtraFieldInputs();
    });
  }

  function wireFieldToggles() {
    root.querySelectorAll(".field-toggle-pill[data-key]").forEach(wirePillClick);
    wireAddFieldBtn();
  }
  wireFieldToggles();

  const toggleFieldsBtn = root.querySelector("#f-toggle-fields-btn");
  const fieldsChecklistWrap = root.querySelector("#f-fields-checklist");
  toggleFieldsBtn?.addEventListener("click", () => {
    fieldsChecklistOpen = !fieldsChecklistOpen;
    fieldsChecklistWrap.style.display = fieldsChecklistOpen ? "" : "none";
    toggleFieldsBtn.classList.toggle("rotated", fieldsChecklistOpen);
    toggleFieldsBtn.setAttribute("aria-expanded", String(fieldsChecklistOpen));
    toggleFieldsBtn.setAttribute("aria-label", fieldsChecklistOpen ? "Hide extra fields" : "Show extra fields");
  });

  if (initialEnabledFields.includes(TODO_LIST_KEY)) wireTodoList();

  // Re-derives the checklist from the currently-selected category/color's own
  // defaults (plus this item's overrides) whenever either changes, then rebuilds
  // the inputs to match — called from the category-pill/color-swatch click handlers.
  function refreshFieldsChecklist() {
    const checklistWrap = root.querySelector("#f-fields-checklist");
    if (!checklistWrap) return;
    const cat = categoryById(state, selectedCategoryId);
    const color = cat?.colors.find((c) => c.id === selectedColorId) || null;
    checklistWrap.innerHTML = extraFieldsChecklistHTML(state, effectiveEnabledFields(cat, color, editingItem));
    wireFieldToggles();
    renderExtraFieldInputs();
  }

  const clearDateBtn = root.querySelector("#clear-date-btn");
  if (clearDateBtn) {
    clearDateBtn.addEventListener("click", () => {
      dateInput.value = "";
      if (hasTimeCheckbox) hasTimeCheckbox.checked = false;
      // A repeating task needs its date as the pattern's anchor — can't be both
      // repeating and dateless, so unscheduling one also clears every repeat rule.
      repeatRules = [];
      repeatRuleUI?.render();
      syncTimeVisibility();
      syncRepeatVisibility();
      clearDateBtn.remove();
    });
  }

  let selectedCategoryId = defaultCategoryId;
  let selectedColorId = defaultColorId;
  const colorList = root.querySelector("#f-color");

  const wireColorSwatches = () => {
    colorList.querySelectorAll(".named-color-swatch[data-color-id]").forEach((sw) => {
      sw.addEventListener("click", () => {
        selectedColorId = sw.dataset.colorId;
        colorList.querySelectorAll(".named-color-swatch[data-color-id]").forEach((s) => s.classList.toggle("selected", s === sw));
        refreshFieldsChecklist();
      });
    });

    colorList.querySelector("#f-add-color-btn").addEventListener("click", () => {
      const category = categoryById(state, selectedCategoryId);
      if (!category) return;
      const snapshot = captureFormSnapshot(root);
      openAddColorPopup({
        actions,
        category,
        onAdded: (color) => {
          restoreFormSnapshot(root, snapshot);
          root.querySelector(`.category-pill[data-id="${selectedCategoryId}"]`)?.click();
          root.querySelector(`.named-color-swatch[data-color-id="${color.id}"]`)?.click();
        },
      });
    });
  };
  wireColorSwatches();

  const syncCategorySelection = () => {
    root.querySelectorAll(".category-pill[data-id]").forEach((pill) => {
      pill.classList.toggle("selected", pill.dataset.id === selectedCategoryId);
    });
  };
  syncCategorySelection();

  root.querySelectorAll(".category-pill[data-id]").forEach((pill) => {
    pill.addEventListener("click", () => {
      selectedCategoryId = pill.dataset.id;
      syncCategorySelection();
      const cat = categoryById(state, selectedCategoryId);
      const stillValid = cat?.colors.some((c) => c.id === selectedColorId);
      selectedColorId = stillValid ? selectedColorId : cat?.colors.find((c) => !c.archived)?.id || cat?.colors[0]?.id || "";
      colorList.innerHTML = colorSwatchesHTML(cat, selectedColorId);
      wireColorSwatches();
      refreshFieldsChecklist();
    });
  });

  root.querySelector("#f-add-category-btn").addEventListener("click", () => {
    const snapshot = captureFormSnapshot(root);
    openAddCategoryPopup({
      actions,
      onAdded: (category) => {
        restoreFormSnapshot(root, snapshot);
        root.querySelector(`.category-pill[data-id="${category.id}"]`)?.click();
      },
    });
  });

  root.querySelector("#overlay").addEventListener("click", (e) => {
    if (e.target.id === "overlay") closeAddOrEditModal(state, actions);
  });
  root.querySelector("#close-btn").addEventListener("click", () => closeAddOrEditModal(state, actions));
  root.querySelector("#cancel-btn").addEventListener("click", () => closeAddOrEditModal(state, actions));

  if (isEdit) {
    root.querySelector("#delete-btn").addEventListener("click", () => {
      // The this/series choice was already made once, when this occurrence was
      // clicked to open it (see recurrenceUI.js) — Delete respects that same scope
      // rather than asking again.
      showConfirm({
        title: `Delete "${esc(editingItem.title)}"?`,
        message: isOccurrenceScope ? "This only removes this one occurrence — the rest of the series stays." : undefined,
        confirmLabel: "Delete",
        danger: true,
      }).then((ok) => {
        if (!ok) return;
        if (isOccurrenceScope) {
          if (localType === "task") actions.deleteTaskOccurrence(editingItem.id, state.modal.occurrenceDate);
          else actions.deleteEventOccurrence(editingItem.id, state.modal.occurrenceDate);
        } else if (localType === "task") {
          actions.removeTask(editingItem.id);
        } else {
          actions.removeEvent(editingItem.id);
        }
        actions.closeModal();
        showToast(`${localType === "task" ? "Task" : "Event"} deleted`, { variant: "danger" });
      });
    });
  } else {
    root.querySelector("#type-toggle").addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      localType = btn.dataset.type;
      renderAddModal(root, state, actions);
    });
  }

  root.querySelector("#save-btn").addEventListener("click", () => {
    const title = root.querySelector("#f-title").value.trim();
    if (!title) {
      root.querySelector("#f-title").focus();
      return;
    }
    const notes = root.querySelector("#f-notes").value.trim();
    const repeat = repeatRules;
    const enabledFields = currentCheckedFieldKeys();

    // Only fields currently checked are read from the form — the rest of an
    // existing record's extra-field values pass through untouched via the store's
    // update logic, so unchecking a field never loses its saved data. Fixed fields
    // land on their own top-level property; user-defined custom fields land in
    // customFields, keyed by field id — see src/extraFields.js.
    const extraFields = {};
    const customFields = { ...(editingItem?.customFields || {}) };
    allFieldDefs(state).forEach((f) => {
      if (!enabledFields.includes(f.key)) return;
      const value = root.querySelector(`#${f.inputId}`)?.value.trim() || "";
      if (f.custom) customFields[f.key] = value;
      else extraFields[f.key] = value;
    });
    extraFields.customFields = customFields;
    if (enabledFields.includes(TODO_LIST_KEY)) extraFields.todoList = todoItems;

    // Beyond whatever the current category/color already turn on by default, this
    // records only the additional fields the user checked for just this one card.
    const category = categoryById(state, selectedCategoryId);
    const color = category?.colors.find((c) => c.id === selectedColorId) || null;
    const defaultsHere = new Set([...(category?.enabledFields || []), ...(color?.enabledFields || [])]);
    extraFields.extraFieldsOverride = enabledFields.filter((k) => !defaultsHere.has(k));

    if (localType === "task") {
      const date = dateInput.value || "";
      const hasTime = date && hasTimeCheckbox?.checked;
      const startTime = hasTime ? root.querySelector("#f-start").value || "09:00" : "";
      const endTime = hasTime ? root.querySelector("#f-end").value || "10:00" : "";
      const baseFields = { title, categoryId: selectedCategoryId, colorId: selectedColorId, notes, ...extraFields };

      if (isOccurrenceScope) {
        const canonicalKey = state.modal.occurrenceDate;
        const exception = { ...baseFields, startTime, endTime, scheduled: !!hasTime };
        if (date && date !== canonicalKey) exception.movedTo = date;
        actions.updateTaskOccurrence(editingItem.id, canonicalKey, exception);
      } else if (isEdit) {
        const datePatch = computeReschedulePatch(editingItem, date, startTime, endTime, { scheduled: !!hasTime });
        actions.updateTask(editingItem.id, { ...baseFields, ...datePatch, repeat });
      } else {
        actions.addTask({ ...baseFields, dueDate: date, startTime, endTime, scheduled: !!hasTime, repeat });
      }
    } else {
      const date = dateInput.value || defaultDate;
      const start = root.querySelector("#f-start").value || "09:00";
      const end = root.querySelector("#f-end").value || "10:00";
      const baseFields = { title, categoryId: selectedCategoryId, colorId: selectedColorId, notes, ...extraFields };

      if (isOccurrenceScope) {
        const canonicalKey = state.modal.occurrenceDate;
        const exception = { ...baseFields, startTime: start, endTime: end };
        if (date !== canonicalKey) exception.movedTo = date;
        actions.updateEventOccurrence(editingItem.id, canonicalKey, exception);
      } else if (isEdit) {
        actions.updateEvent(editingItem.id, { ...baseFields, date, startTime: start, endTime: end, repeat });
      } else {
        actions.addEvent({ ...baseFields, date, startTime: start, endTime: end, repeat });
      }
    }
    localType = "task";
    pendingCustomFieldKeys = [];
    actions.closeModal();
  });
}

export function resetAddModalType() {
  localType = "task";
  pendingCustomFieldKeys = [];
  fieldsChecklistOpen = false;
}

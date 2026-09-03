import { icons } from "../icons.js";
import { esc } from "../utils.js";
import { uid } from "../seed.js";
import { suggestCategories } from "../suggest.js";
import { renderStatsWidget } from "./statsWidget.js";
import { openFormPopup, showChoice, showToast } from "./notify.js";
import { makeColor, pickUnusedColor } from "../categoryColor.js";
import { allFieldKeys, fieldLabels } from "../extraFields.js";
import { formatHourLabel } from "../dateUtils.js";

let activeTab = "categories";
let draft = null; // { categories } — live only while Settings is open
let suggestOpen = false;
let suggestCount = 3;
let suggestTopic = "";
let suggestResults = null; // array of names, or null
let suggestSelected = new Set();
const HOURS_0_23 = Array.from({ length: 24 }, (_, h) => h);

// Category-tab UI state — which panels are expanded. Collapsed by default so a
// category with several subcategories doesn't dump every checkbox on screen at
// once (see categoryBlock/subcatRow). Keyed by category/color id, live only while
// Settings is open.
let openManageFieldsCatIds = new Set();
let openSubcatIds = new Set();
let customizingColorIds = new Set();

// renderSettingsPanel rebuilds the whole modal shell from scratch on every call —
// and it's called on every store update, not just tab switches, since Visibility
// now applies instantly to the store (see wireVisibility) instead of going through
// the draft/Save flow. A fresh #overlay element replays .modal-overlay's fade-in
// (components.css) each time, which looks like a flash on every toggle click if
// left unguarded — this only lets that animation play once per Settings session.
let hasAnimatedOpen = false;

export function resetSettingsDraft() {
  draft = null;
  hasAnimatedOpen = false;
  openManageFieldsCatIds = new Set();
  openSubcatIds = new Set();
  customizingColorIds = new Set();
  suggestOpen = false;
  suggestResults = null;
}

function ensureDraft(state) {
  if (!draft) {
    draft = { categories: structuredClone(state.categories) };
  }
  return draft;
}

export function renderSettingsPanel(root, state, actions) {
  ensureDraft(state);

  const discardAndClose = () => {
    resetSettingsDraft();
    actions.closeModal();
  };

  const saveAndClose = () => {
    actions.applySettingsDraft(draft);
    resetSettingsDraft();
    actions.closeModal();
    showToast("Settings saved");
  };

  root.innerHTML = `
    <div class="modal-overlay ${hasAnimatedOpen ? "no-animate" : ""}" id="overlay">
      <div class="modal-panel" role="dialog" aria-modal="true" style="max-width:520px;">
        <div class="modal-header">
          <h2>Settings</h2>
          <button class="modal-close" id="close-btn">${icons.close}</button>
        </div>
        <div class="settings-tabs">
          <button class="settings-tab ${activeTab === "categories" ? "active" : ""}" data-tab="categories">Categories</button>
          <button class="settings-tab ${activeTab === "insights" ? "active" : ""}" data-tab="insights">Insights</button>
        </div>
        <div class="modal-body" id="tab-body"></div>
        <div class="modal-footer">
          <div class="modal-footer-spacer"></div>
          <button class="btn btn-secondary" id="cancel-btn">Cancel</button>
          <button class="btn btn-primary" id="save-btn">Save Changes</button>
        </div>
      </div>
    </div>
  `;
  hasAnimatedOpen = true;

  root.querySelector("#overlay").addEventListener("click", (e) => {
    if (e.target.id === "overlay") discardAndClose();
  });
  root.querySelector("#close-btn").addEventListener("click", discardAndClose);
  root.querySelector("#cancel-btn").addEventListener("click", discardAndClose);
  root.querySelector("#save-btn").addEventListener("click", saveAndClose);

  root.querySelectorAll(".settings-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      activeTab = tab.dataset.tab;
      renderSettingsPanel(root, state, actions);
    });
  });

  const body = root.querySelector("#tab-body");
  if (activeTab === "categories") {
    renderCategoriesTab(body, state, actions);
  } else {
    renderStatsWidget(body, state);
  }
}

function rerender(body, state, actions) {
  renderCategoriesTab(body, state, actions);
}

function renderCategoriesTab(body, state, actions) {
  body.innerHTML = `
    <div class="settings-section">
      <div class="settings-section-title">Visibility</div>
      <div class="toggle-pill-row">
        <button type="button" class="toggle-pill ${state.showWeekTray ? "is-on" : ""}" id="vis-week">Show Week Tasks</button>
        <button type="button" class="toggle-pill ${state.showDayTray ? "is-on" : ""}" id="vis-day">Show Day Tasks</button>
        <button type="button" class="toggle-pill ${state.showTodoTargetTab ? "is-on" : ""}" id="vis-todo-target">Show To-Do, Target &amp; Record</button>
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Calendar</div>
      <div class="settings-field-label" style="margin-top:0;">Week Starts On</div>
      <div class="type-toggle" id="week-start-toggle">
        <button type="button" data-day="0" class="${(state.weekStartsOn ?? 0) === 0 ? "active" : ""}">Sunday</button>
        <button type="button" data-day="1" class="${state.weekStartsOn === 1 ? "active" : ""}">Monday</button>
      </div>
      <div class="settings-field-label">Starts At</div>
      <select class="settings-select" id="day-start-hour-select">
        ${HOURS_0_23.map(
          (h) => `<option value="${h}" ${(state.dayStartHour ?? 7) === h ? "selected" : ""}>${formatHourLabel(h)}</option>`
        ).join("")}
      </select>
      <div class="settings-field-label">Time Picker Style</div>
      <div class="type-toggle" id="time-picker-style-toggle">
        <button type="button" data-style="native" class="${(state.timePickerStyle || "native") === "native" ? "active" : ""}">Default</button>
        <button type="button" data-style="text" class="${state.timePickerStyle === "text" ? "active" : ""}">Type</button>
        <button type="button" data-style="clock" class="${state.timePickerStyle === "clock" ? "active" : ""}">Clock</button>
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">To-Do</div>
      <p style="font-size:12px; color:var(--color-muted); line-height:1.5; margin:-2px 0 10px;">
        A to-do is a lighter-weight quick-add (Week/Day tray's checkmark button) — just a title, an optional
        deadline, and an optional note, separate from the full Add Task/Event form.
      </p>
      <div class="toggle-pill-row">
        <button type="button" class="toggle-pill ${state.todoDeadlineRequired ? "is-on" : ""}" id="vis-todo-deadline-required">Require a Deadline</button>
        <button type="button" class="toggle-pill ${state.todoShowDetail !== false ? "is-on" : ""}" id="vis-todo-show-detail">Show Detail Field</button>
      </div>
      <div class="settings-field-label">Turn Red This Many Hours Before Deadline</div>
      <input type="number" class="settings-select" id="todo-urgent-hours" min="1" step="1" value="${state.todoUrgentThresholdHours ?? 24}" style="max-width:100px;" />
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Categories</div>
      <p style="font-size:12px; color:var(--color-muted); line-height:1.5; margin:-2px 0 10px;">
        Each category keeps its own set of subcategories (a named color inside that category). A subcategory
        inherits its category's default details automatically — add extras only where a subcategory genuinely needs them.
      </p>
      <div class="category-color-list" id="cat-list">
        ${
          draft.categories.filter((c) => !c.archived).length === 0
            ? `<div class="empty-hint">No categories yet — add one below.</div>`
            : draft.categories.filter((c) => !c.archived).map((c) => categoryBlock(c, state)).join("")
        }
      </div>
      <button type="button" class="btn btn-secondary add-category-trigger" id="add-cat-btn">${icons.plusSmall}<span>Add Category</span></button>
      ${suggestSection()}
    </div>
  `;

  wireCategoryList(body, state, actions);
  wireVisibility(body, state, actions);
  wireWeekStart(body, actions);
  wireTodoSettings(body, state, actions);
  wireAddCategory(body, state, actions);
  wireSuggestSection(body, state, actions);
}

// Applies instantly, same as the rest of this section — no Save step needed.
function wireTodoSettings(body, state, actions) {
  body.querySelector("#vis-todo-deadline-required")?.addEventListener("click", () => {
    actions.setTodoDeadlineRequired(!state.todoDeadlineRequired);
  });
  body.querySelector("#vis-todo-show-detail")?.addEventListener("click", () => {
    actions.setTodoShowDetail(state.todoShowDetail === false);
  });
  const hoursInput = body.querySelector("#todo-urgent-hours");
  hoursInput?.addEventListener("change", () => {
    const hours = Math.max(1, Math.round(Number(hoursInput.value)) || 24);
    hoursInput.value = hours;
    actions.setTodoUrgentThresholdHours(hours);
  });
}

// Applies immediately, same as Visibility — a segmented Sunday/Monday pick
// rather than an independent toggle-pill, since the two options are mutually
// exclusive (reuses .type-toggle, the same segmented-picker style as the Add/
// Edit modal's Task/Event switch).
function wireWeekStart(body, actions) {
  body.querySelectorAll("#week-start-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => actions.setWeekStartsOn(Number(btn.dataset.day)));
  });
  const startHourSelect = body.querySelector("#day-start-hour-select");
  if (startHourSelect) {
    startHourSelect.addEventListener("change", () => actions.setDayStartHour(Number(startHourSelect.value)));
  }
  body.querySelectorAll("#time-picker-style-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => actions.setTimePickerStyle(btn.dataset.style));
  });
}

// Dashed, click-to-add pills for fields not yet on this subcategory — same
// "selected solid / not-yet-selected dashed" pill language as the category's own
// Default Fields row (see .default-field-pill in components.css), rather than a
// checkbox list. There's nothing to show as already-selected here (this only
// ever renders the addable leftovers — see addableKeys in subcatDetail), so
// every pill is the dashed variant.
function addableFieldPillsHTML(state, keys, colorId) {
  if (keys.length === 0) return "";
  const labels = fieldLabels(state);
  return `<div class="default-field-pill-row">${keys
    .map(
      (key) => `
    <button type="button" class="default-field-pill is-unselected" data-color-id="${colorId}" data-key="${key}">${icons.plusSmall}<span>${esc(labels[key])}</span></button>`
    )
    .join("")}</div>`;
}

// A subcategory's real, effective fields are category.enabledFields ∪
// color.enabledFields (see effectiveEnabledFields in addModal.js) — additive only,
// there's no way for a subcategory to turn OFF a category default. These two
// helpers split a color's checklist into that inherited half (read-only, always
// on) and the genuinely-its-own half, so the UI can show each honestly instead of
// re-showing the whole field list as if it were independently configured per color.
function inheritedFieldKeys(category) {
  return category.enabledFields || [];
}
function customFieldKeysFor(category, color) {
  const inherited = new Set(inheritedFieldKeys(category));
  return (color.enabledFields || []).filter((k) => !inherited.has(k));
}
function subcatSummary(category, color, state) {
  const inherited = inheritedFieldKeys(category);
  const custom = customFieldKeysFor(category, color);
  if (inherited.length === 0 && custom.length === 0) return "No details";
  if (custom.length === 0) return "Using default details";
  return `${inherited.length} inherited · ${custom.length} custom`;
}

// Default Fields row: currently-selected fields render as solid pills — click one
// to remove it directly, no checkbox needed. "+ Add" reveals the remaining,
// not-yet-selected fields as dashed pills (same dashed style as every other
// "+ Add" affordance below) interleaved before "More"/"Less"; click one to add
// it. Once expanded, a separate "+ Add" pill creates a brand-new custom field
// type (the one thing picking from allFieldKeys can never offer, since a new
// field doesn't exist to pick yet) and auto-enables it as this category's
// default, same as the Add/Edit modal's own "+ Add New" auto-checks it there.
// See customFieldKeysFor's header comment for why a category's own fields need
// no inherited/custom split (only subcategories do) — a category's enabledFields
// is the whole story here.
function defaultFieldsPillsHTML(cat, state) {
  const fields = cat.enabledFields || [];
  const manageOpen = openManageFieldsCatIds.has(cat.id);
  const selectedPills = fields
    .map(
      (key) => `
    <button type="button" class="default-field-pill is-selected" data-cat-id="${cat.id}" data-key="${key}" aria-label="Remove ${esc(fieldLabels(state)[key])}">${esc(fieldLabels(state)[key])}</button>`
    )
    .join("");
  const unselectedPills = manageOpen
    ? allFieldKeys(state)
        .filter((k) => !fields.includes(k))
        .map(
          (key) => `
    <button type="button" class="default-field-pill is-unselected" data-cat-id="${cat.id}" data-key="${key}">${icons.plusSmall}<span>${esc(fieldLabels(state)[key])}</span></button>`
        )
        .join("")
    : "";
  const createPill = manageOpen
    ? `<button type="button" class="default-field-pill is-create-toggle" data-cat-id="${cat.id}">${icons.plusSmall}<span>Add</span></button>`
    : "";
  // "cat-default-fields" scopes wireCategoryList's queries to just these pills —
  // without it, block.querySelectorAll(".default-field-pill...") would also match
  // a subcategory's own "Add a Field" pills (they share the same classes; see
  // addableFieldPillsHTML), since a subcategory's markup nests inside this same
  // .category-block once expanded.
  return `
    <div class="default-field-pill-row cat-default-fields">
      ${selectedPills}
      ${unselectedPills}
      ${createPill}
      <button type="button" class="default-field-pill is-more-toggle" data-cat-id="${cat.id}">${manageOpen ? icons.minusSmall : icons.plusSmall}<span>${manageOpen ? "Less" : "More"}</span></button>
    </div>
  `;
}

function categoryBlock(cat, state) {
  const subcats = cat.colors.filter((col) => !col.archived);

  return `
    <div class="category-block" data-cat-id="${cat.id}">
      <div class="category-block-header">
        <input type="text" class="category-name-input" value="${esc(cat.name)}" />
        <button type="button" class="remove-btn cat-remove-btn" aria-label="Remove category">${icons.trash}</button>
      </div>

      <div class="settings-field-label">Default Details</div>
      ${defaultFieldsPillsHTML(cat, state)}

      <div class="settings-field-label-row">
        <span class="settings-field-label">Subcategories</span>
        <button type="button" class="add-color-btn-compact add-color-btn" data-cat-id="${cat.id}">${icons.plusSmall}<span>Add</span></button>
      </div>
      <div class="subcat-list">
        ${subcats.length === 0 ? `<div class="empty-hint">No subcategories yet.</div>` : subcats.map((col) => subcatRow(cat, col, state)).join("")}
      </div>
    </div>
  `;
}

function subcatRow(cat, col, state) {
  const isOpen = openSubcatIds.has(col.id);
  return `
    <div class="subcat-row ${isOpen ? "is-open" : ""}" data-cat-id="${cat.id}" data-color-id="${col.id}">
      <button type="button" class="subcat-row-main" data-color-id="${col.id}">
        <span class="subcat-color-dot" style="background:${col.value}"></span>
        <span class="subcat-name">${esc(col.name)}</span>
        <span class="subcat-summary">${esc(subcatSummary(cat, col, state))}</span>
        <span class="subcat-chevron">${icons.chevronRight}</span>
      </button>
      ${isOpen ? subcatDetail(cat, col, state) : ""}
    </div>
  `;
}

// The Fields list always shows the full effective picture (inherited + custom)
// with a checkmark, so you never have to open the editor just to see what's on.
// Only the "add more" editor collapses by default — and stays collapsed even
// once custom fields exist, instead of forcing itself open forever the moment
// custom.length > 0 (that used to leave the raw checkbox list permanently
// expanded for any subcategory that had ever been customized).
function subcatDetail(cat, col, state) {
  const inherited = inheritedFieldKeys(cat);
  const custom = customFieldKeysFor(cat, col);
  const addableKeys = allFieldKeys(state).filter((k) => !inherited.includes(k) && !custom.includes(k));
  const editingOpen = customizingColorIds.has(col.id);
  const labels = fieldLabels(state);

  const inheritedRows = inherited
    .map(
      (k) => `
        <div class="inherited-field-row"><span class="inherited-check">${icons.checkSmall}</span>${esc(labels[k])}<span class="inherited-tag">Inherited</span></div>`
    )
    .join("");
  const customRows = custom
    .map(
      (k) => `
        <button type="button" class="inherited-field-row is-custom" data-color-id="${col.id}" data-key="${k}" aria-label="Remove ${esc(labels[k])}"><span class="inherited-check">${icons.checkSmall}</span>${esc(labels[k])}<span class="inherited-tag">Custom</span></button>`
    )
    .join("");

  return `
    <div class="subcat-detail">
      <div class="color-row-main">
        <input type="color" class="color-value-input" value="${col.value}" aria-label="Color value" />
        <input type="text" class="color-name-input" value="${esc(col.name)}" placeholder="Subcategory name…" />
        <button type="button" class="remove-btn color-remove-btn" aria-label="Remove subcategory">${icons.trash}</button>
      </div>

      <div class="settings-field-label">Details</div>
      ${
        inherited.length || custom.length
          ? `<div class="inherited-field-list">${inheritedRows}${customRows}</div>`
          : `<div class="empty-hint" style="margin:0 0 4px;">No details yet</div>`
      }

      <div class="subcat-detail-actions">
        <button type="button" class="customize-btn" data-color-id="${col.id}">${editingOpen ? "Done" : "Customize"}</button>
        ${custom.length ? `<button type="button" class="reset-defaults-btn" data-color-id="${col.id}">Reset to Category Defaults</button>` : ""}
      </div>

      ${
        editingOpen
          ? `
        <div class="settings-field-label">Add a Detail</div>
        ${
          addableKeys.length
            ? addableFieldPillsHTML(state, addableKeys, col.id)
            : `<div class="empty-hint" style="margin:0;">All details already added</div>`
        }
      `
          : ""
      }
    </div>
  `;
}

function wireCategoryList(body, state, actions) {
  body.querySelectorAll(".category-block").forEach((block) => {
    const catId = block.dataset.catId;
    const category = draft.categories.find((c) => c.id === catId);

    block.querySelector(".category-name-input").addEventListener("change", (e) => {
      category.name = e.target.value.trim() || "Untitled";
      rerender(body, state, actions);
    });

    block.querySelector(".cat-remove-btn").addEventListener("click", () => {
      showChoice({
        title: `Remove "${esc(category.name)}"?`,
        message: "What should happen to tasks/events that already use this category?",
        choices: [
          { label: "Keep it on those cards — just stop offering it for new ones", value: "archive" },
          { label: "Remove it everywhere — those cards lose it right now", value: "delete", danger: true },
        ],
      }).then((choice) => {
        if (!choice) return;
        if (choice === "delete") {
          draft.categories = draft.categories.filter((c) => c.id !== catId);
        } else {
          category.archived = true;
        }
        rerender(body, state, actions);
        showToast(choice === "delete" ? "Category removed" : "Category hidden from new cards");
      });
    });

    // Scoped to just the category's own Default Fields pills (see
    // "cat-default-fields" in defaultFieldsPillsHTML) — block.querySelectorAll
    // would also match a subcategory's "Add a Field" pills once expanded, since
    // those nest inside this same .category-block and share the same classes.
    const catFieldsWrap = block.querySelector(".cat-default-fields");

    // Clicking a selected (solid) pill removes that field directly — no checkbox
    // needed. "+ Done" toggles whether the not-yet-selected fields show as dashed
    // pills to add; re-rendering keeps that open/closed (openManageFieldsCatIds
    // persists across rerender) while refreshing every subcategory's "N inherited"
    // summary below, since it depends on this category's own enabledFields.
    catFieldsWrap.querySelectorAll(".default-field-pill.is-selected").forEach((pill) => {
      pill.addEventListener("click", () => {
        const key = pill.dataset.key;
        category.enabledFields = (category.enabledFields || []).filter((k) => k !== key);
        rerender(body, state, actions);
      });
    });
    catFieldsWrap.querySelectorAll(".default-field-pill.is-unselected").forEach((pill) => {
      pill.addEventListener("click", () => {
        const key = pill.dataset.key;
        category.enabledFields = [...(category.enabledFields || []), key];
        rerender(body, state, actions);
      });
    });
    catFieldsWrap.querySelector(".default-field-pill.is-more-toggle").addEventListener("click", () => {
      if (openManageFieldsCatIds.has(catId)) openManageFieldsCatIds.delete(catId);
      else openManageFieldsCatIds.add(catId);
      rerender(body, state, actions);
    });
    catFieldsWrap.querySelector(".default-field-pill.is-create-toggle")?.addEventListener("click", () => {
      openFormPopup({
        title: "Add Detail",
        submitLabel: "Add",
        bodyHTML: `<div class="field"><label>Detail Name</label><input type="text" id="new-field-label" placeholder="e.g. Budget" /></div>`,
        onSubmit: ({ panel, close }) => {
          const labelInput = panel.querySelector("#new-field-label");
          const label = labelInput.value.trim();
          if (!label) {
            labelInput.focus();
            return;
          }
          close();
          const field = actions.addCustomFieldDef(label);
          category.enabledFields = [...(category.enabledFields || []), field.key];
          rerender(body, state, actions);
          showToast("Detail added");
        },
      });
    });

    block.querySelectorAll(".subcat-row").forEach((row) => {
      const colorId = row.dataset.colorId;
      const color = category.colors.find((c) => c.id === colorId);

      row.querySelector(".subcat-row-main").addEventListener("click", () => {
        if (openSubcatIds.has(colorId)) openSubcatIds.delete(colorId);
        else openSubcatIds.add(colorId);
        rerender(body, state, actions);
      });

      const detail = row.querySelector(".subcat-detail");
      if (!detail) return;

      detail.querySelector(".color-value-input").addEventListener("input", (e) => {
        color.value = e.target.value;
        const dot = row.querySelector(".subcat-color-dot");
        if (dot) dot.style.background = color.value;
      });

      detail.querySelector(".color-name-input").addEventListener("change", (e) => {
        color.name = e.target.value.trim() || "Subcategory";
        rerender(body, state, actions);
      });

      detail.querySelector(".color-remove-btn").addEventListener("click", () => {
        showChoice({
          title: `Remove "${esc(color.name)}"?`,
          message: "What should happen to tasks/events that already use this subcategory?",
          choices: [
            { label: "Keep it on those cards — just stop offering it for new ones", value: "archive" },
            { label: "Remove it everywhere — those cards lose it right now", value: "delete", danger: true },
          ],
        }).then((choice) => {
          if (!choice) return;
          if (choice === "delete") {
            category.colors = category.colors.filter((c) => c.id !== colorId);
          } else {
            color.archived = true;
          }
          openSubcatIds.delete(colorId);
          customizingColorIds.delete(colorId);
          rerender(body, state, actions);
          showToast(choice === "delete" ? "Subcategory removed" : "Subcategory hidden from new cards");
        });
      });

      // These dashed pills only ever offer fields not already on (see addableKeys
      // in subcatDetail) — clicking one always adds. Removing an already-custom
      // one happens by clicking its row in the Details list above instead (next
      // handler down), not here.
      detail.querySelectorAll(".default-field-pill.is-unselected").forEach((pill) => {
        pill.addEventListener("click", () => {
          const key = pill.dataset.key;
          color.enabledFields = [...(color.enabledFields || []), key];
          rerender(body, state, actions);
        });
      });

      detail.querySelectorAll(".inherited-field-row.is-custom").forEach((row) => {
        row.addEventListener("click", () => {
          const key = row.dataset.key;
          color.enabledFields = (color.enabledFields || []).filter((k) => k !== key);
          rerender(body, state, actions);
        });
      });

      detail.querySelector(".customize-btn")?.addEventListener("click", () => {
        if (customizingColorIds.has(colorId)) customizingColorIds.delete(colorId);
        else customizingColorIds.add(colorId);
        rerender(body, state, actions);
      });

      detail.querySelector(".reset-defaults-btn")?.addEventListener("click", () => {
        color.enabledFields = [];
        customizingColorIds.delete(colorId);
        rerender(body, state, actions);
        showToast("Reset to category defaults");
      });
    });

    block.querySelector(".add-color-btn").addEventListener("click", () => {
      openAddColorPopup(category, body, state, actions);
    });
  });
}

function openAddColorPopup(category, body, state, actions) {
  const suggestedValue = pickUnusedColor(category.colors);
  openFormPopup({
    title: `Add Subcategory to "${esc(category.name)}"`,
    submitLabel: "Add",
    bodyHTML: `
      <div class="add-color-form" style="border:none; padding:0; margin:0;">
        <input type="color" id="new-color-value" value="${suggestedValue}" aria-label="Pick color" />
        <input type="text" id="new-color-hex" value="${suggestedValue}" maxlength="7" />
      </div>
      <div class="field">
        <label>Name</label>
        <input type="text" id="new-color-name" placeholder="e.g. Digital System" />
      </div>
    `,
    onMount: (panel) => {
      const valueInput = panel.querySelector("#new-color-value");
      const hexInput = panel.querySelector("#new-color-hex");
      valueInput.addEventListener("input", () => {
        hexInput.value = valueInput.value;
      });
      hexInput.addEventListener("input", () => {
        if (/^#[0-9a-fA-F]{6}$/.test(hexInput.value)) valueInput.value = hexInput.value;
      });
    },
    onSubmit: ({ panel, close }) => {
      const valueInput = panel.querySelector("#new-color-value");
      const hexInput = panel.querySelector("#new-color-hex");
      const nameInput = panel.querySelector("#new-color-name");
      const value = /^#[0-9a-fA-F]{6}$/.test(hexInput.value) ? hexInput.value : valueInput.value;
      category.colors = [...category.colors, makeColor(nameInput.value || "Subcategory", value)];
      close();
      rerender(body, state, actions);
      showToast("Subcategory added");
    },
  });
}

// Applies immediately (not part of the draft/Save flow). This is the master
// on/off switch for the feature itself — off, and the W/D corner buttons on the
// calendar disappear entirely, not just the tray. While it's on, those buttons
// manage their own persisted hide/show state (weekTrayCollapsed/dayTrayCollapsed)
// independently of this setting.
function wireVisibility(body, state, actions) {
  body.querySelector("#vis-week").addEventListener("click", () => {
    actions.setShowWeekTray(!state.showWeekTray);
  });
  body.querySelector("#vis-day").addEventListener("click", () => {
    actions.setShowDayTray(!state.showDayTray);
  });
  body.querySelector("#vis-todo-target").addEventListener("click", () => {
    actions.setShowTodoTargetTab(!state.showTodoTargetTab);
  });
}

function wireAddCategory(body, state, actions) {
  body.querySelector("#add-cat-btn").addEventListener("click", () => {
    openFormPopup({
      title: "Add Category",
      submitLabel: "Add",
      bodyHTML: `
        <div class="field">
          <label>Name</label>
          <input type="text" id="new-cat-name" placeholder="e.g. Fitness" />
        </div>
      `,
      onSubmit: ({ panel, close }) => {
        const nameInput = panel.querySelector("#new-cat-name");
        const name = nameInput.value.trim();
        if (!name) {
          nameInput.focus();
          return;
        }
        draft.categories = [...draft.categories, { id: uid("cat"), name, colors: [makeColor("General", pickUnusedColor([]))], enabledFields: [] }];
        close();
        rerender(body, state, actions);
        showToast("Category added");
      },
    });
  });
}

// ---------- Suggestion flow ----------

function suggestSection() {
  if (!suggestOpen) {
    return `<button type="button" class="suggest-toggle-btn" id="suggest-toggle">${icons.plusSmall}<span>Suggest categories</span></button>`;
  }

  if (suggestResults) {
    return `
      <div class="suggest-box">
        <div class="suggest-title">Suggested categories</div>
        <div class="suggest-results default-field-pill-row">
          ${suggestResults
            .map(
              (name, i) => `
            <button type="button" class="default-field-pill ${suggestSelected.has(i) ? "is-selected" : "is-unselected"}" data-idx="${i}">${suggestSelected.has(i) ? "" : icons.plusSmall}<span>${esc(name)}</span></button>
          `
            )
            .join("")}
        </div>
        <div class="suggest-actions">
          <button class="btn btn-ghost" id="sg-back">Back</button>
          <button class="btn btn-primary" id="sg-add-selected">Add Selected</button>
        </div>
      </div>
    `;
  }

  return `
    <div class="suggest-box">
      <div class="suggest-title">Suggest categories</div>
      <div class="field-row">
        <div class="field" style="flex:0 0 90px;">
          <label>How many?</label>
          <input type="number" id="sg-count" min="1" max="8" value="${suggestCount}" />
        </div>
        <div class="field">
          <label>About what?</label>
          <input type="text" id="sg-topic" placeholder="e.g. college schedule, gym routine…" value="${esc(suggestTopic)}" />
        </div>
      </div>
      <div class="suggest-actions">
        <button class="btn btn-ghost" id="sg-cancel">Cancel</button>
        <button class="btn btn-primary" id="sg-generate">Generate</button>
      </div>
    </div>
  `;
}

function wireSuggestSection(body, state, actions) {
  const toggleBtn = body.querySelector("#suggest-toggle");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      suggestOpen = true;
      suggestResults = null;
      rerender(body, state, actions);
    });
    return;
  }

  const cancelBtn = body.querySelector("#sg-cancel");
  if (cancelBtn) {
    cancelBtn.addEventListener("click", () => {
      suggestOpen = false;
      rerender(body, state, actions);
    });
  }

  const generateBtn = body.querySelector("#sg-generate");
  if (generateBtn) {
    generateBtn.addEventListener("click", () => {
      const countInput = body.querySelector("#sg-count");
      const topicInput = body.querySelector("#sg-topic");
      suggestCount = Math.max(1, Math.min(Number(countInput.value) || 1, 8));
      suggestTopic = topicInput.value;
      suggestResults = suggestCategories(suggestTopic, suggestCount);
      suggestSelected = new Set(suggestResults.map((_, i) => i));
      rerender(body, state, actions);
    });
  }

  const backBtn = body.querySelector("#sg-back");
  if (backBtn) {
    backBtn.addEventListener("click", () => {
      suggestResults = null;
      rerender(body, state, actions);
    });
  }

  body.querySelectorAll(".suggest-results .default-field-pill[data-idx]").forEach((pill) => {
    pill.addEventListener("click", () => {
      const idx = Number(pill.dataset.idx);
      if (suggestSelected.has(idx)) suggestSelected.delete(idx);
      else suggestSelected.add(idx);
      rerender(body, state, actions);
    });
  });

  const addSelectedBtn = body.querySelector("#sg-add-selected");
  if (addSelectedBtn) {
    addSelectedBtn.addEventListener("click", () => {
      const toAdd = (suggestResults || []).filter((_, i) => suggestSelected.has(i));
      suggestOpen = false;
      suggestResults = null;
      suggestTopic = "";
      draft.categories = [
        ...draft.categories,
        ...toAdd.map((name) => ({ id: uid("cat"), name, colors: [makeColor("General", pickUnusedColor([]))], enabledFields: [] })),
      ];
      rerender(body, state, actions);
    });
  }
}

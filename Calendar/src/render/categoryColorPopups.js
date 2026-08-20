import { esc } from "../utils.js";
import { openFormPopup, showToast } from "./notify.js";
import { pickUnusedColor } from "../categoryColor.js";

// Shared "+ Add Category" / "+ Add Color" popups used by every Add/Edit-style form
// (addModal.js, addChooserModal.js) so a new category or color can be created inline
// without leaving the form. Both commit straight to the store (no draft step) — that's
// only appropriate here because none of these forms have a Save-later staging area of
// their own. Settings' own category editor uses a draft instead, so it wires its own
// equivalent popups against that draft rather than these.

export function openAddCategoryPopup({ actions, onAdded }) {
  openFormPopup({
    title: "Add Category",
    submitLabel: "Add",
    bodyHTML: `<div class="field"><label>Name</label><input type="text" id="new-cat-name" placeholder="e.g. Fitness" /></div>`,
    onSubmit: ({ panel, close }) => {
      const nameInput = panel.querySelector("#new-cat-name");
      const name = nameInput.value.trim();
      if (!name) {
        nameInput.focus();
        return;
      }
      close();
      const category = actions.addCategory(name);
      showToast("Category added");
      onAdded(category);
    },
  });
}

export function openAddColorPopup({ actions, category, onAdded }) {
  const suggestedValue = pickUnusedColor(category.colors);
  openFormPopup({
    title: `Add Color to "${esc(category.name)}"`,
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
      close();
      const color = actions.addColorToCategory(category.id, { name: nameInput.value || "Color", value });
      showToast("Color added");
      onAdded(color);
    },
  });
}

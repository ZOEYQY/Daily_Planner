// Single source of truth for "Extra Fields" (Link/Place/People/... toggles on a
// task/event/special day) — used by both addModal.js and settingsPanel.js, which
// used to keep their own separately-maintained copies of this list.
//
// FIXED_FIELD_DEFS are built-in; state.customFieldDefs holds ones a user has
// created (via Settings or the Add/Edit modal's own "+" — see addCustomFieldDef in
// state.js). Custom field values live in item.customFields[key], kept separate from
// the fixed fields' own top-level properties (item.link, item.topic, etc).
export const FIXED_FIELD_DEFS = [
  { key: "link", label: "Link", inputId: "f-link", type: "url", placeholder: "https://…" },
  { key: "place", label: "Place", inputId: "f-place", type: "text", placeholder: "" },
  { key: "people", label: "People", inputId: "f-people", type: "text", placeholder: "e.g. Alice, Bob" },
  { key: "thingsToBring", label: "Things to Bring", inputId: "f-things", type: "text", placeholder: "e.g. Sunscreen, water bottle" },
  { key: "topic", label: "Topic", inputId: "f-topic", type: "text", placeholder: "" },
];

export const TODO_LIST_KEY = "todoList";

function customFieldDef(field) {
  return { key: field.key, label: field.label, inputId: `f-custom-${field.key}`, type: "text", placeholder: "", custom: true };
}

// All field definitions (fixed + custom), NOT including the To-Do List toggle —
// that one gets its own checklist widget instead of a plain text input.
export function allFieldDefs(state) {
  return [...FIXED_FIELD_DEFS, ...(state.customFieldDefs || []).map(customFieldDef)];
}

// Every toggleable key, including "todoList".
export function allFieldKeys(state) {
  return [...allFieldDefs(state).map((f) => f.key), TODO_LIST_KEY];
}

export function fieldLabels(state) {
  return { ...Object.fromEntries(allFieldDefs(state).map((f) => [f.key, f.label])), [TODO_LIST_KEY]: "To-Do List" };
}

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

function customFieldDef(field) {
  return { key: field.key, label: field.label, inputId: `f-custom-${field.key}`, type: "text", placeholder: "", custom: true };
}

// All field definitions (fixed + custom).
export function allFieldDefs(state) {
  return [...FIXED_FIELD_DEFS, ...(state.customFieldDefs || []).map(customFieldDef)];
}

// Every toggleable key. To-Do List and Target used to be here too (as
// TODO_LIST_KEY) but aren't per-category/per-item toggles any more — every
// task/event always has both, in their own modal tab (see addModal.js), gated
// only by the single Settings feature switch rather than this enabled-fields
// system.
export function allFieldKeys(state) {
  return allFieldDefs(state).map((f) => f.key);
}

export function fieldLabels(state) {
  return Object.fromEntries(allFieldDefs(state).map((f) => [f.key, f.label]));
}

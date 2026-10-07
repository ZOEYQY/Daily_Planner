import { icons } from "../icons.js";
import { esc } from "../utils.js";

// Notes are a list of short points, one per line. They're still stored as a
// single `notes` string (one point per line) so the backend, occurrence
// overrides and older multi-line notes all keep working unchanged — an old
// "T15L\nCP6114" note simply opens as two points.
export function notesToList(notes) {
  return String(notes || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

// Markup for the Notes field. The hidden #f-notes input always holds the
// current joined value (saved points plus anything typed but not yet added),
// so the forms' existing save and snapshot code can keep reading
// root.querySelector("#f-notes").value.
export function notesFieldHTML(notes) {
  return `
    <label>Notes (optional)</label>
    <input type="hidden" id="f-notes" value="${esc(notes || "")}" />
    <div class="todo-list notes-list" id="f-notes-list"></div>
    <div class="todo-add-row">
      <input type="text" id="f-notes-new" placeholder="Add a note…" />
      <button type="button" class="btn btn-ghost" id="f-notes-add-btn" aria-label="Add note">${icons.plusSmall}</button>
    </div>`;
}

// Wires the Notes field rendered by notesFieldHTML inside root. Setting the
// hidden input's value and dispatching "input" on it (as restoreFormSnapshot in
// addModal.js does) reloads the list from that value.
export function wireNotesField(root) {
  const hidden = root.querySelector("#f-notes");
  const listEl = root.querySelector("#f-notes-list");
  const newInput = root.querySelector("#f-notes-new");
  const addBtn = root.querySelector("#f-notes-add-btn");
  if (!hidden || !listEl || !newInput) return;

  let points = notesToList(hidden.value);

  const sync = () => {
    const pending = newInput.value.trim();
    hidden.value = [...points.map((p) => p.trim()).filter(Boolean), ...(pending ? [pending] : [])].join("\n");
  };

  function render() {
    listEl.innerHTML = points
      .map(
        (text, i) => `
      <div class="todo-item-row note-item-row">
        <span class="note-bullet" aria-hidden="true">•</span>
        <input type="text" class="note-item-input" data-index="${i}" value="${esc(text)}" aria-label="Note ${i + 1}" />
        <button type="button" class="remove-btn todo-item-remove note-item-remove" data-index="${i}" aria-label="Remove note">${icons.close}</button>
      </div>`
      )
      .join("");
    listEl.querySelectorAll(".note-item-input").forEach((input) => {
      input.addEventListener("input", () => {
        points[Number(input.dataset.index)] = input.value;
        sync();
      });
      // Enter on an existing point jumps to the "Add a note" box instead of
      // submitting the whole form.
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          newInput.focus();
        }
      });
    });
    listEl.querySelectorAll(".note-item-remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        points.splice(Number(btn.dataset.index), 1);
        sync();
        render();
      });
    });
    listEl.style.display = points.length ? "" : "none";
  }

  const submitAdd = () => {
    const text = newInput.value.trim();
    if (!text) return;
    points = [...points, text];
    newInput.value = "";
    sync();
    render();
    newInput.focus();
  };
  addBtn?.addEventListener("click", submitAdd);
  newInput.addEventListener("input", sync);
  newInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
      submitAdd();
    }
  });
  hidden.addEventListener("input", () => {
    points = notesToList(hidden.value);
    newInput.value = "";
    render();
  });

  render();
}

// Read-only bullet list for showing notes on a card or chip.
export function notesBulletsHTML(notes, className = "notes-bullets") {
  const points = notesToList(notes);
  if (points.length === 0) return "";
  return `<ul class="${className}">${points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`;
}

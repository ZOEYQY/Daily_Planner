import { icons } from "../icons.js";

// Shared notification surfaces — every confirm dialog, add-popup, and toast in the
// app goes through here so they always look and animate the same way.

let toastRoot = null;
function ensureToastRoot() {
  if (!toastRoot) {
    toastRoot = document.createElement("div");
    toastRoot.id = "toast-root";
    document.body.appendChild(toastRoot);
  }
  return toastRoot;
}

let popupRoot = null;
function ensurePopupRoot() {
  if (!popupRoot) {
    popupRoot = document.createElement("div");
    popupRoot.id = "popup-root";
    document.body.appendChild(popupRoot);
  }
  return popupRoot;
}

export function showToast(message, { variant = "default" } = {}) {
  const root = ensureToastRoot();
  const glyph = variant === "danger" ? icons.trash : icons.check;
  const el = document.createElement("div");
  el.className = `toast toast-${variant}`;
  el.innerHTML = `<span class="toast-icon">${glyph}</span><span class="toast-msg">${message}</span>`;
  root.appendChild(el);
  requestAnimationFrame(() => el.classList.add("is-visible"));
  setTimeout(() => {
    el.classList.remove("is-visible");
    setTimeout(() => el.remove(), 220);
  }, 2400);
}

// Renders a small stacked popup (works on top of an already-open modal) and wires
// Escape/overlay-click/Cancel to resolve `false`, and the primary action to whatever
// onSubmit decides. onSubmit receives {panel, close} and must call close() itself —
// this lets callers validate input before dismissing.
export function openFormPopup({ title, bodyHTML, submitLabel = "Add", cancelLabel = "Cancel", onMount, onSubmit }) {
  const root = ensurePopupRoot();
  const overlay = document.createElement("div");
  overlay.className = "popup-overlay";
  overlay.innerHTML = `
    <div class="popup-panel" role="dialog" aria-modal="true">
      <div class="popup-title">${title}</div>
      <div class="popup-body">${bodyHTML}</div>
      <div class="popup-actions">
        <button type="button" class="btn btn-ghost" id="popup-cancel">${cancelLabel}</button>
        <button type="button" class="btn btn-primary" id="popup-confirm">${submitLabel}</button>
      </div>
    </div>
  `;
  const panel = overlay.querySelector(".popup-panel");

  // Captured (not bubbled) so this popup's Escape/Enter wins over the app's own
  // global Escape-closes-modal listener, which would otherwise also fire and close
  // whatever modal this popup is stacked on top of.
  const onKeydown = (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
    } else if (e.key === "Enter" && e.target.tagName === "INPUT") {
      e.preventDefault();
      e.stopPropagation();
      overlay.querySelector("#popup-confirm").click();
    }
  };

  function close() {
    document.removeEventListener("keydown", onKeydown, true);
    overlay.classList.remove("is-visible");
    setTimeout(() => overlay.remove(), 150);
  }

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  overlay.querySelector("#popup-cancel").addEventListener("click", close);
  overlay.querySelector("#popup-confirm").addEventListener("click", () => onSubmit({ panel, close }));
  document.addEventListener("keydown", onKeydown, true);

  root.appendChild(overlay);
  onMount?.(panel);
  requestAnimationFrame(() => overlay.classList.add("is-visible"));
  panel.querySelector("input, textarea")?.focus();

  return { close };
}

// Promise-based replacement for window.confirm(), styled to match the rest of the app.
export function showConfirm({ title = "Are you sure?", message = "", confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false } = {}) {
  return new Promise((resolve) => {
    const root = ensurePopupRoot();
    const overlay = document.createElement("div");
    overlay.className = "popup-overlay";
    overlay.innerHTML = `
      <div class="popup-panel" role="alertdialog" aria-modal="true">
        <div class="popup-title">${title}</div>
        ${message ? `<div class="popup-message">${message}</div>` : ""}
        <div class="popup-actions">
          <button type="button" class="btn btn-ghost" id="popup-cancel">${cancelLabel}</button>
          <button type="button" class="btn ${danger ? "btn-danger-solid" : "btn-primary"}" id="popup-confirm">${confirmLabel}</button>
        </div>
      </div>
    `;

    const onKeydown = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        settle(false);
      }
    };

    function settle(result) {
      document.removeEventListener("keydown", onKeydown, true);
      overlay.classList.remove("is-visible");
      setTimeout(() => overlay.remove(), 150);
      resolve(result);
    }

    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) settle(false);
    });
    overlay.querySelector("#popup-cancel").addEventListener("click", () => settle(false));
    overlay.querySelector("#popup-confirm").addEventListener("click", () => settle(true));
    document.addEventListener("keydown", onKeydown, true);

    root.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add("is-visible"));
  });
}

// Like showConfirm, but for a decision with more than one real option (e.g. "delete
// everywhere" vs "just hide going forward") instead of a plain yes/no. Resolves with
// the chosen option's `value`, or `null` if cancelled/dismissed.
export function showChoice({ title = "", message = "", choices = [], cancelLabel = "Cancel" } = {}) {
  return new Promise((resolve) => {
    const root = ensurePopupRoot();
    const overlay = document.createElement("div");
    overlay.className = "popup-overlay";
    overlay.innerHTML = `
      <div class="popup-panel" role="alertdialog" aria-modal="true">
        <div class="popup-title">${title}</div>
        ${message ? `<div class="popup-message">${message}</div>` : ""}
        <div class="popup-choice-list">
          ${choices
            .map(
              (c, i) => `
            <button type="button" class="btn ${c.danger ? "btn-danger-ghost" : "btn-secondary"} popup-choice-btn" data-idx="${i}">${c.label}</button>`
            )
            .join("")}
        </div>
        <div class="popup-actions">
          <button type="button" class="btn btn-ghost" id="popup-cancel">${cancelLabel}</button>
        </div>
      </div>
    `;

    const onKeydown = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        settle(null);
      }
    };

    function settle(result) {
      document.removeEventListener("keydown", onKeydown, true);
      overlay.classList.remove("is-visible");
      setTimeout(() => overlay.remove(), 150);
      resolve(result);
    }

    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) settle(null);
    });
    overlay.querySelector("#popup-cancel").addEventListener("click", () => settle(null));
    overlay.querySelectorAll(".popup-choice-btn").forEach((btn) => {
      btn.addEventListener("click", () => settle(choices[Number(btn.dataset.idx)].value));
    });
    document.addEventListener("keydown", onKeydown, true);

    root.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add("is-visible"));
  });
}

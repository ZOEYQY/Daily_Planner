import { icons } from "../icons.js";
import { esc } from "../utils.js";
import { requireAuth } from "../authGate.js";

function initials(name) {
  return (name || "").trim().charAt(0).toUpperCase() || "?";
}

export function renderTopbar(root, state, actions, currentUser) {
  root.className = "topbar";
  root.innerHTML = `
    <div class="topbar-left">
      <h1 class="topbar-title">Calendar</h1>
      <div class="topbar-search ${state.searchOpen ? "" : "is-collapsed"}">
        ${icons.search}
        <input type="text" id="search-input" placeholder="Search tasks &amp; events…" value="${esc(state.searchQuery)}" />
      </div>
    </div>
    <div class="topbar-right">
      <button class="icon-btn ${state.searchOpen ? "is-active" : ""}" id="btn-search-toggle" title="Search" aria-label="Search">${icons.search}</button>
      <button class="icon-btn account-btn" id="btn-account" title="${currentUser ? esc(currentUser.name) : "Log In / Sign Up"}" aria-label="Account">
        ${currentUser ? `<span class="account-avatar">${esc(initials(currentUser.name))}</span>` : icons.user}
      </button>
      <button class="icon-btn" id="btn-settings" title="Settings" aria-label="Settings">${icons.settings}</button>
      <button class="btn btn-primary" id="btn-add">${icons.plus}<span>Add</span></button>
    </div>
  `;

  const searchWrap = root.querySelector(".topbar-search");
  searchWrap.style.display = state.searchOpen ? "flex" : "none";

  root.querySelector("#btn-search-toggle").addEventListener("click", () => {
    actions.setSearchOpen(!state.searchOpen);
    if (!state.searchOpen) {
      requestAnimationFrame(() => root.querySelector("#search-input")?.focus());
    }
  });

  root.querySelector("#search-input").addEventListener("input", (e) => {
    actions.setSearchQuery(e.target.value);
  });

  root.querySelector("#btn-account").addEventListener("click", () => {
    actions.openModal(currentUser ? { type: "profile" } : { type: "auth", step: "login" });
  });

  root.querySelector("#btn-settings").addEventListener("click", () => {
    requireAuth(currentUser, actions, () => actions.openModal({ type: "settings" }));
  });

  root.querySelector("#btn-add").addEventListener("click", () => {
    requireAuth(currentUser, actions, () => actions.openModal({ type: "add-chooser" }));
  });
}

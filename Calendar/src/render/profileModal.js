import { icons } from "../icons.js";
import { esc } from "../utils.js";
import { showToast } from "./notify.js";

export function renderProfileModal(root, authState, authActions, actions) {
  const user = authState.users.find((u) => u.id === authState.currentUserId);
  if (!user) {
    actions.closeModal();
    return;
  }

  root.innerHTML = `
    <div class="modal-overlay" id="overlay">
      <div class="modal-panel" role="dialog" aria-modal="true" style="max-width:380px;">
        <div class="modal-header">
          <h2>Profile</h2>
          <button class="modal-close" id="close-btn">${icons.close}</button>
        </div>
        <div class="modal-body">
          <div class="field">
            <label>Name</label>
            <input type="text" id="f-name" value="${esc(user.name)}" />
          </div>
          <div class="field">
            <label>Email</label>
            <input type="email" value="${esc(user.email)}" disabled />
          </div>
          <span class="profile-verified-badge">${icons.checkSmall}<span>Verified</span></span>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-danger-ghost" id="logout-btn">Log Out</button>
          <div class="modal-footer-spacer"></div>
          <button type="button" class="btn btn-secondary" id="cancel-btn">Cancel</button>
          <button type="button" class="btn btn-primary" id="save-btn">Save Changes</button>
        </div>
      </div>
    </div>
  `;

  root.querySelector("#overlay").addEventListener("click", (e) => {
    if (e.target.id === "overlay") actions.closeModal();
  });
  root.querySelector("#close-btn").addEventListener("click", () => actions.closeModal());
  root.querySelector("#cancel-btn").addEventListener("click", () => actions.closeModal());

  root.querySelector("#logout-btn").addEventListener("click", () => {
    authActions.logOut();
    actions.closeModal();
    showToast("Logged out");
  });

  root.querySelector("#save-btn").addEventListener("click", () => {
    const name = root.querySelector("#f-name").value.trim();
    if (!name) {
      root.querySelector("#f-name").focus();
      return;
    }
    authActions.updateProfile(user.id, { name });
    actions.closeModal();
    showToast("Profile updated");
  });
}

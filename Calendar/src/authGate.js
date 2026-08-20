import { showToast } from "./render/notify.js";

// Guest (no logged-in user) is a view-only demo shell — every entry point that
// would create/edit/configure something routes through here instead of running
// directly, so guests can navigate and look around but never actually do anything.
export function requireAuth(currentUser, actions, action) {
  if (currentUser) {
    action();
    return true;
  }
  showToast("Sign in to do that");
  actions.openModal({ type: "auth", step: "login" });
  return false;
}

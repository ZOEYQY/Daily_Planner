import { icons } from "../icons.js";
import { esc } from "../utils.js";
import { showToast } from "./notify.js";

// Field-validation errors shown inline in the form — distinct from showToast (which
// is for transient confirmations) since these need to stay visible until fixed.
let formError = null;
let lastModalKey = null;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function wireClose(root, actions) {
  root.querySelector("#overlay").addEventListener("click", (e) => {
    if (e.target.id === "overlay") actions.closeModal();
  });
  root.querySelector("#close-btn").addEventListener("click", () => actions.closeModal());
}

function wireEnterToSubmit(root, submit) {
  root.querySelectorAll("input").forEach((inp) => {
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
    });
  });
}

export function renderAuthModal(root, state, authState, actions, authActions) {
  const step = state.modal?.step || "login";
  const modalKey = `${step}:${state.modal?.userId || ""}`;
  if (modalKey !== lastModalKey) {
    formError = null;
    lastModalKey = modalKey;
  }

  if (step === "signup") return renderSignup(root, state, actions, authActions);
  if (step === "verify") return renderVerify(root, state, authState, actions, authActions);
  return renderLogin(root, state, actions, authActions);
}

function renderLogin(root, state, actions, authActions) {
  root.innerHTML = `
    <div class="modal-overlay" id="overlay">
      <div class="modal-panel" role="dialog" aria-modal="true" style="max-width:380px;">
        <div class="modal-header">
          <h2>Log In</h2>
          <button class="modal-close" id="close-btn">${icons.close}</button>
        </div>
        <div class="modal-body">
          <div class="field">
            <label>Email</label>
            <input type="email" id="f-email" placeholder="you@example.com" />
          </div>
          <div class="field">
            <label>Password</label>
            <input type="password" id="f-password" />
          </div>
          ${formError ? `<div class="field-error">${esc(formError)}</div>` : ""}
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-ghost" id="switch-signup">Sign Up instead</button>
          <div class="modal-footer-spacer"></div>
          <button type="button" class="btn btn-primary" id="submit-btn">Log In</button>
        </div>
      </div>
    </div>
  `;
  wireClose(root, actions);

  root.querySelector("#switch-signup").addEventListener("click", () => {
    actions.openModal({ type: "auth", step: "signup" });
  });

  const submit = () => {
    const email = root.querySelector("#f-email").value.trim();
    const password = root.querySelector("#f-password").value;
    if (!email || !password) {
      formError = "Enter your email and password.";
      renderLogin(root, state, actions, authActions);
      return;
    }
    const result = authActions.logIn({ email, password });
    if (result.ok) {
      actions.closeModal();
      showToast("Logged in");
      return;
    }
    if (result.reason === "unverified") {
      actions.openModal({ type: "auth", step: "verify", userId: result.userId });
      return;
    }
    formError = result.reason === "bad-password" ? "Incorrect password." : "No account with that email.";
    renderLogin(root, state, actions, authActions);
  };

  root.querySelector("#submit-btn").addEventListener("click", submit);
  wireEnterToSubmit(root, submit);
}

function renderSignup(root, state, actions, authActions) {
  root.innerHTML = `
    <div class="modal-overlay" id="overlay">
      <div class="modal-panel" role="dialog" aria-modal="true" style="max-width:380px;">
        <div class="modal-header">
          <h2>Sign Up</h2>
          <button class="modal-close" id="close-btn">${icons.close}</button>
        </div>
        <div class="modal-body">
          <div class="field">
            <label>Name</label>
            <input type="text" id="f-name" placeholder="Your name" />
          </div>
          <div class="field">
            <label>Email</label>
            <input type="email" id="f-email" placeholder="you@example.com" />
          </div>
          <div class="field">
            <label>Password</label>
            <input type="password" id="f-password" />
          </div>
          <div class="field">
            <label>Confirm Password</label>
            <input type="password" id="f-confirm" />
          </div>
          ${formError ? `<div class="field-error">${esc(formError)}</div>` : ""}
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-ghost" id="switch-login">Log In instead</button>
          <div class="modal-footer-spacer"></div>
          <button type="button" class="btn btn-primary" id="submit-btn">Sign Up</button>
        </div>
      </div>
    </div>
  `;
  wireClose(root, actions);

  root.querySelector("#switch-login").addEventListener("click", () => {
    actions.openModal({ type: "auth", step: "login" });
  });

  const submit = () => {
    const name = root.querySelector("#f-name").value.trim();
    const email = root.querySelector("#f-email").value.trim();
    const password = root.querySelector("#f-password").value;
    const confirm = root.querySelector("#f-confirm").value;

    if (!name) {
      formError = "Enter your name.";
    } else if (!EMAIL_RE.test(email)) {
      formError = "Enter a valid email address.";
    } else if (password.length < 6) {
      formError = "Password must be at least 6 characters.";
    } else if (password !== confirm) {
      formError = "Passwords don't match.";
    } else {
      formError = null;
    }
    if (formError) {
      renderSignup(root, state, actions, authActions);
      return;
    }

    const result = authActions.signUp({ name, email, password });
    if (!result.ok) {
      formError = "An account with that email already exists.";
      renderSignup(root, state, actions, authActions);
      return;
    }
    actions.openModal({ type: "auth", step: "verify", userId: result.userId });
  };

  root.querySelector("#submit-btn").addEventListener("click", submit);
  wireEnterToSubmit(root, submit);
}

function renderVerify(root, state, authState, actions, authActions) {
  const userId = state.modal.userId;
  const user = authState.users.find((u) => u.id === userId);
  if (!user) {
    actions.closeModal();
    return;
  }

  root.innerHTML = `
    <div class="modal-overlay" id="overlay">
      <div class="modal-panel" role="dialog" aria-modal="true" style="max-width:380px;">
        <div class="modal-header">
          <h2>Verify Your Email</h2>
          <button class="modal-close" id="close-btn">${icons.close}</button>
        </div>
        <div class="modal-body">
          <p style="font-size:13px; color:var(--color-ink-secondary); line-height:1.5; margin:0;">
            We'd normally send a code to <strong>${esc(user.email)}</strong>.
          </p>
          <div class="verify-code-callout">
            Real email isn't connected yet — here's your code for now:
            <span class="verify-code-callout-code">${esc(user.verificationCode || "")}</span>
          </div>
          <div class="field">
            <label>Verification Code</label>
            <input type="text" id="f-code" inputmode="numeric" maxlength="6" placeholder="123456" />
          </div>
          ${formError ? `<div class="field-error">${esc(formError)}</div>` : ""}
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-ghost" id="resend-btn">Resend Code</button>
          <div class="modal-footer-spacer"></div>
          <button type="button" class="btn btn-primary" id="submit-btn">Verify</button>
        </div>
      </div>
    </div>
  `;
  wireClose(root, actions);

  root.querySelector("#resend-btn").addEventListener("click", () => {
    authActions.resendCode(userId);
    showToast("New code generated");
  });

  const submit = () => {
    const code = root.querySelector("#f-code").value.trim();
    const result = authActions.verifyEmail(userId, code);
    if (!result.ok) {
      formError = "Incorrect code.";
      renderVerify(root, state, authState, actions, authActions);
      return;
    }
    actions.closeModal();
    showToast("Email verified — you're logged in");
  };

  root.querySelector("#submit-btn").addEventListener("click", submit);
  wireEnterToSubmit(root, submit);
}

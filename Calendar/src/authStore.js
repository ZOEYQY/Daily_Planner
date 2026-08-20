import { uid } from "./seed.js";

const AUTH_KEY = "monoCalendar.auth.v1";

function loadAuth() {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || !Array.isArray(parsed.users)) return null;
    return parsed;
  } catch {
    return null;
  }
}

function persistAuth(state) {
  try {
    localStorage.setItem(AUTH_KEY, JSON.stringify({ users: state.users, currentUserId: state.currentUserId }));
  } catch {
    /* storage unavailable — auth still works in-memory for this session */
  }
}

function normalizeEmail(email) {
  return (email || "").trim().toLowerCase();
}

function genCode() {
  return String(Math.floor(100000 + Math.random() * 900000));
}

class AuthStore {
  constructor() {
    const persisted = loadAuth();
    this.state = { users: persisted?.users || [], currentUserId: persisted?.currentUserId || null };
    this.listeners = new Set();
  }

  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  set(patch) {
    this.state = { ...this.state, ...(typeof patch === "function" ? patch(this.state) : patch) };
    persistAuth(this.state);
    this.listeners.forEach((fn) => fn(this.state));
  }

  // No real backend yet, so there's nowhere secure to hash/salt a password against —
  // it's stored as plain text here purely to drive the mock sign-up/login UI flow.
  // MOCK ONLY — never do this once a real backend exists.
  signUp({ name, email, password }) {
    const normalizedEmail = normalizeEmail(email);
    if (this.state.users.some((u) => u.email === normalizedEmail)) {
      return { ok: false, reason: "duplicate-email" };
    }
    const user = {
      id: uid("user"),
      name: (name || "").trim() || "Unnamed",
      email: normalizedEmail,
      password,
      verified: false,
      verificationCode: genCode(),
      createdAt: Date.now(),
    };
    this.set((s) => ({ users: [...s.users, user] }));
    return { ok: true, userId: user.id };
  }

  verifyEmail(userId, code) {
    const user = this.state.users.find((u) => u.id === userId);
    if (!user || user.verificationCode !== code) {
      return { ok: false, reason: "bad-code" };
    }
    this.set((s) => ({
      users: s.users.map((u) => (u.id === userId ? { ...u, verified: true, verificationCode: null } : u)),
      currentUserId: userId,
    }));
    return { ok: true };
  }

  resendCode(userId) {
    this.set((s) => ({ users: s.users.map((u) => (u.id === userId ? { ...u, verificationCode: genCode() } : u)) }));
  }

  logIn({ email, password }) {
    const normalizedEmail = normalizeEmail(email);
    const user = this.state.users.find((u) => u.email === normalizedEmail);
    if (!user) return { ok: false, reason: "not-found" };
    if (user.password !== password) return { ok: false, reason: "bad-password" };
    if (!user.verified) return { ok: false, reason: "unverified", userId: user.id };
    this.set({ currentUserId: user.id });
    return { ok: true, userId: user.id };
  }

  logOut() {
    this.set({ currentUserId: null });
  }

  updateProfile(userId, patch) {
    this.set((s) => ({ users: s.users.map((u) => (u.id === userId ? { ...u, ...patch } : u)) }));
  }

  getCurrentUser() {
    return this.state.users.find((u) => u.id === this.state.currentUserId) || null;
  }
}

export const authStore = new AuthStore();

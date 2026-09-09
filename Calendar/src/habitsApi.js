// Thin wrapper over the habit (打卡) API in Calendar/habits.py. "./api" resolves
// to /calendar/api when the page is served at /calendar/ (combined app.py) and to
// /api when served at / (Calendar/app.py) — both mount the same blueprint.
// Anything that opens the calendar WITHOUT that server (file://, Live Server, …)
// gets a rejected promise here; habitsView.js turns that into a friendly notice.
const BASE = "./api";

async function req(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const habitsApi = {
  list: () => req("/habits"),
  create: (data) => req("/habits", { method: "POST", body: JSON.stringify(data) }),
  update: (id, data) => req(`/habits/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  remove: (id) => req(`/habits/${id}`, { method: "DELETE" }),
  toggleCheckin: (habitId, date) =>
    req("/checkins", { method: "POST", body: JSON.stringify({ habit_id: habitId, date }) }),
};

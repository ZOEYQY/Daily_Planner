import { esc } from "../utils.js";

export function renderStatsWidget(root, state) {
  const rows = state.categories
    .map((c) => ({
      name: c.name,
      count: state.tasks.filter((t) => t.categoryId === c.id).length + state.events.filter((e) => e.categoryId === c.id).length,
    }))
    .sort((a, b) => b.count - a.count);

  const max = Math.max(1, ...rows.map((r) => r.count));

  root.innerHTML = `
    <div class="stats-card">
      <div class="stats-title">Items by Category</div>
      ${
        rows.every((r) => r.count === 0)
          ? `<div class="stats-empty">No tasks or events yet.</div>`
          : rows
              .map(
                (r) => `
        <div class="stats-row">
          <div class="stats-label">${esc(r.name)}</div>
          <div class="stats-track"><div class="stats-fill" style="width:${(r.count / max) * 100}%"></div></div>
          <div class="stats-value">${r.count}</div>
        </div>`
              )
              .join("")
      }
    </div>
  `;
}

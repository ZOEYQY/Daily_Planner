const escapeMap = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(str) {
  return String(str ?? "").replace(/[&<>"']/g, (ch) => escapeMap[ch]);
}

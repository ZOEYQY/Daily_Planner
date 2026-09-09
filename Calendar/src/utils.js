const escapeMap = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(str) {
  return String(str ?? "").replace(/[&<>"']/g, (ch) => escapeMap[ch]);
}

// Near-black or white — whichever stays readable as text/icons on a solid fill
// of `hex`. Used where a chip/row is painted in its category colour (which can
// be anything from #000 to a pale pink) and the text has to follow. Falls back
// to dark ink for anything it can't parse.
export function inkOn(hex) {
  const m = /^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.exec(String(hex ?? "").trim());
  if (!m) return "#1b1b1b";
  let h = m[1];
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  // Perceived brightness (ITU-R BT.601). >0.62 → the fill is light, use dark ink.
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.62 ? "#1b1b1b" : "#ffffff";
}

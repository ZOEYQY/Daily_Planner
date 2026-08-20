import { uid } from "./seed.js";
import { SWATCH_COLORS } from "./swatches.js";

export function makeColor(name, value) {
  return { id: uid("color"), name: name.trim() || "Color", value, enabledFields: [] };
}

export function pickUnusedColor(existingColors) {
  const used = new Set(existingColors.map((c) => c.value));
  return SWATCH_COLORS.find((c) => !used.has(c)) || SWATCH_COLORS[existingColors.length % SWATCH_COLORS.length];
}

function uid(prefix) {
  return `${prefix}_${Math.random().toString(36).slice(2, 9)}`;
}

export function buildSeedData() {
  return { categories: [], tasks: [], events: [] };
}

export { uid };

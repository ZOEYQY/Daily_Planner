function toMinutes(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

/**
 * Assigns each event a column index + column count among events it overlaps with,
 * so overlapping events render side-by-side instead of on top of each other.
 * Events are expected to already belong to a single day, sorted by start time.
 */
export function layoutDayEvents(events) {
  const sorted = [...events].sort((a, b) => toMinutes(a.startTime) - toMinutes(b.startTime));
  const results = [];
  let cluster = [];
  let clusterEnd = -Infinity;

  const flushCluster = () => {
    if (cluster.length === 0) return;
    const columnEnds = [];
    const placed = [];
    for (const evt of cluster) {
      const start = toMinutes(evt.startTime);
      const end = Math.max(toMinutes(evt.endTime), start + 15);
      let col = columnEnds.findIndex((endTime) => endTime <= start);
      if (col === -1) {
        col = columnEnds.length;
        columnEnds.push(end);
      } else {
        columnEnds[col] = end;
      }
      placed.push({ evt, col, start, end });
    }
    const cols = columnEnds.length;
    placed.forEach(({ evt, col, start, end }) => {
      results.push({ event: evt, col, cols, startMin: start, endMin: end });
    });
    cluster = [];
    clusterEnd = -Infinity;
  };

  for (const evt of sorted) {
    const start = toMinutes(evt.startTime);
    const end = Math.max(toMinutes(evt.endTime), start + 15);
    if (cluster.length > 0 && start >= clusterEnd) {
      flushCluster();
    }
    cluster.push(evt);
    clusterEnd = Math.max(clusterEnd, end);
  }
  flushCluster();

  return results;
}

export const HOUR_ROW_PX = 56;

export function minutesToTop(minutes) {
  return (minutes / 60) * HOUR_ROW_PX;
}

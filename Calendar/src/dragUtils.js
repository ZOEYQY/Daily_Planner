const CLICK_THRESHOLD = 4;

/**
 * Starts tracking a pointer interaction (mouse, touch, or pen) from a
 * pointerdown event, distinguishing a plain click/tap from a drag. onStart
 * runs immediately and its return value is passed through as `ctx` to
 * onMove/onEnd/onClick. Only one of onEnd (drag completed) or onClick (no
 * meaningful movement) fires, never both.
 */
export function startPointerInteraction(e, { onStart, onMove, onEnd, onClick } = {}) {
  e.preventDefault();
  const pointerId = e.pointerId;
  const startX = e.clientX;
  const startY = e.clientY;
  let moved = false;
  const ctx = onStart ? onStart(e) : undefined;

  function handleMove(ev) {
    if (ev.pointerId !== pointerId) return;
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    if (!moved && Math.hypot(dx, dy) > CLICK_THRESHOLD) moved = true;
    if (moved && onMove) onMove(ev, { dx, dy, ctx });
  }

  function handleUp(ev) {
    if (ev.pointerId !== pointerId) return;
    window.removeEventListener("pointermove", handleMove);
    window.removeEventListener("pointerup", handleUp);
    window.removeEventListener("pointercancel", handleUp);
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    if (moved) {
      if (onEnd) onEnd(ev, { dx, dy, ctx });
    } else if (onClick) {
      onClick(ev, { ctx });
    }
  }

  window.addEventListener("pointermove", handleMove);
  window.addEventListener("pointerup", handleUp);
  window.addEventListener("pointercancel", handleUp);
}

export function snapMinutes(minutes, step = 15) {
  return Math.round(minutes / step) * step;
}

const AUTOSCROLL_EDGE_PX = 56;
const AUTOSCROLL_MAX_SPEED_PX = 16;

/**
 * Auto-scrolls `scrollEl` while a drag's pointer sits near its top/bottom edge.
 * Call update(clientY) on every pointer move and stop() when the drag ends.
 * onAutoScrollTick (if given) fires once per actual scroll step with the last
 * known clientY, so a caller can keep extending a live preview/resize even
 * while the pointer holds still at the edge and only the content is scrolling
 * underneath it.
 */
export function createAutoScroller(scrollEl, onAutoScrollTick) {
  let raf = null;
  let speed = 0;
  let lastClientY = null;

  function frame() {
    if (!scrollEl.isConnected) {
      raf = null;
      return;
    }
    if (speed !== 0) {
      const max = scrollEl.scrollHeight - scrollEl.clientHeight;
      const next = Math.max(0, Math.min(max, scrollEl.scrollTop + speed));
      if (next !== scrollEl.scrollTop) {
        scrollEl.scrollTop = next;
        onAutoScrollTick?.(lastClientY);
      }
    }
    raf = requestAnimationFrame(frame);
  }

  function update(clientY) {
    lastClientY = clientY;
    const rect = scrollEl.getBoundingClientRect();
    if (clientY < rect.top + AUTOSCROLL_EDGE_PX) {
      const intensity = (rect.top + AUTOSCROLL_EDGE_PX - clientY) / AUTOSCROLL_EDGE_PX;
      speed = -Math.ceil(intensity * AUTOSCROLL_MAX_SPEED_PX);
    } else if (clientY > rect.bottom - AUTOSCROLL_EDGE_PX) {
      const intensity = (clientY - (rect.bottom - AUTOSCROLL_EDGE_PX)) / AUTOSCROLL_EDGE_PX;
      speed = Math.ceil(intensity * AUTOSCROLL_MAX_SPEED_PX);
    } else {
      speed = 0;
    }
    if (!raf) raf = requestAnimationFrame(frame);
  }

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = null;
    speed = 0;
    lastClientY = null;
  }

  return { update, stop };
}

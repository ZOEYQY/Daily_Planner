const CLICK_THRESHOLD = 4;
// A finger wobbles more than a mouse, so a touch tap tolerates more movement.
const TOUCH_CLICK_THRESHOLD = 8;
// On a phone the same items also need to scroll, so a touch drag only starts
// after the finger is held still this long (press-and-hold, like a phone's own
// calendar). A quick swipe scrolls instead, and a quick tap is still a click.
const LONG_PRESS_MS = 350;

let lastTouchDragAt = 0;
/** True while (or just after) a press-and-hold touch drag — the swipe-to-change
 *  -day gesture in mobile.js checks this so a drag never also pages the view. */
export function touchDragRecentlyActive() {
  return Date.now() - lastTouchDragAt < 400;
}

/**
 * Starts tracking a pointer interaction (mouse, touch, or pen) from a
 * pointerdown event, distinguishing a plain click/tap from a drag. onStart
 * runs immediately and its return value is passed through as `ctx` to
 * onMove/onEnd/onClick. Only one of onEnd (drag completed) or onClick (no
 * meaningful movement) fires, never both.
 *
 * Touch: when the element lets the page scroll (CSS touch-action other than
 * "none" — see styles/mobile.css), a drag needs press-and-hold first; moving
 * before that hands the gesture to the browser's scrolling and nothing fires.
 * Elements kept at touch-action:none (resize handles, reorder handles) drag
 * straight away as before.
 */
export function startPointerInteraction(e, { onStart, onMove, onEnd, onClick } = {}) {
  e.preventDefault();
  const pointerId = e.pointerId;
  const startX = e.clientX;
  const startY = e.clientY;
  const isTouch = e.pointerType === "touch";
  const target = e.currentTarget instanceof Element ? e.currentTarget : null;
  const needsHold = isTouch && !!target && getComputedStyle(target).touchAction !== "none";
  const threshold = isTouch ? TOUCH_CLICK_THRESHOLD : CLICK_THRESHOLD;
  let moved = false;
  let armed = !needsHold;
  let holdTimer = null;
  const ctx = onStart ? onStart(e) : undefined;

  // Once armed, stop the page from scrolling under the dragging finger. Must be
  // a non-passive touchmove listener — CSS touch-action is fixed at touchstart.
  function blockScroll(ev) {
    if (armed) ev.preventDefault();
  }
  if (needsHold) {
    holdTimer = setTimeout(() => {
      armed = true;
      lastTouchDragAt = Date.now();
      target.classList.add("is-touch-armed");
      if (navigator.vibrate) navigator.vibrate(12);
    }, LONG_PRESS_MS);
    window.addEventListener("touchmove", blockScroll, { passive: false });
  }

  function cleanup() {
    clearTimeout(holdTimer);
    window.removeEventListener("pointermove", handleMove);
    window.removeEventListener("pointerup", handleUp);
    window.removeEventListener("pointercancel", handleUp);
    window.removeEventListener("touchmove", blockScroll);
    target?.classList.remove("is-touch-armed");
    if (needsHold && armed) lastTouchDragAt = Date.now();
  }

  // The finger moved before the hold finished: it's a scroll, not a drag.
  // Undo whatever onStart set up and step aside.
  function abandon() {
    cleanup();
    ctx?.autoScroll?.stop?.();
    ctx?.previewEl?.remove?.();
    ctx?.row?.classList?.remove("is-reordering");
  }

  function handleMove(ev) {
    if (ev.pointerId !== pointerId) return;
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    if (!armed) {
      if (Math.hypot(dx, dy) > threshold) abandon();
      return;
    }
    if (needsHold) lastTouchDragAt = Date.now();
    if (!moved && Math.hypot(dx, dy) > threshold) moved = true;
    if (moved && onMove) onMove(ev, { dx, dy, ctx });
  }

  function handleUp(ev) {
    if (ev.pointerId !== pointerId) return;
    const wasArmed = armed;
    cleanup();
    // The browser took the gesture over (started scrolling) before a hold.
    if (ev.type === "pointercancel" && !wasArmed) {
      abandon();
      return;
    }
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

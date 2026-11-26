// Phone niceties that don't belong to any one view.
//
// Swipe left / right anywhere on the calendar body pages to the next /
// previous month, week or day — the same as pressing the ‹ › buttons in the
// header (it literally clicks them, so the paging rules in calendarHeader.js
// stay the single source of truth). Only a quick, clearly sideways flick
// counts, so vertical scrolling and press-and-hold drags (see dragUtils.js)
// are never mistaken for a swipe.
import { touchDragRecentlyActive } from "./dragUtils.js";

const MIN_DISTANCE = 60;   // px sideways before it counts as a swipe
const MAX_DURATION = 600;  // ms — slower than this is a drag / read, not a flick
const SIDEWAYS_RATIO = 1.6; // |dx| must beat |dy| by this much

export function enableSwipeNavigation(el) {
  let start = null;

  el.addEventListener(
    "touchstart",
    (e) => {
      if (e.touches.length !== 1) {
        start = null;
        return;
      }
      // Leave sideways-scrollable strips (e.g. a long chip row) alone.
      const scroller = e.target.closest?.("[data-no-swipe], input, textarea, select");
      if (scroller) {
        start = null;
        return;
      }
      const t = e.touches[0];
      start = { x: t.clientX, y: t.clientY, at: Date.now() };
    },
    { passive: true }
  );

  el.addEventListener(
    "touchend",
    (e) => {
      if (!start) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - start.x;
      const dy = t.clientY - start.y;
      const dt = Date.now() - start.at;
      start = null;
      if (dt > MAX_DURATION || touchDragRecentlyActive()) return;
      if (Math.abs(dx) < MIN_DISTANCE || Math.abs(dx) < Math.abs(dy) * SIDEWAYS_RATIO) return;
      const btn = document.getElementById(dx < 0 ? "nav-next" : "nav-prev");
      if (!btn) return; // views without paging (打卡, Custom)
      btn.click();
      el.classList.remove("swipe-in-left", "swipe-in-right");
      void el.offsetWidth; // restart the little slide-in animation
      el.classList.add(dx < 0 ? "swipe-in-left" : "swipe-in-right");
    },
    { passive: true }
  );

  el.addEventListener("touchcancel", () => {
    start = null;
  });

  el.addEventListener("animationend", () => {
    el.classList.remove("swipe-in-left", "swipe-in-right");
  });
}

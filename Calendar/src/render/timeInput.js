import { esc } from "../utils.js";
import { formatTime } from "../dateUtils.js";
import { openFormPopup } from "./notify.js";

// Three interchangeable ways to fill in a time, chosen once in Settings
// (state.timePickerStyle: "native" | "text" | "clock") and applied everywhere
// a time is picked — the Add/Edit modal's Start/End, and the To-Do quick-add's
// deadline time.
//
// In native mode this is just <input type="time" id="${id}">, unchanged. In
// text/clock mode, a plain text input can't both *display* something
// friendly ("2:30 PM") and *be* the canonical "HH:MM" every existing read
// site (root.querySelector("#f-start").value, captureFormSnapshot in
// addModal.js, etc.) expects — so those two jobs are split across two
// elements: a hidden <input id="${id}"> that's always the real "HH:MM"
// value (so every read site keeps working completely unmodified), and a
// separate visible text/button-like input the user actually types into or
// taps, synced to the hidden one on blur/confirm.
export function timeInputHTML(id, value, state) {
  const style = state.timePickerStyle || "native";
  if (style === "text" || style === "clock") {
    const display = value ? formatTime(value) : "";
    const readonlyAttr = style === "clock" ? "readonly" : "";
    const placeholder = style === "clock" ? "Tap to set a time" : "e.g. 2:30 PM";
    return `
      <input type="hidden" id="${id}" value="${esc(value || "")}" />
      <input type="text" class="time-input-visible" data-time-style="${style}" data-for="${id}"
             value="${esc(display)}" placeholder="${placeholder}" ${readonlyAttr} />
    `;
  }
  return `<input type="time" id="${id}" value="${esc(value || "")}" />`;
}

// If something else set the hidden field's value directly (e.g. restoring a
// form snapshot after an inline "+ Add Category" popup re-renders the whole
// modal — see restoreFormSnapshot in addModal.js), the separate visible
// input in text/clock mode doesn't know to update on its own. Re-syncs its
// displayed text from the hidden field's current canonical value. No-op in
// native mode (nothing separate to sync).
export function syncVisibleTimeDisplay(root, id) {
  const hidden = root.querySelector(`#${id}`);
  if (!hidden || hidden.type !== "hidden") return;
  const visible = root.querySelector(`.time-input-visible[data-for="${id}"]`);
  if (visible) visible.value = hidden.value ? formatTime(hidden.value) : "";
}

// Call once per render after the field's HTML is in the DOM (root can be the
// whole modal or just a popup panel — anything containing the field). No-op
// in native mode, where there's nothing but the browser's own input to wire.
export function wireTimeInput(root, id) {
  const hidden = root.querySelector(`#${id}`);
  if (!hidden || hidden.type !== "hidden") return;
  const visible = root.querySelector(`.time-input-visible[data-for="${id}"]`);
  if (!visible) return;

  if (visible.dataset.timeStyle === "text") {
    visible.addEventListener("blur", () => {
      const hhmm = parseTimeText(visible.value);
      hidden.value = hhmm || "";
      visible.value = hhmm ? formatTime(hhmm) : "";
    });
  } else {
    visible.addEventListener("click", () => {
      openClockPicker(hidden.value, (hhmm) => {
        hidden.value = hhmm;
        visible.value = formatTime(hhmm);
      });
    });
  }
}

// A generous but bounded set of shapes: "14:30", "2:30pm", "2:30 PM", "230pm".
// Returns "HH:MM" (24h) or null if it can't confidently parse the text.
function parseTimeText(text) {
  const s = text.trim().toLowerCase();
  const m = s.match(/^(\d{1,2}):(\d{2})\s*(am|pm)?$/) || s.match(/^(\d{1,2})(\d{2})\s*(am|pm)?$/);
  if (!m) return null;
  let h = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  const period = m[3];
  if (min > 59 || h > 23 || (period && (h < 1 || h > 12))) return null;
  if (period === "pm" && h < 12) h += 12;
  if (period === "am" && h === 12) h = 0;
  return `${String(h).padStart(2, "0")}:${String(min).padStart(2, "0")}`;
}

const CLOCK_MINUTES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];

// 12 numbers arranged clockwise around a circle, 12 o'clock at the top —
// shared layout math for both the hour face (1-12) and the minute face
// (00, 05, ... 55), just with different labels/values swapped in per step.
function faceButtonsHTML(labels, values, selectedValue) {
  const R = 96;
  const BTN = 34;
  return labels
    .map((label, i) => {
      const angle = (i / labels.length) * 2 * Math.PI - Math.PI / 2;
      const x = R + R * Math.cos(angle) - BTN / 2;
      const y = R + R * Math.sin(angle) - BTN / 2;
      const selected = values[i] === selectedValue;
      return `<button type="button" class="clock-face-btn ${selected ? "is-selected" : ""}" style="left:${x}px; top:${y}px;" data-value="${values[i]}">${label}</button>`;
    })
    .join("");
}

// A tap-based (not drag-based) analog clock: pick an hour, the face swaps to
// minutes, pick a minute, done — rather than a full drag-the-hand
// interaction, which needs continuous pointer tracking for not much
// practical benefit here. currentHHMM is the hidden field's actual "HH:MM"
// value (never the friendly display text), so parsing it back is trivial.
function openClockPicker(currentHHMM, onConfirm) {
  let hour24 = 9;
  let minute = 0;
  if (currentHHMM) {
    const [h, m] = currentHHMM.split(":").map(Number);
    if (Number.isFinite(h) && Number.isFinite(m)) {
      hour24 = h;
      minute = m;
    }
  }
  let period = hour24 >= 12 ? "PM" : "AM";
  let step = "hour"; // "hour" | "minute"

  const hourLabels = Array.from({ length: 12 }, (_, i) => (i === 0 ? "12" : String(i)));
  const hourValues = Array.from({ length: 12 }, (_, i) => i);
  const minuteLabels = CLOCK_MINUTES.map((m) => String(m).padStart(2, "0"));

  const displayHour12 = () => (hour24 % 12 === 0 ? 12 : hour24 % 12);

  function renderFace(panel) {
    const face = panel.querySelector("#clock-face");
    if (step === "hour") {
      face.innerHTML = faceButtonsHTML(hourLabels, hourValues, displayHour12() % 12);
    } else {
      face.innerHTML = faceButtonsHTML(minuteLabels, CLOCK_MINUTES, minute);
    }
    face.querySelectorAll(".clock-face-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const v = Number(btn.dataset.value);
        if (step === "hour") {
          // v is 0-11 (0 stands in for the "12" label) — see hourValues above.
          hour24 = period === "PM" ? (v === 0 ? 12 : v + 12) : v === 0 ? 0 : v;
          step = "minute";
        } else {
          minute = v;
        }
        renderPanel(panel);
      });
    });
  }

  function renderPanel(panel) {
    panel.querySelector("#clock-hour-label").textContent = String(displayHour12());
    panel.querySelector("#clock-hour-label").classList.toggle("is-active", step === "hour");
    panel.querySelector("#clock-minute-label").textContent = String(minute).padStart(2, "0");
    panel.querySelector("#clock-minute-label").classList.toggle("is-active", step === "minute");
    panel.querySelectorAll(".clock-period-btn").forEach((btn) => {
      btn.classList.toggle("is-selected", btn.dataset.period === period);
    });
    renderFace(panel);
  }

  openFormPopup({
    title: "Set Time",
    submitLabel: "Set",
    bodyHTML: `
      <div class="clock-picker">
        <div class="clock-picker-display">
          <span class="clock-time-label is-active" id="clock-hour-label">${displayHour12()}</span>
          <span>:</span>
          <span class="clock-time-label" id="clock-minute-label">${String(minute).padStart(2, "0")}</span>
          <div class="clock-period-group">
            <button type="button" class="clock-period-btn" data-period="AM">AM</button>
            <button type="button" class="clock-period-btn" data-period="PM">PM</button>
          </div>
        </div>
        <div class="clock-face" id="clock-face"></div>
      </div>
    `,
    onMount: (panel) => {
      panel.querySelector("#clock-hour-label").addEventListener("click", () => {
        step = "hour";
        renderPanel(panel);
      });
      panel.querySelector("#clock-minute-label").addEventListener("click", () => {
        step = "minute";
        renderPanel(panel);
      });
      panel.querySelectorAll(".clock-period-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          const wasPM = period === "PM";
          period = btn.dataset.period;
          if (period === "PM" && !wasPM && hour24 < 12) hour24 += 12;
          if (period === "AM" && wasPM && hour24 >= 12) hour24 -= 12;
          renderPanel(panel);
        });
      });
      renderPanel(panel);
    },
    onSubmit: ({ close }) => {
      close();
      onConfirm(`${String(hour24).padStart(2, "0")}:${String(minute).padStart(2, "0")}`);
    },
  });
}

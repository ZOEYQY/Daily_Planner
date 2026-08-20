import { icons } from "../icons.js";
import { esc } from "../utils.js";
import { parseISODate } from "../dateUtils.js";
import { ruleOverlap } from "../selectors.js";
import { openFormPopup, showChoice, showToast } from "./notify.js";
import { uid } from "../seed.js";

const WEEKDAY_FULL = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const ORDINALS = ["", "first", "second", "third", "fourth", "fifth"];

// A rule's weekdays/ordinal/interval are fixed at the moment it's added (see the
// "+ Add rule" popup) — not derived live from the form's date field, since an item
// can carry several rules, each covering several weekdays at once.
export function ruleLabel(rule) {
  const interval = rule.interval || 1;
  const every = interval > 1 ? `Every ${interval} ` : "";
  if (rule.freq === "yearly") {
    return interval > 1 ? `Every ${interval} years` : "Yearly";
  }
  const days = rule.weekdays.map((w) => WEEKDAY_FULL[w]).join(", ");
  if (rule.freq === "monthly") {
    const ordinal = ORDINALS[rule.ordinal] || `${rule.ordinal}th`;
    return interval > 1 ? `${every}months on the ${ordinal} ${days}` : `Monthly on the ${ordinal} ${days}`;
  }
  return interval > 1 ? `${every}weeks on ${days}` : `Weekly on ${days}`;
}

function repeatChipsHTML(rules) {
  const chips = rules
    .map(
      (r) => `
    <span class="repeat-rule-chip" data-rule-id="${r.id}">
      <span>${esc(ruleLabel(r))}</span>
      <button type="button" class="repeat-rule-remove" data-rule-id="${r.id}" aria-label="Remove rule">${icons.close}</button>
    </span>`
    )
    .join("");
  return `${chips}<button type="button" class="btn btn-ghost repeat-add-btn" id="add-repeat-rule-btn">${icons.plusSmall}<span>Add rule</span></button>`;
}

// Factory for the "Repeat" chip list + "Add Repeat Rule" popup, shared between the
// full Add/Edit modal (addModal.js) and the Special Day quick-add (addChooserModal.js)
// so the recurrence UI/logic isn't duplicated between them.
//
// getRules/setRules read and write the caller's own rules array (a plain closure
// variable — this module has no state of its own). getAnchorDate() returns the
// current value of whatever date input anchors the recurrence.
export function createRepeatRuleUI({ container, getRules, setRules, getAnchorDate, onChange }) {
  function render() {
    container.innerHTML = repeatChipsHTML(getRules());
    wire();
  }

  function wire() {
    container.querySelectorAll(".repeat-rule-remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        setRules(getRules().filter((r) => r.id !== btn.dataset.ruleId));
        onChange?.();
        render();
      });
    });
    container.querySelector("#add-repeat-rule-btn")?.addEventListener("click", openAddPopup);
  }

  function openAddPopup() {
    const anchor = getAnchorDate();
    const initialWeekday = anchor ? parseISODate(anchor).getDay() : new Date().getDay();
    openFormPopup({
      title: "Add Repeat Rule",
      submitLabel: "Add",
      bodyHTML: `
        <div class="field">
          <label>Frequency</label>
          <select id="rule-freq">
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
            <option value="yearly">Yearly</option>
          </select>
        </div>
        <div class="field" id="rule-interval-wrap">
          <label>Every</label>
          <div style="display:flex; gap:8px; align-items:center;">
            <input type="number" id="rule-interval" min="1" value="1" style="max-width:80px;" />
            <span id="rule-interval-unit">week(s)</span>
          </div>
        </div>
        <div class="field" id="rule-weekdays-wrap">
          <label>Day(s) of week</label>
          <div class="weekday-picker" id="rule-weekdays">
            ${WEEKDAY_FULL.map((w, i) => `<button type="button" class="weekday-pill ${i === initialWeekday ? "selected" : ""}" data-day="${i}">${w.slice(0, 3)}</button>`).join("")}
          </div>
        </div>
        <div class="field" id="rule-ordinal-wrap" style="display:none;">
          <label>Which one?</label>
          <select id="rule-ordinal">
            ${ORDINALS.slice(1)
              .map((o, i) => `<option value="${i + 1}">${o}</option>`)
              .join("")}
          </select>
        </div>
      `,
      onMount: (panel) => {
        const freqSel = panel.querySelector("#rule-freq");
        const ordWrap = panel.querySelector("#rule-ordinal-wrap");
        const weekdaysWrap = panel.querySelector("#rule-weekdays-wrap");
        const intervalUnit = panel.querySelector("#rule-interval-unit");
        const sync = () => {
          const freq = freqSel.value;
          ordWrap.style.display = freq === "monthly" ? "" : "none";
          weekdaysWrap.style.display = freq === "yearly" ? "none" : "";
          intervalUnit.textContent = freq === "yearly" ? "year(s)" : freq === "monthly" ? "month(s)" : "week(s)";
        };
        sync();
        freqSel.addEventListener("change", sync);
        panel.querySelectorAll(".weekday-pill").forEach((pill) => {
          pill.addEventListener("click", () => pill.classList.toggle("selected"));
        });
      },
      onSubmit: ({ panel, close }) => {
        const freq = panel.querySelector("#rule-freq").value;
        const interval = Math.max(1, Number(panel.querySelector("#rule-interval").value) || 1);
        const weekdays = Array.from(panel.querySelectorAll(".weekday-pill.selected")).map((p) => Number(p.dataset.day));
        if (freq !== "yearly" && weekdays.length === 0) return;
        const newRule = { id: uid("rule"), freq, weekdays, interval };
        if (freq === "monthly") newRule.ordinal = Number(panel.querySelector("#rule-ordinal").value);

        const rules = getRules();
        const overlapping = rules.filter((r) => ruleOverlap(newRule, r).relation !== "none");
        const isDuplicate = overlapping.some((r) => ruleOverlap(newRule, r).relation === "equal");
        close();
        if (isDuplicate) {
          showToast("That rule already exists");
          return;
        }
        if (overlapping.length > 0) {
          showChoice({
            title: "These repeat rules overlap",
            message: `"${ruleLabel(newRule)}" covers the same dates as ${overlapping.map((r) => `"${ruleLabel(r)}"`).join(", ")}.`,
            choices: [
              { label: `Keep "${ruleLabel(newRule)}"`, value: "new" },
              { label: overlapping.length > 1 ? "Keep the existing rules" : `Keep "${ruleLabel(overlapping[0])}"`, value: "existing" },
            ],
          }).then((choice) => {
            if (choice !== "new") return;
            const overlappingIds = new Set(overlapping.map((r) => r.id));
            setRules([...rules.filter((r) => !overlappingIds.has(r.id)), newRule]);
            onChange?.();
            render();
          });
          return;
        }
        setRules([...rules, newRule]);
        onChange?.();
        render();
      },
    });
  }

  render();
  return { render };
}

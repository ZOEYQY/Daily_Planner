import { toISODate, today, daysBetweenISO } from "./dateUtils.js";

// A task is overdue if it has a due date in the past and isn't done yet.
export function isOverdue(task, todayISO = toISODate(today())) {
  return !!task.dueDate && task.dueDate < todayISO && !task.done;
}

// Whole days a to-do has been sitting past its deadline (0 if not overdue).
// Drives the "nag" escalation — see overdueSeverity and getDayTodos.
export function daysOverdue(task, todayISO = toISODate(today())) {
  if (!isOverdue(task, todayISO)) return 0;
  return Math.max(0, daysBetweenISO(task.dueDate, todayISO));
}

// Buckets days-overdue into an escalation level 0-4. The To-Do panel's nag
// styling (calendar.css .nag-sev-*) gets louder at each step: a week late is
// visibly worse than a day late, two weeks late pulses.
export function overdueSeverity(days) {
  if (days <= 0) return 0;
  if (days <= 2) return 1;
  if (days <= 6) return 2;
  if (days <= 13) return 3;
  return 4;
}

/**
 * Builds the patch to apply when a task's date/time changes, silently
 * tracking reschedule history along the way.
 *
 * Only counts as a reschedule when the task already had a date AND the new
 * date is strictly later than the old one (postponement) — same-day time
 * changes and pulling a task earlier never count, and a task's first-ever
 * scheduling (no prior date) never counts either.
 */
export function computeReschedulePatch(task, newDate, newStartTime, newEndTime, extra = {}) {
  const oldDate = task.dueDate || "";
  const isPostponement = !!oldDate && !!newDate && newDate > oldDate;

  if (!isPostponement) {
    return { dueDate: newDate, startTime: newStartTime, endTime: newEndTime, ...extra };
  }

  const history = [...(task.rescheduleHistory || []), { date: oldDate, startTime: task.startTime || "" }];

  return {
    dueDate: newDate,
    startTime: newStartTime,
    endTime: newEndTime,
    rescheduleCount: (task.rescheduleCount || 0) + 1,
    rescheduleHistory: history,
    overdueReschedule: task.overdueReschedule || isOverdue(task),
    ...extra,
  };
}

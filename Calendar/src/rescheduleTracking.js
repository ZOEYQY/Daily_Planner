import { toISODate, today } from "./dateUtils.js";

// A task is overdue if it has a due date in the past and isn't done yet.
export function isOverdue(task, todayISO = toISODate(today())) {
  return !!task.dueDate && task.dueDate < todayISO && !task.done;
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

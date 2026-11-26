// What the widget's buttons do. Each tap saves where the widget is looking
// (which day, which hours) and redraws every Daily Planner widget, so the big
// one and the medium one under it move together.
import { AppIntentManager, AppIntentProtocol, Widget } from "scripting"

export const HOURS_TOTAL = 9 // large (6 h) + medium underneath (3 h)

function num(key: string, fallback: number): number {
  const v = Storage.get<number>(key)
  return typeof v === "number" && isFinite(v) ? v : fallback
}

function markManual() {
  Storage.set("manualAt", Date.now())
}

// ▲ / ▼ — move the visible hours up or down by `delta` hours.
export const ShiftHoursIntent = AppIntentManager.register({
  name: "DailyPlannerShiftHours",
  protocol: AppIntentProtocol.AppIntent,
  perform: async (delta: number) => {
    const current = num("hourStart", num("lastStart", 7))
    const next = Math.max(0, Math.min(24 - HOURS_TOTAL, current + delta))
    Storage.set("hourStart", next)
    markManual()
    Widget.reloadAll()
  },
})

// ◀ / ▶ — previous / next day.
export const ShiftDayIntent = AppIntentManager.register({
  name: "DailyPlannerShiftDay",
  protocol: AppIntentProtocol.AppIntent,
  perform: async (delta: number) => {
    Storage.set("dayOffset", num("dayOffset", 0) + delta)
    markManual()
    Widget.reloadAll()
  },
})

// Tap the date — back to today, around now.
export const ResetViewIntent = AppIntentManager.register({
  name: "DailyPlannerResetView",
  protocol: AppIntentProtocol.AppIntent,
  perform: async (_: undefined) => {
    Storage.remove("hourStart")
    Storage.remove("dayOffset")
    Storage.remove("manualAt")
    Widget.reloadAll()
  },
})

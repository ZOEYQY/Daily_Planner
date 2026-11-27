// Daily Planner home-screen widget for the "Scripting" app.
//
// Looks like the app's Day view (Calendar/src/render/dayGridView.js +
// styles/calendar.css): "THU 26" heading, the DAY tray, hour rows with
// "8 AM" labels, and each event as a block in its colour with the lighter
// title panel in the middle.
//
//   Large widget  — heading with buttons + the first 6 hours.
//   Medium widget — put it right under the large one: it carries on with the
//                   next 3 hours, so together they look like one Day view.
//
//   ▲ ▼  earlier / later hours      ◀ ▶  previous / next day
//   tap the date — back to today    ＋   opens the website's Add screen
//   tap an event — opens that day in the website
//
// After 30 minutes without a tap it goes back to today, around now.
import {
  Button,
  Circle,
  HStack,
  Capsule,
  Image,
  Link,
  Rectangle,
  RoundedRectangle,
  Spacer,
  Text,
  VStack,
  Widget,
  ZStack,
  modifiers,
} from "scripting"
import { HOURS_TOTAL, ResetViewIntent, ShiftDayIntent, ShiftHoursIntent } from "./app_intents"

const LARGE_HOURS = 6
const MEDIUM_HOURS = HOURS_TOTAL - LARGE_HOURS // 3
const MANUAL_KEEP_MS = 30 * 60 * 1000
const EMBEDDED_FEED_URL = "__FEED_URL__"

// ── the app's look (Calendar/styles/tokens.css) ─────────────────────
const C = {
  surface: "#ffffff",
  surfaceAlt: "#fcfcfb",
  border: "#e5e4dd",
  ink: "#0b0b0b",
  muted: "#898781",
  active: "#0b0b0b",
  fallback: "#898781",
  button: "#f1f0ec",
}
const WEEKDAY_SHORT = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

type Item = {
  kind: "event" | "task" | "special" | string
  title: string
  start: string | null
  end: string | null
  allDay: boolean
  done: boolean
  color: string | null
  place?: string
}
type Feed = { name: string; generated: string; days: { date: string; items: Item[] }[] }

// ── helpers (same maths as the website) ─────────────────────────────
function isoOf(d: Date) {
  const p = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
function minutes(hhmm: string | null | undefined): number | null {
  if (!hhmm) return null
  const [h, m] = hhmm.split(":").map(Number)
  return isFinite(h) && isFinite(m) ? h * 60 + m : null
}
// formatTime / formatHourLabel in Calendar/src/dateUtils.js
function formatTime(hhmm: string | null) {
  const t = minutes(hhmm)
  if (t == null) return ""
  const h = Math.floor(t / 60), m = t % 60
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, "0")} ${h >= 12 ? "PM" : "AM"}`
}
function formatHourLabel(h: number) {
  return `${h % 12 === 0 ? 12 : h % 12} ${h >= 12 ? "PM" : "AM"}`
}
function hexRGB(hex: string | null): number[] | null {
  let h = String(hex || "").replace("#", "").trim()
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2]
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16))
}
// inkOn in Calendar/src/utils.js: dark text on light colours
function inkOn(hex: string) {
  const rgb = hexRGB(hex)
  if (!rgb) return "#1b1b1b"
  return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255 > 0.62 ? "#1b1b1b" : "#ffffff"
}
// CSS color-mix(in srgb, a p%, b)
function mix(a: string, p: number, b: string) {
  const x = hexRGB(a) || hexRGB(C.fallback)!, y = hexRGB(b)!
  return "#" + x.map((v, i) => Math.round(v * p + y[i] * (1 - p)).toString(16).padStart(2, "0")).join("")
}
function colorOf(it: Item) {
  return hexRGB(it.color) ? (it.color as string) : C.fallback
}

// Side-by-side columns for overlapping blocks — layoutDayEvents in
// Calendar/src/timeLayout.js.
type Placed = { item: Item; col: number; cols: number; startMin: number; endMin: number }
function layoutDay(items: Item[]): Placed[] {
  const span = (it: Item) => {
    const s = minutes(it.start) as number
    return [s, Math.max(minutes(it.end) ?? s + 60, s + 15)]
  }
  const sorted = [...items].sort((a, b) => span(a)[0] - span(b)[0])
  const out: Placed[] = []
  let cluster: Item[] = [], clusterEnd = -Infinity
  const flush = () => {
    const ends: number[] = [], placed: { it: Item; c: number; s: number; e: number }[] = []
    for (const it of cluster) {
      const [s, e] = span(it)
      let c = ends.findIndex((t) => t <= s)
      if (c === -1) { c = ends.length; ends.push(e) } else ends[c] = e
      placed.push({ it, c, s, e })
    }
    placed.forEach((p) => out.push({ item: p.it, col: p.c, cols: ends.length, startMin: p.s, endMin: p.e }))
    cluster = []; clusterEnd = -Infinity
  }
  for (const it of sorted) {
    const [s, e] = span(it)
    if (cluster.length && s >= clusterEnd) flush()
    cluster.push(it)
    clusterEnd = Math.max(clusterEnd, e)
  }
  if (cluster.length) flush()
  return out
}

// Which hours to show when nobody has pressed ▲▼: from just before now, or
// from the next/ongoing block if that is later.
function autoStart(timed: Item[], nowMin: number | null) {
  if (nowMin == null) {
    // another day: start at its first block (or 8 AM)
    const first = timed.map((it) => minutes(it.start) as number).sort((a, b) => a - b)[0]
    return clampStart(first != null ? Math.floor(first / 60) : 8)
  }
  const live = timed
    .filter((it) => (minutes(it.end) ?? (minutes(it.start) as number) + 60) > nowMin)
    .sort((a, b) => (minutes(a.start) as number) - (minutes(b.start) as number))[0]
  let start = Math.floor(nowMin / 60) - 1
  if (live) {
    const s = Math.floor((minutes(live.start) as number) / 60)
    start = s > start + LARGE_HOURS - 2 ? s : Math.min(start, s)
  }
  return clampStart(start)
}
function clampStart(h: number) {
  return Math.max(0, Math.min(24 - HOURS_TOTAL, h))
}

// ── data ────────────────────────────────────────────────────────────
async function loadFeed(url: string): Promise<{ feed: Feed | null; offline: boolean }> {
  try {
    const res = await fetch(url)
    if (!res.ok) throw new Error("HTTP " + res.status)
    const feed = (await res.json()) as Feed
    if (!feed || !Array.isArray(feed.days)) throw new Error("bad data")
    Storage.set("feed", feed)
    return { feed, offline: false }
  } catch (e) {
    return { feed: Storage.get<Feed>("feed"), offline: true }
  }
}
function siteLink(feedUrl: string, params: string) {
  const m = /^(https?:\/\/[^/]+)/.exec(feedUrl)
  return `${m ? m[1] : ""}/calendar/?${params}`
}

// Positions a child at (x, y) inside a top-left aligned ZStack.
const at = (x: number, y: number, w: number, h: number) =>
  modifiers().frame({ width: Math.max(0, w), height: Math.max(0, h) }).offset({ x, y })

// ── pieces ──────────────────────────────────────────────────────────
function EventBlock({ b, w, h, url }: { b: Placed; w: number; h: number; url: string }) {
  const it = b.item
  const color = colorOf(it)
  if (it.kind === "task") {
    // .timegrid-task-block: pale tint, coloured border, checkbox + title
    const showTime = h >= 34
    return (
      <Link url={url}>
        <ZStack alignment="topLeading">
          <RoundedRectangle
            cornerRadius={4}
            fill={mix(color, 0.14, C.surface)}
            stroke={{ shapeStyle: mix(color, 0.55, C.border), strokeStyle: { lineWidth: 1.25 } }}
          />
          <VStack alignment="leading" spacing={1} padding={{ horizontal: 5, top: h < 22 ? 2 : 4 }}>
            <HStack spacing={4}>
              <Image
                systemName={it.done ? "checkmark.square.fill" : "square"}
                font={10}
                foregroundStyle={color}
              />
              <Text font={11} fontWeight="semibold" foregroundStyle={C.ink} lineLimit={1}
                opacity={it.done ? 0.5 : 1}>
                {it.title}
              </Text>
            </HStack>
            {showTime ? (
              <Text font={9.5} foregroundStyle={C.ink} opacity={0.8} lineLimit={1}>
                {`${formatTime(it.start)} – ${formatTime(it.end)}`}
              </Text>
            ) : null}
          </VStack>
        </ZStack>
      </Link>
    )
  }
  // .timegrid-event: solid block; .timegrid-event-header-panel: 85% colour +
  // 15% white panel with the title and time, centred.
  const ink = inkOn(color)
  const narrow = w < 70
  const compact = h < 26
  const showTime = !compact && !narrow && h >= 38
  const titleLines = Math.max(1, Math.floor((h - (showTime ? 14 : 0) - 8) / 14))
  const panelMods = modifiers()
    .padding({ horizontal: narrow ? 2 : 5, vertical: compact ? 0 : 3 })
    .frame({ maxWidth: "infinity" })
  if (!compact) panelMods.background(<RoundedRectangle cornerRadius={5} fill={mix(color, 0.85, "#ffffff")} />)
  panelMods.padding({ horizontal: narrow ? 1 : 6, vertical: 2 })
  return (
    <Link url={url}>
      <ZStack>
        <RoundedRectangle cornerRadius={4} fill={color} />
        <VStack
          spacing={1}
          modifiers={panelMods}
        >
          <Text
            font={narrow ? 10 : 13}
            fontWeight="bold"
            foregroundStyle={ink}
            lineLimit={titleLines}
            minScaleFactor={0.7}
            multilineTextAlignment="center"
          >
            {it.title}
          </Text>
          {showTime ? (
            <Text font={10.5} fontWeight="semibold" foregroundStyle={ink} opacity={0.92} lineLimit={1} minScaleFactor={0.8}>
              {`${formatTime(it.start)} – ${formatTime(it.end)}`}
            </Text>
          ) : null}
        </VStack>
      </ZStack>
    </Link>
  )
}

// The timeline: hour labels, hour lines and blocks for `hours` hours from
// `startHour`, filling a W×H box.
function Timeline(props: {
  W: number; H: number; startHour: number; hours: number; timed: Item[]; dayUrl: string; topLine: boolean
}) {
  const { W, H, startHour, hours, timed, dayUrl, topLine } = props
  const gutter = 40
  const hourH = H / hours
  const winStart = startHour * 60, winEnd = (startHour + hours) * 60
  const yFor = (min: number) => ((min - winStart) / 60) * hourH
  const nodes: any[] = []
  // gutter divider
  nodes.push(<Rectangle key="g" fill={C.border} modifiers={at(gutter - 0.75, 0, 0.75, H)} />)
  for (let i = 0; i < hours; i++) {
    const y = i * hourH
    const h = startHour + i
    if (i > 0 || topLine) nodes.push(<Rectangle key={"l" + i} fill={C.border} modifiers={at(gutter, y, W - gutter, 0.75)} />)
    if (h > 0) {
      nodes.push(
        <Text key={"t" + i} font={9} fontWeight="semibold" foregroundStyle={C.muted} lineLimit={1}
          multilineTextAlignment="trailing"
          modifiers={modifiers().frame({ width: gutter - 6, alignment: "trailing" }).offset({ x: 0, y: i === 0 ? y + 2 : y - 6 })}>
          {formatHourLabel(h)}
        </Text>
      )
    }
  }
  const colW = W - gutter
  for (const b of layoutDay(timed)) {
    if (b.endMin <= winStart || b.startMin >= winEnd) continue
    const y1 = Math.max(yFor(b.startMin), 0), y2 = Math.min(yFor(b.endMin), H)
    if (y2 - y1 < 4) continue
    const bw = colW / b.cols
    const x = gutter + b.col * bw + 2, w = bw - 4, h = y2 - y1 - 1
    nodes.push(
      <ZStack key={`e${b.startMin}-${b.col}-${b.item.title}`} modifiers={at(x, y1 + 0.5, w, h)}>
        <EventBlock b={b} w={w} h={h} url={dayUrl} />
      </ZStack>
    )
  }
  return (
    <ZStack alignment="topLeading" frame={{ width: W, height: H, alignment: "topLeading" }}>
      {nodes}
    </ZStack>
  )
}

function IconButton({ intent, icon }: { intent: any; icon: string }) {
  return (
    <Button intent={intent} buttonStyle="plain">
      <Image systemName={icon} font={12} fontWeight="semibold" foregroundStyle={C.ink}
        modifiers={modifiers().frame({ width: 30, height: 28 }).background(<RoundedRectangle cornerRadius={8} fill={C.button} />)} />
    </Button>
  )
}

// Heading: ◀ THU (26) ▶ … ▲ ▼ ＋  — the date sits over the day column like
// .timegrid-head-col.
function Heading({ date, isToday, addUrl }: { date: Date; isToday: boolean; addUrl: string }) {
  return (
    <HStack spacing={6} padding={{ horizontal: 10 }} frame={{ maxWidth: "infinity", height: 40 }} background={C.surfaceAlt}>
      <IconButton intent={ShiftDayIntent(-1)} icon="chevron.left" />
      <Button intent={ResetViewIntent(undefined)} buttonStyle="plain">
        <HStack spacing={6}>
          <Text font={11} fontWeight="bold" foregroundStyle={C.muted}>{WEEKDAY_SHORT[date.getDay()]}</Text>
          <ZStack frame={{ width: 24, height: 24 }}>
            {isToday ? <Circle fill={C.active} /> : null}
            <Text font={13} fontWeight="semibold" foregroundStyle={isToday ? "#ffffff" : C.ink}>{String(date.getDate())}</Text>
          </ZStack>
          <Text font={11} fontWeight="semibold" foregroundStyle={C.muted}>{MONTH_SHORT[date.getMonth()]}</Text>
        </HStack>
      </Button>
      <IconButton intent={ShiftDayIntent(1)} icon="chevron.right" />
      <Spacer />
      <IconButton intent={ShiftHoursIntent(-1)} icon="chevron.up" />
      <IconButton intent={ShiftHoursIntent(1)} icon="chevron.down" />
      <Link url={addUrl}>
        <Image systemName="plus" font={13} fontWeight="bold" foregroundStyle="#ffffff"
          modifiers={modifiers().frame({ width: 30, height: 28 }).background(<RoundedRectangle cornerRadius={8} fill={C.active} />)} />
      </Link>
    </HStack>
  )
}

// DAY tray: untimed items as pills; a special day stretches across in its
// solid colour, as in the app.
function Tray({ items }: { items: Item[] }) {
  const specials = items.filter((it) => it.kind === "special")
  const plain = items.filter((it) => it.kind !== "special")
  return (
    <HStack spacing={4} padding={{ trailing: 6 }} frame={{ maxWidth: "infinity", height: 24 }}>
      <Text font={8} fontWeight="bold" foregroundStyle={C.muted} frame={{ width: 36 }}>DAY</Text>
      {specials.map((it, i) => (
        <Text key={"s" + i} font={10} fontWeight="semibold" foregroundStyle={inkOn(colorOf(it))} lineLimit={1}
          modifiers={modifiers().padding({ horizontal: 7, vertical: 2 }).frame({ maxWidth: "infinity" })
            .background(<Capsule fill={colorOf(it)} />)}>
          {it.title}
        </Text>
      ))}
      {plain.slice(0, 3).map((it, i) => (
        <Text key={"p" + i} font={10} fontWeight="semibold" foregroundStyle={C.ink} lineLimit={1} opacity={it.done ? 0.5 : 1}
          modifiers={modifiers().padding({ horizontal: 7, vertical: 2 })
            .background(<Capsule fill={mix(colorOf(it), 0.12, C.surface)} />)}>
          {(it.kind === "task" && it.done ? "✓ " : "") + it.title}
        </Text>
      ))}
      {specials.length ? null : <Spacer />}
    </HStack>
  )
}

function Divider_() {
  return <Rectangle fill={C.border} frame={{ maxWidth: "infinity", height: 0.75 }} />
}

function Message({ title, text }: { title: string; text: string }) {
  return (
    <VStack spacing={6} padding={16} frame={{ maxWidth: "infinity", maxHeight: "infinity" }}>
      <Text font={15} fontWeight="bold" foregroundStyle={C.ink}>{title}</Text>
      <Text font={12} foregroundStyle={C.muted} multilineTextAlignment="center">{text}</Text>
    </VStack>
  )
}

// ── main ────────────────────────────────────────────────────────────
async function main() {
  // The copy on the website's widget page has the owner's link filled in here;
  // the copy in the repo keeps the placeholder and uses the saved link instead.
  const embedded = EMBEDDED_FEED_URL.startsWith("http") ? EMBEDDED_FEED_URL : null
  const feedUrl = Storage.get<string>("feedUrl") || embedded
  const family = Widget.family
  const fam = String(family ?? "").toLowerCase()
  // Fall back to typical iPhone sizes if the app doesn't report one.
  const size = Widget.displaySize as any
  const guess = fam.includes("medium") ? [364, 170] : fam.includes("large") ? [364, 382] : [170, 170]
  const W: number = size && size.width > 0 ? size.width : guess[0]
  const H: number = size && size.height > 0 ? size.height : guess[1]
  const refresh = { policy: "after" as const, date: new Date(Date.now() + 15 * 60 * 1000) }

  if (!feedUrl) {
    Widget.present(
      <ZStack widgetBackground={C.surface}>
        <Message title="Daily Planner" text="打开 Scripting app，运行 Daily Planner 一次，贴上你的链接。" />
      </ZStack>,
      refresh
    )
    return
  }

  // Taps older than 30 minutes are forgotten: back to today, around now.
  const manualAt = Storage.get<number>("manualAt")
  if (!manualAt || Date.now() - manualAt > MANUAL_KEEP_MS) {
    Storage.remove("hourStart")
    Storage.remove("dayOffset")
    Storage.remove("manualAt")
  }
  const dayOffset = Storage.get<number>("dayOffset") || 0

  const now = new Date()
  const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() + dayOffset)
  const iso = isoOf(date)
  const isToday = dayOffset === 0

  const { feed, offline } = await loadFeed(feedUrl)
  const day = feed?.days.find((d) => d.date === iso)
  const items = day ? day.items : []
  const timed = items.filter((it) => !it.allDay && minutes(it.start) != null)
  const untimed = items.filter((it) => it.allDay || minutes(it.start) == null)

  const nowMin = isToday ? now.getHours() * 60 + now.getMinutes() : null
  const saved = Storage.get<number>("hourStart")
  const startHour = typeof saved === "number" ? clampStart(saved) : autoStart(timed, nowMin)
  Storage.set("lastStart", startHour) // the ▲▼ buttons start from here

  const dayUrl = siteLink(feedUrl, `date=${iso}`)
  const addUrl = siteLink(feedUrl, `date=${iso}&add=1`)

  // Pick the layout from the widget's real size as well as its family name,
  // so tall sizes (e.g. extra-large portrait) also get the Day view.
  const isPlainLarge = fam === "systemlarge" || fam === "large"
  const isMedium = fam.includes("medium") || (!fam.includes("large") && W > 250 && H < 250)
  const isBig = !isMedium && (fam.includes("large") || H >= 250)

  if (isBig) {
    const trayH = untimed.length ? 24 : 0
    const headH = 40
    const timelineH = H - headH - trayH - (trayH ? 0.75 : 0) - 0.75 - 6
    // The large widget shows 6 hours (the medium one under it shows the next
    // 3); a taller widget shows as many hours as fit at the app's spacing.
    const hours = isPlainLarge ? LARGE_HOURS : Math.max(3, Math.min(18, Math.round(timelineH / 56)))
    const bigStart = Math.max(0, Math.min(24 - hours, startHour))
    Widget.present(
      <VStack spacing={0} frame={{ width: W, height: H, alignment: "top" }} widgetBackground={C.surface}>
        <Heading date={date} isToday={isToday} addUrl={addUrl} />
        <Divider_ />
        {trayH ? <Tray items={untimed} /> : null}
        {trayH ? <Divider_ /> : null}
        <Timeline W={W} H={timelineH} startHour={bigStart} hours={hours} timed={timed} dayUrl={dayUrl} topLine={false} />
        <Spacer />
        {offline ? (
          <Text font={7} foregroundStyle={C.muted} frame={{ maxWidth: "infinity", alignment: "trailing" }} padding={{ trailing: 8 }}>
            {feed ? "离线 · 上次的资料" : "连不上网络"}
          </Text>
        ) : null}
      </VStack>,
      refresh
    )
    return
  }

  if (isMedium) {
    // Carries on under the large widget: the next 3 hours of the same day.
    Widget.present(
      <VStack spacing={0} frame={{ width: W, height: H, alignment: "top" }} widgetBackground={C.surface}>
        <Timeline W={W} H={H - 8} startHour={startHour + LARGE_HOURS} hours={MEDIUM_HOURS} timed={timed} dayUrl={dayUrl} topLine={false} />
        <Spacer />
      </VStack>,
      refresh
    )
    return
  }

  // Small widget (or anything else): just the next thing, as text.
  const next = timed
    .filter((it) => nowMin == null || (minutes(it.end) ?? (minutes(it.start) as number) + 60) > nowMin)
    .sort((a, b) => (minutes(a.start) as number) - (minutes(b.start) as number))[0]
  Widget.present(
    <Link url={dayUrl}>
      <VStack alignment="leading" spacing={4} padding={14} frame={{ maxWidth: "infinity", maxHeight: "infinity", alignment: "topLeading" }}
        widgetBackground={C.surface}>
        <Text font={11} fontWeight="bold" foregroundStyle={C.muted}>{`${WEEKDAY_SHORT[date.getDay()]} ${date.getDate()}`}</Text>
        {next ? (
          <VStack alignment="leading" spacing={2}>
            <Text font={15} fontWeight="bold" foregroundStyle={C.ink} lineLimit={3}>{next.title}</Text>
            <Text font={11} foregroundStyle={C.muted}>{`${formatTime(next.start)} – ${formatTime(next.end)}`}</Text>
          </VStack>
        ) : (
          <Text font={13} foregroundStyle={C.muted}>Nothing coming up</Text>
        )}
        <Spacer />
        <Text font={7} foregroundStyle={C.muted}>{`${String(family)} · ${Math.round(W)}×${Math.round(H)}`}</Text>
      </VStack>
    </Link>,
    refresh
  )
}

main()

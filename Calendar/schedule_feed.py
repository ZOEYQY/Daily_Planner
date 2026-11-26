"""Home-screen widget data for a profile's Calendar.

The iPhone widget (a Scriptable script — see scripts/daily_planner_widget.js
and the /calendar/widget page) can't log in, so it fetches
/calendar/widget/<profile>/<token>.json, where the token is an HMAC of the
profile id with the server's secret key. Anyone with that URL can read the
profile's upcoming schedule, so it is only shown to the logged-in owner.

Repeating items are expanded here into dated occurrences using the same rules
as the client's selectors.js — weekly / monthly (nth weekday) / yearly,
"every N", per-rule date ranges, and single-occurrence edits, moves and
deletes — so the widget shows exactly what the app shows.
"""
import datetime as dt
import hashlib
import hmac



# ── tokens ──────────────────────────────────────────────────────────
def feed_token(secret_key, profile_id):
    key = (secret_key or "").encode() if isinstance(secret_key, str) else (secret_key or b"")
    return hmac.new(key, f"calendar-widget:{profile_id}".encode(), hashlib.sha256).hexdigest()[:32]


def token_ok(secret_key, profile_id, token):
    return hmac.compare_digest(feed_token(secret_key, profile_id), token or "")


# ── recurrence (mirrors Calendar/src/selectors.js + dateUtils.js) ───
def _parse(iso):
    try:
        return dt.date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return None


def _js_day(d):
    """JavaScript getDay(): Sunday = 0 ... Saturday = 6."""
    return (d.weekday() + 1) % 7


def _start_of_week(d):
    return d - dt.timedelta(days=_js_day(d))  # weeks start Sunday, as in weeksBetween


def _weeks_between(a, b):
    return round((_start_of_week(b) - _start_of_week(a)).days / 7)


def _months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def _nth_weekday_of_month(d):
    return -(-d.day // 7)  # ceil(day / 7)


def is_repeating(item):
    return isinstance(item.get("repeat"), list) and len(item["repeat"]) > 0


def _rule_matches(rule, iso, anchor_iso):
    start = rule.get("startDate") or anchor_iso
    if iso < start:
        return False
    if rule.get("endDate") and iso > rule["endDate"]:
        return False
    date, anchor = _parse(iso), _parse(anchor_iso)
    phase = _parse(rule["startDate"]) if rule.get("startDate") else anchor
    if not date or not anchor or not phase:
        return False
    interval = rule.get("interval") or 1
    weekdays = rule.get("weekdays") or []
    freq = rule.get("freq")
    if freq == "weekly":
        return _js_day(date) in weekdays and _weeks_between(phase, date) % interval == 0
    if freq == "monthly":
        return (_js_day(date) in weekdays
                and _nth_weekday_of_month(date) == rule.get("ordinal")
                and _months_between(phase, date) % interval == 0)
    if freq == "yearly":
        return (date.month == anchor.month and date.day == anchor.day
                and (date.year - anchor.year) % interval == 0)
    return False


def occurrences(item, date_field, first, last):
    """(display date ISO, canonical key, resolved item) for each occurrence of
    ``item`` displayed between ``first`` and ``last`` (ISO strings, inclusive)."""
    anchor = item.get(date_field)
    if not anchor:
        return []
    anchor = str(anchor)[:10]
    if not is_repeating(item):
        if first <= anchor <= last:
            return [(anchor, anchor, {**item, "done": bool(item.get("done"))})]
        return []
    exceptions = item.get("exceptions") or {}
    done_dates = set(item.get("doneDates") or [])
    out = []

    def resolved(key):
        ex = exceptions.get(key) or {}
        overrides = ex if not ex.get("deleted") else {}
        merged = {**item, **{k: v for k, v in overrides.items() if k not in ("movedTo", "deleted")}}
        merged["done"] = key in done_dates
        return merged

    d, end = _parse(first), _parse(last)
    while d <= end:
        iso = d.isoformat()
        if any(_rule_matches(rule, iso, anchor) for rule in item["repeat"] if isinstance(rule, dict)):
            ex = exceptions.get(iso) or {}
            moved_away = ex.get("movedTo") and ex["movedTo"] != iso
            if not ex.get("deleted") and not moved_away:
                out.append((iso, iso, resolved(iso)))
        d += dt.timedelta(days=1)
    # Occurrences moved onto a date inside the window from anywhere in the series.
    for key, ex in exceptions.items():
        if not isinstance(ex, dict):
            continue
        moved = ex.get("movedTo")
        if moved and moved != key and first <= moved <= last and not ex.get("deleted"):
            out.append((moved, key, resolved(key)))
    return out


# ── widget data ─────────────────────────────────────────────────────
FALLBACK_COLOR = "#898781"  # same grey the app uses (selectors.js)


def _color_for(categories, item):
    cat = categories.get(item.get("categoryId")) or {}
    for col in cat.get("colors") or []:
        if isinstance(col, dict) and col.get("id") == item.get("colorId"):
            return col.get("value") or FALLBACK_COLOR
    return FALLBACK_COLOR


def build_widget_data(state, name="", today=None, days_before=1, days_after=7):
    """Each day from ``today - days_before`` to ``today + days_after`` with its
    items, all-day ones first, then by start time. The widget picks "today" by
    the phone's own clock, so the server's time zone doesn't matter."""
    state = state if isinstance(state, dict) else {}
    today = today or dt.date.today()
    first = today - dt.timedelta(days=days_before)
    last = today + dt.timedelta(days=days_after)
    first_iso, last_iso = first.isoformat(), last.isoformat()
    categories = {c.get("id"): c for c in state.get("categories") or [] if isinstance(c, dict)}
    by_day = {}

    def add(day, occ, kind, timed):
        by_day.setdefault(day, []).append({
            "kind": kind,
            "title": occ.get("title") or "(no title)",
            "start": occ.get("startTime") if timed else "",
            "end": occ.get("endTime") if timed else "",
            "allDay": not timed,
            "done": bool(occ.get("done")) if kind == "task" else False,
            "color": _color_for(categories, occ),
            "category": (categories.get(occ.get("categoryId")) or {}).get("name", ""),
            "place": occ.get("place") or "",
        })

    def items(key):
        return [x for x in state.get(key) or [] if isinstance(x, dict)]

    for ev in items("events"):
        for day, _, occ in occurrences(ev, "date", first_iso, last_iso):
            add(day, occ, "event", bool(_hhmm(occ.get("startTime"))))
    for task in items("tasks"):
        for day, _, occ in occurrences(task, "dueDate", first_iso, last_iso):
            add(day, occ, "task", bool(occ.get("scheduled") and _hhmm(occ.get("startTime"))))
    for sd in items("specialDays"):
        for day, _, occ in occurrences(sd, "date", first_iso, last_iso):
            add(day, occ, "special", False)

    days = []
    d = first
    while d <= last:
        iso = d.isoformat()
        entries = by_day.get(iso, [])
        entries.sort(key=lambda e: (not e["allDay"], e["start"] or "", e["title"]))
        days.append({"date": iso, "items": entries})
        d += dt.timedelta(days=1)
    return {"name": name, "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "days": days}


def _hhmm(value):
    try:
        h, m = str(value).split(":")[:2]
        return int(h), int(m)
    except (ValueError, AttributeError):
        return None

"""Calendar subscription feed (iCalendar / .ics).

Lets a phone's own calendar app subscribe to a profile's Calendar, so the
schedule shows in the phone's calendar widgets (home screen / lock screen)
without opening Daily Planner. Read-only: changes are still made in the app.

The feed URL carries a secret token (an HMAC of the profile id with the
server's secret key), because calendar apps can't log in. Anyone with the URL
can read that profile's schedule, so it is only shown to the logged-in owner.

Repeating items are expanded here into single dated occurrences over a window
(the past month to a year ahead) using the same rules as the client's
selectors.js — weekly / monthly (nth weekday) / yearly, "every N", per-rule
date ranges, and single-occurrence edits, moves and deletes — rather than
translated to RRULEs, so the phone shows exactly what the app shows.
"""
import datetime as dt
import hashlib
import hmac

WINDOW_PAST_DAYS = 31
WINDOW_FUTURE_DAYS = 366


# ── tokens ──────────────────────────────────────────────────────────
def feed_token(secret_key, profile_id):
    key = (secret_key or "").encode() if isinstance(secret_key, str) else (secret_key or b"")
    return hmac.new(key, f"calendar-feed:{profile_id}".encode(), hashlib.sha256).hexdigest()[:32]


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


# ── iCalendar output ────────────────────────────────────────────────
def _esc(text):
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line):
    """Lines longer than 75 bytes continue on the next line after a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not parts else 74):
            parts.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    parts.append(cur)
    return "\r\n ".join(parts)


def _hhmm(value):
    try:
        h, m = str(value).split(":")[:2]
        return int(h), int(m)
    except (ValueError, AttributeError):
        return None


def _local_dt(day, hhmm):
    h, m = hhmm
    return dt.datetime(day.year, day.month, day.day) + dt.timedelta(hours=h, minutes=m)


def _fmt_dt(value):
    return value.strftime("%Y%m%dT%H%M%S")


def _vevent(lines, uid, stamp, title, day_iso, start=None, end=None, category="", location="", notes=""):
    day = _parse(day_iso)
    lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", f"SUMMARY:{_esc(title or '(no title)')}"]
    start_hm = _hhmm(start) if start else None
    if start_hm:
        begin = _local_dt(day, start_hm)
        end_hm = _hhmm(end) if end else None
        finish = _local_dt(day, end_hm) if end_hm else begin + dt.timedelta(hours=1)
        if finish <= begin:
            finish = begin + dt.timedelta(hours=1)
        # Floating local time: shown at the same clock time on the phone.
        lines += [f"DTSTART:{_fmt_dt(begin)}", f"DTEND:{_fmt_dt(finish)}"]
    else:
        lines += [f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
                  f"DTEND;VALUE=DATE:{(day + dt.timedelta(days=1)).strftime('%Y%m%d')}",
                  "TRANSP:TRANSPARENT"]
    if category:
        lines.append(f"CATEGORIES:{_esc(category)}")
    if location:
        lines.append(f"LOCATION:{_esc(location)}")
    if notes:
        lines.append(f"DESCRIPTION:{_esc(notes)}")
    lines.append("END:VEVENT")


def build_ics(state, calendar_name="Daily Planner", today=None, now=None):
    state = state if isinstance(state, dict) else {}
    today = today or dt.date.today()
    now = now or dt.datetime.now(dt.timezone.utc)
    first = (today - dt.timedelta(days=WINDOW_PAST_DAYS)).isoformat()
    last = (today + dt.timedelta(days=WINDOW_FUTURE_DAYS)).isoformat()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    categories = {c.get("id"): c.get("name", "") for c in state.get("categories") or [] if isinstance(c, dict)}

    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Daily Planner//Calendar feed//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(calendar_name)}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT15M", "X-PUBLISHED-TTL:PT15M",
    ]

    def items(name):
        return [x for x in state.get(name) or [] if isinstance(x, dict)]

    for ev in items("events"):
        for day, key, occ in occurrences(ev, "date", first, last):
            _vevent(lines, f"ev-{ev.get('id')}-{key}@daily-planner", stamp, occ.get("title"), day,
                    occ.get("startTime"), occ.get("endTime"), categories.get(occ.get("categoryId"), ""),
                    occ.get("place") or "", occ.get("notes") or "")

    for task in items("tasks"):
        for day, key, occ in occurrences(task, "dueDate", first, last):
            mark = "✅ " if occ.get("done") else "☐ "
            timed = occ.get("scheduled") and occ.get("startTime")
            _vevent(lines, f"task-{task.get('id')}-{key}@daily-planner", stamp, mark + (occ.get("title") or ""),
                    day, occ.get("startTime") if timed else None, occ.get("endTime") if timed else None,
                    categories.get(occ.get("categoryId"), ""), occ.get("place") or "", occ.get("notes") or "")

    for sd in items("specialDays"):
        for day, key, occ in occurrences(sd, "date", first, last):
            _vevent(lines, f"day-{sd.get('id')}-{key}@daily-planner", stamp, "🎉 " + (occ.get("title") or ""),
                    day, category=categories.get(occ.get("categoryId"), ""), notes=occ.get("notes") or "")

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"

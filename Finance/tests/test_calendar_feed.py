"""Calendar subscription feed (Calendar/ics_feed.py): recurrence expansion must
match the client's selectors.js, and the output must be valid iCalendar."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Calendar import ics_feed  # noqa: E402

TODAY = dt.date(2026, 11, 26)  # a Thursday
NOW = dt.datetime(2026, 11, 26, 1, 0, tzinfo=dt.timezone.utc)


def dates(item, field="date", first="2026-11-01", last="2026-12-31"):
    return sorted(d for d, _, _ in ics_feed.occurrences(item, field, first, last))


def test_single_event():
    ev = {"id": "e1", "date": "2026-11-27", "repeat": []}
    assert dates(ev) == ["2026-11-27"]
    assert dates({**ev, "date": "2027-03-01"}) == []


def test_weekly_mon_wed_every_week():
    ev = {"id": "e", "date": "2026-11-02", "repeat": [{"freq": "weekly", "weekdays": [1, 3], "interval": 1}]}
    got = dates(ev, last="2026-11-15")
    assert got == ["2026-11-02", "2026-11-04", "2026-11-09", "2026-11-11"]


def test_weekly_every_other_week_and_rule_range():
    ev = {"id": "e", "date": "2026-11-02",
          "repeat": [{"freq": "weekly", "weekdays": [1], "interval": 2, "endDate": "2026-12-01"}]}
    assert dates(ev) == ["2026-11-02", "2026-11-16", "2026-11-30"]


def test_monthly_nth_weekday():
    # 2nd Tuesday of each month
    ev = {"id": "e", "date": "2026-11-10", "repeat": [{"freq": "monthly", "weekdays": [2], "ordinal": 2, "interval": 1}]}
    assert dates(ev) == ["2026-11-10", "2026-12-08"]


def test_yearly():
    sd = {"id": "b", "date": "2020-12-05", "repeat": [{"freq": "yearly", "interval": 1}]}
    assert dates(sd) == ["2026-12-05"]


def test_exceptions_deleted_and_moved():
    ev = {"id": "e", "date": "2026-11-02", "title": "Class",
          "repeat": [{"freq": "weekly", "weekdays": [1], "interval": 1}],
          "exceptions": {"2026-11-09": {"deleted": True},
                         "2026-11-16": {"movedTo": "2026-11-18", "title": "Class (moved)"}}}
    occ = ics_feed.occurrences(ev, "date", "2026-11-01", "2026-11-30")
    shown = sorted((d, o["title"]) for d, _, o in occ)
    assert shown == [("2026-11-02", "Class"), ("2026-11-18", "Class (moved)"),
                     ("2026-11-23", "Class"), ("2026-11-30", "Class")]


def test_build_ics_contents():
    state = {
        "categories": [{"id": "c1", "name": "MMU", "colors": []}],
        "events": [
            {"id": "e1", "title": "Calculus, Lecture", "date": "2026-11-27", "startTime": "08:30",
             "endTime": "10:30", "categoryId": "c1", "repeat": [], "place": "FCI"},
            {"id": "e2", "title": "All day thing", "date": "2026-11-28", "startTime": "", "repeat": []},
        ],
        "tasks": [
            {"id": "t1", "title": "Homework", "dueDate": "2026-11-27", "scheduled": True,
             "startTime": "20:00", "endTime": "21:00", "done": True, "repeat": []},
            {"id": "t2", "title": "Buy book", "dueDate": "2026-11-29", "scheduled": False, "repeat": []},
        ],
        "specialDays": [{"id": "s1", "title": "Mum birthday", "date": "2026-12-05", "repeat": []}],
    }
    ics = ics_feed.build_ics(state, "Daily Planner · Test", today=TODAY, now=NOW)
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert ics.count("BEGIN:VEVENT") == ics.count("END:VEVENT") == 5
    assert "SUMMARY:Calculus\\, Lecture" in ics
    assert "DTSTART:20261127T083000" in ics and "DTEND:20261127T103000" in ics
    assert "CATEGORIES:MMU" in ics and "LOCATION:FCI" in ics
    assert "DTSTART;VALUE=DATE:20261128" in ics  # untimed event -> all day
    assert "SUMMARY:✅ Homework" in ics and "SUMMARY:☐ Buy book" in ics
    assert "SUMMARY:🎉 Mum birthday" in ics
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


def test_repeating_task_done_per_occurrence():
    task = {"id": "t", "title": "Gym", "dueDate": "2026-11-02", "scheduled": True, "startTime": "07:00",
            "repeat": [{"freq": "weekly", "weekdays": [1], "interval": 1}], "doneDates": ["2026-11-09"]}
    occ = {d: o["done"] for d, _, o in ics_feed.occurrences(task, "dueDate", "2026-11-01", "2026-11-17")}
    assert occ == {"2026-11-02": False, "2026-11-09": True, "2026-11-16": False}


def test_empty_state_is_valid():
    ics = ics_feed.build_ics(None, today=TODAY, now=NOW)
    assert "BEGIN:VEVENT" not in ics and ics.endswith("END:VCALENDAR\r\n")


def test_token():
    tok = ics_feed.feed_token("secret", "p1")
    assert ics_feed.token_ok("secret", "p1", tok)
    assert not ics_feed.token_ok("secret", "p2", tok)
    assert not ics_feed.token_ok("other", "p1", tok)
    assert not ics_feed.token_ok("secret", "p1", "")

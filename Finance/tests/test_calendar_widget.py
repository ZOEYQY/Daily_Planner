"""Home-screen widget data (Calendar/schedule_feed.py): recurrence expansion
must match the client's selectors.js, and each day lists its items in order."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Calendar import schedule_feed  # noqa: E402

TODAY = dt.date(2026, 11, 26)  # a Thursday


def dates(item, field="date", first="2026-11-01", last="2026-12-31"):
    return sorted(d for d, _, _ in schedule_feed.occurrences(item, field, first, last))


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
    occ = schedule_feed.occurrences(ev, "date", "2026-11-01", "2026-11-30")
    shown = sorted((d, o["title"]) for d, _, o in occ)
    assert shown == [("2026-11-02", "Class"), ("2026-11-18", "Class (moved)"),
                     ("2026-11-23", "Class"), ("2026-11-30", "Class")]


def test_widget_data():
    state = {
        "categories": [{"id": "c1", "name": "MMU", "colors": [{"id": "k1", "value": "#2a78d6"}]}],
        "events": [
            {"id": "e1", "title": "Calculus", "date": "2026-11-26", "startTime": "08:30", "endTime": "10:30",
             "categoryId": "c1", "colorId": "k1", "repeat": [], "place": "FCI"},
            {"id": "e0", "title": "Early", "date": "2026-11-26", "startTime": "07:00", "endTime": "08:00", "repeat": []},
            {"id": "e2", "title": "All day thing", "date": "2026-11-26", "startTime": "", "repeat": []},
        ],
        "tasks": [
            {"id": "t1", "title": "Homework", "dueDate": "2026-11-27", "scheduled": True,
             "startTime": "20:00", "endTime": "21:00", "done": True, "repeat": []},
            {"id": "t2", "title": "Buy book", "dueDate": "2026-11-27", "scheduled": False, "repeat": []},
        ],
        "specialDays": [{"id": "s1", "title": "Mum birthday", "date": "2026-11-28", "repeat": []}],
    }
    data = schedule_feed.build_widget_data(state, name="Test", today=TODAY)
    days = {d["date"]: d["items"] for d in data["days"]}
    assert min(days) == "2026-11-25" and max(days) == "2026-12-03"
    today = days["2026-11-26"]
    assert [i["title"] for i in today] == ["All day thing", "Early", "Calculus"]
    calc = today[2]
    assert calc == {"kind": "event", "title": "Calculus", "start": "08:30", "end": "10:30", "allDay": False,
                    "done": False, "color": "#2a78d6", "category": "MMU", "place": "FCI"}
    assert today[1]["color"] == "#898781"  # no category -> the app's grey
    tomorrow = days["2026-11-27"]
    assert [(i["title"], i["allDay"], i["done"]) for i in tomorrow] == [("Buy book", True, False), ("Homework", False, True)]
    assert days["2026-11-28"][0]["kind"] == "special"


def test_repeating_task_done_per_occurrence():
    task = {"id": "t", "title": "Gym", "dueDate": "2026-11-02", "scheduled": True, "startTime": "07:00",
            "repeat": [{"freq": "weekly", "weekdays": [1], "interval": 1}], "doneDates": ["2026-11-09"]}
    occ = {d: o["done"] for d, _, o in schedule_feed.occurrences(task, "dueDate", "2026-11-01", "2026-11-17")}
    assert occ == {"2026-11-02": False, "2026-11-09": True, "2026-11-16": False}


def test_empty_state():
    data = schedule_feed.build_widget_data(None, today=TODAY)
    assert len(data["days"]) == 9 and all(d["items"] == [] for d in data["days"])


def test_token():
    tok = schedule_feed.feed_token("secret", "p1")
    assert schedule_feed.token_ok("secret", "p1", tok)
    assert not schedule_feed.token_ok("secret", "p2", tok)
    assert not schedule_feed.token_ok("other", "p1", tok)
    assert not schedule_feed.token_ok("secret", "p1", "")


def test_widget_zip_is_public_and_has_the_script():
    import io
    import zipfile
    import app as webapp

    resp = webapp.app.test_client().get("/calendar/widget/DailyPlanner.zip")
    assert resp.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(resp.data)).namelist()
    assert {"script.json", "index.tsx", "widget.tsx", "app_intents.tsx"} <= set(names)

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

STATE = {
    "categories": [
        {"id": "cat_1", "name": "Work", "enabledFields": ["link"], "colors": [
            {"id": "color_1", "name": "General", "value": "#3a9163", "enabledFields": []},
            {"id": "color_2", "name": "Urgent", "value": "#c0392b", "enabledFields": ["place"]}]},
        {"id": "cat_2", "name": "Home", "enabledFields": [], "colors": []},
    ],
    "tasks": [
        {"id": "tsk_1", "title": "Report", "done": False, "categoryId": "cat_1", "colorId": "color_1",
         "dueDate": "2026-10-03", "startTime": "09:00", "endTime": "10:00", "scheduled": True, "notes": "",
         "rescheduleCount": 2, "rescheduleHistory": [{"from": "2026-10-01", "to": "2026-10-03"}],
         "overdueReschedule": False, "repeat": [{"id": "rule_1", "freq": "weekly", "weekdays": [5], "interval": 1}],
         "exceptions": {"2026-10-10": {"deleted": True}}, "doneDates": ["2026-09-26"], "link": "", "place": "",
         "people": "", "thingsToBring": "", "topic": "", "todoList": [{"id": "td_1", "text": "Draft", "done": True}],
         "targets": [], "recordNotes": "", "customFields": {"field_1": "x"}, "extraFieldsOverride": []},
        {"id": "tsk_2", "title": "Someday", "done": False, "dueDate": "", "startTime": "", "scheduled": False},
    ],
    "events": [
        {"id": "evt_1", "title": "Dentist", "categoryId": "cat_2", "colorId": "", "date": "2026-10-05",
         "startTime": "14:00", "endTime": "15:00", "notes": "", "repeat": [], "exceptions": {}, "place": "Clinic"},
    ],
    "specialDays": [{"id": "spd_1", "title": "Birthday", "date": "2026-11-02", "repeat": [], "exceptions": {}}],
    "customFieldDefs": [{"id": "field_1", "key": "field_1", "label": "Mood"}],
    "customDates": ["2026-10-02"],
    "view": "week",
    "cursorDate": "2026-10-02",
    "weekStartsOn": 1,
    "todoOrder": ["tsk_2", "tsk_1"],
    "modal": None,
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from flask import Flask

    from Calendar import habits
    from Finance import database

    monkeypatch.setenv("DATABASE_URL", URL.create(
        "sqlite", database=str(tmp_path / "calendar.sqlite")).render_as_string(hide_password=False))
    database._engine.cache_clear()
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(habits.bp, url_prefix="/api")
    test_client = app.test_client()
    with test_client.session_transaction() as session_data:
        session_data["auth_profile"] = "profile-a"
    yield test_client
    database._engine.cache_clear()


def _sql(statement, **params):
    from Finance import database

    with Session(database._engine(database.database_url())) as session:
        return session.execute(text(statement), params).all()


def _put(client, state, version, profile="profile-a"):
    return client.put("/api/calendar-state", json={"profile_id": profile, "state": state, "version": version})


def test_calendar_state_round_trips_through_tables(client):
    assert _put(client, STATE, 0).json == {"version": 1}
    assert client.get("/api/calendar-state?profile_id=profile-a").json == {"state": STATE, "version": 1}

    assert _sql("SELECT title, date, start_time FROM calendar_events WHERE profile_id = 'profile-a'") == [
        ("Dentist", "2026-10-05", "14:00")]
    assert _sql("SELECT id, due_date, reschedule_count FROM calendar_tasks ORDER BY position") == [
        ("tsk_1", "2026-10-03", 2), ("tsk_2", None, None)]
    assert _sql("""
        SELECT c.name, COUNT(k.id) FROM calendar_categories c
        LEFT JOIN calendar_category_colors k ON k.profile_id = c.profile_id AND k.parent_position = c.position
        GROUP BY c.name ORDER BY c.name
    """) == [("Home", 0), ("Work", 2)]
    payload = _sql("SELECT payload FROM calendar_states")[0][0]
    assert "tasks" not in payload and '"view"' in payload

    smaller = {**STATE, "tasks": [], "events": []}
    assert _put(client, smaller, 1).json == {"version": 2}
    assert client.get("/api/calendar-state?profile_id=profile-a").json["state"] == smaller
    assert _sql("SELECT COUNT(*) FROM calendar_tasks") == [(0,)]


def test_unfitting_calendar_state_is_kept_whole(client):
    odd = {"tasks": "not a list", "view": "month"}
    assert _put(client, odd, 0).status_code == 200
    assert client.get("/api/calendar-state?profile_id=profile-a").json["state"] == odd
    assert _sql("SELECT COUNT(*) FROM calendar_state_lists") == [(0,)]

    assert _put(client, STATE, 1).status_code == 200
    assert client.get("/api/calendar-state?profile_id=profile-a").json["state"] == STATE


def test_legacy_calendar_blob_is_read_then_migrated(client, monkeypatch, capsys):
    import sys

    from Finance import database

    with Session(database._engine(database.database_url())) as session:
        session.add(database.CalendarState(profile_id="profile-a", payload=STATE, version=7))
        session.add(database.CalendarState(profile_id="profile-b", payload={"tasks": 5}, version=1))
        session.commit()

    assert client.get("/api/calendar-state?profile_id=profile-a").json == {"state": STATE, "version": 7}

    import migrate_documents_to_tables
    monkeypatch.setattr(sys, "argv", ["migrate", "--apply"])
    migrate_documents_to_tables.main()
    assert "and 1 Calendar states (1 left as JSON)" in capsys.readouterr().out

    assert client.get("/api/calendar-state?profile_id=profile-a").json == {"state": STATE, "version": 7}
    assert _sql("SELECT COUNT(*) FROM calendar_tasks WHERE profile_id = 'profile-a'") == [(2,)]

    imported = client.post("/api/calendar-state/import", json={
        "profile_id": "profile-a", "source_id": "old-phone",
        "state": {"tasks": [{"id": "tsk_9", "title": "From phone"}]},
    })
    assert imported.json["imported"] is True and imported.json["version"] == 8
    tasks = client.get("/api/calendar-state?profile_id=profile-a").json["state"]["tasks"]
    assert [task["id"] for task in tasks] == ["tsk_1", "tsk_2", "tsk_9"]

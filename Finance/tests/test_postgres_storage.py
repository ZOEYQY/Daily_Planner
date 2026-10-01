from sqlalchemy.engine import URL


def test_flat_json_migration_and_database_storage(tmp_path, monkeypatch):
    import database
    import finance_helpers
    from migrate_json_to_postgres import collect_documents

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    expense = [{"id": "txn-1", "amount": 12.5, "tags": ["food"]}]
    (data_dir / "expenses.json").write_text(
        '[{"id":"txn-1","amount":12.5,"tags":["food"]}]', encoding="utf-8")
    monkeypatch.setenv(
        "DATABASE_URL",
        URL.create("sqlite", database=str(tmp_path / "test.sqlite")).render_as_string(hide_password=False),
    )
    database._engine.cache_clear()
    monkeypatch.setattr(finance_helpers, "DATA_DIR", str(data_dir))

    documents = collect_documents(str(data_dir))
    profile = documents["profiles.json"]["profiles"][0]
    store_key = f"profiles/{profile['id']}/expenses.json"
    assert documents[store_key] == expense

    assert database.import_documents(documents) == (2, 0)
    assert finance_helpers.load_data(
        str(data_dir / "profiles" / profile["id"] / "expenses.json"), []) == expense
    assert database.import_documents(documents) == (0, 2)
    database._engine.cache_clear()


def test_render_refuses_missing_database_fallback(monkeypatch):
    import pytest

    import finance_helpers

    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        finance_helpers._database_store()


def test_render_refuses_non_postgres_database_url(monkeypatch):
    import pytest

    from Finance import database

    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///ephemeral.sqlite")
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        database.require_postgres()


def test_render_refuses_missing_external_receipt_storage(monkeypatch):
    import pytest

    from Finance import receipt_storage

    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("CLOUDINARY_URL", raising=False)
    with pytest.raises(RuntimeError, match="CLOUDINARY_URL"):
        receipt_storage.require_cloudinary()


def test_habits_and_calendar_state_use_shared_database(tmp_path, monkeypatch):
    from flask import Flask

    from Finance import database
    from Calendar import habits

    monkeypatch.setenv(
        "DATABASE_URL",
        URL.create("sqlite", database=str(tmp_path / "shared.sqlite")).render_as_string(hide_password=False),
    )
    database._engine.cache_clear()
    habits.init(str(tmp_path / "unused-habits.sqlite"))
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(habits.bp, url_prefix="/api")
    client = app.test_client()
    with client.session_transaction() as session_data:
        session_data["auth_profile"] = "profile-a"

    created = client.post("/api/habits", json={"name": "Walk", "emoji": "Shoe"})
    assert created.status_code == 200
    habit_id = created.json["id"]
    assert client.get("/api/habits").json["habits"][0]["name"] == "Walk"
    assert client.patch(f"/api/habits/{habit_id}", json={"name": "Long walk"}).json == {"ok": True}
    assert client.get("/api/habits").json["habits"][0]["name"] == "Long walk"
    assert client.post("/api/checkins", json={"habit_id": habit_id, "date": habits.dt.date.today().isoformat()}).json == {"on": True}

    missing = client.get("/api/calendar-state?profile_id=profile-a")
    assert missing.json == {"state": None, "version": 0}
    payload = {"categories": [], "tasks": [{"id": "task-1"}], "events": []}
    saved = client.put("/api/calendar-state", json={"profile_id": "profile-a", "state": payload, "version": 0})
    assert saved.status_code == 200
    assert saved.json["version"] == 1
    updated = client.put("/api/calendar-state", json={"profile_id": "profile-a", "state": {**payload, "tasks": []}, "version": 1})
    assert updated.status_code == 200
    assert updated.json["version"] == 2
    stale = client.put("/api/calendar-state", json={"profile_id": "profile-a", "state": payload, "version": 0})
    assert stale.status_code == 409
    assert client.get("/api/calendar-state?profile_id=profile-a").json["state"] == {**payload, "tasks": []}

    device_one = client.post("/api/calendar-state/import", json={
        "profile_id": "profile-a",
        "source_id": "device-one",
        "state": {"tasks": [{"id": "legacy-one", "title": "One"}]},
    })
    assert device_one.status_code == 200 and device_one.json["imported"] is True
    device_two = client.post("/api/calendar-state/import", json={
        "profile_id": "profile-a",
        "source_id": "device-two",
        "state": {"tasks": [{"id": "legacy-two", "title": "Two"}]},
    })
    assert device_two.status_code == 200 and device_two.json["imported"] is True
    assert {task["id"] for task in device_two.json["state"]["tasks"]} == {"legacy-one", "legacy-two"}
    duplicate = client.post("/api/calendar-state/import", json={
        "profile_id": "profile-a",
        "source_id": "device-two",
        "state": {"tasks": [{"id": "legacy-two", "title": "stale"}]},
    })
    assert duplicate.status_code == 200 and duplicate.json["imported"] is False
    assert next(task for task in duplicate.json["state"]["tasks"] if task["id"] == "legacy-two")["title"] == "Two"

    with client.session_transaction() as session_data:
        session_data["auth_profile"] = "profile-b"
    assert client.get("/api/habits").json["habits"] == []
    assert client.get("/api/calendar-state?profile_id=profile-b").json["state"] is None
    assert client.get("/api/calendar-state?profile_id=profile-a").status_code == 403
    assert client.put("/api/calendar-state", json={
        "profile_id": "profile-a", "state": payload, "version": 0,
    }).status_code == 403
    database._engine.cache_clear()


def test_receipt_metadata_is_persisted_in_shared_database(tmp_path, monkeypatch):
    from Finance import database, receipt_storage

    monkeypatch.setenv(
        "DATABASE_URL",
        URL.create("sqlite", database=str(tmp_path / "shared.sqlite")).render_as_string(hide_password=False),
    )
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key:secret@example")
    database._engine.cache_clear()
    database._engine(database.database_url())
    monkeypatch.setattr(
        receipt_storage.cloudinary.uploader,
        "upload",
        lambda *args, **kwargs: {"public_id": kwargs["public_id"], "format": "jpg"},
    )

    receipt_storage.upload("profile-a", "receipt-1.jpg", b"image-bytes")
    asset = database.get_receipt_asset("profile-a", "receipt-1.jpg")
    assert asset.storage_key == "daily-planner/profile-a/receipt-1"
    assert asset.format == "jpg"
    assert not (tmp_path / "receipt-1.jpg").exists()
    database._engine.cache_clear()


def test_legacy_habits_import_into_shared_database(tmp_path, monkeypatch):
    import sqlite3

    from sqlalchemy.engine import URL

    from Finance import database
    from Calendar.migrate_habits_to_postgres import migrate

    monkeypatch.setenv(
        "DATABASE_URL",
        URL.create("sqlite", database=str(tmp_path / "shared.sqlite")).render_as_string(hide_password=False),
    )
    database._engine.cache_clear()
    monkeypatch.setattr(database, "require_postgres", lambda: database._engine(database.database_url()))
    database.save_document("profiles.json", {"profiles": [{"id": "profile-a", "name": "A"}]})
    legacy_path = tmp_path / "legacy-habits.sqlite"
    with sqlite3.connect(legacy_path) as source:
        source.executescript("""
            CREATE TABLE habits (id INTEGER PRIMARY KEY, name TEXT, emoji TEXT, color TEXT, sort INTEGER, archived INTEGER, created_at TEXT, owner TEXT);
            CREATE TABLE checkins (habit_id INTEGER, date TEXT, note TEXT DEFAULT '');
            INSERT INTO habits VALUES (7, 'Walk', 'Shoe', '#123456', 2, 0, '2026-09-30 08:15:00', 'profile-a');
            INSERT INTO checkins VALUES (7, '2026-09-30', 'outside');
        """)

    assert migrate(str(legacy_path)) == (1, 1)
    from Finance.database import Habit, HabitCheckin
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    with Session(database._engine(database.database_url())) as target:
        habit = target.scalar(select(Habit).where(Habit.owner == "profile-a"))
        checkin = target.get(HabitCheckin, (habit.id, "2026-09-30"))
        assert (habit.name, habit.emoji, habit.color, habit.sort) == ("Walk", "Shoe", "#123456", 2)
        assert habit.created_at.isoformat() == "2026-09-30T08:15:00"
        assert checkin.note == "outside"
    database._engine.cache_clear()

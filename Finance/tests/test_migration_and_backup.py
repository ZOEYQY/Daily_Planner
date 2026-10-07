"""The JSON -> PostgreSQL migration script, the backup/restore script, and
per-profile isolation of Habits and Calendar data. Uses a throwaway SQLite
database, or real PostgreSQL when FINANCE_TEST_POSTGRES_URL is set."""
import json
import os
import sqlite3
import sys

import pytest
from sqlalchemy.engine import URL

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from conftest import POSTGRES_TEST_URL  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    from Finance import database

    if POSTGRES_TEST_URL:
        monkeypatch.setenv("DATABASE_URL", POSTGRES_TEST_URL)
        database._engine.cache_clear()
        assert "test" in POSTGRES_TEST_URL
        database._tables()
        database.calendar_tables()
        database.Base.metadata.drop_all(database._engine(database.database_url()))
        database._engine.cache_clear()
    else:
        monkeypatch.setenv("DATABASE_URL", URL.create(
            "sqlite", database=str(tmp_path / "shared.sqlite")).render_as_string(hide_password=False))
        database._engine.cache_clear()
    yield database
    database._engine.cache_clear()


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def local_data(tmp_path):
    data = tmp_path / "data"
    _write(data / "profiles.json", {"schema_version": 1, "profiles": [
        {"id": "pa", "name": "Alice", "created_at": "2026-01-01", "password_hash": "scrypt:32768:8:1$salt$abc"},
        {"id": "pb", "name": "Bob", "created_at": "2026-02-01", "password": "bob-plain-password"},
    ]})
    _write(data / "profiles" / "pa" / "expenses.json", [
        {"id": "t1", "date": "2026-09-01", "type": "expense", "category": "Food", "account": "Cash",
         "item": "lunch", "amount": 12.5, "tags": ["work"], "deleted_at": None},
        {"id": "t2", "date": "2026-09-02", "type": "income", "category": "Salary", "account": "Bank",
         "item": "pay", "amount": 1000},
    ])
    _write(data / "profiles" / "pa" / "debts.json", {"schema_version": 1, "debts": [
        {"id": "d1", "direction": "owed_to_me", "counterparty": "Carol", "principal": 50.0,
         "date": "2026-09-01", "due_date": "", "payments": [{"id": "p1", "date": "2026-09-03", "amount": 20.0}]},
    ]})
    _write(data / "profiles" / "pb" / "expenses.json", [
        {"id": "t3", "date": "2026-09-05", "type": "expense", "category": "Fun", "account": "Cash",
         "item": "movie", "amount": 9.0},
    ])
    habits_db = tmp_path / "habits.db"
    with sqlite3.connect(habits_db) as con:
        con.executescript("""
            CREATE TABLE habits (id INTEGER PRIMARY KEY, name TEXT, emoji TEXT, color TEXT, sort INTEGER,
                                 archived INTEGER, created_at TEXT, owner TEXT);
            CREATE TABLE checkins (habit_id INTEGER, date TEXT, note TEXT DEFAULT '');
            INSERT INTO habits VALUES (1, 'Walk', '', '#123456', 0, 0, '2026-09-30 08:15:00', 'pa');
            INSERT INTO habits VALUES (2, 'Read', '', '#654321', 1, 0, '2026-09-30 09:00:00', 'pb');
            INSERT INTO checkins VALUES (1, '2026-09-30', 'outside');
            INSERT INTO checkins VALUES (1, '2026-10-01', '');
        """)
    con.close()
    return data, habits_db


def _snapshot(data, habits_db):
    files = {p: p.read_bytes() for p in sorted(data.rglob("*")) if p.is_file()}
    files[habits_db] = habits_db.read_bytes()
    return files


def test_dry_run_writes_nothing(db, local_data):
    import migrate_json_to_postgres as migration

    data, habits_db = local_data
    result = migration.run(str(data), str(habits_db), apply=False)
    assert result["records"]["expenses.json"] == 3
    assert db.load_document("profiles.json", None) is None
    assert db.load_document("profiles/pa/expenses.json", None) is None


def test_migration_imports_everything_and_is_idempotent(db, local_data):
    import migrate_json_to_postgres as migration
    from werkzeug.security import check_password_hash

    data, habits_db = local_data
    before = _snapshot(data, habits_db)
    first = migration.run(str(data), str(habits_db), apply=True)
    summary = migration.format_summary(first)
    assert "Users migrated: 2" in summary
    assert "Profiles migrated: 2" in summary
    assert "Expenses migrated: 2" in summary
    assert "Income migrated: 1" in summary
    assert "Diary entries migrated: 0" in summary
    assert "Habits migrated: 2" in summary
    # 1 debt + 1 debt payment + 2 check-ins
    assert "Other records migrated: 4" in summary
    assert "Duplicates skipped: 0" in summary
    assert "Alice: 2 records, income 1000.00, expense 12.50, 2 account balance(s): JSON = PostgreSQL" in summary

    assert db.load_document("profiles/pa/expenses.json", None) == json.loads(
        (data / "profiles" / "pa" / "expenses.json").read_text(encoding="utf-8"))
    assert db.load_document("profiles/pa/debts.json", None)["debts"][0]["payments"][0]["amount"] == 20.0
    profiles = {p["id"]: p for p in db.load_document("profiles.json", None)["profiles"]}
    assert "password" not in profiles["pb"]
    assert check_password_hash(profiles["pb"]["password_hash"], "bob-plain-password")
    assert db.legacy_document_keys() == []  # all stored as rows, not JSON blobs

    second = migration.run(str(data), str(habits_db), apply=True)
    assert "Users migrated: 0" in migration.format_summary(second)
    assert "Other records migrated: 0" in migration.format_summary(second)
    # 3 stores (2 + 1 debts + 1 = 4 records) + 2 habits already present
    assert "Duplicates skipped: 6" in migration.format_summary(second)
    assert len(db.load_document("profiles.json", None)["profiles"]) == 2
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session
    with Session(db._engine(db.database_url())) as session:
        assert session.scalar(select(func.count()).select_from(db.Habit)) == 2
        assert session.scalar(select(func.count()).select_from(db.HabitCheckin)) == 2
        walk = session.scalar(select(db.Habit).where(db.Habit.owner == "pa"))
        assert walk.created_at.isoformat().startswith("2026-09-30T08:15:00")
    assert _snapshot(data, habits_db) == before  # sources untouched


def test_migration_never_merges_into_an_existing_account_with_the_same_name(db, local_data):
    import migrate_json_to_postgres as migration

    db.save_document("profiles.json", {"schema_version": 1, "profiles": [
        {"id": "someone-else", "name": "alice", "created_at": "2026-03-01", "password_hash": "scrypt:1:1:1$s$h"}]})
    db.save_document("profiles/someone-else/expenses.json", [{"id": "x", "amount": 1.0}])
    data, habits_db = local_data
    result = migration.run(str(data), str(habits_db), apply=True)
    assert result["status"] == {"pa": "conflict", "pb": "new"}
    assert db.load_document("profiles/pa/expenses.json", None) is None
    assert db.load_document("profiles/someone-else/expenses.json", None) == [{"id": "x", "amount": 1.0}]
    assert "NOT migrated" in migration.format_summary(result)


def test_migration_validates_before_writing(db, tmp_path):
    import migrate_json_to_postgres as migration

    data = tmp_path / "bad"
    _write(data / "profiles.json", {"profiles": [{"id": "pa", "name": "A"}, {"id": "pa", "name": "B"}]})
    _write(data / "profiles" / "pa" / "expenses.json", "not a list")
    with pytest.raises(migration.MigrationError) as error:
        migration.run(str(data), str(tmp_path / "none.db"), apply=True)
    assert any("duplicate id" in problem for problem in error.value.problems)
    assert db.load_document("profiles.json", None) is None


def test_backup_and_restore_round_trip(db, local_data, tmp_path, monkeypatch):
    import backup_database
    import migrate_json_to_postgres as migration

    data, habits_db = local_data
    migration.run(str(data), str(habits_db), apply=True)
    expected = {key: db.load_document(key, None) for key in ("profiles.json", "profiles/pa/expenses.json",
                                                              "profiles/pa/debts.json", "profiles/pb/expenses.json")}
    path, counts = backup_database.backup(str(tmp_path / "out" / "b.json.gz"))
    assert counts["finance_transactions"] == 3 and counts["habits"] == 2

    with pytest.raises(backup_database.BackupError, match="Refusing"):
        backup_database.restore(path)  # never into a database that has data

    monkeypatch.setenv("DATABASE_URL", URL.create(
        "sqlite", database=str(tmp_path / "restored.sqlite")).render_as_string(hide_password=False))
    db._engine.cache_clear()
    backup_database.restore(path)
    assert {key: db.load_document(key, None) for key in expected} == expected


def test_encrypted_backups_are_verified_before_restore(db, local_data, tmp_path, monkeypatch):
    import backup_database
    import migrate_json_to_postgres as migration

    data, habits_db = local_data
    migration.run(str(data), str(habits_db), apply=True)
    monkeypatch.setenv("BACKUP_PASSPHRASE", "correct horse battery staple")
    out = tmp_path / "enc"
    first, counts1 = backup_database.backup(str(out / "daily-planner-20261001-020000.json.gz.enc"), encrypt=True)
    second, counts2 = backup_database.backup(str(out / "daily-planner-20261002-020000.json.gz.enc"), encrypt=True)
    assert counts1 == counts2 and counts1["finance_transactions"] == 3
    raw = open(second, "rb").read()
    assert b"lunch" not in raw and b"scrypt:" not in raw  # nothing readable without the passphrase
    assert backup_database.read_backup(second)["row_counts"] == counts2
    manifest = backup_database.write_manifest(str(out))
    assert [entry["file"] for entry in manifest["backups"]] == [os.path.basename(second), os.path.basename(first)]
    assert all(entry["sha256"] and entry["encrypted"] for entry in manifest["backups"])

    monkeypatch.setenv("BACKUP_PASSPHRASE", "a wrong passphrase!!")
    with pytest.raises(backup_database.BackupError, match="decrypt"):
        backup_database.read_backup(second)
    monkeypatch.setenv("BACKUP_PASSPHRASE", "correct horse battery staple")

    tampered = bytearray(raw)
    tampered[-5] ^= 1
    open(second, "wb").write(bytes(tampered))
    with pytest.raises(backup_database.BackupError, match="Checksum mismatch"):
        backup_database.restore(second)
    os.remove(first + ".sha256")
    with pytest.raises(backup_database.BackupError, match="Missing checksum"):
        backup_database.read_backup(first)

    monkeypatch.setenv("DATABASE_URL", URL.create(
        "sqlite", database=str(tmp_path / "fresh.sqlite")).render_as_string(hide_password=False))
    db._engine.cache_clear()
    counts, _ = backup_database.restore(first, skip_checksum=True)
    assert counts == counts1
    assert db.load_document("profiles/pa/expenses.json", None)[0]["item"] == "lunch"


def test_retention_keeps_7_daily_4_weekly_3_monthly():
    import datetime as dt

    import backup_database

    start = dt.datetime(2026, 1, 1, 2, 0)
    stamps = [start + dt.timedelta(days=offset) for offset in range(200)]  # one backup per day
    kept = sorted(backup_database.select_retained(stamps))
    newest = stamps[-1]
    assert all(stamp in kept for stamp in stamps[-7:])  # last 7 days
    weekly = {s.isocalendar()[:2] for s in kept}
    monthly = {(s.year, s.month) for s in kept}
    assert len(weekly) >= 4 and len(monthly) == 3
    assert (newest - kept[0]).days < 100  # nothing older than ~3 months survives
    assert len(kept) <= 7 + 4 + 3


def test_rotation_only_deletes_old_backup_files(tmp_path):
    import backup_database

    for day in range(1, 31):
        name = f"daily-planner-202609{day:02d}-020000.json.gz.enc"
        (tmp_path / name).write_bytes(b"x")
        (tmp_path / (name + ".sha256")).write_text("0  x")
    (tmp_path / "notes.txt").write_text("keep me")
    removed = backup_database.rotate(str(tmp_path))
    remaining = sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".enc"))
    assert "daily-planner-20260930-020000.json.gz.enc" in remaining
    assert len(remaining) + len(removed) == 30 and len(remaining) <= 14
    assert (tmp_path / "notes.txt").read_text() == "keep me"
    assert not any((tmp_path / (name + ".sha256")).exists() for name in removed)


def _habits_client(db, profile):
    from flask import Flask

    from Calendar import habits

    habits.init("unused.sqlite")
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(habits.bp, url_prefix="/api")
    client = app.test_client()
    with client.session_transaction() as session_data:
        session_data["auth_profile"] = profile
    return client


def test_habits_and_calendar_are_isolated_per_profile(db):
    import datetime as dt

    alice, bob = _habits_client(db, "pa"), _habits_client(db, "pb")
    habit_id = alice.post("/api/habits", json={"name": "Alice habit"}).json["id"]
    today = dt.date.today().isoformat()
    assert alice.post("/api/checkins", json={"habit_id": habit_id, "date": today}).json == {"on": True}

    assert bob.get("/api/habits").json["habits"] == []
    assert bob.post("/api/checkins", json={"habit_id": habit_id, "date": today}).status_code == 404
    bob.patch(f"/api/habits/{habit_id}", json={"name": "hacked"})
    bob.delete(f"/api/habits/{habit_id}")
    listed = alice.get("/api/habits").json
    assert [h["name"] for h in listed["habits"]] == ["Alice habit"]
    assert listed["checkins"] == {str(habit_id): [today]}
    assert alice.patch(f"/api/habits/{habit_id}", json={"name": {"bad": 1}}).status_code == 400

    state = {"tasks": [{"id": "t1", "title": "Alice task"}], "view": "week"}
    assert alice.put("/api/calendar-state", json={"profile_id": "pa", "state": state, "version": 0}).status_code == 200
    assert bob.get("/api/calendar-state?profile_id=pa").status_code == 403
    assert bob.put("/api/calendar-state", json={"profile_id": "pa", "state": {}, "version": 1}).status_code == 403
    assert bob.get("/api/calendar-state?profile_id=pb").json == {"state": None, "version": 0}
    assert alice.get("/api/calendar-state?profile_id=pa").json["state"] == state


def test_deleting_a_profile_removes_its_habits_and_calendar(db):
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    alice, bob = _habits_client(db, "pa"), _habits_client(db, "pb")
    alice.post("/api/habits", json={"name": "A"})
    bob.post("/api/habits", json={"name": "B"})
    alice.put("/api/calendar-state", json={"profile_id": "pa", "state": {"tasks": [{"id": "t"}]}, "version": 0})
    db.delete_profile_rows("pa")
    with Session(db._engine(db.database_url())) as session:
        assert session.scalars(select(db.Habit.owner)).all() == ["pb"]
        assert session.scalar(select(func.count()).select_from(db.CalendarState)) == 0
    assert alice.get("/api/calendar-state?profile_id=pa").json == {"state": None, "version": 0}


def test_combined_app_requires_login_and_hides_non_asset_calendar_files(db, monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    import app as root_app

    client = root_app.app.test_client()
    assert client.get("/calendar/", headers={"Accept": "text/html"}).status_code == 302  # login page
    assert client.get("/calendar/api/habits").status_code == 401
    with client.session_transaction() as session_data:
        session_data["auth_profile"] = session_data["profile_id"] = "pa"
    monkeypatch.setattr(root_app, "authed_profile", lambda: {"id": "pa", "name": "Alice"})
    assert client.get("/calendar/src/main.js").status_code == 200
    for blocked in ("habits.py", "_migrate.json", "app.py", "__pycache__/habits.cpython-314.pyc", "../app.py"):
        assert client.get(f"/calendar/{blocked}").status_code == 404, blocked


def test_receipt_images_are_backed_up_and_restored(db, tmp_path, monkeypatch):
    import cloudinary.uploader
    from sqlalchemy.orm import Session

    import backup_database
    from Finance import receipt_storage

    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key:secret@demo")
    with Session(db._engine(db.database_url())) as session:
        session.add(db.ReceiptAsset(profile_id="pa", filename="r1.png", storage_key="daily-planner/pa/r1", format="png"))
        session.commit()
    monkeypatch.setattr(receipt_storage, "load", lambda profile_id, filename: (b"\x89PNG-bytes", "image/png"))
    path, _ = backup_database.backup(str(tmp_path / "b.json.gz"), include_receipts=True)
    assert backup_database.read_backup(path)["receipt_files"] == {"pa/r1.png": "iVBORy1ieXRlcw=="}

    uploads = []
    monkeypatch.setattr(cloudinary.uploader, "upload", lambda data, **kw: uploads.append((data, kw)))
    monkeypatch.setenv("DATABASE_URL", URL.create(
        "sqlite", database=str(tmp_path / "restored.sqlite")).render_as_string(hide_password=False))
    db._engine.cache_clear()
    counts, restored = backup_database.restore(path, restore_receipts=True)
    assert counts["receipt_assets"] == 1 and restored == 1
    assert uploads[0][0] == b"\x89PNG-bytes"
    assert uploads[0][1]["public_id"] == "daily-planner/pa/r1" and uploads[0][1]["type"] == "private"

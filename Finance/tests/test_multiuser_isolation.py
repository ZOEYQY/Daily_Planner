"""Multi-user safety: one profile can never read or change another's data,
also when requests run concurrently, and concurrent sign-ups never lose an
account. Runs in every storage mode (JSON files, SQLite, PostgreSQL)."""
import os
import threading

import pytest


def _signup(app, name):
    client = app.test_client()
    password = f"{name.lower()}-password"
    resp = client.post("/profiles/create", data={"name": name, "password": password, "password2": password})
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return client


def _profile_id(helpers, name):
    return helpers.find_profile_by_name(helpers.load_profiles(), name)["id"]


def _records(helpers, data_dir, profile_id):
    return helpers.load_data(str(data_dir / "profiles" / profile_id / "expenses.json"), [])


def _add(client, item, account="Wallet"):
    resp = client.post("/add", data={"date": "2026-09-01", "type": "expense", "category": "Food",
                                     "account": account, "item": item, "amount": "10"})
    assert resp.status_code in (200, 302)


def test_user_cannot_read_update_or_delete_another_users_record(app, _patched_helpers, data_dir):
    alice, bob = _signup(app, "Alice"), _signup(app, "Bob")
    alice.post("/accounts", data={"name": "Wallet", "purpose": "spending"})
    bob.post("/accounts", data={"name": "Wallet", "purpose": "spending"})
    _add(alice, "alice-secret-sushi")
    alice_id, bob_id = _profile_id(_patched_helpers, "Alice"), _profile_id(_patched_helpers, "Bob")
    record_id = _records(_patched_helpers, data_dir, alice_id)[0]["id"]

    # Bob guesses Alice's record id in every URL that takes one.
    assert "alice-secret-sushi" not in bob.get(f"/update/{record_id}", follow_redirects=True).get_data(as_text=True)
    bob.post(f"/update/{record_id}", data={"date": "2026-09-01", "type": "expense", "category": "Food",
                                          "account": "Wallet", "item": "hacked", "amount": "999"})
    bob.post(f"/delete/{record_id}")

    alice_record = _records(_patched_helpers, data_dir, alice_id)[0]
    assert (alice_record["item"], alice_record["amount"], alice_record.get("deleted_at")) == \
        ("alice-secret-sushi", 10.0, None)
    assert _records(_patched_helpers, data_dir, bob_id) == []
    assert "alice-secret-sushi" not in bob.get("/view").get_data(as_text=True)
    assert "alice-secret-sushi" not in bob.get("/data/export.csv").get_data(as_text=True)
    assert "alice-secret-sushi" in alice.get("/view").get_data(as_text=True)


def test_backup_download_contains_only_own_data(app):
    alice, bob = _signup(app, "Alice"), _signup(app, "Bob")
    alice.post("/accounts", data={"name": "Alice Only Wallet", "purpose": "spending"})
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(bob.get("/data/backup.zip").data)) as archive:
        content = "".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    assert "Alice Only Wallet" not in content
    assert "profiles.json" not in archive.namelist()  # never the shared account index


def test_store_paths_resolve_per_request_not_per_process(app, _patched_helpers):
    """Two requests in flight at the same time each see only their own
    profile's files (the old design rewrote module globals per request)."""
    import finance_routes

    barrier = threading.Barrier(2)
    seen = {}

    def request_as(profile_id):
        with app.test_request_context("/"):
            _patched_helpers.apply_profile_paths(profile_id)
            barrier.wait()  # both requests are now active at once
            seen[profile_id] = (os.fspath(finance_routes.f_expense), os.fspath(_patched_helpers.f_debts),
                                os.fspath(finance_routes.RECEIPTS_DIR))

    threads = [threading.Thread(target=request_as, args=(pid,)) for pid in ("profile-a", "profile-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for profile_id, paths in seen.items():
        other = "profile-b" if profile_id == "profile-a" else "profile-a"
        assert all(profile_id in path and other not in path for path in paths)


def test_concurrent_requests_from_two_users_stay_isolated(app, _patched_helpers, data_dir):
    alice, bob = _signup(app, "Alice"), _signup(app, "Bob")
    alice.post("/accounts", data={"name": "Wallet", "purpose": "spending"})
    bob.post("/accounts", data={"name": "Wallet", "purpose": "spending"})
    errors = []

    def work(client, prefix):
        try:
            for index in range(8):
                _add(client, f"{prefix}-{index}")
                assert f"{'bob' if prefix == 'alice' else 'alice'}-" not in client.get("/view").get_data(as_text=True)
        except Exception as error:  # surfaced below
            errors.append(error)

    threads = [threading.Thread(target=work, args=(alice, "alice")), threading.Thread(target=work, args=(bob, "bob"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    for name, prefix in (("Alice", "alice"), ("Bob", "bob")):
        items = {r["item"] for r in _records(_patched_helpers, data_dir, _profile_id(_patched_helpers, name))}
        assert items == {f"{prefix}-{index}" for index in range(8)}


def test_concurrent_signups_never_lose_an_account(app, _patched_helpers):
    names = [f"User{index}" for index in range(8)]
    barrier = threading.Barrier(len(names))
    errors = []

    def signup(name):
        try:
            client = app.test_client()
            barrier.wait()
            password = f"{name}-password"
            resp = client.post("/profiles/create", data={"name": name, "password": password, "password2": password})
            assert resp.status_code == 302
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=signup, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    stored = {p["name"] for p in _patched_helpers.load_profiles()}
    assert set(names) <= stored


def test_duplicate_name_is_rejected_atomically(_patched_helpers, app):
    _signup(app, "Alice")
    with pytest.raises(_patched_helpers.ProfileNameTaken):
        _patched_helpers.create_profile("alice", "another-password")
    resp = app.test_client().post("/profiles/create", data={"name": "ALICE", "password": "pw123456", "password2": "pw123456"})
    assert resp.status_code == 400
    assert [p["name"].casefold() for p in _patched_helpers.load_profiles()].count("alice") == 1


def test_passwords_are_stored_only_as_hashes(app, _patched_helpers):
    _signup(app, "Alice")
    profile = _patched_helpers.find_profile_by_name(_patched_helpers.load_profiles(), "Alice")
    assert "password" not in profile
    assert profile["password_hash"].startswith(("scrypt:", "pbkdf2:"))
    assert "alice-password" not in repr(_patched_helpers.load_profiles())
    assert _patched_helpers.check_profile_password(profile, "alice-password")
    assert not _patched_helpers.check_profile_password(profile, "wrong")


def test_login_logout_and_session(app):
    _signup(app, "Alice")
    client = app.test_client()
    assert client.get("/view").status_code == 302  # gated
    assert client.post("/profiles/login", data={"name": "Alice", "password": "wrong-password"}).status_code == 401
    assert client.get("/view").status_code == 302
    assert client.post("/profiles/login", data={"name": "alice", "password": "alice-password"}).status_code == 302
    assert client.get("/view").status_code == 200
    client.post("/profiles/switch-off")
    assert client.get("/view").status_code == 302


def test_unclaimed_legacy_profile_can_only_be_claimed_once(app, _patched_helpers):
    _patched_helpers.save_profiles([{"id": "legacy", "name": "Old", "created_at": "2026-01-01"}])
    first, second = app.test_client(), app.test_client()
    assert first.post("/profiles/login", data={"name": "Old", "password": "first-password"}).status_code == 302
    assert second.post("/profiles/login", data={"name": "Old", "password": "second-password"}).status_code == 401
    profile = _patched_helpers.find_profile(_patched_helpers.load_profiles(), "legacy")
    assert _patched_helpers.check_profile_password(profile, "first-password")


def test_data_survives_engine_restart(app, _patched_helpers, data_dir):
    """Application restart -> new database connection -> data still there."""
    import database

    alice = _signup(app, "Alice")
    alice.post("/accounts", data={"name": "Wallet", "purpose": "spending"})
    _add(alice, "persisted-item")
    database._engine.cache_clear()  # drop every pooled connection, as a restart would

    fresh = app.test_client()
    assert fresh.post("/profiles/login", data={"name": "Alice", "password": "alice-password"}).status_code == 302
    assert "persisted-item" in fresh.get("/view").get_data(as_text=True)

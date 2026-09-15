"""Simple multi-user: name-only profiles, each with fully isolated Finance data."""
import json


def _profiles(patched_helpers):
    return patched_helpers.load_profiles()


def test_anonymous_session_is_gated_to_profiles_page(app):
    anon = app.test_client()  # never visited /profiles/create -> no session profile
    resp = anon.get("/plan")
    assert resp.status_code == 302
    assert "/profiles" in resp.headers["Location"]

    resp = anon.get("/profiles")
    assert resp.status_code == 200  # the picker itself is reachable


def test_first_visit_bootstraps_a_profile_from_legacy_flat_files(app, data_dir, _patched_helpers):
    # simulate the pre-profiles layout: flat files sitting directly in data/
    data_dir.mkdir(exist_ok=True)
    (data_dir / "expenses.json").write_text(json.dumps([
        {"date": "2026-01-01", "type": "income", "category": "Salary",
         "account": "Wallet", "item": "legacy pay", "amount": 500},
    ]), encoding="utf-8")

    anon = app.test_client()
    anon.get("/plan")  # triggers the bootstrap/migration via before_request

    profiles = _profiles(_patched_helpers)
    assert len(profiles) == 1
    pid = profiles[0]["id"]
    migrated = json.loads((data_dir / "profiles" / pid / "expenses.json").read_text())
    assert migrated[0]["item"] == "legacy pay"
    assert not (data_dir / "expenses.json").exists()  # moved, not copied


def test_create_profile_activates_it_immediately(client, load):
    # `client` fixture already created + activated "Test User"
    body = client.get("/plan").get_data(as_text=True)
    assert "Test User" in body  # shown via the current_profile nav badge


def test_two_profiles_have_fully_isolated_data(app):
    alice = app.test_client()
    bob = app.test_client()
    alice.post("/profiles/create", data={"name": "Alice"})
    bob.post("/profiles/create", data={"name": "Bob"})

    alice.post("/accounts", data={"name": "Alice Wallet", "purpose": "spending"})
    alice.post("/add", data={"date": "2026-09-01", "type": "expense", "category": "Food",
                             "account": "Alice Wallet", "item": "sushi", "amount": "20"})

    bob.post("/accounts", data={"name": "Bob Wallet", "purpose": "spending"})

    alice_view = alice.get("/view").get_data(as_text=True)
    bob_view = bob.get("/view").get_data(as_text=True)
    assert "sushi" in alice_view
    assert "sushi" not in bob_view
    assert "Alice Wallet" not in bob.get("/accounts").get_data(as_text=True)


def test_profiles_page_lists_both_and_marks_current(app):
    alice = app.test_client()
    alice.post("/profiles/create", data={"name": "Alice"})
    alice.post("/profiles/create", data={"name": "Carla"})  # switches to Carla

    page = alice.get("/profiles").get_data(as_text=True)
    assert "Alice" in page and "Carla" in page
    assert "Currently active" in page


def test_switch_profile(app, _patched_helpers):
    c = app.test_client()
    c.post("/profiles/create", data={"name": "Alice"})
    c.post("/accounts", data={"name": "A1", "purpose": "spending"})

    c.post("/profiles/create", data={"name": "Bob"})  # now active: Bob
    assert "A1" not in c.get("/accounts").get_data(as_text=True)

    alice_id = next(p["id"] for p in _profiles(_patched_helpers) if p["name"] == "Alice")

    resp = c.post("/profiles/switch", data={"id": alice_id})
    assert resp.status_code == 302
    assert "A1" in c.get("/accounts").get_data(as_text=True)


def test_switch_to_unknown_profile_rejected(client):
    resp = client.post("/profiles/switch", data={"id": "nope"})
    assert resp.status_code == 200
    assert "no longer exists" in resp.get_data(as_text=True)


def test_switch_off_clears_session(app):
    c = app.test_client()
    c.post("/profiles/create", data={"name": "Alice"})
    resp = c.post("/profiles/switch-off")
    assert resp.status_code == 302
    assert "/profiles" in resp.headers["Location"]
    assert c.get("/plan").status_code == 302  # gated again


def test_rename_profile(app, _patched_helpers):
    c = app.test_client()
    c.post("/profiles/create", data={"name": "Alice"})
    pid = _profiles(_patched_helpers)[0]["id"]

    c.post(f"/profiles/{pid}/rename", data={"name": "Alicia"})
    assert _profiles(_patched_helpers)[0]["name"] == "Alicia"
    assert "Alicia" in c.get("/profiles").get_data(as_text=True)


def test_delete_profile_requires_exact_name_confirmation(app, _patched_helpers):
    c = app.test_client()
    c.post("/profiles/create", data={"name": "Alice"})
    pid = _profiles(_patched_helpers)[0]["id"]

    resp = c.post(f"/profiles/{pid}/delete", data={"confirm_name": "wrong"})
    assert resp.status_code == 200
    assert "Type" in resp.get_data(as_text=True)
    assert len(_profiles(_patched_helpers)) == 1  # not deleted

    resp = c.post(f"/profiles/{pid}/delete", data={"confirm_name": "alice"})  # case-insensitive
    assert resp.status_code == 302
    assert _profiles(_patched_helpers) == []
    assert c.get("/plan").status_code == 302  # session profile gone -> gated again


def test_delete_profile_removes_its_data_directory(app, _patched_helpers, data_dir):
    c = app.test_client()
    c.post("/profiles/create", data={"name": "Alice"})
    pid = _profiles(_patched_helpers)[0]["id"]
    c.post("/accounts", data={"name": "A1", "purpose": "spending"})
    assert (data_dir / "profiles" / pid).exists()

    c.post(f"/profiles/{pid}/delete", data={"confirm_name": "Alice"})
    assert not (data_dir / "profiles" / pid).exists()


def test_create_profile_requires_a_name(client):
    resp = client.post("/profiles/create", data={"name": "   "})
    assert resp.status_code == 200
    assert "Enter a name" in resp.get_data(as_text=True)


def test_backup_only_covers_the_active_profile(app):
    import io
    import zipfile

    alice = app.test_client()
    alice.post("/profiles/create", data={"name": "Alice"})
    alice.post("/accounts", data={"name": "A1", "purpose": "spending"})

    bob = app.test_client()
    bob.post("/profiles/create", data={"name": "Bob"})

    alice_zip = zipfile.ZipFile(io.BytesIO(alice.get("/data/backup.zip").get_data()))
    accounts = json.loads(alice_zip.read("accounts.json"))
    assert accounts and accounts[0]["name"] == "A1"

    bob_zip = zipfile.ZipFile(io.BytesIO(bob.get("/data/backup.zip").get_data()))
    assert "accounts.json" not in bob_zip.namelist()  # Bob never created any -> nothing to back up

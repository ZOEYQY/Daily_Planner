"""Password-protected Profiles with isolated Finance data."""
import json

from conftest import USE_DATABASE


def _profiles(patched_helpers):
    return patched_helpers.load_profiles()


def _create_profile(client, name, password=None):
    password = password or f"{name.lower()}-test-password"
    return client.post("/profiles/create", data={
        "name": name, "password": password, "password2": password,
    })


def _login(client, name, password=None):
    password = password or f"{name.lower()}-test-password"
    return client.post("/profiles/login", data={"name": name, "password": password})


def test_anonymous_session_is_gated_to_profiles_page(app):
    anon = app.test_client()  # never visited /profiles/create -> no session profile
    resp = anon.get("/plan")
    assert resp.status_code == 302
    assert "/profiles" in resp.headers["Location"]

    resp = anon.get("/profiles")
    assert resp.status_code == 200  # the picker itself is reachable


def test_anonymous_visit_does_not_move_legacy_data(app, data_dir, _patched_helpers):
    # Legacy files remain untouched until an explicit migration/import.
    data_dir.mkdir(exist_ok=True)
    (data_dir / "expenses.json").write_text(json.dumps([
        {"date": "2026-01-01", "type": "income", "category": "Salary",
         "account": "Wallet", "item": "legacy pay", "amount": 500},
    ]), encoding="utf-8")

    anon = app.test_client()
    assert anon.get("/plan").status_code == 302
    assert _profiles(_patched_helpers) == []
    assert json.loads((data_dir / "expenses.json").read_text())[0]["item"] == "legacy pay"


def test_create_profile_activates_it_immediately(client, load):
    # `client` fixture already created + activated "Test User"
    body = client.get("/plan").get_data(as_text=True)
    assert "Test User" in body  # shown via the current_profile nav badge


def test_two_profiles_have_fully_isolated_data(app):
    alice = app.test_client()
    bob = app.test_client()
    _create_profile(alice, "Alice")
    _create_profile(bob, "Bob")

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
    _create_profile(alice, "Alice")
    _create_profile(alice, "Carla")

    page = alice.get("/profiles").get_data(as_text=True)
    assert "Carla" in page
    assert "Change password" in page
    assert "Alice" not in page  # other Profile names are intentionally private


def test_switch_profile(app, _patched_helpers):
    c = app.test_client()
    _create_profile(c, "Alice")
    c.post("/accounts", data={"name": "A1", "purpose": "spending"})

    _create_profile(c, "Bob")
    assert "A1" not in c.get("/accounts").get_data(as_text=True)

    resp = _login(c, "Alice")
    assert resp.status_code == 302
    assert "A1" in c.get("/accounts").get_data(as_text=True)


def test_switch_to_unknown_profile_rejected(client):
    resp = _login(client, "Unknown")
    assert resp.status_code == 401
    assert "Wrong profile name or password" in resp.get_data(as_text=True)


def test_switch_off_clears_session(app):
    c = app.test_client()
    _create_profile(c, "Alice")
    resp = c.post("/profiles/switch-off")
    assert resp.status_code == 302
    assert "/profiles" in resp.headers["Location"]
    assert c.get("/plan").status_code == 302  # gated again


def test_rename_profile(app, _patched_helpers):
    c = app.test_client()
    _create_profile(c, "Alice")

    c.post("/profiles/rename", data={"name": "Alicia"})
    assert _profiles(_patched_helpers)[0]["name"] == "Alicia"
    assert "Alicia" in c.get("/profiles").get_data(as_text=True)


def test_delete_profile_requires_exact_name_confirmation(app, _patched_helpers):
    c = app.test_client()
    _create_profile(c, "Alice")
    pid = _profiles(_patched_helpers)[0]["id"]

    resp = c.post("/profiles/delete", data={"password": "wrong-password"})
    assert resp.status_code == 401
    assert "Wrong password" in resp.get_data(as_text=True)
    assert len(_profiles(_patched_helpers)) == 1  # not deleted

    resp = c.post("/profiles/delete", data={"password": "alice-test-password"})
    assert resp.status_code == 302
    assert _profiles(_patched_helpers) == []
    assert c.get("/plan").status_code == 302  # session profile gone -> gated again


def test_delete_profile_removes_its_data_directory(app, _patched_helpers, data_dir):
    c = app.test_client()
    _create_profile(c, "Alice")
    pid = _profiles(_patched_helpers)[0]["id"]
    c.post("/accounts", data={"name": "A1", "purpose": "spending"})
    accounts_path = str(data_dir / "profiles" / pid / "accounts.json")
    assert _patched_helpers.load_data(accounts_path, None)
    if not USE_DATABASE:  # JSON mode keeps each profile in its own folder
        assert (data_dir / "profiles" / pid).exists()

    c.post("/profiles/delete", data={"password": "alice-test-password"})
    assert _patched_helpers.load_data(accounts_path, None) is None
    assert not (data_dir / "profiles" / pid).exists()


def test_create_profile_requires_a_name(client):
    resp = client.post("/profiles/create", data={
        "name": "   ", "password": "test-password", "password2": "test-password",
    })
    assert resp.status_code == 400
    assert "Enter a name" in resp.get_data(as_text=True)


def test_backup_only_covers_the_active_profile(app):
    import io
    import zipfile

    alice = app.test_client()
    _create_profile(alice, "Alice")
    alice.post("/accounts", data={"name": "A1", "purpose": "spending"})

    bob = app.test_client()
    _create_profile(bob, "Bob")

    alice_zip = zipfile.ZipFile(io.BytesIO(alice.get("/data/backup.zip").get_data()))
    accounts = json.loads(alice_zip.read("accounts.json"))
    assert accounts and accounts[0]["name"] == "A1"

    bob_zip = zipfile.ZipFile(io.BytesIO(bob.get("/data/backup.zip").get_data()))
    assert "accounts.json" not in bob_zip.namelist()  # Bob never created any -> nothing to back up

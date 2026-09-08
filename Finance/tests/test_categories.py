"""User-defined category CRUD + integration with records/budgets."""
from conftest import category_id, categories_list


def _add_expense(client, category, account="Wallet", amount="12.50", date="2026-09-08"):
    return client.post("/add", data={
        "date": date,
        "type": "expense",
        "category": category,
        "new_account": account,
        "purpose": "spending",
        "item": "Test",
        "amount": amount,
    })


def test_categories_seeded_on_first_visit(client, load):
    resp = client.get("/categories")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Food" in body and "Salary" in body

    stored = load("categories.json")
    assert stored["schema_version"] == 1
    names = {c["name"] for c in stored["categories"]}
    assert {"Food", "Transport", "Salary", "Savings"} <= names
    for c in stored["categories"]:
        assert c["id"] and c["kind"] and "order" in c


def test_create_category_then_it_appears_on_add_form(client):
    resp = client.post("/categories", data={
        "action": "create", "name": "Gaming", "kind": "expense", "color": "#112233",
    })
    assert resp.status_code == 302

    add_page = client.get("/add").get_data(as_text=True)
    assert '"Gaming"' in add_page  # embedded in the categories-data JSON


def test_duplicate_category_rejected(client):
    client.post("/categories", data={"action": "create", "name": "Gaming", "kind": "expense"})
    resp = client.post("/categories", data={"action": "create", "name": "gaming", "kind": "expense"})
    assert resp.status_code == 200
    assert "already exists" in resp.get_data(as_text=True)


def test_reserved_name_rejected(client):
    resp = client.post("/categories", data={
        "action": "create", "name": "Transfer In", "kind": "income",
    })
    assert "reserved" in resp.get_data(as_text=True)


def test_rename_category_rewrites_existing_records(client, load):
    _add_expense(client, "Food")
    cid = category_id(load, "Food", "expense")

    resp = client.post("/categories", data={"action": "rename", "id": cid, "name": "Groceries"})
    assert resp.status_code == 302

    records = load("expenses.json")
    assert records[0]["category"] == "Groceries"
    assert "Groceries" in client.get("/view").get_data(as_text=True)


def test_category_in_use_cannot_be_deleted_only_archived(client, load):
    _add_expense(client, "Food")
    cid = category_id(load, "Food", "expense")

    resp = client.post("/categories", data={"action": "delete", "id": cid})
    assert resp.status_code == 200
    assert "archive it instead" in resp.get_data(as_text=True)
    assert any(c["name"] == "Food" for c in categories_list(load))

    # archiving is allowed and hides it from the Add form
    client.post("/categories", data={"action": "archive", "id": cid})
    add_page = client.get("/add").get_data(as_text=True)
    assert '"Food"' not in add_page
    # ...but the historical record still reads "Food"
    assert load("expenses.json")[0]["category"] == "Food"


def test_unused_category_can_be_deleted(client, load):
    client.post("/categories", data={"action": "create", "name": "Temp", "kind": "expense"})
    cid = category_id(load, "Temp", "expense")
    resp = client.post("/categories", data={"action": "delete", "id": cid})
    assert resp.status_code == 302
    assert not any(c["name"] == "Temp" for c in categories_list(load))


def test_default_category_prefills_add_form(client, load):
    client.get("/categories")  # seed the store
    cid = category_id(load, "Food", "expense")
    client.post("/categories", data={"action": "set_default", "id": cid})

    add_page = client.get("/add").get_data(as_text=True)
    assert '"expense": "Food"' in add_page

    stored = load("categories.json")
    defaults = [c for c in stored["categories"] if c["is_default"]]
    assert len(defaults) == 1 and defaults[0]["name"] == "Food"


def test_archived_category_still_editable_on_a_record(client, load):
    _add_expense(client, "Food")
    cid = category_id(load, "Food", "expense")
    client.post("/categories", data={"action": "archive", "id": cid})

    rid = load("expenses.json")[0]["id"]
    edit_page = client.get(f"/update/{rid}").get_data(as_text=True)
    # archived category re-injected so the edit dropdown can still show it
    assert '"Food"' in edit_page

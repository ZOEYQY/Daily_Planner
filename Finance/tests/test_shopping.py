"""Shopping list / purchase-decision flow."""
from conftest import shopping_items


def _create_item(client, **overrides):
    data = {
        "action": "create",
        "name": "Mechanical Keyboard",
        "estimated_price": "350",
        "category": "Food",
        "priority": "high",
        "reasons_for": "My current one is broken.",
        "reasons_against": "It is expensive.",
    }
    data.update(overrides)
    return client.post("/shopping", data=data)


def test_shopping_page_loads_empty(client):
    resp = client.get("/shopping")
    assert resp.status_code == 200
    assert "No shopping items yet" in resp.get_data(as_text=True)


def test_create_item(client, load):
    resp = _create_item(client)
    assert resp.status_code == 302

    items = shopping_items(load)
    assert len(items) == 1
    it = items[0]
    assert it["name"] == "Mechanical Keyboard"
    assert it["estimated_price"] == 350.0
    assert it["priority"] == "high"
    assert it["status"] == "open" and it["decision"] == "undecided"
    assert it["reasons_for"] and it["reasons_against"]
    assert it["id"] and it["date_added"]


def test_create_requires_name(client):
    resp = client.post("/shopping", data={"action": "create", "name": "   "})
    assert resp.status_code == 200
    assert "Item name is required" in resp.get_data(as_text=True)


def test_create_rejects_bad_price(client):
    resp = _create_item(client, estimated_price="not-a-number")
    assert resp.status_code == 200
    assert "must be a number" in resp.get_data(as_text=True)


def test_create_rejects_unknown_category(client):
    resp = _create_item(client, category="Nonsense")
    assert resp.status_code == 200
    assert "not one of your expense categories" in resp.get_data(as_text=True)


def test_decide_dont_buy_dismisses_item(client, load):
    _create_item(client)
    item_id = shopping_items(load)[0]["id"]

    resp = client.post("/shopping", data={
        "action": "decide", "id": item_id, "decision": "dont_buy",
    })
    assert resp.status_code == 302
    it = shopping_items(load)[0]
    assert it["decision"] == "dont_buy" and it["status"] == "dismissed"


def test_buy_then_record_purchase_creates_expense(client, load, make_account):
    make_account("Wallet")
    _create_item(client)
    item_id = shopping_items(load)[0]["id"]

    client.post("/shopping", data={"action": "decide", "id": item_id, "decision": "buy"})
    resp = client.post("/shopping", data={
        "action": "purchase", "id": item_id, "account": "Wallet",
        "date": "2026-09-08", "amount": "350",
    })
    assert resp.status_code == 302

    records = load("expenses.json")
    assert len(records) == 1
    exp = records[0]
    assert exp["type"] == "expense" and exp["amount"] == 350.0
    assert exp["category"] == "Food" and exp["account"] == "Wallet"
    assert exp["item"] == "Mechanical Keyboard"
    assert exp["source"] == "shopping_list"

    it = shopping_items(load)[0]
    assert it["status"] == "bought" and it["expense_created"] is True
    assert it["actual_price"] == 350.0

    assert "Mechanical Keyboard" in client.get("/view").get_data(as_text=True)


def test_purchase_requires_valid_account(client, load):
    _create_item(client)
    item_id = shopping_items(load)[0]["id"]
    client.post("/shopping", data={"action": "decide", "id": item_id, "decision": "buy"})

    resp = client.post("/shopping", data={
        "action": "purchase", "id": item_id, "account": "", "amount": "350",
    })
    assert resp.status_code == 200
    assert "Choose an account" in resp.get_data(as_text=True)
    assert load("expenses.json") is None


def test_update_item(client, load):
    _create_item(client)
    item_id = shopping_items(load)[0]["id"]

    resp = client.post("/shopping", data={
        "action": "update", "id": item_id, "name": "Keyboard v2",
        "estimated_price": "400", "priority": "low", "category": "",
    })
    assert resp.status_code == 302
    it = shopping_items(load)[0]
    assert it["name"] == "Keyboard v2" and it["estimated_price"] == 400.0
    assert it["priority"] == "low" and it["category"] == ""


def test_delete_item(client, load):
    _create_item(client)
    item_id = shopping_items(load)[0]["id"]
    resp = client.post("/shopping", data={"action": "delete", "id": item_id})
    assert resp.status_code == 302
    assert shopping_items(load) == []

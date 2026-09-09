"""Existing Finance flows must keep working after the category refactor."""


def test_add_expense_still_works(client, load):
    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "new_account": "Wallet", "purpose": "spending",
        "item": "Lunch", "amount": "12.50",
    })
    assert resp.status_code == 302
    assert "/add" in resp.headers["Location"]

    records = load("expenses.json")
    assert records[0]["category"] == "Food" and records[0]["amount"] == 12.5

    view = client.get("/view").get_data(as_text=True)
    assert "Lunch" in view and "Wallet" in view


def test_add_rejects_category_that_is_not_the_users(client):
    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": "TotallyMadeUp",
        "new_account": "Wallet", "item": "x", "amount": "5",
    })
    assert resp.status_code == 200
    assert "not one of your expense categories" in resp.get_data(as_text=True)


def test_transfer_flow_still_works(client, load, make_account):
    make_account("Wallet")
    make_account("Bank", "savings")

    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "transfer", "account": "Wallet",
        "to_account": "Bank", "amount": "100",
    })
    assert resp.status_code == 302

    cats = {r["category"] for r in load("expenses.json")}
    assert {"Transfer In", "Transfer Out"} <= cats


def test_budget_uses_dynamic_categories(client, load):
    client.post("/categories", data={"action": "create", "name": "Gaming", "kind": "expense"})

    budget_page = client.get("/plan").get_data(as_text=True)
    assert "Gaming" in budget_page

    resp = client.post("/budget", data={
        "category": "Gaming", "amount": "100", "period": "monthly",
    })
    assert resp.status_code == 302
    assert load("budget.json")[0]["category"] == "Gaming"


def test_core_pages_load(client):
    for path in ["/finance", "/view", "/summary", "/plan", "/accounts"]:
        assert client.get(path).status_code == 200


def test_receipt_endpoint_without_key_is_graceful(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    resp = client.post("/analyze-receipt", data={})
    # no image -> 400, never a 500 from the category-schema refactor
    assert resp.status_code == 400

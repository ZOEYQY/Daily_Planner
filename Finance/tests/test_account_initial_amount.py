"""Accounts: opening/initial balance on create and edit."""


def _accounts(load):
    return load("accounts.json") or []


def test_create_account_with_initial_amount_sets_balance(client, load):
    client.post("/accounts", data={"name": "Bank", "purpose": "spending", "initial_amount": "500"})
    stored = _accounts(load)
    assert stored[0]["initial_amount"] == 500.0

    page = client.get("/accounts").get_data(as_text=True)
    assert "RM 500.00" in page


def test_create_account_without_initial_amount_defaults_to_zero(client, load):
    client.post("/accounts", data={"name": "Wallet", "purpose": "spending"})
    stored = _accounts(load)
    assert stored[0]["initial_amount"] == 0.0


def test_balance_combines_initial_amount_and_transactions(client, load):
    client.post("/accounts", data={"name": "Bank", "purpose": "spending", "initial_amount": "1000"})
    client.post("/add", data={"date": "2026-09-01", "type": "income", "category": "Salary",
                              "account": "Bank", "item": "pay", "amount": "500"})
    client.post("/add", data={"date": "2026-09-02", "type": "expense", "category": "Food",
                              "account": "Bank", "item": "lunch", "amount": "50"})

    page = client.get("/accounts").get_data(as_text=True)
    # 1000 opening + 500 income - 50 expense = 1450
    assert "RM 1450.00" in page


def test_negative_initial_amount_rejected(client, load):
    client.post("/accounts", data={"name": "Bad", "purpose": "spending", "initial_amount": "-100"})
    assert _accounts(load) == []


def test_edit_account_updates_initial_amount(client, load):
    client.post("/accounts", data={"name": "Bank", "purpose": "spending", "initial_amount": "200"})
    client.post("/edit_account/Bank", data={"name": "Bank", "purpose": "spending", "initial_amount": "800"})

    stored = _accounts(load)
    assert stored[0]["initial_amount"] == 800.0
    page = client.get("/accounts").get_data(as_text=True)
    assert "RM 800.00" in page


def test_edit_account_rejects_negative_initial_amount(client, load):
    client.post("/accounts", data={"name": "Bank", "purpose": "spending", "initial_amount": "200"})
    resp = client.post("/edit_account/Bank", data={"name": "Bank", "purpose": "spending", "initial_amount": "-5"})
    assert b"cannot be negative" in resp.get_data()

    stored = _accounts(load)
    assert stored[0]["initial_amount"] == 200.0  # unchanged


def test_networth_includes_initial_amount_as_asset(client, load):
    client.post("/accounts", data={"name": "Bank", "purpose": "spending", "initial_amount": "1000"})
    page = client.get("/networth").get_data(as_text=True)
    assert "RM 1000.00" in page

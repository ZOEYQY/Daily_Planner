"""Full Finance-module audit (2026-09-11): transfer integrity, shared money
parsing, account-existence checks, shopping double-purchase guard, balance
consistency across pages."""
import math


def _records(load):
    return [r for r in (load("expenses.json") or []) if not r.get("deleted_at")]


def _all_records(load):
    return load("expenses.json") or []


# ================= TRANSFER: SAME-ACCOUNT REJECTION =================

def test_transfer_rejects_nonexistent_destination_account(client, load, make_account):
    """The source account got an existence check; the destination must too —
    otherwise a forged to_account creates a Transfer Out that leaves the
    source account with nothing real receiving the money on the other end."""
    make_account("Wallet")
    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "transfer", "account": "Wallet",
        "to_account": "PhantomBank", "amount": "10",
    })
    assert resp.status_code == 200
    assert "existing destination account" in resp.get_data(as_text=True).lower()
    assert _records(load) == []


def test_same_account_transfer_rejected(client, load, make_account):
    make_account("Wallet")
    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "transfer", "account": "Wallet",
        "to_account": "Wallet", "amount": "10",
    })
    assert resp.status_code == 200
    assert "must be different" in resp.get_data(as_text=True)
    assert _records(load) == []


def test_different_account_transfer_still_works_and_is_linked(client, load, make_account):
    make_account("Wallet")
    make_account("Bank", "savings")
    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "transfer", "account": "Wallet",
        "to_account": "Bank", "amount": "100",
    })
    assert resp.status_code == 302

    recs = _records(load)
    assert len(recs) == 2
    out_leg = next(r for r in recs if r["category"] == "Transfer Out")
    in_leg = next(r for r in recs if r["category"] == "Transfer In")
    assert out_leg["amount"] == in_leg["amount"] == 100.0
    assert out_leg["transfer_id"] == in_leg["transfer_id"]
    assert out_leg["transfer_id"]  # non-empty


# ================= TRANSFER: EDIT/DELETE INTEGRITY =================

def test_editing_a_transfer_leg_is_blocked(client, load, make_account):
    make_account("Wallet")
    make_account("Bank", "savings")
    client.post("/add", data={
        "date": "2026-09-08", "type": "transfer", "account": "Wallet",
        "to_account": "Bank", "amount": "100",
    })
    out_leg = next(r for r in _records(load) if r["category"] == "Transfer Out")

    resp = client.post(f"/update/{out_leg['id']}", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "account": "Wallet", "item": "hacked", "amount": "999",
    })
    assert resp.status_code == 200
    assert "one side of a transfer" in resp.get_data(as_text=True)

    # unchanged on disk
    reloaded = next(r for r in _records(load) if r["id"] == out_leg["id"])
    assert reloaded["amount"] == 100.0
    assert reloaded["category"] == "Transfer Out"


def test_deleting_one_transfer_leg_deletes_both(client, load, make_account):
    make_account("Wallet")
    make_account("Bank", "savings")
    client.post("/add", data={
        "date": "2026-09-08", "type": "transfer", "account": "Wallet",
        "to_account": "Bank", "amount": "50",
    })
    out_leg = next(r for r in _records(load) if r["category"] == "Transfer Out")

    client.post(f"/delete/{out_leg['id']}")

    assert _records(load) == []  # both legs hidden from active view
    all_recs = _all_records(load)
    assert len(all_recs) == 2
    assert all(r.get("deleted_at") for r in all_recs)


def test_restoring_one_transfer_leg_restores_both(client, load, make_account):
    make_account("Wallet")
    make_account("Bank", "savings")
    client.post("/add", data={
        "date": "2026-09-08", "type": "transfer", "account": "Wallet",
        "to_account": "Bank", "amount": "50",
    })
    out_leg = next(r for r in _records(load) if r["category"] == "Transfer Out")
    client.post(f"/delete/{out_leg['id']}")

    client.post(f"/trash/{out_leg['id']}/restore")

    recs = _records(load)
    assert len(recs) == 2
    assert not any(r.get("deleted_at") for r in recs)


def test_purging_one_transfer_leg_purges_both(client, load, make_account):
    make_account("Wallet")
    make_account("Bank", "savings")
    client.post("/add", data={
        "date": "2026-09-08", "type": "transfer", "account": "Wallet",
        "to_account": "Bank", "amount": "50",
    })
    out_leg = next(r for r in _records(load) if r["category"] == "Transfer Out")
    client.post(f"/delete/{out_leg['id']}")

    client.post(f"/trash/{out_leg['id']}/purge")

    assert _all_records(load) == []


# ================= SHARED MONEY PARSING (_parse_money) =================

def test_amount_rejects_nan(client, load, make_account):
    make_account("Wallet")
    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "account": "Wallet", "item": "x", "amount": "nan",
    })
    assert resp.status_code == 200
    assert "not a valid number" in resp.get_data(as_text=True)
    assert _records(load) == []


def test_amount_rejects_infinity(client, load, make_account):
    make_account("Wallet")
    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "account": "Wallet", "item": "x", "amount": "inf",
    })
    assert resp.status_code == 200
    assert "too large" in resp.get_data(as_text=True)
    assert _records(load) == []


def test_amount_rejects_absurdly_large_value(client, load, make_account):
    make_account("Wallet")
    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "account": "Wallet", "item": "x", "amount": "99999999999999",
    })
    assert resp.status_code == 200
    assert "exceeds the maximum" in resp.get_data(as_text=True)
    assert _records(load) == []


def test_amount_accepts_scientific_notation(client, load, make_account):
    make_account("Wallet")
    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "account": "Wallet", "item": "x", "amount": "1e2",
    })
    assert resp.status_code == 302
    assert _records(load)[0]["amount"] == 100.0


def test_amount_rejects_currency_symbols_and_commas(client, load, make_account):
    make_account("Wallet")
    for bad in ("RM12.50", "1,200", "$5"):
        resp = client.post("/add", data={
            "date": "2026-09-08", "type": "expense", "category": "Food",
            "account": "Wallet", "item": "x", "amount": bad,
        })
        assert resp.status_code == 200
        assert "must be a number" in resp.get_data(as_text=True)
    assert _records(load) == []


def test_smallest_valid_amount_accepted(client, load, make_account):
    make_account("Wallet")
    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "account": "Wallet", "item": "x", "amount": "0.01",
    })
    assert resp.status_code == 302
    assert _records(load)[0]["amount"] == 0.01


# ================= ACCOUNT-EXISTENCE VALIDATION =================

def test_add_rejects_nonexistent_account(client, load, make_account):
    make_account("Wallet")
    resp = client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "account": "PhantomBank", "item": "x", "amount": "10",
    })
    assert resp.status_code == 200
    assert "existing account" in resp.get_data(as_text=True)
    assert _records(load) == []


def test_update_rejects_nonexistent_account(client, load, make_account):
    make_account("Wallet")
    client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "account": "Wallet", "item": "x", "amount": "10",
    })
    rid = _records(load)[0]["id"]

    resp = client.post(f"/update/{rid}", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "account": "PhantomBank", "item": "x", "amount": "10",
    })
    assert resp.status_code == 200
    assert "existing account" in resp.get_data(as_text=True)
    assert _records(load)[0]["account"] == "Wallet"  # unchanged


# ================= SHOPPING: DOUBLE-PURCHASE GUARD =================

def test_buying_shopping_item_creates_exactly_one_expense(client, load, make_account):
    make_account("Wallet")
    client.post("/shopping", data={
        "action": "create", "name": "Headphones", "estimated_price": "350", "category": "Food",
    })
    item_id = load("shopping.json")["items"][0]["id"]

    resp = client.post("/shopping", data={"action": "purchase", "id": item_id, "account": "Wallet"})
    assert resp.status_code == 302
    assert len(_records(load)) == 1
    assert _records(load)[0]["amount"] == 350.0


def test_buying_shopping_item_twice_does_not_duplicate_expense(client, load, make_account):
    make_account("Wallet")
    client.post("/shopping", data={
        "action": "create", "name": "Headphones", "estimated_price": "350", "category": "Food",
    })
    item_id = load("shopping.json")["items"][0]["id"]

    client.post("/shopping", data={"action": "purchase", "id": item_id, "account": "Wallet"})
    resp = client.post("/shopping", data={"action": "purchase", "id": item_id, "account": "Wallet"})

    assert "already been bought" in resp.get_data(as_text=True)
    assert len(_records(load)) == 1  # still exactly one expense


def test_deleting_shopping_item_keeps_the_expense(client, load, make_account):
    make_account("Wallet")
    client.post("/shopping", data={
        "action": "create", "name": "Headphones", "estimated_price": "350", "category": "Food",
    })
    item_id = load("shopping.json")["items"][0]["id"]
    client.post("/shopping", data={"action": "purchase", "id": item_id, "account": "Wallet"})

    client.post("/shopping", data={"action": "delete", "id": item_id})

    assert load("shopping.json")["items"] == []
    assert len(_records(load)) == 1  # the expense survives


def test_reopen_then_buy_again_is_allowed(client, load, make_account):
    """Reopening is the explicit path to a legitimate second purchase."""
    make_account("Wallet")
    client.post("/shopping", data={
        "action": "create", "name": "Headphones", "estimated_price": "350", "category": "Food",
    })
    item_id = load("shopping.json")["items"][0]["id"]
    client.post("/shopping", data={"action": "purchase", "id": item_id, "account": "Wallet"})

    client.post("/shopping", data={"action": "reopen", "id": item_id})
    resp = client.post("/shopping", data={"action": "purchase", "id": item_id, "account": "Wallet"})

    assert resp.status_code == 302
    assert len(_records(load)) == 2


# ================= ACCOUNT BALANCE: OPENING AMOUNT CONSISTENCY =================

def test_dashboard_and_accounts_page_agree_on_balance_with_opening_amount(client, make_account):
    make_account("Wallet", initial_amount=500)
    client.post("/add", data={
        "date": "2026-09-08", "type": "income", "category": "Salary",
        "account": "Wallet", "item": "pay", "amount": "200",
    })
    client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "account": "Wallet", "item": "lunch", "amount": "50",
    })
    # 500 opening + 200 income - 50 expense = 650
    accounts_page = client.get("/accounts").get_data(as_text=True)
    assert "RM 650.00" in accounts_page


def test_dashboard_spending_balance_treats_saving_type_as_positive(client, make_account):
    """Regression: the dashboard used to subtract "saving"-type rows on a
    spending account instead of adding them, unlike every other balance
    calculation in the app."""
    make_account("Wallet", initial_amount=0)
    client.post("/add", data={
        "date": "2026-09-08", "type": "saving", "category": "Savings",
        "account": "Wallet", "item": "stash", "amount": "100",
    })
    dashboard = client.get("/finance").get_data(as_text=True)
    # If "saving" were wrongly subtracted, balance would show -100.00.
    assert "-RM 100.00" not in dashboard and "-100.00" not in dashboard


def test_afford_check_context_includes_opening_amount(client, make_account, monkeypatch):
    import finance_routes
    monkeypatch.setattr(finance_routes, "_ai_structured",
                        lambda *a, **k: {"verdict": "yes", "reasoning": "fine"})
    make_account("Save", "savings", initial_amount=1000)
    resp = client.post("/afford", data={"amount": "50"})
    body = resp.get_json()
    assert body["context"]["savings_balance"] == 1000.0


# ================= GOAL STATUS: EXPLICIT CANCEL RESPECTED =================

def test_cancelled_goal_stays_cancelled_even_when_fully_funded(client, load, make_account):
    make_account("Wallet")
    client.post("/goals", data={
        "action": "create", "name": "Trip", "target": "100", "type": "short",
    })
    goal_id = load("goals.json")[0]["id"]

    client.post("/goals", data={
        "action": "save", "goal_id": str(goal_id), "amount": "100",
        "account": "Wallet", "goal_name": "Trip",
    })
    client.post(f"/cancel_goal/{goal_id}")

    page = client.get("/plan?tab=goals").get_data(as_text=True)
    # The goal is 100% funded but was explicitly cancelled — must not
    # silently flip back to "Completed".
    assert "Cancelled" in page

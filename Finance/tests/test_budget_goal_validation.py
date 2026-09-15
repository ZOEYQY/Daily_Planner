"""Budget + Goal amount/reference validation — closing the gap where these
routes used to accept bare float(...) with no negative/NaN/Infinity checks
and no existence checks on the account/category/goal being referenced."""


def _account(client, name="Wallet"):
    client.post("/accounts", data={"name": name, "purpose": "spending"})


# ================= BUDGET =================

def test_budget_rejects_negative_amount(client, load):
    resp = client.post("/budget", data={"category": "Food", "amount": "-50"})
    assert resp.status_code == 200
    assert "negative" in resp.get_data(as_text=True).lower()
    assert load("budget.json") in (None, [])


def test_budget_rejects_zero_amount(client):
    resp = client.post("/budget", data={"category": "Food", "amount": "0"})
    assert "greater than 0" in resp.get_data(as_text=True)


def test_budget_rejects_nan_and_infinity(client):
    for bad in ("nan", "inf", "-inf"):
        resp = client.post("/budget", data={"category": "Food", "amount": bad})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True).lower()
        assert "not a valid number" in body or "too large" in body or "negative" in body


def test_budget_rejects_unknown_category(client, load):
    resp = client.post("/budget", data={"category": "TotallyMadeUp", "amount": "100"})
    assert "not one of your expense categories" in resp.get_data(as_text=True)
    assert load("budget.json") in (None, [])


def test_budget_rejects_huge_amount(client):
    resp = client.post("/budget", data={"category": "Food", "amount": "9999999999999"})
    assert "exceeds the maximum" in resp.get_data(as_text=True)


def test_edit_budget_does_not_crash_on_garbage_amount(client, load):
    client.post("/budget", data={"category": "Food", "amount": "300"})
    resp = client.post("/edit_budget/Food", data={
        "category": "Food", "amount": "not-a-number", "period": "monthly",
    })
    # must not 500 — this used to be a bare float() with zero exception handling
    assert resp.status_code == 200
    assert "must be a number" in resp.get_data(as_text=True)
    assert load("budget.json")[0]["amount"] == 300


def test_edit_budget_rejects_unknown_category(client):
    client.post("/budget", data={"category": "Food", "amount": "300"})
    resp = client.post("/edit_budget/Food", data={
        "category": "NotReal", "amount": "300", "period": "monthly",
    })
    assert "not one of your expense categories" in resp.get_data(as_text=True)


# ================= GOALS =================

def test_goal_create_rejects_negative_target(client, load):
    resp = client.post("/goals", data={"action": "create", "name": "Trip", "target": "-100", "type": "short"})
    assert "negative" in resp.get_data(as_text=True).lower()
    assert load("goals.json") in (None, [])


def test_goal_create_rejects_zero_target(client):
    resp = client.post("/goals", data={"action": "create", "name": "Trip", "target": "0", "type": "short"})
    assert "greater than 0" in resp.get_data(as_text=True)


def test_goal_contribution_rejects_unknown_account(client, load):
    client.post("/goals", data={"action": "create", "name": "Trip", "target": "1000", "type": "short"})
    goal_id = load("goals.json")[0]["id"]

    resp = client.post("/goals", data={
        "action": "save", "goal_id": str(goal_id), "goal_name": "Trip",
        "amount": "100", "account": "GhostAccount",
    })
    assert "existing account" in resp.get_data(as_text=True).lower()
    assert load("expenses.json") in (None, [])


def test_goal_contribution_rejects_unknown_goal(client, load):
    _account(client)
    resp = client.post("/goals", data={
        "action": "save", "goal_id": "999999", "goal_name": "Ghost",
        "amount": "100", "account": "Wallet",
    })
    assert "no longer exists" in resp.get_data(as_text=True).lower()
    assert load("expenses.json") in (None, [])


def test_goal_contribution_rejects_negative_amount(client, load):
    _account(client)
    client.post("/goals", data={"action": "create", "name": "Trip", "target": "1000", "type": "short"})
    goal_id = load("goals.json")[0]["id"]

    resp = client.post("/goals", data={
        "action": "save", "goal_id": str(goal_id), "goal_name": "Trip",
        "amount": "-50", "account": "Wallet",
    })
    assert "negative" in resp.get_data(as_text=True).lower()
    assert load("expenses.json") in (None, [])


def test_goal_contribution_uses_real_goal_name_not_form_value(client, load):
    """The item label must come from the server-side goal, not a
    client-supplied goal_name — a mismatched/spoofed form value shouldn't
    change what gets recorded."""
    _account(client)
    client.post("/goals", data={"action": "create", "name": "Real Trip Name", "target": "1000", "type": "short"})
    goal_id = load("goals.json")[0]["id"]

    client.post("/goals", data={
        "action": "save", "goal_id": str(goal_id), "goal_name": "Spoofed Name",
        "amount": "100", "account": "Wallet",
    })
    record = load("expenses.json")[0]
    assert "Real Trip Name" in record["item"]
    assert "Spoofed Name" not in record["item"]


def test_edit_goal_rejects_negative_target(client, load):
    client.post("/goals", data={"action": "create", "name": "Trip", "target": "1000", "type": "short"})
    goal_id = load("goals.json")[0]["id"]

    resp = client.post(f"/edit_goal/{goal_id}", data={"name": "Trip", "target": "-1"})
    assert "negative" in resp.get_data(as_text=True).lower()
    assert load("goals.json")[0]["target"] == 1000


def test_edit_goal_rejects_invalid_status(client, load):
    client.post("/goals", data={"action": "create", "name": "Trip", "target": "1000", "type": "short"})
    goal_id = load("goals.json")[0]["id"]

    resp = client.post(f"/edit_goal/{goal_id}", data={
        "name": "Trip", "target": "1000", "status": "Whatever I Want",
    })
    assert "valid status" in resp.get_data(as_text=True).lower()
    assert load("goals.json")[0]["status"] == "In Progress"


def test_edit_goal_accepts_every_known_status(client, load):
    client.post("/goals", data={"action": "create", "name": "Trip", "target": "1000", "type": "short"})
    goal_id = load("goals.json")[0]["id"]

    for status in ("In Progress", "Paused", "Cancelled", "Completed"):
        resp = client.post(f"/edit_goal/{goal_id}", data={
            "name": "Trip", "target": "1000", "status": status,
        })
        assert resp.status_code == 302
        assert load("goals.json")[0]["status"] == status

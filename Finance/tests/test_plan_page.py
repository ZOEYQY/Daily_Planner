"""The combined /plan page (budgets + goals + a summary of both)."""


def _account(client, name="Wallet"):
    client.post("/accounts", data={"name": name, "purpose": "spending"})


def test_budget_and_goals_redirect_to_plan(client):
    for path in ("/budget", "/goals"):
        resp = client.get(path)
        assert resp.status_code == 302 and "/plan" in resp.headers["Location"]


def test_plan_shows_budgets_and_goals_together(client):
    _account(client)
    client.post("/budget", data={"category": "Food", "amount": "300", "period": "monthly"})
    client.post("/goals", data={"action": "create", "name": "Japan Trip",
                                "target": "5000", "type": "short"})

    body = client.get("/plan").get_data(as_text=True)
    assert "Food" in body           # budget card
    assert "Japan Trip" in body     # goal card
    assert "Budgets — this period" in body and "Goals — active" in body


def test_plan_summary_totals(client):
    _account(client)
    client.post("/budget", data={"category": "Food", "amount": "300", "period": "monthly"})
    client.post("/add", data={"date": "2026-09-05", "type": "expense", "category": "Food",
                              "account": "Wallet", "item": "x", "amount": "120"})
    client.post("/goals", data={"action": "create", "name": "Trip", "target": "1000", "type": "short"})
    client.post("/goals", data={"action": "save", "goal_id": "1", "goal_name": "Trip",
                                "amount": "250", "account": "Wallet"})

    body = client.get("/plan").get_data(as_text=True)
    assert "RM 120.00" in body and "RM 300.00" in body   # budget spent / limit
    assert "RM 250.00" in body and "RM 1000.00" in body  # goal saved / target


def test_budget_create_error_renders_plan(client):
    resp = client.post("/budget", data={"category": "", "amount": ""})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Category and amount required" in body and "Goals — active" in body


def test_goal_create_error_renders_plan(client):
    resp = client.post("/goals", data={"action": "create", "name": "", "target": "", "type": ""})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "All fields required" in body and "Budgets — this period" in body


def test_goal_actions_return_to_plan(client):
    _account(client)
    client.post("/goals", data={"action": "create", "name": "G", "target": "100", "type": "short"})
    gid = "1"
    for path in (f"/pause_goal/{gid}", f"/resume_goal/{gid}", f"/edit_goal/{gid}"):
        resp = client.post(path, data={"name": "G", "target": "100"})
        assert resp.status_code == 302 and "/plan" in resp.headers["Location"]

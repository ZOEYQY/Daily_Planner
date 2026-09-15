"""The /plan page: three tabs — Summary, Budgets, Goals."""


def _account(client, name="Wallet"):
    client.post("/accounts", data={"name": name, "purpose": "spending"})


def _setup(client):
    _account(client)
    client.post("/budget", data={"category": "Food", "amount": "300", "period": "monthly"})
    client.post("/goals", data={"action": "create", "name": "Japan Trip",
                                "target": "5000", "type": "short"})


def test_budget_and_goals_redirect_into_plan_tabs(client):
    assert "/plan?tab=budgets" in client.get("/budget").headers["Location"]
    assert "/plan?tab=goals" in client.get("/goals").headers["Location"]


def test_plan_defaults_to_summary_tab(client):
    _setup(client)
    body = client.get("/plan").get_data(as_text=True)
    assert "Plan — Summary" in body
    assert "Budgets — this period" in body and "Goals — active" in body
    # the management UI is NOT on the summary tab
    assert "Your Budgets" not in body and "Create Goal" not in body


def test_budgets_tab_has_budget_ui_only(client):
    _setup(client)
    body = client.get("/plan?tab=budgets").get_data(as_text=True)
    assert "Your Budgets" in body and "Food" in body and "Save Budget" in body
    assert "Create Goal" not in body


def test_goals_tab_has_goal_ui_only(client):
    _setup(client)
    body = client.get("/plan?tab=goals").get_data(as_text=True)
    assert "Create Goal" in body and "Japan Trip" in body
    assert "Your Budgets" not in body


def test_bad_tab_falls_back_to_summary(client):
    assert "Plan — Summary" in client.get("/plan?tab=nonsense").get_data(as_text=True)


def test_summary_tab_totals(client):
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


def test_budget_create_error_lands_on_budgets_tab(client):
    resp = client.post("/budget", data={"category": "", "amount": ""})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Category and amount required" in body
    assert "Save Budget" in body and "Create Goal" not in body


def test_goal_create_error_lands_on_goals_tab(client):
    resp = client.post("/goals", data={"action": "create", "name": "", "target": "", "type": ""})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "All fields required" in body
    assert "Create Goal" in body and "Save Budget" not in body


def test_goal_actions_return_to_plan(client):
    _account(client)
    client.post("/goals", data={"action": "create", "name": "G", "target": "100", "type": "short"})
    gid = "1"
    for path in (f"/pause_goal/{gid}", f"/resume_goal/{gid}", f"/edit_goal/{gid}"):
        resp = client.post(path, data={"name": "G", "target": "100"})
        assert resp.status_code == 302 and "/plan" in resp.headers["Location"]

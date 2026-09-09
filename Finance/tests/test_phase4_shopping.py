"""Phase 4: cooling-off period, pre-buy budget check, hindsight rating, price tracking."""


def _items(load):
    raw = load("shopping.json")
    return raw["items"] if raw else []


def _account(client, name="Wallet"):
    client.post("/accounts", data={"name": name, "purpose": "spending"})


def _create(client, **over):
    data = {"action": "create", "name": "Keyboard", "estimated_price": "350", "category": "Food"}
    data.update(over)
    client.post("/shopping", data=data)
    return _items


# ================= COOLING-OFF PERIOD =================

def test_wait_days_starts_cooldown_on_decide(client, load):
    _create(client, wait_days="7")
    iid = _items(load)[0]["id"]

    client.post("/shopping", data={"action": "decide", "id": iid, "decision": "buy"})
    it = _items(load)[0]
    assert it["wait_until"] and it["wait_until"] > it["decision_date"]


def test_wait_decision_defaults_to_14_days(client, load):
    _create(client)  # wait_days 0
    iid = _items(load)[0]["id"]
    client.post("/shopping", data={"action": "decide", "id": iid, "decision": "wait"})
    it = _items(load)[0]
    assert it["wait_days"] == 14 and it["wait_until"]


def test_purchase_blocked_during_cooldown(client, load):
    _account(client)
    _create(client, wait_days="30")
    iid = _items(load)[0]["id"]
    client.post("/shopping", data={"action": "decide", "id": iid, "decision": "buy"})

    resp = client.post("/shopping", data={
        "action": "purchase", "id": iid, "account": "Wallet", "amount": "350",
    })
    assert resp.status_code == 200
    assert "cooling-off period" in resp.get_data(as_text=True)
    assert load("expenses.json") in (None, [])


def test_purchase_force_overrides_cooldown(client, load):
    _account(client)
    _create(client, wait_days="30")
    iid = _items(load)[0]["id"]
    client.post("/shopping", data={"action": "decide", "id": iid, "decision": "buy"})

    resp = client.post("/shopping", data={
        "action": "purchase", "id": iid, "account": "Wallet", "amount": "350", "force": "1",
    })
    assert resp.status_code == 302
    assert _items(load)[0]["status"] == "bought"


def test_skip_wait_clears_cooldown(client, load):
    _account(client)
    _create(client, wait_days="30")
    iid = _items(load)[0]["id"]
    client.post("/shopping", data={"action": "decide", "id": iid, "decision": "buy"})
    client.post("/shopping", data={"action": "skip_wait", "id": iid})
    assert _items(load)[0]["wait_until"] == ""

    resp = client.post("/shopping", data={
        "action": "purchase", "id": iid, "account": "Wallet", "amount": "350",
    })
    assert resp.status_code == 302


def test_zero_wait_days_no_cooldown(client, load):
    _account(client)
    _create(client, wait_days="0")
    iid = _items(load)[0]["id"]
    client.post("/shopping", data={"action": "decide", "id": iid, "decision": "buy"})
    assert _items(load)[0]["wait_until"] == ""
    resp = client.post("/shopping", data={
        "action": "purchase", "id": iid, "account": "Wallet", "amount": "350",
    })
    assert resp.status_code == 302


# ================= PRE-BUY BUDGET CHECK =================

def test_budget_check_shows_on_item(client):
    _account(client)
    client.post("/budget", data={"category": "Food", "amount": "200", "period": "monthly"})
    client.post("/add", data={"date": "2026-09-02", "type": "expense", "category": "Food",
                              "account": "Wallet", "item": "groceries", "amount": "150"})
    _create(client, estimated_price="80", category="Food")

    body = client.get("/shopping").get_data(as_text=True)
    assert "Budget check (Food" in body
    # 150 spent + 80 = 230 / 200 -> over by 30
    assert "230.00" in body and "30.00 over" in body


def test_no_budget_check_without_budget(client):
    _create(client, category="Food")
    assert "Budget check" not in client.get("/shopping").get_data(as_text=True)


# ================= PRICE TRACKING =================

def test_add_and_delete_price_check(client, load):
    _create(client)
    iid = _items(load)[0]["id"]

    client.post("/shopping", data={"action": "add_price_check", "id": iid,
                                   "price": "320", "source": "Shopee", "note": "sale"})
    client.post("/shopping", data={"action": "add_price_check", "id": iid,
                                   "price": "299.90", "source": "Lazada"})
    checks = _items(load)[0]["price_checks"]
    assert len(checks) == 2 and checks[0]["price"] == 320.0 and checks[0]["source"] == "Shopee"

    body = client.get("/shopping").get_data(as_text=True)
    assert "lowest RM 299.90" in body

    cid = checks[0]["id"]
    client.post("/shopping", data={"action": "delete_price_check", "id": iid, "check_id": cid})
    assert len(_items(load)[0]["price_checks"]) == 1


def test_price_check_rejects_bad_price(client, load):
    _create(client)
    iid = _items(load)[0]["id"]
    resp = client.post("/shopping", data={"action": "add_price_check", "id": iid, "price": "abc"})
    assert resp.status_code == 200
    assert "must be a number" in resp.get_data(as_text=True)


# ================= HINDSIGHT RATING =================

def test_rate_bought_item(client, load):
    _account(client)
    _create(client, estimated_price="350")
    iid = _items(load)[0]["id"]
    client.post("/shopping", data={"action": "decide", "id": iid, "decision": "buy"})
    client.post("/shopping", data={"action": "purchase", "id": iid, "account": "Wallet", "amount": "380"})

    client.post("/shopping", data={"action": "rate", "id": iid, "worth_it": "no",
                                   "hindsight_note": "barely used it"})
    it = _items(load)[0]
    assert it["worth_it"] == "no" and it["hindsight_note"] == "barely used it" and it["rated_at"]

    body = client.get("/shopping").get_data(as_text=True)
    assert "Not worth it" in body
    # history summary line: rated 1/1, (0 worth it, 1 not, 0 meh), +RM 30.00 vs estimates
    assert "rated 1/1" in body
    assert "0 worth it, 1 not, 0 meh" in body
    assert "RM 30.00" in body


def test_cannot_rate_open_item(client, load):
    _create(client)
    iid = _items(load)[0]["id"]
    resp = client.post("/shopping", data={"action": "rate", "id": iid, "worth_it": "yes"})
    assert resp.status_code == 200
    assert "Only bought items" in resp.get_data(as_text=True)

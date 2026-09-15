"""AI purchase advisor for shopping-list items.

The Gemini call itself (``_advise_purchase``) is monkeypatched — these tests
cover the route wiring, persistence, validation, and graceful degradation.
"""
from conftest import shopping_items


CANNED = {
    "recommendation": "wait",
    "reasoning": "You have a working one and the budget is tight this month.",
    "suggested_wait_days": 14,
    "confidence": "medium",
    "generated_at": "2026-09-08",
}


def test_advise_returns_and_persists(client, load, monkeypatch):
    import finance_routes
    monkeypatch.setattr(finance_routes, "_advise_purchase", lambda item, ctx: dict(CANNED))

    client.post("/shopping", data={
        "action": "create", "name": "Keyboard", "estimated_price": "350", "category": "Food",
    })
    item_id = shopping_items(load)[0]["id"]

    resp = client.post("/shopping/advise", data={"id": item_id})
    assert resp.status_code == 200
    advice = resp.get_json()["advice"]
    assert advice["recommendation"] == "wait"
    assert advice["suggested_wait_days"] == 14

    stored = shopping_items(load)[0]["ai_suggestion"]
    assert stored["reasoning"].startswith("You have a working one")


def test_advice_shows_on_page_after(client, load, monkeypatch):
    import finance_routes
    monkeypatch.setattr(finance_routes, "_advise_purchase", lambda item, ctx: {
        "recommendation": "dont_buy", "reasoning": "You already own one that works.",
        "suggested_wait_days": None, "confidence": "high", "generated_at": "2026-09-08",
    })
    client.post("/shopping", data={"action": "create", "name": "Blender", "estimated_price": "120"})
    item_id = shopping_items(load)[0]["id"]
    client.post("/shopping/advise", data={"id": item_id})

    page = client.get("/shopping").get_data(as_text=True)
    assert "You already own one that works." in page
    assert "Don't buy" in page


def test_advise_context_is_built_from_real_data(client, load, monkeypatch):
    """The advisor must receive the user's finance context, not just the item."""
    import finance_routes
    captured = {}

    def fake_advise(item, context):
        captured["context"] = context
        captured["item"] = item
        return dict(CANNED)

    monkeypatch.setattr(finance_routes, "_advise_purchase", fake_advise)

    # an existing Food expense + a Food budget => context should reflect both
    client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "new_account": "Wallet", "item": "Lunch", "amount": "30",
    })
    client.post("/budget", data={"category": "Food", "amount": "200", "period": "monthly"})
    client.post("/shopping", data={
        "action": "create", "name": "Snacks", "estimated_price": "40", "category": "Food",
    })
    item_id = shopping_items(load)[0]["id"]

    resp = client.post("/shopping/advise", data={"id": item_id})
    assert resp.status_code == 200
    ctx = captured["context"]
    assert ctx["category_spent_this_month"] == 30.0
    assert ctx["category_budget"]["limit"] == 200
    assert "balance_this_month" in ctx


def test_advise_unknown_item_404(client):
    resp = client.post("/shopping/advise", data={"id": "does-not-exist"})
    assert resp.status_code == 404


def test_advise_without_api_key_is_graceful(client, load, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client.post("/shopping", data={"action": "create", "name": "Lamp", "estimated_price": "60"})
    item_id = shopping_items(load)[0]["id"]

    resp = client.post("/shopping/advise", data={"id": item_id})
    assert resp.status_code == 503
    assert "not configured" in resp.get_json()["error"]
    assert shopping_items(load)[0]["ai_suggestion"] is None


def test_advise_normaliser_clamps_bad_model_output():
    from finance_routes import _normalise_purchase_advice
    out = _normalise_purchase_advice({
        "recommendation": "maybe", "reasoning": "  ", "suggested_wait_days": -5,
        "confidence": "banana",
    })
    assert out["recommendation"] == "wait"
    assert out["suggested_wait_days"] is None
    assert out["confidence"] == "low"
    assert out["reasoning"]

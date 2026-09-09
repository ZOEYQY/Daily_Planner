"""Next-month income forecast (statistical baseline + optional AI refine)."""
from datetime import date


def _month_key(n_ago):
    y, m = date.today().year, date.today().month
    for _ in range(n_ago):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return f"{y}-{m:02d}"


def _next_month():
    y, m = date.today().year, date.today().month
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def _account(client):
    client.post("/accounts", data={"name": "Bank", "purpose": "spending"})


def _income(client, month_key, amount, category="Salary"):
    client.post("/add", data={
        "date": f"{month_key}-15", "type": "income", "category": category,
        "account": "Bank", "item": "pay", "amount": str(amount),
    })


def test_forecast_needs_two_completed_months(client):
    _account(client)
    _income(client, _month_key(1), 1000)   # only one completed month
    resp = client.post("/forecast-income")
    assert resp.status_code == 400
    assert "at least two" in resp.get_json()["error"].lower()


def test_forecast_baseline_without_key(client, load, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _account(client)
    _income(client, _month_key(3), 1000)
    _income(client, _month_key(2), 1200)
    _income(client, _month_key(1), 1100)

    resp = client.post("/forecast-income")
    assert resp.status_code == 200
    f = resp.get_json()["forecast"]
    assert f["source"] == "baseline"
    assert f["target"] == _next_month()
    assert f["low"] == 1000.0 and f["high"] == 1200.0
    assert f["low"] <= f["estimate"] <= f["high"]

    stored = load("insights.json")["reviews"][f"forecast-{_next_month()}"]
    assert stored["estimate"] == f["estimate"]


def test_forecast_ai_refine(client, monkeypatch):
    import finance_routes
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured = {}

    def fake(prompt, schema, name, tokens=300):
        captured["prompt"] = prompt
        return {"estimate": 1150, "low": 900, "high": 1400,
                "confidence": "medium", "reasoning": "Your shifts have been steady."}

    monkeypatch.setattr(finance_routes, "_openai_structured", fake)
    _account(client)
    _income(client, _month_key(2), 1000)
    _income(client, _month_key(1), 1300)

    f = client.post("/forecast-income").get_json()["forecast"]
    assert f["source"] == "ai" and f["estimate"] == 1150.0
    assert f["low"] == 900.0 and f["high"] == 1400.0 and f["confidence"] == "medium"
    assert "recurring_income" in captured["prompt"]


def test_forecast_ai_failure_falls_back_to_baseline(client, monkeypatch):
    import finance_routes
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(finance_routes, "_openai_structured",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    _account(client)
    _income(client, _month_key(2), 1000)
    _income(client, _month_key(1), 1000)

    resp = client.post("/forecast-income")
    assert resp.status_code == 200
    f = resp.get_json()["forecast"]
    assert f["source"] == "baseline" and f["estimate"] == 1000.0


def test_forecast_ai_output_clamped(client, monkeypatch):
    import finance_routes
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(finance_routes, "_openai_structured", lambda *a, **k: {
        "estimate": -50, "low": 2000, "high": 500, "confidence": "banana", "reasoning": "x",
    })
    _account(client)
    _income(client, _month_key(2), 800)
    _income(client, _month_key(1), 800)

    f = client.post("/forecast-income").get_json()["forecast"]
    assert f["estimate"] == 0.0            # negative clamped
    assert f["low"] == 500.0 and f["high"] == 2000.0   # low/high swapped
    assert f["confidence"] == "low"        # invalid enum -> low


def test_forecast_excludes_transfers(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _account(client)
    client.post("/accounts", data={"name": "Save", "purpose": "savings"})
    _income(client, _month_key(2), 1000)
    _income(client, _month_key(1), 1000)
    # a transfer creates a "Transfer In" income row — must NOT count as salary
    client.post("/add", data={"date": f"{_month_key(1)}-20", "type": "transfer",
                              "account": "Bank", "to_account": "Save", "amount": "5000"})

    f = client.post("/forecast-income").get_json()["forecast"]
    assert f["high"] == 1000.0   # the 5000 transfer is ignored


def test_forecast_shows_on_summary(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _account(client)
    _income(client, _month_key(2), 1000)
    _income(client, _month_key(1), 1000)
    client.post("/forecast-income")

    page = client.get("/summary").get_data(as_text=True)
    assert "Next month's income" in page and "RM 1000.00" in page

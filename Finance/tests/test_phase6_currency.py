"""Phase 6: standalone currency converter (RM base, user-maintained rates)."""
import io
import zipfile


def _rates(load):
    raw = load("rates.json")
    return raw["rates"] if raw else []


def test_rates_seeded_on_first_visit(client, load):
    resp = client.get("/currency")
    assert resp.status_code == 200
    assert "US Dollar" in resp.get_data(as_text=True)

    stored = load("rates.json")
    assert stored["base"] == "MYR"
    codes = {r["code"] for r in stored["rates"]}
    assert {"USD", "SGD", "JPY"} <= codes
    for r in stored["rates"]:
        assert r["rate_to_myr"] > 0 and r["updated_at"]


def test_convert_foreign_to_myr(client, load):
    client.get("/currency")  # seed
    # set USD to a known rate first
    client.post("/currency", data={"action": "update_rate", "code": "USD", "rate_to_myr": "4.50"})

    body = client.get("/currency?amount=100&from=USD&to=MYR").get_data(as_text=True)
    assert "100 USD" in body and "450.0000 MYR" in body


def test_convert_myr_to_foreign(client):
    client.get("/currency")
    client.post("/currency", data={"action": "update_rate", "code": "USD", "rate_to_myr": "4.00"})
    body = client.get("/currency?amount=40&from=MYR&to=USD").get_data(as_text=True)
    assert "40 MYR" in body and "10.0000 USD" in body


def test_convert_cross_currency(client):
    client.get("/currency")
    client.post("/currency", data={"action": "update_rate", "code": "USD", "rate_to_myr": "4.00"})
    client.post("/currency", data={"action": "update_rate", "code": "SGD", "rate_to_myr": "2.00"})
    # 100 USD = 400 MYR = 200 SGD
    body = client.get("/currency?amount=100&from=USD&to=SGD").get_data(as_text=True)
    assert "200.0000 SGD" in body


def test_add_update_delete_rate(client, load):
    client.get("/currency")
    client.post("/currency", data={"action": "add_rate", "code": "krw",
                                   "name": "Korean Won", "rate_to_myr": "0.0035"})
    krw = next(r for r in _rates(load) if r["code"] == "KRW")
    assert krw["name"] == "Korean Won" and krw["rate_to_myr"] == 0.0035

    client.post("/currency", data={"action": "update_rate", "code": "KRW", "rate_to_myr": "0.0036"})
    assert next(r for r in _rates(load) if r["code"] == "KRW")["rate_to_myr"] == 0.0036

    client.post("/currency", data={"action": "delete_rate", "code": "KRW"})
    assert not any(r["code"] == "KRW" for r in _rates(load))


def test_add_rate_validation(client):
    client.get("/currency")
    assert "already in the list" in client.post("/currency", data={
        "action": "add_rate", "code": "USD", "rate_to_myr": "5"}).get_data(as_text=True)
    assert "base currency" in client.post("/currency", data={
        "action": "add_rate", "code": "MYR", "rate_to_myr": "1"}).get_data(as_text=True)
    assert "greater than 0" in client.post("/currency", data={
        "action": "add_rate", "code": "XYZ", "rate_to_myr": "0"}).get_data(as_text=True)


def test_convert_bad_input(client):
    client.get("/currency")
    assert "must be a number" in client.get("/currency?amount=abc&from=USD&to=MYR").get_data(as_text=True)


def test_backup_includes_rates(client):
    client.get("/currency")
    zf = zipfile.ZipFile(io.BytesIO(client.get("/data/backup.zip").get_data()))
    assert "rates.json" in zf.namelist()


def test_converter_does_not_touch_transactions(client, load):
    client.get("/currency")
    client.post("/currency", data={"action": "add_rate", "code": "PHP", "rate_to_myr": "0.08"})
    client.get("/currency?amount=1000&from=PHP&to=MYR")
    assert load("expenses.json") in (None, [])

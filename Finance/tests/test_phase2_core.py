"""Phase 2: recurring transactions, richer /view search, tags, split transactions."""
import io
import zipfile


def _records(load, deleted=False):
    recs = load("expenses.json") or []
    return recs if deleted else [r for r in recs if not r.get("deleted_at")]


def _rules(load):
    raw = load("recurring.json")
    return raw["rules"] if raw else []


def _add(client, **over):
    data = {
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "new_account": "Wallet", "purpose": "spending", "item": "Lunch", "amount": "12.50",
    }
    data.update(over)
    return client.post("/add", data=data)


# ================= TAGS =================

def test_normalize_tags_unit():
    from finance_helpers import normalize_tags
    assert normalize_tags("  #Japan-Trip , reimbursable, japan-trip ,") == ["Japan-Trip", "reimbursable"]
    assert normalize_tags("") == []
    assert normalize_tags(["a", "a", "b"]) == ["a", "b"]
    assert normalize_tags(None) == []


def test_add_record_with_tags(client, load):
    _add(client, tags="reimbursable, japan-trip")
    rec = _records(load)[0]
    assert rec["tags"] == ["reimbursable", "japan-trip"]


def test_update_record_tags(client, load):
    _add(client, tags="old")
    rid = _records(load)[0]["id"]
    client.post(f"/update/{rid}", data={
        "date": "2026-09-08", "type": "expense", "category": "Food",
        "account": "Wallet", "item": "Lunch", "amount": "12.50", "tags": "new, fresh",
    })
    assert _records(load)[0]["tags"] == ["new", "fresh"]


def test_view_filter_by_tag(client):
    _add(client, item="Tagged", tags="trip")
    _add(client, item="Untagged")
    body = client.get("/view?tag=trip").get_data(as_text=True)
    assert "Tagged" in body and "Untagged" not in body


def test_csv_export_joins_tags(client):
    _add(client, item="X", tags="a, b")
    body = client.get("/data/export.csv").get_data(as_text=True)
    assert "a;b" in body


# ================= SEARCH =================

def test_view_search_and_filters(client):
    _add(client, item="Coffee", category="Food", amount="8", account="Wallet")
    _add(client, item="Bus fare", category="Transport", amount="2.50",
         new_account="Card", account="")
    client.post("/add", data={"date": "2026-08-01", "type": "income", "category": "Salary",
                              "account": "Wallet", "item": "Payday", "amount": "3000"})

    assert "Coffee" in client.get("/view?q=coffee").get_data(as_text=True)
    assert "Bus fare" not in client.get("/view?q=coffee").get_data(as_text=True)

    only_income = client.get("/view?type=income").get_data(as_text=True)
    assert "Payday" in only_income and "Coffee" not in only_income

    only_transport = client.get("/view?category=Transport").get_data(as_text=True)
    assert "Bus fare" in only_transport and "Coffee" not in only_transport

    band = client.get("/view?min=5&max=100").get_data(as_text=True)
    assert "Coffee" in band and "Bus fare" not in band and "Payday" not in band

    dated = client.get("/view?start=2026-08-01&end=2026-08-31").get_data(as_text=True)
    assert "Payday" in dated and "Coffee" not in dated


def test_view_totals_reflect_filter(client):
    _add(client, amount="10", category="Food")
    _add(client, amount="25", category="Transport")
    body = client.get("/view?category=Food").get_data(as_text=True)
    assert "1</strong> record" in body
    assert "expense <strong>RM 10.00" in body


# ================= SPLIT TRANSACTIONS =================

def _split(client, **over):
    data = {
        "date": "2026-09-08", "type": "expense", "new_account": "Wallet", "purpose": "spending",
        "item": "Supermarket run",
        "split_category": ["Food", "Transport"],
        "split_amount": ["40", "15"],
        "split_item": ["Groceries", "Parking"],
    }
    data.update(over)
    return client.post("/add", data=data)


def test_split_creates_linked_records(client, load):
    resp = _split(client)
    assert resp.status_code == 302

    recs = _records(load)
    assert len(recs) == 2
    assert {r["category"] for r in recs} == {"Food", "Transport"}
    assert {r["amount"] for r in recs} == {40.0, 15.0}
    split_ids = {r["split_id"] for r in recs}
    assert len(split_ids) == 1
    assert all(r["split_label"] == "Food + Transport" for r in recs)
    assert all(r["date"] == "2026-09-08" and r["account"] == "Wallet" for r in recs)
    assert {r["item"] for r in recs} == {"Supermarket run — Groceries", "Supermarket run — Parking"}


def test_split_needs_two_lines(client):
    resp = _split(client, split_category=["Food"], split_amount=["40"], split_item=["x"])
    assert resp.status_code == 200
    assert "at least two lines" in resp.get_data(as_text=True)


def test_split_rejects_unknown_category(client):
    resp = _split(client, split_category=["Food", "Nonsense"], split_amount=["10", "5"],
                  split_item=["", ""])
    assert resp.status_code == 200
    assert "not one of your expense categories" in resp.get_data(as_text=True)


def test_split_line_independently_deletable(client, load):
    _split(client)
    recs = _records(load)
    rid = recs[0]["id"]
    client.post(f"/delete/{rid}")
    remaining = _records(load)
    assert len(remaining) == 1 and remaining[0]["split_id"] == recs[1]["split_id"]


# ================= RECURRING =================

def _make_account(client, name="Wallet"):
    client.post("/accounts", data={"name": name, "purpose": "spending"})


def _create_rule(client, **over):
    data = {
        "action": "create", "type": "expense", "category": "Bills", "account": "Wallet",
        "amount": "55", "item": "Netflix", "frequency": "monthly", "interval": "1",
        "start_date": "2026-01-01",
    }
    data.update(over)
    return client.post("/recurring", data=data)


def test_advance_date_unit():
    from finance_helpers import advance_date
    assert advance_date("2026-01-31", "monthly", 1) == "2026-02-28"
    assert advance_date("2026-01-15", "monthly", 2) == "2026-03-15"
    assert advance_date("2026-01-01", "weekly", 1) == "2026-01-08"
    assert advance_date("2024-02-29", "yearly", 1) == "2025-02-28"


def test_create_recurring_rule(client, load):
    _make_account(client)
    resp = _create_rule(client)
    assert resp.status_code == 302
    rules = _rules(load)
    assert len(rules) == 1
    assert rules[0]["next_due"] == "2026-01-01"
    assert rules[0]["id"] and rules[0]["active"] is True
    assert "Netflix" in client.get("/recurring").get_data(as_text=True)


def test_recurring_requires_existing_account(client):
    resp = _create_rule(client, account="Ghost")
    assert resp.status_code == 200
    assert "existing account" in resp.get_data(as_text=True)


def test_post_recurring_catches_up_and_advances(client, load):
    _make_account(client)
    _create_rule(client, start_date="2026-06-01")  # several months before 2026-09-09
    rule_id = _rules(load)[0]["id"]

    client.post("/recurring", data={"action": "post", "id": rule_id})

    posted = [r for r in _records(load) if r.get("source") == "recurring"]
    assert len(posted) >= 3
    assert all(r["recurring_id"] == rule_id for r in posted)
    assert all(r["category"] == "Bills" and r["amount"] == 55.0 for r in posted)
    # next_due advanced past today
    assert _rules(load)[0]["next_due"] > "2026-09-09"


def test_recurring_skip_advances_without_posting(client, load):
    _make_account(client)
    _create_rule(client, start_date="2026-09-01")
    rule_id = _rules(load)[0]["id"]

    client.post("/recurring", data={"action": "skip", "id": rule_id})
    assert _records(load) == []
    assert _rules(load)[0]["next_due"] == "2026-10-01"


def test_recurring_pause_resume_delete(client, load):
    _make_account(client)
    _create_rule(client)
    rule_id = _rules(load)[0]["id"]

    client.post("/recurring", data={"action": "pause", "id": rule_id})
    assert _rules(load)[0]["active"] is False
    # paused rule posts nothing
    client.post("/recurring", data={"action": "post_all"})
    assert [r for r in _records(load) if r.get("source") == "recurring"] == []

    client.post("/recurring", data={"action": "resume", "id": rule_id})
    assert _rules(load)[0]["active"] is True

    client.post("/recurring", data={"action": "delete", "id": rule_id})
    assert _rules(load) == []


def test_recurring_auto_post_on_dashboard(client, load):
    _make_account(client)
    _create_rule(client, start_date="2026-08-01", auto_post="on")
    # merely loading the dashboard materialises due auto-post occurrences
    client.get("/finance")
    posted = [r for r in _records(load) if r.get("source") == "recurring"]
    assert len(posted) >= 1


def test_recurring_end_date_stops_rule(client, load):
    _make_account(client)
    _create_rule(client, start_date="2026-06-01", end_date="2026-07-15")
    rule_id = _rules(load)[0]["id"]
    client.post("/recurring", data={"action": "post", "id": rule_id})

    posted = [r for r in _records(load) if r.get("source") == "recurring"]
    # only the 2026-06-01 and 2026-07-01 occurrences fall on/before the end date
    assert {r["date"] for r in posted} == {"2026-06-01", "2026-07-01"}
    assert _rules(load)[0]["active"] is False


def test_backup_includes_recurring(client, load):
    _make_account(client)
    _create_rule(client)
    zf = zipfile.ZipFile(io.BytesIO(client.get("/data/backup.zip").get_data()))
    assert "recurring.json" in zf.namelist()

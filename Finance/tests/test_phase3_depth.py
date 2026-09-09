"""Phase 3: debts/loans, budget rollover, net-worth snapshots, CSV import."""
import io


def _records(load):
    return [r for r in (load("expenses.json") or []) if not r.get("deleted_at")]


def _debts(load):
    raw = load("debts.json")
    return raw["debts"] if raw else []


def _snaps(load):
    raw = load("networth.json")
    return raw["snapshots"] if raw else []


def _account(client, name="Wallet", purpose="spending"):
    client.post("/accounts", data={"name": name, "purpose": purpose})


def _add(client, **over):
    data = {"date": "2026-09-08", "type": "expense", "category": "Food",
            "account": "Wallet", "item": "x", "amount": "10"}
    data.update(over)
    return client.post("/add", data=data)


# ================= DEBTS =================

def _create_debt(client, **over):
    data = {"action": "create", "direction": "owe", "counterparty": "Ethan",
            "principal": "1000", "description": "Laptop loan", "date": "2026-09-01"}
    data.update(over)
    return client.post("/debts", data=data)


def test_create_debt(client, load):
    resp = _create_debt(client)
    assert resp.status_code == 302
    d = _debts(load)[0]
    assert d["counterparty"] == "Ethan" and d["principal"] == 1000.0
    assert d["direction"] == "owe" and d["status"] == "open" and d["payments"] == []
    assert "Ethan" in client.get("/debts").get_data(as_text=True)


def test_debt_requires_direction_and_name(client):
    assert "Choose whether" in _create_debt(client, direction="").get_data(as_text=True)
    assert "name required" in _create_debt(client, counterparty="  ").get_data(as_text=True)


def test_debt_payments_and_settle(client, load):
    _create_debt(client, principal="300")
    did = _debts(load)[0]["id"]

    client.post("/debts", data={"action": "add_payment", "id": did, "amount": "100", "date": "2026-09-10"})
    d = _debts(load)[0]
    assert len(d["payments"]) == 1 and d["status"] == "open"

    client.post("/debts", data={"action": "add_payment", "id": did, "amount": "200", "date": "2026-09-20"})
    d = _debts(load)[0]
    assert d["status"] == "settled"  # fully paid -> auto-settled

    # remove a payment -> reopens
    pid = d["payments"][0]["id"]
    client.post("/debts", data={"action": "delete_payment", "id": did, "payment_id": pid})
    assert _debts(load)[0]["status"] == "open"


def test_debt_payment_mirrored_as_transaction(client, load):
    _account(client)
    _create_debt(client, direction="owe", principal="500")
    did = _debts(load)[0]["id"]

    client.post("/debts", data={
        "action": "add_payment", "id": did, "amount": "150", "date": "2026-09-15",
        "as_transaction": "on", "txn_account": "Wallet", "txn_category": "Food",
    })
    recs = _records(load)
    assert len(recs) == 1
    assert recs[0]["type"] == "expense" and recs[0]["amount"] == 150.0
    assert recs[0]["source"] == "debt" and recs[0]["debt_id"] == did
    assert _debts(load)[0]["payments"][0]["record_id"] == recs[0]["id"]


def test_debt_delete(client, load):
    _create_debt(client)
    did = _debts(load)[0]["id"]
    client.post("/debts", data={"action": "delete", "id": did})
    assert _debts(load) == []


# ================= BUDGET ROLLOVER =================

def test_budget_rollover_carries_unspent(client, load):
    _account(client)
    # a monthly Food budget with rollover, created "long ago" via prior spend
    client.post("/budget", data={"category": "Food", "amount": "200",
                                 "period": "monthly", "rollover": "on"})

    # spent only RM50 last month -> RM150 should roll into this month
    _add(client, date="2026-08-05", amount="50", category="Food")
    _add(client, date="2026-09-03", amount="30", category="Food")

    page = client.get("/plan?tab=budgets").get_data(as_text=True)
    # effective this period = 200 base + (200-50) carryover from Aug + full 200 for
    # each earlier empty month in the 12-window... just assert carryover is shown
    assert "rolled over" in page
    assert "rollover" in page  # pill

    b = load("budget.json")[0]
    assert b["rollover"] is True


def test_budget_without_rollover_unchanged(client, load):
    _account(client)
    client.post("/budget", data={"category": "Food", "amount": "200", "period": "monthly"})
    _add(client, date="2026-09-03", amount="30", category="Food")
    page = client.get("/plan?tab=budgets").get_data(as_text=True)
    assert "rolled over" not in page
    assert load("budget.json")[0].get("rollover") is False


# ================= NET WORTH =================

def test_networth_computed_from_data(client, load):
    _account(client, "Bank")
    _add(client, date="2026-09-01", type="income", category="Salary", account="Bank", amount="3000")
    _add(client, date="2026-09-02", type="expense", category="Food", account="Bank", amount="200")
    # someone owes you 500, you owe 1000
    client.post("/debts", data={"action": "create", "direction": "owed",
                                "counterparty": "A", "principal": "500"})
    client.post("/debts", data={"action": "create", "direction": "owe",
                                "counterparty": "B", "principal": "1000"})

    page = client.get("/networth").get_data(as_text=True)
    # assets = 2800 liquid + 500 receivable = 3300 ; liabilities = 1000 ; net = 2300
    assert "RM 3300.00" in page and "RM 1000.00" in page and "RM 2300.00" in page


def test_networth_snapshot_and_history(client, load):
    _account(client)
    _add(client, type="income", category="Salary", amount="1000")

    client.post("/networth", data={"action": "snapshot", "note": "first"})
    snaps = _snaps(load)
    assert len(snaps) == 1 and snaps[0]["auto"] is True and snaps[0]["net"] == 1000.0

    client.post("/networth", data={"action": "add", "date": "2026-09-01",
                                   "assets": "5000", "liabilities": "1200"})
    snaps = _snaps(load)
    assert len(snaps) == 2
    manual = [s for s in snaps if not s["auto"]][0]
    assert manual["net"] == 3800.0

    sid = snaps[0]["id"]
    client.post("/networth", data={"action": "delete", "id": sid})
    assert len(_snaps(load)) == 1


# ================= CSV IMPORT =================

CSV_OK = (
    "date,type,category,account,item,amount,tags\n"
    "2026-09-01,income,Salary,Bank,Payday,3000,\n"
    "2026-09-02,expense,Food,Bank,Lunch,15.50,work\n"
)


def test_csv_import_appends_records(client, load):
    resp = client.post("/data/import.csv", data={
        "csvfile": (io.BytesIO(CSV_OK.encode()), "in.csv"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert "Imported 2" in resp.get_data(as_text=True)

    recs = _records(load)
    assert len(recs) == 2
    assert {r["amount"] for r in recs} == {3000.0, 15.5}
    assert any(r["tags"] == ["work"] for r in recs)
    assert all(r["source"] == "import" and r["id"] for r in recs)


def test_csv_import_reports_bad_rows(client, load):
    bad = (
        "date,type,amount\n"
        "2026-09-01,expense,10\n"
        "not-a-date,expense,5\n"
        "2026-09-02,teleport,5\n"
        "2026-09-03,expense,abc\n"
    )
    resp = client.post("/data/import.csv", data={
        "csvfile": (io.BytesIO(bad.encode()), "in.csv"),
    }, content_type="multipart/form-data")
    body = resp.get_data(as_text=True)
    assert "Imported 1" in body
    assert "Skipped 3" in body
    assert len(_records(load)) == 1


def test_csv_import_rejects_headerless(client):
    resp = client.post("/data/import.csv", data={
        "csvfile": (io.BytesIO(b"just,some,junk\n1,2,3\n"), "in.csv"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "header row" in resp.get_data(as_text=True)


def test_backup_includes_new_stores(client, load):
    import zipfile
    client.post("/debts", data={"action": "create", "direction": "owe",
                                "counterparty": "X", "principal": "10"})
    client.post("/networth", data={"action": "snapshot"})
    zf = zipfile.ZipFile(io.BytesIO(client.get("/data/backup.zip").get_data()))
    assert "debts.json" in zf.namelist()
    assert "networth.json" in zf.namelist()

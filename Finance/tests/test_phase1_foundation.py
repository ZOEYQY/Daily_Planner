"""Phase 1: stable transaction IDs, soft-delete/trash, backup/restore, CSV export."""
import io
import json
import zipfile


def _add(client, category="Food", amount="12.50", item="Lunch", account="Wallet"):
    return client.post("/add", data={
        "date": "2026-09-08", "type": "expense", "category": category,
        "new_account": account, "purpose": "spending", "item": item, "amount": amount,
    })


def _records(load, deleted=False):
    recs = load("expenses.json") or []
    if deleted:
        return recs
    return [r for r in recs if not r.get("deleted_at")]


# ---------- stable IDs ----------

def test_new_records_get_a_stable_id(client, load):
    _add(client, item="A")
    _add(client, item="B")
    recs = load("expenses.json")
    ids = [r["id"] for r in recs]
    assert len(ids) == 2 and len(set(ids)) == 2

    # ids survive an unrelated write (editing one record)
    client.post(f"/update/{ids[0]}", data={"amount": "99", "type": "expense", "category": "Food"})
    assert [r["id"] for r in load("expenses.json")] == ids


def test_legacy_records_without_id_are_backfilled(client, load, data_dir):
    # simulate an old file written before ids existed
    (data_dir / "expenses.json").write_text(json.dumps([
        {"date": "2026-01-01", "type": "expense", "category": "Food",
         "account": "Wallet", "item": "old", "amount": 5},
    ]), encoding="utf-8")

    client.get("/view")  # any read goes through load_records()
    rec = load("expenses.json")[0]
    assert rec.get("id")


def test_update_unknown_id_redirects(client):
    resp = client.get("/update/nope-not-real")
    assert resp.status_code == 302 and "/view" in resp.headers["Location"]


# ---------- soft delete / trash ----------

def test_delete_is_soft_and_hidden_from_totals(client, load):
    _add(client, amount="40", item="Keep")
    _add(client, amount="10", item="Remove")
    rid = next(r["id"] for r in load("expenses.json") if r["item"] == "Remove")

    resp = client.post(f"/delete/{rid}")
    assert resp.status_code == 302

    # still on disk, flagged
    raw = load("expenses.json")
    removed = next(r for r in raw if r["id"] == rid)
    assert removed["deleted_at"]

    # gone from the records list and the summary math
    view = client.get("/view").get_data(as_text=True)
    assert "Remove" not in view and "Keep" in view
    summary = client.get("/summary").get_data(as_text=True)
    assert "40.00" in summary  # only the kept expense counts


def test_trash_restore_and_purge(client, load):
    _add(client, item="Trashable")
    rid = load("expenses.json")[0]["id"]
    client.post(f"/delete/{rid}")

    trash_page = client.get("/trash").get_data(as_text=True)
    assert "Trashable" in trash_page

    client.post(f"/trash/{rid}/restore")
    assert not load("expenses.json")[0].get("deleted_at")
    assert "Trashable" in client.get("/view").get_data(as_text=True)

    # delete again, then purge for good
    client.post(f"/delete/{rid}")
    client.post(f"/trash/{rid}/purge")
    assert load("expenses.json") == []


def test_empty_trash(client, load):
    _add(client, item="X")
    _add(client, item="Y")
    for r in list(load("expenses.json")):
        client.post(f"/delete/{r['id']}")
    client.post("/trash/empty")
    assert load("expenses.json") == []


def test_goal_contribution_delete_is_soft_and_returns_to_plan(client, load):
    client.post("/goals", data={
        "action": "create", "name": "Trip", "target": "1000", "type": "short",
    })
    goal_id = load("goals.json")[0]["id"]
    client.post("/goals", data={
        "action": "save", "goal_id": str(goal_id), "goal_name": "Trip",
        "amount": "100", "account": "Bank",
    })
    rid = load("expenses.json")[0]["id"]

    resp = client.post(f"/delete/{rid}", data={"source": "goal"})
    assert resp.status_code == 302 and "/plan" in resp.headers["Location"]
    assert load("expenses.json")[0]["deleted_at"]


# ---------- CSV export ----------

def test_csv_export(client, load):
    _add(client, item="Coffee", amount="8.50")
    resp = client.get("/data/export.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers["Content-Disposition"]

    body = resp.get_data(as_text=True)
    assert body.splitlines()[0].startswith("id,date,type,category,account,item,amount")
    assert "Coffee" in body and "8.5" in body


def test_csv_export_excludes_trashed_by_default(client, load):
    _add(client, item="Live")
    _add(client, item="Dead")
    dead_id = next(r["id"] for r in load("expenses.json") if r["item"] == "Dead")
    client.post(f"/delete/{dead_id}")

    assert "Dead" not in client.get("/data/export.csv").get_data(as_text=True)
    assert "Dead" in client.get("/data/export.csv?trashed=1").get_data(as_text=True)


# ---------- backup / restore ----------

def test_backup_is_a_zip_of_the_stores(client, load):
    _add(client, item="Backed up")
    resp = client.get("/data/backup.zip")
    assert resp.status_code == 200 and resp.mimetype == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.get_data()))
    assert "expenses.json" in zf.namelist()
    assert "categories.json" in zf.namelist()
    payload = json.loads(zf.read("expenses.json"))
    assert payload[0]["item"] == "Backed up"


def test_restore_round_trips(client, load):
    _add(client, item="Original", amount="50")
    backup = client.get("/data/backup.zip").get_data()

    # change state after the backup
    _add(client, item="AddedLater", amount="999")
    assert len(_records(load)) == 2

    resp = client.post("/data/restore", data={
        "backup": (io.BytesIO(backup), "finance-backup.zip"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 302

    items = [r["item"] for r in _records(load)]
    assert items == ["Original"]


def test_restore_rejects_non_zip(client):
    resp = client.post("/data/restore", data={
        "backup": (io.BytesIO(b"not a zip"), "x.zip"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "not a valid" in resp.get_data(as_text=True).lower()


def test_restore_rejects_bad_json_in_zip(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("expenses.json", "{ this is not json")
    buf.seek(0)
    resp = client.post("/data/restore", data={
        "backup": (buf, "bad.zip"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "not valid json" in resp.get_data(as_text=True).lower()


def test_data_page_loads(client):
    assert client.get("/data").status_code == 200

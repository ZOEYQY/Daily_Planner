"""One database transaction per request: a request that changes several
stores either saves all of them or none (no half-written state)."""
import os

import pytest

from conftest import USE_DATABASE

pytestmark = pytest.mark.skipif(not USE_DATABASE, reason="transactions apply to database storage")


def _path(data_dir, profile_id, name):
    return str(data_dir / "profiles" / profile_id / name)


def test_failure_after_first_store_rolls_back_both(client, app, _patched_helpers, data_dir, profile_id):
    """Account rename rewrites the transactions, then the account list. If
    the second write fails, the first must not stay saved."""
    import finance_routes

    client.post("/accounts", data={"name": "Old Wallet", "purpose": "spending"})
    client.post("/add", data={"date": "2026-09-01", "type": "expense", "category": "Food",
                              "account": "Old Wallet", "item": "lunch", "amount": "10"})
    real_save = finance_routes.save_data

    def failing_save(path, data):
        if os.fspath(path).endswith("accounts.json"):
            raise RuntimeError("simulated crash between the two writes")
        return real_save(path, data)

    finance_routes.save_data = failing_save
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            client.post("/edit_account/Old Wallet", data={"name": "New Wallet", "purpose": "spending"})
    finally:
        finance_routes.save_data = real_save

    records = _patched_helpers.load_data(_path(data_dir, profile_id, "expenses.json"), [])
    accounts = _patched_helpers.load_data(_path(data_dir, profile_id, "accounts.json"), [])
    assert [r["account"] for r in records] == ["Old Wallet"]
    assert [a["name"] for a in accounts] == ["Old Wallet"]

    # Without the failure, the same request saves both.
    client.post("/edit_account/Old Wallet", data={"name": "New Wallet", "purpose": "spending"})
    records = _patched_helpers.load_data(_path(data_dir, profile_id, "expenses.json"), [])
    accounts = _patched_helpers.load_data(_path(data_dir, profile_id, "accounts.json"), [])
    assert [r["account"] for r in records] == ["New Wallet"]
    assert [a["name"] for a in accounts] == ["New Wallet"]


def test_crash_rolls_back_and_success_commits(app, _patched_helpers, data_dir):
    target = str(data_dir / "profiles" / "p1" / "goals.json")

    @app.route("/_test/write-then-crash")
    def write_then_crash():
        _patched_helpers.save_data(target, [{"id": 1, "name": "never saved"}])
        raise RuntimeError("unexpected bug")

    @app.route("/_test/write-ok")
    def write_ok():
        _patched_helpers.save_data(target, [{"id": 2, "name": "saved"}])
        return "ok"

    app.config["PROPAGATE_EXCEPTIONS"] = False
    plain = app.test_client()
    assert plain.get("/_test/write-then-crash").status_code == 500
    assert _patched_helpers.load_data(target, None) is None
    assert plain.get("/_test/write-ok").status_code == 200
    assert _patched_helpers.load_data(target, None) == [{"id": 2, "name": "saved"}]


def test_reads_inside_a_request_see_its_own_uncommitted_writes(app, _patched_helpers, data_dir):
    target = str(data_dir / "profiles" / "p1" / "budget.json")

    @app.route("/_test/read-own-write")
    def read_own_write():
        _patched_helpers.save_data(target, [{"category": "Food", "amount": 5.0}])
        return str(_patched_helpers.load_data(target, None))

    assert app.test_client().get("/_test/read-own-write").get_data(as_text=True) == \
        "[{'category': 'Food', 'amount': 5.0}]"

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session


@pytest.fixture
def db(tmp_path, monkeypatch):
    import database

    monkeypatch.setenv("DATABASE_URL", URL.create(
        "sqlite", database=str(tmp_path / "tables.sqlite")).render_as_string(hide_password=False))
    database._engine.cache_clear()
    yield database
    database._engine.cache_clear()


def _sql(database, statement, **params):
    with Session(database._engine(database.database_url())) as session:
        return session.execute(text(statement), params).all()


STORE_PAYLOADS = {
    "expenses.json": [
        {"id": "t1", "date": "2026-09-02", "type": "income", "category": "Salary", "account": "TNG",
         "item": "Tutoring 陈", "amount": 45.0, "receipt": "a.jpeg", "tags": ["work", "cash"]},
        {"id": "t2", "date": "2026-09-03", "type": "expense", "category": "Goal Savings", "goal_id": 1,
         "account": "TNG", "item": "Goal: Trip", "amount": 100},
        {"id": "t3", "date": "2026-09-04", "type": "expense", "category": "Food", "account": "TNG",
         "item": None, "amount": 12.5, "receipt": None, "tags": [], "deleted_at": "2026-09-05T10:00:00",
         "currency": "RM"},
    ],
    "budget.json": [{"category": "Food", "amount": 100.0, "period": "weekly", "rollover": False}],
    "accounts.json": [{"name": "TNG", "purpose": "spending", "initial_amount": 50.0}],
    "goals.json": [{"id": 1, "name": "Trip", "type": "saving", "target": 500.0, "target_date": None,
                    "priority": "medium", "notes": "", "status": "In Progress"}],
    "categories.json": {"schema_version": 1, "categories": [
        {"id": "c1", "name": "Food", "kind": "expense", "icon": "", "color": "#fff", "order": 0,
         "archived": False, "is_default": True, "created_at": "2026-09-09"}]},
    "shopping.json": {"schema_version": 1, "items": [
        {"id": "s1", "name": "Shoes", "estimated_price": 200.0, "actual_price": None, "target_date": "",
         "ai_suggestion": {"recommendation": "wait", "confidence": 0.4}, "wait_days": 3,
         "price_checks": [{"id": "p1", "date": "2026-09-01", "price": 189.9, "source": "shop", "note": ""}]},
        {"id": "s2", "name": "Bag", "price_checks": []},
        {"id": "s3", "name": "Hat"},
    ]},
    "recurring.json": {"schema_version": 1, "rules": [
        {"id": "r1", "type": "expense", "amount": 30.0, "tags": [], "frequency": "monthly", "interval": 1,
         "start_date": "2026-01-31", "end_date": "", "next_due": "2026-10-31", "last_posted": "",
         "active": True, "auto_post": False}]},
    "debts.json": {"schema_version": 1, "debts": [
        {"id": "d1", "direction": "owe", "counterparty": "Ann", "principal": 300.0, "date": "2026-09-01",
         "due_date": "", "status": "open", "payments": [
             {"id": "dp1", "date": "2026-09-10", "amount": 100.0, "note": "", "record_id": ""},
             {"id": "dp2", "date": "2026-09-20", "amount": 50.0, "note": "half", "record_id": "t9"}]}]},
    "networth.json": {"schema_version": 1, "snapshots": [
        {"id": "n1", "date": "2026-09-30", "assets": 1000.0, "liabilities": 200.0, "net": 800.0,
         "note": "", "auto": True, "breakdown": {"accounts": [["TNG", 50.0]]}}]},
    "insights.json": {"schema_version": 1, "reviews": {
        "2026-09": {"narrative": "Good month", "suggestions": ["Cook more"], "generated_at": "2026-10-01"},
        "forecast-2026-10": {"estimate": 900.0, "low": 800, "source": "ai"}}},
    "rates.json": {"schema_version": 1, "base": "MYR", "rates": [
        {"code": "USD", "name": "US Dollar", "rate_to_myr": 4.7, "updated_at": "2026-09-09"}]},
}


def test_every_store_round_trips_through_tables(db):
    db.save_document("profiles.json", {"schema_version": 1, "profiles": [
        {"id": "pa", "name": "ZOEY", "created_at": "2026-09-01", "password_hash": "scrypt:x"}]})
    for name, payload in STORE_PAYLOADS.items():
        db.save_document(f"profiles/pa/{name}", payload)

    assert db.legacy_document_keys() == []
    assert db.load_document("profiles.json", None)["profiles"][0]["name"] == "ZOEY"
    for name, payload in STORE_PAYLOADS.items():
        assert db.load_document(f"profiles/pa/{name}", None) == payload, name
        assert db.load_document(f"profiles/pb/{name}", "missing") == "missing"


def test_records_are_queryable_rows(db):
    db.save_document("profiles/pa/expenses.json", STORE_PAYLOADS["expenses.json"])
    db.save_document("profiles/pb/expenses.json", [{"id": "x", "type": "expense", "amount": 999.0}])
    db.save_document("profiles/pa/debts.json", STORE_PAYLOADS["debts.json"])

    assert _sql(db, """
        SELECT type, SUM(amount) FROM finance_transactions
        WHERE profile_id = :p AND deleted_at IS NULL GROUP BY type ORDER BY type
    """, p="pa") == [("expense", 100.0), ("income", 45.0)]
    assert _sql(db, "SELECT goal_id FROM finance_transactions WHERE id = 't2'") == [(1,)]
    assert _sql(db, """
        SELECT d.counterparty, d.principal - SUM(p.amount)
        FROM finance_debts d JOIN finance_debt_payments p
          ON p.profile_id = d.profile_id AND p.parent_position = d.position
        GROUP BY d.counterparty, d.principal
    """) == [("Ann", 150.0)]


def test_saving_replaces_rows_and_profile_delete_clears_them(db):
    key = "profiles/pa/debts.json"
    db.save_document(key, STORE_PAYLOADS["debts.json"])
    db.save_document(key, {"schema_version": 1, "debts": []})
    assert db.load_document(key, None) == {"schema_version": 1, "debts": []}
    assert _sql(db, "SELECT COUNT(*) FROM finance_debt_payments") == [(0,)]

    for name, payload in STORE_PAYLOADS.items():
        db.save_document(f"profiles/pa/{name}", payload)
        db.save_document(f"profiles/pb/{name}", payload)
    db.delete_documents("profiles/pa/")
    for name, payload in STORE_PAYLOADS.items():
        assert db.load_document(f"profiles/pa/{name}", None) is None
        assert db.load_document(f"profiles/pb/{name}", None) == payload


def test_unfitting_payloads_fall_back_to_documents_and_back(db):
    key = "profiles/pa/expenses.json"
    db.save_document(key, {"not": "a list"})
    assert db.legacy_document_keys() == [key]
    assert db.load_document(key, None) == {"not": "a list"}

    db.save_document(key, STORE_PAYLOADS["expenses.json"])
    assert db.legacy_document_keys() == []
    assert db.load_document(key, None) == STORE_PAYLOADS["expenses.json"]

    db.save_document(key, [1, 2])
    assert db.load_document(key, None) == [1, 2]
    assert _sql(db, "SELECT COUNT(*) FROM finance_transactions") == [(0,)]

    db.save_document("profiles/pa/notes.json", {"free": "form"})
    assert db.load_document("profiles/pa/notes.json", None) == {"free": "form"}


def test_legacy_documents_are_read_then_converted(db, monkeypatch, capsys):
    import sys

    import migrate_documents_to_tables
    from database import FinanceDocument

    with Session(db._engine(db.database_url())) as session:
        for name, payload in STORE_PAYLOADS.items():
            session.add(FinanceDocument(key=f"profiles/pa/{name}", payload=payload))
        session.add(FinanceDocument(key="profiles/pa/notes.json", payload={"free": "form"}))
        session.commit()

    assert db.load_document("profiles/pa/rates.json", None) == STORE_PAYLOADS["rates.json"]
    assert db.import_documents({"profiles/pa/rates.json": {}}) == (0, 1)

    monkeypatch.setattr(sys, "argv", ["migrate", "--apply"])
    migrate_documents_to_tables.main()
    assert "Converted 11 Finance documents (1 left as JSON) and 0 Calendar states" in capsys.readouterr().out
    assert db.legacy_document_keys() == ["profiles/pa/notes.json"]
    for name, payload in STORE_PAYLOADS.items():
        assert db.load_document(f"profiles/pa/{name}", None) == payload

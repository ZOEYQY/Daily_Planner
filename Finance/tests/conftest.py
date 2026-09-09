"""Shared pytest fixtures for the Finance module.

Each test gets a throwaway data directory: the JSON path constants on
``finance_helpers`` / ``finance_routes`` are monkeypatched to point inside
a per-test ``tmp_path`` so nothing touches ``Finance/data/``.
"""
import json
import os
import sys

import pytest

FIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FIN_DIR not in sys.path:
    sys.path.insert(0, FIN_DIR)


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def client(data_dir, tmp_path, monkeypatch):
    import finance_helpers
    import finance_routes

    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir()

    monkeypatch.setattr(finance_helpers, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(finance_helpers, "f_categories", str(data_dir / "categories.json"))
    monkeypatch.setattr(finance_helpers, "f_shopping", str(data_dir / "shopping.json"))
    monkeypatch.setattr(finance_helpers, "f_recurring", str(data_dir / "recurring.json"))
    monkeypatch.setattr(finance_helpers, "f_debts", str(data_dir / "debts.json"))
    monkeypatch.setattr(finance_helpers, "f_networth", str(data_dir / "networth.json"))
    monkeypatch.setattr(finance_helpers, "f_insights", str(data_dir / "insights.json"))
    monkeypatch.setattr(finance_helpers, "f_rates", str(data_dir / "rates.json"))

    monkeypatch.setattr(finance_routes, "f_expense", str(data_dir / "expenses.json"))
    monkeypatch.setattr(finance_routes, "f_budget", str(data_dir / "budget.json"))
    monkeypatch.setattr(finance_routes, "f_accounts", str(data_dir / "accounts.json"))
    monkeypatch.setattr(finance_routes, "f_goals", str(data_dir / "goals.json"))
    monkeypatch.setattr(finance_routes, "RECEIPTS_DIR", str(receipts_dir))

    from flask import Flask

    app = Flask(
        __name__,
        template_folder=os.path.join(FIN_DIR, "templates"),
        static_folder=os.path.join(FIN_DIR, "static"),
    )
    app.register_blueprint(finance_routes.finance_bp)
    app.config.update(TESTING=True)

    return app.test_client()


@pytest.fixture
def load(data_dir):
    """Read one of the JSON stores back, or ``None`` if it was never written."""
    def _load(name):
        path = data_dir / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    return _load


@pytest.fixture
def make_account(client):
    def _make(name, purpose="spending"):
        resp = client.post("/accounts", data={"name": name, "purpose": purpose})
        assert resp.status_code in (200, 302)
        return name
    return _make


def categories_list(load):
    """Helper: the stored category records (after seeding)."""
    stored = load("categories.json")
    return stored["categories"] if stored else []


def category_id(load, name, kind):
    for c in categories_list(load):
        if c["name"] == name and c["kind"] == kind:
            return c["id"]
    raise AssertionError(f"category {name!r} ({kind}) not found")


def shopping_items(load):
    stored = load("shopping.json")
    return stored["items"] if stored else []

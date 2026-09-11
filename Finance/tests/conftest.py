"""Shared pytest fixtures for the Finance module.

Each test gets a throwaway data directory, and the `client` fixture is
pre-activated as a single profile ("Test User") — created via a real POST to
/profiles/create before the test body runs, so the session cookie already
carries an active profile_id and every existing test (written before
profiles existed) keeps working unchanged. Tests that specifically exercise
multi-profile behavior create additional profiles/clients of their own.
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
def _patched_helpers(data_dir, tmp_path, monkeypatch):
    """Repoints every root path finance_helpers computes paths from. Per-
    profile store paths (f_categories, f_expense, ...) are no longer patched
    directly — they're reassigned per-request by finance_routes' before_request
    hook once these roots point into the test's tmp dir."""
    import finance_helpers

    receipts_root = tmp_path / "receipts"
    receipts_root.mkdir()

    monkeypatch.setattr(finance_helpers, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(finance_helpers, "PROFILES_ROOT", str(data_dir / "profiles"))
    monkeypatch.setattr(finance_helpers, "PROFILES_INDEX_FILE", str(data_dir / "profiles.json"))
    monkeypatch.setattr(finance_helpers, "RECEIPTS_ROOT", str(receipts_root))
    return finance_helpers


@pytest.fixture
def app(_patched_helpers):
    import finance_routes
    from flask import Flask

    flask_app = Flask(
        __name__,
        template_folder=os.path.join(FIN_DIR, "templates"),
        static_folder=os.path.join(FIN_DIR, "static"),
    )
    flask_app.secret_key = "test-secret"
    flask_app.register_blueprint(finance_routes.finance_bp)
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    c = app.test_client()
    # Activates a single default profile so every pre-existing test (written
    # before profiles existed) keeps working without touching /profiles itself.
    resp = c.post("/profiles/create", data={"name": "Test User"})
    assert resp.status_code == 302
    return c


@pytest.fixture
def profile_id(_patched_helpers, client):
    """The id of the auto-created "Test User" profile the `client` fixture activated."""
    return _patched_helpers.load_profiles()[0]["id"]


@pytest.fixture
def load(data_dir, profile_id):
    """Read one of the active profile's JSON stores back, or ``None`` if it
    was never written."""
    def _load(name):
        path = data_dir / "profiles" / profile_id / name
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

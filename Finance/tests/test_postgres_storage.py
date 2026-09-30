from sqlalchemy.engine import URL


def test_flat_json_migration_and_database_storage(tmp_path, monkeypatch):
    import database
    import finance_helpers
    from migrate_json_to_postgres import collect_documents

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    expense = [{"id": "txn-1", "amount": 12.5, "tags": ["food"]}]
    (data_dir / "expenses.json").write_text(
        '[{"id":"txn-1","amount":12.5,"tags":["food"]}]', encoding="utf-8")
    monkeypatch.setenv(
        "DATABASE_URL",
        URL.create("sqlite", database=str(tmp_path / "test.sqlite")).render_as_string(hide_password=False),
    )
    database._engine.cache_clear()
    monkeypatch.setattr(finance_helpers, "DATA_DIR", str(data_dir))

    documents = collect_documents(str(data_dir))
    profile = documents["profiles.json"]["profiles"][0]
    store_key = f"profiles/{profile['id']}/expenses.json"
    assert documents[store_key] == expense

    assert database.import_documents(documents) == (2, 0)
    assert finance_helpers.load_data(
        str(data_dir / "profiles" / profile["id"] / "expenses.json"), []) == expense
    assert database.import_documents(documents) == (0, 2)
    database._engine.cache_clear()

"""Import existing Finance JSON stores into the configured SQLAlchemy database.

Preview with ``python Finance/migrate_json_to_postgres.py`` and apply with
``python Finance/migrate_json_to_postgres.py --apply``.
"""
import argparse
import json
import os
import uuid
from datetime import date

import database


FINANCE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(FINANCE_DIR, "data")
STORE_NAMES = (
    "expenses.json", "budget.json", "accounts.json", "goals.json",
    "categories.json", "shopping.json", "recurring.json", "debts.json",
    "networth.json", "insights.json", "rates.json",
)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def _legacy_profile_id(data_dir):
    return uuid.uuid5(uuid.NAMESPACE_URL, os.path.abspath(data_dir)).hex


def collect_documents(data_dir=DATA_DIR):
    documents = {}
    profiles_path = os.path.join(data_dir, "profiles.json")
    profiles_root = os.path.join(data_dir, "profiles")
    flat_stores = {
        name: os.path.join(data_dir, name)
        for name in STORE_NAMES
        if os.path.isfile(os.path.join(data_dir, name))
    }

    if os.path.isfile(profiles_path):
        profiles_document = _read_json(profiles_path)
        profiles = profiles_document.get("profiles", []) if isinstance(profiles_document, dict) else []
        if not profiles and flat_stores:
            profile_id = _legacy_profile_id(data_dir)
            profiles_document = {
                "schema_version": 1,
                "profiles": [{
                    "id": profile_id,
                    "name": "My Finance",
                    "created_at": date.today().isoformat(),
                }],
            }
            profiles = profiles_document["profiles"]
        documents["profiles.json"] = profiles_document
        for profile in profiles:
            profile_id = profile.get("id") if isinstance(profile, dict) else None
            if not profile_id or os.path.basename(profile_id) != profile_id:
                continue
            profile_dir = os.path.join(profiles_root, profile_id)
            if not os.path.isdir(profile_dir):
                continue
            for filename in STORE_NAMES:
                path = os.path.join(profile_dir, filename)
                if os.path.isfile(path):
                    documents[f"profiles/{profile_id}/{filename}"] = _read_json(path)
        if profiles and flat_stores:
            profile_id = profiles[0].get("id")
            for filename, path in flat_stores.items():
                key = f"profiles/{profile_id}/{filename}"
                if key not in documents:
                    documents[key] = _read_json(path)
        return documents

    if flat_stores:
        profile_id = _legacy_profile_id(data_dir)
        documents["profiles.json"] = {
            "schema_version": 1,
            "profiles": [{
                "id": profile_id,
                "name": "My Finance",
                "created_at": date.today().isoformat(),
            }],
        }
        for filename, path in flat_stores.items():
            documents[f"profiles/{profile_id}/{filename}"] = _read_json(path)

    return documents


def main():
    parser = argparse.ArgumentParser(description="Migrate Finance JSON stores into PostgreSQL.")
    parser.add_argument("--apply", action="store_true", help="write documents to the configured database")
    parser.add_argument("--replace", action="store_true", help="overwrite documents already in the database")
    args = parser.parse_args()

    if args.apply and not database.configured():
        parser.error("DATABASE_URL is required; configure it in the environment or Finance/.env")

    documents = collect_documents()
    if not documents:
        print(f"No Finance JSON data found under {DATA_DIR}")
        return
    print(f"Found {len(documents)} JSON documents:")
    for key in sorted(documents):
        print(f"  {key}")

    if not args.apply:
        print("Preview only. Run again with --apply to import; source JSON files will remain unchanged.")
        return

    imported, skipped = database.import_documents(documents, replace=args.replace)
    print(f"Imported or updated: {imported}; skipped because the database already had them: {skipped}")


if __name__ == "__main__":
    main()

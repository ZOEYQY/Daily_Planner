"""Per-account health check of the database in DATABASE_URL, optionally
compared with a backup file. Used after a restore (see DATABASE.md,
EMERGENCY DATA RECOVERY) and safe to run any time: it only reads.

    python scripts/verify_database.py
    python scripts/verify_database.py --against backup/database/<file>.json.gz.enc

For every Profile it reports transactions, income and expense totals,
accounts, budgets, debts, Habits, check-ins and Calendar items. With
--against, the same figures are computed from the backup's rows and any
difference is listed (exit code 1).
"""
import argparse
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

from sqlalchemy import select  # noqa: E402

from Finance import database  # noqa: E402

COUNTED = {  # table -> column holding the owner
    "finance_transactions": "profile_id", "finance_accounts": "profile_id", "finance_budgets": "profile_id",
    "finance_goals": "profile_id", "finance_debts": "profile_id", "finance_debt_payments": "profile_id",
    "finance_categories": "profile_id", "finance_shopping_items": "profile_id",
    "finance_recurring_rules": "profile_id", "calendar_tasks": "profile_id", "calendar_events": "profile_id",
    "calendar_special_days": "profile_id", "habits": "owner", "receipt_assets": "profile_id",
}


def _figures(tables):
    """{profile id: {...}} from a {table name: [row dicts]} mapping."""
    figures = defaultdict(lambda: defaultdict(float))
    names = {}
    for row in tables.get("finance_profiles", []):
        names[row.get("id")] = row.get("name")
    habit_owner = {row["id"]: row["owner"] for row in tables.get("habits", [])}
    for table, owner in COUNTED.items():
        for row in tables.get(table, []):
            figures[row[owner]][table] += 1
    for row in tables.get("habit_checkins", []):
        figures[habit_owner.get(row["habit_id"])]["habit_checkins"] += 1
    for row in tables.get("finance_transactions", []):
        if (row.get("deleted_at") or "") == "" and row.get("amount") is not None and row.get("type") in ("income", "expense"):
            figures[row["profile_id"]][row["type"]] += row["amount"]
    result = {}
    for profile_id, values in figures.items():
        values = {k: (round(v, 2) if k in ("income", "expense") else int(v)) for k, v in values.items()}
        result[profile_id] = values
    for profile_id in names:
        result.setdefault(profile_id, {})
    return names, result


def database_tables():
    database._tables()
    database.calendar_tables()
    metadata = database.Base.metadata
    wanted = set(COUNTED) | {"finance_profiles", "habit_checkins"}
    with database._engine(database.database_url()).connect() as connection:
        return {name: [dict(r) for r in connection.execute(select(metadata.tables[name])).mappings()]
                for name in wanted}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Per-account database health check.")
    parser.add_argument("--against", help="backup file to compare with")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, "backup", "backup.env"))
    except ImportError:
        pass
    if not database.configured():
        parser.error("DATABASE_URL is not set.")

    names, live = _figures(database_tables())
    print(f"{len(names)} profile(s) in the database")
    for profile_id, values in sorted(live.items(), key=lambda item: names.get(item[0]) or ""):
        label = names.get(profile_id, f"(no profile) {profile_id}")
        summary = ", ".join(f"{k.replace('finance_', '')}={v}" for k, v in sorted(values.items()))
        print(f"  {label}: {summary or 'no data'}")
    orphans = [pid for pid in live if pid not in names]
    if orphans:
        print(f"WARNING: rows owned by {len(orphans)} id(s) with no profile: {orphans}")

    if args.against:
        import backup_database

        document = backup_database.read_backup(args.against)
        tables = {name: [{k: backup_database._decode(_column(name, k), v) for k, v in row.items()} for row in rows]
                  for name, rows in document["tables"].items()}
        backup_names, expected = _figures(tables)
        problems = []
        if backup_names != names:
            problems.append("profile list differs")
        for profile_id in set(expected) | set(live):
            if expected.get(profile_id, {}) != live.get(profile_id, {}):
                problems.append(f"{names.get(profile_id, profile_id)}: backup {expected.get(profile_id)} "
                                f"vs database {live.get(profile_id)}")
        if problems:
            print("MISMATCH against the backup:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(f"MATCH: every profile's counts and totals equal the backup ({document['created_at']}).")
    return 0


def _column(table, key):
    return database.Base.metadata.tables[table].c[key]


if __name__ == "__main__":
    sys.exit(main())

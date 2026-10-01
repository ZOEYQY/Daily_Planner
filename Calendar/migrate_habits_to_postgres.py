"""Import an existing local Habit SQLite database into the shared PostgreSQL database."""
import argparse
import datetime as dt
import os
import sqlite3
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from Finance import database


def migrate(sqlite_path, default_profile_id=None):
    if not database.configured():
        raise RuntimeError("DATABASE_URL must point to the shared PostgreSQL database.")
    database.require_postgres()
    if not os.path.isfile(sqlite_path):
        raise FileNotFoundError(sqlite_path)

    profiles_document = database.load_document("profiles.json", {})
    profiles = profiles_document.get("profiles", []) if isinstance(profiles_document, dict) else []
    known_profiles = {p.get("id") for p in profiles if isinstance(p, dict) and p.get("id")}
    default_owner = default_profile_id or (next(iter(known_profiles)) if len(known_profiles) == 1 else None)
    if default_owner and default_owner not in known_profiles:
        raise RuntimeError(f"Default Profile {default_owner} does not exist in PostgreSQL.")

    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    columns = {row[1] for row in source.execute("PRAGMA table_info(habits)")}
    habit_rows = source.execute("SELECT * FROM habits ORDER BY id").fetchall()
    checkin_rows = source.execute("SELECT habit_id, date, note FROM checkins ORDER BY habit_id, date").fetchall()
    source.close()

    from Finance.database import Habit, HabitCheckin

    with Session(database._engine(database.database_url())) as target:
        owners = {row["owner"] if "owner" in columns and row["owner"] else default_owner for row in habit_rows}
        if None in owners:
            raise RuntimeError("Legacy habits need a default owner; import Profiles first or specify a single Profile.")
        unknown = owners - known_profiles
        if unknown:
            raise RuntimeError(f"SQLite contains Habit owners absent from PostgreSQL Profiles: {sorted(unknown)}")
        for owner in owners:
            if target.scalar(select(Habit.id).where(Habit.owner == owner).limit(1)) is not None:
                raise RuntimeError(f"PostgreSQL already contains habits for Profile {owner}; refusing duplicate import.")

        id_map = {}
        for old in habit_rows:
            owner = old["owner"] if "owner" in columns and old["owner"] else default_owner
            habit = Habit(
                owner=owner,
                name=old["name"],
                emoji=old["emoji"] or "",
                color=old["color"] or "#3a9163",
                sort=old["sort"] or 0,
                archived=bool(old["archived"]),
                created_at=dt.datetime.fromisoformat(old["created_at"]) if old["created_at"] else dt.datetime.now(dt.timezone.utc),
            )
            target.add(habit)
            target.flush()
            id_map[old["id"]] = habit.id

        target.add_all([
            HabitCheckin(habit_id=id_map[row["habit_id"]], date=row["date"], note=row["note"] or "")
            for row in checkin_rows if row["habit_id"] in id_map
        ])
        target.commit()

    return len(habit_rows), len(checkin_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, help="Path to the old .data/habits.db file")
    parser.add_argument("--default-profile-id", help="Required only when unassigned habits exist and PostgreSQL has multiple Profiles")
    args = parser.parse_args()
    habits, checkins = migrate(os.path.abspath(args.sqlite), args.default_profile_id)
    print(f"Imported {habits} habits and {checkins} check-ins. SQLite source was not changed.")


if __name__ == "__main__":
    main()

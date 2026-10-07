"""Move Finance stores and Calendar states from JSON blobs into the tables.

Data saved before the tables existed sits in ``finance_documents`` (one JSON
blob per Finance store) and ``calendar_states.payload`` (one blob per
profile's Calendar). The app already reads those and converts each one the
next time it is saved; this script converts everything at once.

Preview with ``python Finance/migrate_documents_to_tables.py`` and apply with
``python Finance/migrate_documents_to_tables.py --apply``. Each store is
checked to read back identically before its JSON document is removed; stores
that don't fit the tables are left as documents and listed.
"""
import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

import calendar_tables
import database
import finance_tables


def _legacy_calendar_rows(session):
    converted = select(calendar_tables.lists_table.c.profile_id)
    return session.scalars(select(database.CalendarState).where(
        database.CalendarState.profile_id.not_in(converted)).order_by(database.CalendarState.profile_id)).all()


def migrate_calendar(apply):
    """Convert legacy Calendar states; returns (converted, kept as JSON)."""
    converted = kept = 0
    with Session(database._engine(database.database_url())) as session:
        for row in _legacy_calendar_rows(session):
            state = row.payload
            if calendar_tables._prepare(row.profile_id, state) is None:
                print(f"  keep     calendar state for {row.profile_id} (its shape doesn't fit)")
                kept += 1
                continue
            print(f"  convert  calendar state for {row.profile_id}")
            converted += 1
            if apply:
                calendar_tables.store_state(session, row, state)
                session.flush()
                if calendar_tables.load_state(session, row) != state:
                    raise SystemExit(f"Calendar state for {row.profile_id} did not read back identically; nothing was changed.")
        if apply:
            session.commit()
    return converted, kept


def main():
    parser = argparse.ArgumentParser(description="Convert Finance JSON documents into SQL tables.")
    parser.add_argument("--apply", action="store_true", help="write the converted rows to the database")
    args = parser.parse_args()

    if not database.configured():
        parser.error("DATABASE_URL is required; configure it in the environment or Finance/.env")

    calendar_converted, calendar_kept = migrate_calendar(args.apply)
    keys = database.legacy_document_keys()

    convertible = []
    for key in keys:
        payload = database.load_document(key, None)
        if finance_tables.can_store(key, payload):
            convertible.append((key, payload))
            print(f"  convert  {key}")
        else:
            print(f"  keep     {key} (not a table-backed store, or its shape doesn't fit)")

    if not args.apply:
        print(f"Preview only: {len(convertible)} of {len(keys)} Finance documents and "
              f"{calendar_converted} of {calendar_converted + calendar_kept} Calendar states can be converted. "
              "Run again with --apply. Back up the database first.")
        return

    for key, payload in convertible:
        database.save_document(key, payload)
        if database.load_document(key, None) != payload:
            raise SystemExit(f"{key} did not read back identically; stopping. Restore from your backup.")
    print(f"Converted {len(convertible)} Finance documents ({len(keys) - len(convertible)} left as JSON) "
          f"and {calendar_converted} Calendar states ({calendar_kept} left as JSON).")


if __name__ == "__main__":
    main()

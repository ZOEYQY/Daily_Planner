"""Migrate every piece of local Daily Planner data into the PostgreSQL database
named by ``DATABASE_URL``.

What it moves:

* Finance JSON stores under ``Finance/data`` (the profiles index, each
  profile's ``profiles/<id>/*.json``, and pre-profile flat ``*.json`` files);
* Habits and check-ins from the local SQLite file ``.data/habits.db``;
* any Finance store or Calendar state that is still a whole JSON blob in the
  database (written before the relational tables existed) is converted to rows.

Safety:

* Dry run by default. Nothing is written without ``--apply``.
* Every source file is validated first; any problem aborts before writing.
* Source files are only read, never modified or deleted.
* Re-running is safe. Profiles are merged by id; a Profile whose name is
  already used by a different account is skipped and reported, never merged
  or overwritten. A store or a Profile's Habits that already exist in the
  database are skipped, so no duplicates are created.
* Every imported store is read back and compared with its source.
* A ``password`` stored in plain text (never written by this app, but checked
  anyway) is replaced by a Werkzeug hash, so the same password still works.

Usage (from the repository root)::

    python scripts/migrate_json_to_postgres.py            # preview
    python scripts/migrate_json_to_postgres.py --apply    # migrate

Back up the database first (scripts/backup_database.py).
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from Finance import calendar_tables, database, finance_tables  # noqa: E402
from Finance.migrate_json_to_postgres import DATA_DIR as FINANCE_DATA_DIR  # noqa: E402
from Finance.migrate_json_to_postgres import collect_documents  # noqa: E402

DEFAULT_HABITS_DB = os.path.join(ROOT, ".data", "habits.db")
PROFILES_KEY = "profiles.json"
_HASH_METHODS = ("scrypt:", "pbkdf2:", "argon2", "bcrypt")

# Human labels for the "Other records" breakdown.
STORE_LABELS = {
    "budget.json": "budgets", "accounts.json": "accounts", "goals.json": "goals",
    "categories.json": "categories", "shopping.json": "shopping items",
    "recurring.json": "recurring rules", "debts.json": "debts",
    "networth.json": "net-worth snapshots", "insights.json": "AI insights",
    "rates.json": "currency rates",
}
CHILD_LABELS = {"payments": "debt payments", "price_checks": "shopping price checks"}


class MigrationError(Exception):
    def __init__(self, problems, written=False):
        super().__init__(problems)
        self.problems = problems
        self.written = written


def _engine():
    return database._engine(database.database_url())


# ---------- validation ----------

def _looks_hashed(value):
    return isinstance(value, str) and value.startswith(_HASH_METHODS) and value.count("$") >= 2


def _secure_password(profile, notes):
    """Never store a plaintext password: hash any found, keeping the login."""
    plain = profile.pop("password", None)
    current = profile.get("password_hash")
    if current is not None and not _looks_hashed(current):
        plain, current = current, None
        profile.pop("password_hash")
    if current is None and isinstance(plain, str) and plain:
        profile["password_hash"] = generate_password_hash(plain)
        notes.append(f"Profile {profile['name']!r}: plaintext password replaced with a secure hash.")


def validate(documents):
    """(profiles, {store key: payload}, notes). Raises MigrationError listing
    every problem found, before anything is written."""
    problems, notes = [], []
    profiles_doc = documents.get(PROFILES_KEY, {"profiles": []})
    raw_profiles = profiles_doc.get("profiles") if isinstance(profiles_doc, dict) else None
    if not isinstance(raw_profiles, list):
        raise MigrationError(["profiles.json: expected an object with a \"profiles\" list."])

    profiles, seen_ids, seen_names = [], set(), set()
    for index, profile in enumerate(raw_profiles):
        where = f"profiles.json profile #{index + 1}"
        if not isinstance(profile, dict):
            problems.append(f"{where}: not an object.")
            continue
        profile = dict(profile)
        profile_id, name = profile.get("id"), profile.get("name")
        if not isinstance(profile_id, str) or not profile_id or "/" in profile_id or "\\" in profile_id \
                or len(profile_id) > finance_tables.PROFILE_ID_MAX:
            problems.append(f"{where}: invalid id {profile_id!r}.")
            continue
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{where}: missing name.")
            continue
        if profile_id in seen_ids:
            problems.append(f"{where}: duplicate id {profile_id}.")
            continue
        if name.casefold() in seen_names:
            problems.append(f"{where}: duplicate name {name!r}.")
            continue
        seen_ids.add(profile_id)
        seen_names.add(name.casefold())
        _secure_password(profile, notes)
        profiles.append(profile)

    stores = {}
    for key, payload in sorted(documents.items()):
        if key == PROFILES_KEY:
            continue
        parts = key.split("/")
        if len(parts) != 3 or parts[0] != "profiles" or parts[2] not in finance_tables.STORES:
            problems.append(f"{key}: unexpected file.")
        elif parts[1] not in seen_ids:
            problems.append(f"{key}: belongs to no Profile in profiles.json.")
        elif not isinstance(payload, (list, dict)):
            problems.append(f"{key}: expected a JSON list or object, found {type(payload).__name__}.")
        else:
            stores[key] = payload
    if problems:
        raise MigrationError(problems)
    return profiles, stores, notes


def count_records(filename, payload):
    """(records, Counter of child records) in one store payload."""
    spec = finance_tables.STORES[filename]
    unpacked = finance_tables._unpack(spec, payload)
    if unpacked is None:
        return 0, Counter()
    children = Counter()
    for _, record in unpacked[2]:
        for name in spec.children:
            if isinstance(record.get(name), list):
                children[name] += len(record[name])
    return len(unpacked[2]), children


# ---------- steps ----------

def merge_profiles(profiles, apply):
    """{profile id: "new" | "present" | "conflict"}. New Profiles are appended
    to the database's index under a lock; existing ones are never changed."""
    def _plan(raw):
        current = list(raw["profiles"]) if isinstance(raw, dict) and isinstance(raw.get("profiles"), list) else []
        ids = {p.get("id") for p in current if isinstance(p, dict)}
        names = {p.get("name", "").casefold(): p.get("id") for p in current if isinstance(p, dict)}
        status, added = {}, []
        for profile in profiles:
            if profile["id"] in ids:
                status[profile["id"]] = "present"
            elif names.get(profile["name"].casefold()) not in (None, profile["id"]):
                status[profile["id"]] = "conflict"
            else:
                status[profile["id"]] = "new"
                added.append(profile)
        if not added or not apply:
            return None, status
        document = dict(raw) if isinstance(raw, dict) else {"schema_version": 1}
        document["profiles"] = current + added
        return document, status

    if apply:
        return database.update_document(PROFILES_KEY, None, _plan)
    return _plan(database.load_document(PROFILES_KEY, None))[1]


def import_stores(stores, apply):
    """(imported keys, keys already in the database)."""
    imported, present = [], []
    with Session(_engine()) as session:
        for key, payload in stores.items():
            if database._document_exists(session, key):
                present.append(key)
                continue
            imported.append(key)
            if apply:
                database._store_document(session, key, payload)
        if apply:
            session.commit()
    if apply:
        for key in imported:
            if database.load_document(key, None) != stores[key]:
                raise MigrationError([f"{key}: did not read back identically after import."], written=True)
    return imported, present


def _read_habits(sqlite_path):
    if not sqlite_path or not os.path.isfile(sqlite_path):
        return [], [], False
    source = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "habits" not in tables:
            return [], [], True
        columns = {row[1] for row in source.execute("PRAGMA table_info(habits)")}
        habits = [dict(row) for row in source.execute("SELECT * FROM habits ORDER BY id")]
        checkins = [dict(row) for row in source.execute(
            "SELECT habit_id, date, note FROM checkins ORDER BY habit_id, date")] if "checkins" in tables else []
    finally:
        source.close()
    for habit in habits:
        habit["owner"] = habit.get("owner") if "owner" in columns else ""
    return habits, checkins, True


def _created_at(value):
    if isinstance(value, str) and value:
        try:
            return dt.datetime.fromisoformat(value)
        except ValueError:
            pass
    return dt.datetime.now(dt.timezone.utc)


def import_habits(sqlite_path, default_profile_id, apply, incoming_ids=()):
    """Stats dict. Each Profile's Habits are imported once: a Profile that
    already has Habits in the database is skipped."""
    from Finance.database import Habit, HabitCheckin

    habits, checkins, found = _read_habits(sqlite_path)
    stats = {"found": found, "habits": 0, "checkins": 0, "skipped_owners": [], "unowned": 0}
    if not habits:
        return stats
    stored = database.load_document(PROFILES_KEY, {})
    known = {p.get("id") for p in (stored.get("profiles", []) if isinstance(stored, dict) else [])
             if isinstance(p, dict)} | set(incoming_ids)
    if not default_profile_id and len(known) == 1:
        default_profile_id = next(iter(known))
    by_owner = {}
    for habit in habits:
        owner = habit["owner"] or default_profile_id
        if not owner or owner not in known:
            stats["unowned"] += 1
            continue
        by_owner.setdefault(owner, []).append(habit)
    checkins_by_habit = {}
    for checkin in checkins:
        checkins_by_habit.setdefault(checkin["habit_id"], []).append(checkin)

    with Session(_engine()) as session:
        for owner, owned in by_owner.items():
            if session.scalar(select(Habit.id).where(Habit.owner == owner).limit(1)) is not None:
                stats["skipped_owners"].append(owner)
                stats["skipped_habits"] = stats.get("skipped_habits", 0) + len(owned)
                continue
            for old in owned:
                old_checkins = checkins_by_habit.get(old["id"], [])
                stats["habits"] += 1
                stats["checkins"] += len(old_checkins)
                if not apply:
                    continue
                habit = Habit(
                    owner=owner, name=(old.get("name") or "Habit")[:60], emoji=(old.get("emoji") or "")[:48],
                    color=(old.get("color") or "#3a9163")[:9], sort=old.get("sort") or 0,
                    archived=bool(old.get("archived")), created_at=_created_at(old.get("created_at")),
                )
                session.add(habit)
                session.flush()
                session.add_all([HabitCheckin(habit_id=habit.id, date=c["date"], note=c["note"] or "")
                                 for c in old_checkins])
        if apply:
            session.commit()
    return stats


def convert_legacy_blobs(apply):
    """(Finance stores converted, Calendar states converted, kept as JSON,
    Calendar items in the converted states)."""
    finance = calendar = kept = items = 0
    for key in database.legacy_document_keys():
        payload = database.load_document(key, None)
        if not finance_tables.can_store(key, payload):
            kept += 1
            continue
        finance += 1
        if apply:
            database.save_document(key, payload)
            if database.load_document(key, None) != payload:
                raise MigrationError([f"{key}: did not read back identically after conversion."], written=True)
    with Session(_engine()) as session:
        converted = select(calendar_tables.lists_table.c.profile_id)
        rows = session.scalars(select(database.CalendarState).where(
            database.CalendarState.profile_id.not_in(converted))).all()
        for row in rows:
            state = row.payload
            if calendar_tables._prepare(row.profile_id, state) is None:
                kept += 1
                continue
            calendar += 1
            items += sum(len(state.get(name) or []) for name in calendar_tables.LISTS)
            if apply:
                calendar_tables.store_state(session, row, state)
                session.flush()
                if calendar_tables.load_state(session, row) != state:
                    raise MigrationError([f"Calendar state {row.profile_id}: did not read back identically."])
        if apply:
            session.commit()
    return finance, calendar, kept, items


# ---------- validation against the source ----------

def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _rounded(figures):
    figures["income"] = round(figures["income"], 2)
    figures["expense"] = round(figures["expense"], 2)
    figures["balances"] = {k: round(v, 2) for k, v in figures["balances"].items() if round(v, 2)}
    figures["types"] = dict(figures["types"])
    return figures


def json_figures(records):
    """Count, ids, income/expense totals and per-account balances of one
    transactions list, by the same rules the SQL side uses."""
    figures = {"count": len(records), "ids": sorted(str(r.get("id")) for r in records if r.get("id")),
               "income": 0.0, "expense": 0.0, "balances": Counter(), "types": Counter()}
    for record in records:
        if record.get("deleted_at") or not _number(record.get("amount")) or not isinstance(record.get("type"), str):
            continue
        sign = {"income": 1, "expense": -1}.get(record["type"])
        if sign is None:
            continue
        figures[record["type"]] += record["amount"]
        figures["types"][record["type"]] += 1
        if isinstance(record.get("account"), str):
            figures["balances"][record["account"]] += sign * record["amount"]
    return _rounded(figures)


def database_figures(session, profile_id):
    """The same figures computed independently with SQL on the relational
    rows (not by re-reading the JSON payload)."""
    from sqlalchemy import func

    table = finance_tables.STORES["expenses.json"].table
    mine = table.c.profile_id == profile_id
    live = mine & (func.coalesce(table.c.deleted_at, "") == "") & table.c.amount.is_not(None)
    figures = {
        "count": session.scalar(select(func.count()).select_from(table).where(mine)),
        "ids": sorted(session.scalars(select(table.c.id).where(mine, table.c.id.is_not(None))).all()),
        "income": 0.0, "expense": 0.0, "balances": Counter(), "types": Counter(),
    }
    rows = session.execute(select(table.c.type, table.c.account, func.sum(table.c.amount), func.count())
                           .where(live, table.c.type.in_(("income", "expense")))
                           .group_by(table.c.type, table.c.account)).all()
    for kind, account, total, count in rows:
        figures[kind] += total
        figures["types"][kind] += count
        if account is not None:
            figures["balances"][account] += total if kind == "income" else -total
    return _rounded(figures)


def validate_migration(profiles, stores, imported):
    """Compare the source JSON with PostgreSQL for every store imported in
    this run. Returns report lines; raises MigrationError on any mismatch."""
    from sqlalchemy import func

    problems, lines = [], []
    stored_doc = database.load_document(PROFILES_KEY, {})
    stored = {p.get("id"): p for p in (stored_doc.get("profiles", []) if isinstance(stored_doc, dict) else [])
              if isinstance(p, dict)}
    imported_ids = {key.split("/")[1] for key in imported}
    for profile in profiles:
        if profile["id"] in imported_ids and stored.get(profile["id"], {}).get("name") != profile["name"]:
            problems.append(f"Profile {profile['name']!r}: missing or renamed in the database.")
    table = finance_tables.STORES["expenses.json"].table
    with Session(_engine()) as session:
        for key in sorted(imported):
            if not key.endswith("/expenses.json"):
                continue
            profile_id = key.split("/")[1]
            source = json_figures(stores[key])
            if finance_tables.exists(session, key):
                target = database_figures(session, profile_id)
            else:  # kept whole as a JSON document (shape didn't fit the table)
                target = json_figures(database.load_document(key, []))
            for field in ("count", "ids", "income", "expense", "balances", "types"):
                if source[field] != target[field]:
                    problems.append(f"{key}: {field} differs (JSON {source[field]!r} vs PostgreSQL {target[field]!r}).")
            foreign = session.scalar(select(func.count()).select_from(table).where(
                table.c.profile_id != profile_id, table.c.id.in_(source["ids"] or [""])))
            if foreign:
                problems.append(f"{key}: {foreign} of its record ids also appear under another profile.")
            name = next((p["name"] for p in profiles if p["id"] == profile_id), profile_id)
            lines.append(f"  {name}: {source['count']} records, income {source['income']:.2f}, "
                         f"expense {source['expense']:.2f}, {len(source['balances'])} account balance(s): "
                         "JSON = PostgreSQL")
    if problems:
        raise MigrationError(problems, written=True)
    lines.append(f"  {len(imported)} imported store(s) read back identical to their JSON (ids, timestamps, order).")
    return lines


# ---------- orchestration ----------

def run(data_dir=FINANCE_DATA_DIR, habits_db=DEFAULT_HABITS_DB, apply=False, default_profile_id=None):
    if not database.configured():
        raise MigrationError(["DATABASE_URL is not set."])
    documents = collect_documents(data_dir)
    profiles, stores, notes = validate(documents)

    status = merge_profiles(profiles, apply)
    eligible = {pid for pid, state in status.items() if state != "conflict"}
    stores = {key: payload for key, payload in stores.items() if key.split("/")[1] in eligible}
    imported, present = import_stores(stores, apply)
    habit_stats = import_habits(habits_db, default_profile_id, apply, incoming_ids=eligible)
    blobs = convert_legacy_blobs(apply)
    validation = validate_migration(profiles, stores, imported) if apply else []

    records, children, types = Counter(), Counter(), Counter()
    for key in imported:
        filename = key.split("/")[2]
        count, child_counts = count_records(filename, stores[key])
        records[filename] += count
        children.update(child_counts)
        if filename == "expenses.json" and isinstance(stores[key], list):
            types.update(r.get("type") if r.get("type") in ("income", "expense") else "other"
                         for r in stores[key] if isinstance(r, dict))
    skipped = sum(count_records(key.split("/")[2], stores[key])[0] for key in present)
    return {
        "apply": apply,
        "profiles": profiles,
        "status": status,
        "imported_stores": imported,
        "present_stores": present,
        "records": records,
        "children": children,
        "habits": habit_stats,
        "blobs": blobs,
        "notes": notes,
        "types": types,
        "duplicates_skipped": skipped + habit_stats.get("skipped_habits", 0),
        "validation": validation,
    }


def format_summary(result):
    status = Counter(result["status"].values())
    new_profiles = status["new"]
    records, children, habits = result["records"], result["children"], result["habits"]
    other = {STORE_LABELS[name]: count for name, count in records.items() if name != "expenses.json" and count}
    other.update({CHILD_LABELS[name]: count for name, count in children.items() if count})
    if result["types"]["other"]:
        other["transactions of other types"] = result["types"]["other"]
    if habits["checkins"]:
        other["habit check-ins"] = habits["checkins"]
    verb = "migrated" if result["apply"] else "to migrate"
    lines = [
        "" if result["apply"] else "DRY RUN: nothing was written. Re-run with --apply to migrate.",
        f"Users {verb}: {new_profiles}",
        f"Profiles {verb}: {new_profiles}",
        f"Expenses {verb}: {result['types']['expense']}",
        f"Income {verb}: {result['types']['income']}",
        f"Diary entries {verb}: 0",
        f"Calendar records {verb}: {result['blobs'][3]}",
        f"Habits {verb}: {habits['habits']}",
        f"Other records {verb}: {sum(other.values())}",
        f"Duplicates skipped: {result['duplicates_skipped']}",
    ]
    lines += [f"  {label}: {count}" for label, count in sorted(other.items())]
    lines += [
        "",
        "Notes:",
        "  A Profile is the login account (name + password), so Users = Profiles.",
        "  This app has no Diary module; there are no diary entries to migrate.",
        "  Calendar data reaches PostgreSQL from each browser on its first login (import endpoint);",
        "  the Calendar count above is items converted from older whole-state database blobs.",
        f"  Profiles already in the database (skipped): {status['present']}",
        f"  Stores already in the database (skipped): {len(result['present_stores'])}",
    ]
    if status["conflict"]:
        names = [p["name"] for p in result["profiles"] if result["status"][p["id"]] == "conflict"]
        lines.append(f"  NOT migrated, name used by another account: {', '.join(names)} "
                     "(rename the Profile in Finance/data/profiles.json and re-run)")
    without_password = [p["name"] for p in result["profiles"]
                        if result["status"][p["id"]] == "new" and not p.get("password_hash")]
    if without_password:
        lines.append(f"  Profiles without a password yet (first login sets it): {', '.join(without_password)}")
    if habits["skipped_owners"]:
        lines.append(f"  Profiles whose Habits were already in the database (skipped): {len(habits['skipped_owners'])}")
    if habits["unowned"]:
        lines.append(f"  Habits with no matching Profile (not migrated; pass --default-profile-id): {habits['unowned']}")
    finance_blobs, calendar_blobs, kept, _ = result["blobs"]
    lines.append(f"  Legacy JSON blobs converted to rows: {finance_blobs} Finance, {calendar_blobs} Calendar "
                 f"({kept} kept as JSON because their shape doesn't fit the tables)")
    lines += [f"  {note}" for note in result["notes"]]
    lines.append("  Source files were not modified.")
    if result["validation"]:
        lines += ["", "Validation (JSON source vs PostgreSQL, computed with SQL):"] + result["validation"]
    return "\n".join(line for line in lines if line is not None).lstrip("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Migrate local JSON/SQLite data into PostgreSQL.")
    parser.add_argument("--apply", action="store_true", help="write to the database (default: preview only)")
    parser.add_argument("--data-dir", default=FINANCE_DATA_DIR, help="Finance JSON folder (default: Finance/data)")
    parser.add_argument("--habits-db", default=DEFAULT_HABITS_DB, help="Habit SQLite file (default: .data/habits.db)")
    parser.add_argument("--default-profile-id", help="owner for Habits created before Profiles existed")
    args = parser.parse_args(argv)

    if not database.configured():
        parser.error("DATABASE_URL is not set (environment or .env).")
    database.require_postgres()
    try:
        result = run(os.path.abspath(args.data_dir), os.path.abspath(args.habits_db),
                     args.apply, args.default_profile_id)
    except MigrationError as error:
        print("Migration stopped. " + ("Stores imported before this point were kept; restore from your "
              "backup if needed." if error.written else "Nothing was written.") , file=sys.stderr)
        for problem in error.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(format_summary(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

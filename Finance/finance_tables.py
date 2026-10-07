"""Relational tables for the Finance JSON stores.

Each store that used to be one JSON document (expenses.json, debts.json, ...)
is kept as real SQL rows: one row per record, typed columns for the known
fields, child tables for nested lists (debt payments, shopping price checks).
The app still reads and writes whole stores through ``load_document`` /
``save_document`` in database.py; this module converts between that payload
shape and the rows.

Nothing is dropped in the conversion:

* a field that is missing, ``None``, or not the column's type (e.g. a
  ``due_date`` of ``""``) is kept in the row's ``extra`` JSON column, as is
  any field the table has no column for;
* every save is rebuilt in memory and compared against the original payload
  first; a payload that would not round-trip exactly is stored as a JSON
  document in ``finance_documents`` instead (database.py handles that).

Example query::

    SELECT category, SUM(amount) FROM finance_transactions
    WHERE profile_id = '...' AND type = 'expense' AND deleted_at IS NULL
    GROUP BY category;
"""
import math
import re
from datetime import date

from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, Date, DateTime, Float, Index, Integer, String, Table, Text,
    delete, func, insert, select, true,
)
from sqlalchemy.dialects.postgresql import JSONB

try:
    from . import database
except ImportError:
    import database


JSON_TYPE = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
STR, FLOAT, INT, BOOL, DATE, ANY = "str", "float", "int", "bool", "date", "json"
_COLUMN_TYPES = {STR: Text, FLOAT: Float, INT: BigInteger, BOOL: Boolean, DATE: Date, ANY: JSON_TYPE}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_INTERNAL_COLUMNS = {"profile_id", "position", "parent_position", "extra"}
PROFILE_ID_MAX = 128

metadata = database.Base.metadata

# One row per stored document: whether it was a bare list or an envelope dict,
# and the envelope's other keys (schema_version, base currency, ...).
stores_table = Table(
    "finance_stores", metadata,
    Column("profile_id", String(PROFILE_ID_MAX), primary_key=True),
    Column("store", String(64), primary_key=True),
    Column("shape", String(8), nullable=False),
    Column("meta", JSON_TYPE),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)


def column_name(field):
    """SQL column for a JSON field: ``dueDate`` -> ``due_date``."""
    return _CAMEL_RE.sub("_", field).lower()


class StoreSpec:
    """How one JSON store maps onto a table.

    ``list_key`` is the envelope key holding the records (``None`` for a bare
    list). ``filename`` is ``None`` for tables used outside STORES (Calendar). ``key_column`` is set for stores whose records are a dict keyed by
    name rather than a list (insights). ``scoped`` is False only for the
    global profiles index.
    """

    def __init__(self, filename, table_name, fields, list_key=None, key_column=None,
                 scoped=True, children=None, indexes=()):
        self.filename = filename
        self.list_key = list_key
        self.key_column = key_column
        self.scoped = scoped
        self.fields = dict(fields)
        self.children = {}
        assert not _INTERNAL_COLUMNS & {column_name(name) for name in self.fields}

        columns = []
        if scoped:
            columns.append(Column("profile_id", String(PROFILE_ID_MAX), primary_key=True))
        columns.append(Column("position", Integer, primary_key=True, autoincrement=False))
        if key_column:
            columns.append(Column(key_column, Text, nullable=False))
        columns += [Column(column_name(name), _COLUMN_TYPES[kind]) for name, kind in self.fields.items()]
        for name in children or {}:
            columns.append(Column(f"has_{column_name(name)}", Boolean, nullable=False, default=False))
        columns.append(Column("extra", JSON_TYPE))
        self.table = Table(table_name, metadata, *columns)
        for index in indexes:
            index_name, index_columns = index[0], index[1]
            unique = len(index) > 2 and index[2] == "unique"
            Index(index_name, *(self.table.c[name] for name in index_columns), unique=unique)

        for name, (child_table_name, child_fields) in (children or {}).items():
            child_fields = dict(child_fields)
            assert not _INTERNAL_COLUMNS & {column_name(n) for n in child_fields}
            child_columns = []
            if scoped:
                child_columns.append(Column("profile_id", String(PROFILE_ID_MAX), primary_key=True))
            child_columns += [
                Column("parent_position", Integer, primary_key=True, autoincrement=False),
                Column("position", Integer, primary_key=True, autoincrement=False),
            ]
            child_columns += [Column(column_name(n), _COLUMN_TYPES[k]) for n, k in child_fields.items()]
            child_columns.append(Column("extra", JSON_TYPE))
            self.children[name] = (Table(child_table_name, metadata, *child_columns), child_fields)

    def scope_filter(self, table, profile_id):
        return table.c.profile_id == profile_id if self.scoped else true()


STORES = {spec.filename: spec for spec in (
    StoreSpec("expenses.json", "finance_transactions", [
        ("id", STR), ("date", DATE), ("type", STR), ("category", STR), ("account", STR),
        ("item", STR), ("amount", FLOAT), ("receipt", STR), ("tags", ANY),
        ("transfer_id", STR), ("split_id", STR), ("split_label", STR), ("goal_id", INT),
        ("recurring_id", STR), ("debt_id", STR), ("source", STR), ("deleted_at", STR),
    ], indexes=[("ix_finance_transactions_profile_date", ("profile_id", "date"))]),
    StoreSpec("budget.json", "finance_budgets", [
        ("category", STR), ("amount", FLOAT), ("period", STR), ("rollover", BOOL),
    ]),
    StoreSpec("accounts.json", "finance_accounts", [
        ("name", STR), ("purpose", STR), ("initial_amount", FLOAT),
    ]),
    StoreSpec("goals.json", "finance_goals", [
        ("id", INT), ("name", STR), ("type", STR), ("target", FLOAT), ("target_date", DATE),
        ("priority", STR), ("notes", STR), ("status", STR),
    ]),
    StoreSpec("categories.json", "finance_categories", [
        ("id", STR), ("name", STR), ("kind", STR), ("icon", STR), ("color", STR),
        ("order", INT), ("archived", BOOL), ("is_default", BOOL), ("created_at", DATE),
    ], list_key="categories"),
    StoreSpec("shopping.json", "finance_shopping_items", [
        ("id", STR), ("name", STR), ("category", STR), ("estimated_price", FLOAT),
        ("actual_price", FLOAT), ("priority", STR), ("status", STR), ("decision", STR),
        ("decision_date", DATE), ("date_added", DATE), ("target_date", DATE),
        ("purchased_at", DATE), ("wait_days", INT), ("wait_until", DATE),
        ("expense_created", BOOL), ("reasons_for", STR), ("reasons_against", STR),
        ("notes", STR), ("worth_it", STR), ("hindsight_note", STR), ("rated_at", DATE),
        ("ai_suggestion", ANY),
    ], list_key="items", children={
        "price_checks": ("finance_shopping_price_checks", [
            ("id", STR), ("date", DATE), ("price", FLOAT), ("source", STR), ("note", STR),
        ]),
    }),
    StoreSpec("recurring.json", "finance_recurring_rules", [
        ("id", STR), ("type", STR), ("category", STR), ("account", STR), ("item", STR),
        ("amount", FLOAT), ("tags", ANY), ("frequency", STR), ("interval", INT),
        ("start_date", DATE), ("end_date", DATE), ("next_due", DATE), ("last_posted", DATE),
        ("active", BOOL), ("auto_post", BOOL), ("created_at", DATE),
    ], list_key="rules"),
    StoreSpec("debts.json", "finance_debts", [
        ("id", STR), ("direction", STR), ("counterparty", STR), ("description", STR),
        ("principal", FLOAT), ("date", DATE), ("due_date", DATE), ("account", STR),
        ("status", STR), ("notes", STR), ("created_at", DATE),
    ], list_key="debts", children={
        "payments": ("finance_debt_payments", [
            ("id", STR), ("date", DATE), ("amount", FLOAT), ("note", STR), ("record_id", STR),
        ]),
    }),
    StoreSpec("networth.json", "finance_networth_snapshots", [
        ("id", STR), ("date", DATE), ("assets", FLOAT), ("liabilities", FLOAT), ("net", FLOAT),
        ("note", STR), ("auto", BOOL), ("breakdown", ANY),
    ], list_key="snapshots"),
    StoreSpec("insights.json", "finance_insights", [
        ("narrative", STR), ("suggestions", ANY), ("generated_at", STR),
    ], list_key="reviews", key_column="review_key"),
    StoreSpec("rates.json", "finance_currency_rates", [
        ("code", STR), ("name", STR), ("rate_to_myr", FLOAT), ("updated_at", DATE),
    ], list_key="rates"),
    StoreSpec("profiles.json", "finance_profiles", [
        ("id", STR), ("name", STR), ("created_at", DATE), ("password_hash", STR),
    ], list_key="profiles", scoped=False,
        indexes=[("uq_finance_profiles_id", ("id",), "unique")]),
)}


# ---------- value conversion ----------

def _fits(kind, value):
    if kind == STR:
        return isinstance(value, str)
    if kind == FLOAT:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if kind == INT:
        return isinstance(value, int) and not isinstance(value, bool) and -2**63 <= value < 2**63
    if kind == BOOL:
        return isinstance(value, bool)
    if kind == DATE:
        if not (isinstance(value, str) and _DATE_RE.match(value)):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    return value is not None


def _to_column(kind, value):
    if kind == FLOAT:
        return float(value)
    if kind == DATE:
        return date.fromisoformat(value)
    return value


def _from_column(kind, value):
    if kind == FLOAT:
        return float(value)
    if kind == DATE:
        return value.isoformat()
    return value


def _split_record(fields, record, children=None):
    """``record`` -> (column values, extra dict, {child name: list of child records})."""
    row = dict.fromkeys(column_name(name) for name in fields)
    extra = {}
    nested = {}
    for name, value in record.items():
        kind = fields.get(name)
        if kind is not None and _fits(kind, value):
            row[column_name(name)] = _to_column(kind, value)
        elif (children and name in children and isinstance(value, list)
              and all(isinstance(child, dict) for child in value)):
            nested[name] = value
        else:
            extra[name] = value
    return row, extra or None, nested


def _join_record(fields, row, extra, nested=None):
    record = {
        name: _from_column(kind, row[column_name(name)])
        for name, kind in fields.items() if row[column_name(name)] is not None
    }
    record.update(nested or {})
    record.update(extra or {})
    return record


# ---------- key / payload shape ----------

def _parse_key(key):
    """``"profiles/<id>/debts.json"`` -> (spec, "<id>"); ``"profiles.json"`` or a
    flat legacy ``"debts.json"`` -> (spec, ""). Anything else -> (None, None)."""
    parts = key.split("/")
    if len(parts) == 1:
        profile_id, filename = "", parts[0]
    elif len(parts) == 3 and parts[0] == "profiles" and parts[1]:
        profile_id, filename = parts[1], parts[2]
    else:
        return None, None
    spec = STORES.get(filename)
    if spec is None or len(profile_id) > PROFILE_ID_MAX or (profile_id and not spec.scoped):
        return None, None
    return spec, profile_id


def _key_for(profile_id, filename):
    return f"profiles/{profile_id}/{filename}" if profile_id else filename


def _unpack(spec, payload):
    """Split a payload into (shape, envelope meta, [(review_key, record), ...]);
    ``None`` if it isn't a shape the table can hold."""
    if isinstance(payload, list) and not spec.key_column:
        # Some stores are bare lists; older envelope stores were too.
        shape, meta, records = "list", None, payload
    elif isinstance(payload, dict) and spec.list_key and spec.list_key in payload:
        shape = "dict"
        meta = {k: v for k, v in payload.items() if k != spec.list_key}
        records = payload[spec.list_key]
    else:
        return None
    if spec.key_column:
        if not isinstance(records, dict):
            return None
        items = list(records.items())
    else:
        if not isinstance(records, list):
            return None
        items = [(None, record) for record in records]
    if not all(isinstance(record, dict) for _, record in items):
        return None
    return shape, meta, items


def _pack(spec, shape, meta, items):
    if spec.key_column:
        records = {key: record for key, record in items}
    else:
        records = [record for _, record in items]
    if shape == "list":
        return records
    payload = dict(meta or {})
    payload[spec.list_key] = records
    return payload


def build_rows(spec, profile_id, items):
    rows = []
    child_rows = {name: [] for name in spec.children}
    for position, (key, record) in enumerate(items):
        row, extra, nested = _split_record(spec.fields, record, spec.children)
        row.update(position=position, extra=extra)
        if spec.scoped:
            row["profile_id"] = profile_id
        if spec.key_column:
            row[spec.key_column] = key
        for name, (_, child_fields) in spec.children.items():
            row[f"has_{column_name(name)}"] = name in nested
            for child_position, child in enumerate(nested.get(name, [])):
                child_row, child_extra, _ = _split_record(child_fields, child)
                child_row.update(parent_position=position, position=child_position, extra=child_extra)
                if spec.scoped:
                    child_row["profile_id"] = profile_id
                child_rows[name].append(child_row)
        rows.append(row)
    return rows, child_rows


def rows_to_items(spec, rows, child_rows):
    grouped = {name: {} for name in spec.children}
    for name, (_, child_fields) in spec.children.items():
        for child in child_rows[name]:
            grouped[name].setdefault(child["parent_position"], []).append(
                _join_record(child_fields, child, child["extra"]))
    items = []
    for row in rows:
        nested = {
            name: grouped[name].get(row["position"], [])
            for name in spec.children if row[f"has_{column_name(name)}"]
        }
        record = _join_record(spec.fields, row, row["extra"], nested)
        items.append((row[spec.key_column] if spec.key_column else None, record))
    return items


def _prepare(key, payload):
    """(spec, profile_id, shape, meta, rows, child_rows) when ``payload`` can be
    stored relationally and reads back identical; otherwise ``None``."""
    spec, profile_id = _parse_key(key)
    if spec is None:
        return None
    unpacked = _unpack(spec, payload)
    if unpacked is None:
        return None
    shape, meta, items = unpacked
    rows, child_rows = build_rows(spec, profile_id, items)
    if _pack(spec, shape, meta, rows_to_items(spec, rows, child_rows)) != payload:
        return None
    return spec, profile_id, shape, meta, rows, child_rows


# ---------- session operations (called from database.py) ----------

def can_store(key, payload):
    return _prepare(key, payload) is not None


def _store_row(session, spec, profile_id, lock=False):
    query = select(stores_table).where(
        stores_table.c.profile_id == profile_id, stores_table.c.store == spec.filename)
    if lock:
        query = query.with_for_update()
    return session.execute(query).mappings().first()


def exists(session, key):
    spec, profile_id = _parse_key(key)
    return spec is not None and _store_row(session, spec, profile_id) is not None


def select_items(session, spec, profile_id):
    """``[(key, record), ...]`` stored in ``spec``'s tables for ``profile_id``."""
    rows = session.execute(
        select(spec.table).where(spec.scope_filter(spec.table, profile_id))
        .order_by(spec.table.c.position)).mappings().all()
    child_rows = {
        name: session.execute(
            select(table).where(spec.scope_filter(table, profile_id))
            .order_by(table.c.parent_position, table.c.position)).mappings().all()
        for name, (table, _) in spec.children.items()
    }
    return rows_to_items(spec, rows, child_rows)


def insert_rows(session, spec, rows, child_rows):
    if rows:
        session.execute(insert(spec.table), rows)
    for name, (table, _) in spec.children.items():
        if child_rows[name]:
            session.execute(insert(table), child_rows[name])


def delete_items(session, spec, profile_id):
    for table, _ in spec.children.values():
        session.execute(delete(table).where(spec.scope_filter(table, profile_id)))
    session.execute(delete(spec.table).where(spec.scope_filter(spec.table, profile_id)))


def load(session, key):
    """(True, payload) if ``key`` is stored in tables, else (False, None)."""
    spec, profile_id = _parse_key(key)
    if spec is None:
        return False, None
    store = _store_row(session, spec, profile_id)
    if store is None:
        return False, None
    return True, _pack(spec, store["shape"], store["meta"], select_items(session, spec, profile_id))


def _clear(session, spec, profile_id):
    delete_items(session, spec, profile_id)
    session.execute(delete(stores_table).where(
        stores_table.c.profile_id == profile_id, stores_table.c.store == spec.filename))


def save(session, key, payload):
    """Replace ``key``'s rows with ``payload``. Returns False (writing nothing)
    when the payload can't be held in tables, so the caller can keep it as a
    JSON document instead."""
    prepared = _prepare(key, payload)
    if prepared is None:
        return False
    spec, profile_id, shape, meta, rows, child_rows = prepared
    _store_row(session, spec, profile_id, lock=True)
    _clear(session, spec, profile_id)
    insert_rows(session, spec, rows, child_rows)
    session.execute(insert(stores_table).values(
        profile_id=profile_id, store=spec.filename, shape=shape, meta=meta))
    return True


def remove(session, key):
    spec, profile_id = _parse_key(key)
    if spec is not None:
        _clear(session, spec, profile_id)


def remove_prefix(session, prefix):
    """Delete every table-backed store whose document key starts with ``prefix``."""
    stored = session.execute(select(stores_table.c.profile_id, stores_table.c.store)).all()
    for profile_id, filename in stored:
        if _key_for(profile_id, filename).startswith(prefix):
            remove(session, _key_for(profile_id, filename))

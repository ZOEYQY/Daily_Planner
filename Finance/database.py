"""SQLAlchemy-backed storage for Finance stores, Habits, Calendar and receipts.

Finance stores and Calendar items are kept as relational rows (see
finance_tables.py and calendar_tables.py). The
``finance_documents`` table holds any payload that doesn't fit those tables,
and data written before the tables existed until it is next saved or migrated
with ``migrate_documents_to_tables.py``.
"""
import copy
import logging
import os
import threading
from contextlib import contextmanager
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class FinanceDocument(Base):
    __tablename__ = "finance_documents"

    key: Mapped[str] = mapped_column(String(768), primary_key=True)
    payload: Mapped[object] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Habit(Base):
    __tablename__ = "habits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    emoji: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    color: Mapped[str] = mapped_column(String(9), nullable=False, default="#3a9163")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class HabitCheckin(Base):
    __tablename__ = "habit_checkins"

    habit_id: Mapped[int] = mapped_column(ForeignKey("habits.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")


class CalendarState(Base):
    __tablename__ = "calendar_states"

    profile_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[object] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class CalendarImport(Base):
    __tablename__ = "calendar_imports"

    profile_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    imported_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ReceiptAsset(Base):
    __tablename__ = "receipt_assets"

    profile_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    format: Mapped[str] = mapped_column(String(16), nullable=False)


def database_url():
    try:
        from dotenv import load_dotenv
        finance_dir = os.path.dirname(os.path.abspath(__file__))
        load_dotenv(os.path.join(finance_dir, ".env"))
        load_dotenv(os.path.join(os.path.dirname(finance_dir), ".env"))
    except ImportError:
        pass
    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _tables():
    try:
        from . import finance_tables
    except ImportError:
        import finance_tables
    return finance_tables


def calendar_tables():
    try:
        from . import calendar_tables as tables
    except ImportError:
        import calendar_tables as tables
    return tables


_ENGINES = {}
_ENGINE_LOCK = threading.Lock()
# Any constant: serialises table creation across processes (gunicorn workers
# booting together on a deploy that adds tables would otherwise race).
_SCHEMA_LOCK_ID = 7_412_029_001


def _create_schema(engine):
    _tables()  # registers the Finance and Calendar tables on Base.metadata
    calendar_tables()
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _SCHEMA_LOCK_ID})
        Base.metadata.create_all(connection)


def _engine(url):
    """The shared engine for ``url``; created (and missing tables created)
    exactly once per process, even when the first requests arrive together."""
    engine = _ENGINES.get(url)
    if engine is None:
        with _ENGINE_LOCK:
            engine = _ENGINES.get(url)
            if engine is None:
                engine = create_engine(url, pool_pre_ping=True)
                _create_schema(engine)
                _ENGINES[url] = engine
    return engine


def _clear_engines():
    """Forget (and close) every engine, as a process restart would."""
    with _ENGINE_LOCK:
        for engine in _ENGINES.values():
            engine.dispose()
        _ENGINES.clear()


_engine.cache_clear = _clear_engines  # kept for existing callers and tests


def configured():
    return bool(database_url())


def require_postgres():
    url = database_url()
    if not url.startswith("postgresql+"):
        raise RuntimeError("Render requires DATABASE_URL to point to PostgreSQL; local-file storage is disabled.")
    _engine(url)


# ---------- one transaction per web request ----------
# Inside a Flask request (once the Finance blueprint is registered), every
# store read and write shares ONE session. finish_request() commits it when
# the request completes and rolls it back if the request crashed (an
# unhandled exception; deliberate error responses such as a 503 "AI not
# configured" still keep their writes), so a request that
# changes several stores (delete an account -> records, categories, recurring
# rules, shopping list) is all-or-nothing and never leaves half-written state.
# Outside a request (scripts, tests) each call commits on its own.

UNIT_OF_WORK_FLAG = "daily_planner_unit_of_work"
_log = logging.getLogger(__name__)


def _request_unit():
    try:
        from flask import current_app, g, has_request_context
    except ImportError:
        return None
    if not has_request_context() or not current_app.extensions.get(UNIT_OF_WORK_FLAG):
        return None
    url = database_url()
    unit = g.get("_db_unit")
    if unit is None or g.get("_db_unit_url") != url:
        if unit is not None:
            unit.close()
        unit = Session(_engine(url))
        g._db_unit, g._db_unit_url = unit, url
    return unit


@contextmanager
def _session(write):
    unit = _request_unit()
    if unit is not None:
        yield unit
        if write:
            unit.flush()  # surface constraint errors inside the route
        return
    with Session(_engine(database_url())) as session:
        yield session
        if write:
            session.commit()


def _mark_request_failed(_sender, exception=None, **_extra):
    from flask import g

    g._db_request_failed = True


def finish_request(response):
    """after_request hook: commit the request's writes, or roll them back if
    the request crashed. A failed commit becomes a 500, never a silent
    "saved" page."""
    from flask import g

    unit = g.pop("_db_unit", None)
    g.pop("_db_unit_url", None)
    if unit is None:
        return response
    try:
        if g.pop("_db_request_failed", False):
            unit.rollback()
        else:
            unit.commit()
    except Exception:
        unit.rollback()
        _log.exception("Database commit failed; the request's changes were rolled back.")
        from flask import Response
        response = Response("The change could not be saved. Nothing was changed; please try again.",
                            status=500, mimetype="text/plain")
    finally:
        unit.close()
    return response


def close_request(_exc=None):
    """teardown hook: an unfinished unit (an exception skipped finish_request)
    is closed, which rolls it back."""
    from flask import g

    unit = g.pop("_db_unit", None)
    g.pop("_db_unit_url", None)
    if unit is not None:
        unit.close()


def init_unit_of_work(app):
    from flask import got_request_exception

    app.extensions[UNIT_OF_WORK_FLAG] = True
    got_request_exception.connect(_mark_request_failed, app)


def _load(session, key, default):
    found, payload = _tables().load(session, key)
    if found:
        return payload
    document = session.get(FinanceDocument, key)
    return default if document is None else document.payload


def load_document(key, default):
    with _session(write=False) as session:
        return _load(session, key, default)


_UPDATE_LOCK = threading.RLock()


def _lock_key(session, key):
    """Hold a PostgreSQL transaction-scoped advisory lock on ``key`` until
    commit/rollback, serialising read-modify-write across every worker and
    instance. (Other databases are only used locally / in tests, where the
    in-process lock in update_document is enough.)"""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": "daily-planner:" + key})


def update_document(key, default, mutate):
    """Atomically load ``key``, apply ``mutate(payload) -> (new_payload,
    result)`` and save ``new_payload`` (None = unchanged). Returns ``result``.
    An exception from ``mutate`` rolls back and writes nothing."""
    with _UPDATE_LOCK, Session(_engine(database_url())) as session:
        _lock_key(session, key)
        payload = copy.deepcopy(_load(session, key, default))
        new_payload, result = mutate(payload)
        if new_payload is not None:
            _store_document(session, key, new_payload)
        session.commit()
        return result


def _store_document(session, key, payload):
    """Write ``payload`` to the store tables, or to ``finance_documents`` when
    it can't be held there; whichever isn't used is cleared for ``key``."""
    tables = _tables()
    document = session.get(FinanceDocument, key)
    if tables.save(session, key, payload):
        if document is not None:
            session.delete(document)
        return
    tables.remove(session, key)
    if document is None:
        session.add(FinanceDocument(key=key, payload=payload))
    else:
        document.payload = payload


def _document_exists(session, key):
    return _tables().exists(session, key) or session.get(FinanceDocument, key) is not None


def save_document(key, payload):
    with _session(write=True) as session:
        _store_document(session, key, payload)


def delete_documents(prefix):
    with _session(write=True) as session:
        session.query(FinanceDocument).filter(FinanceDocument.key.like(prefix + "%")).delete(synchronize_session=False)
        _tables().remove_prefix(session, prefix)


def delete_profile_rows(profile_id):
    """Delete a profile's Habits, check-ins and Calendar rows (Finance stores
    go through ``delete_documents``; receipt images through receipt_storage,
    which also removes them from Cloudinary)."""
    from sqlalchemy import delete, select

    calendar = calendar_tables()
    with _session(write=True) as session:
        habit_ids = select(Habit.id).where(Habit.owner == profile_id)
        session.execute(delete(HabitCheckin).where(HabitCheckin.habit_id.in_(habit_ids)))
        session.execute(delete(Habit).where(Habit.owner == profile_id))
        for spec in calendar.LISTS.values():
            _tables().delete_items(session, spec, profile_id)
        session.execute(delete(calendar.lists_table).where(calendar.lists_table.c.profile_id == profile_id))
        session.execute(delete(CalendarState).where(CalendarState.profile_id == profile_id))
        session.execute(delete(CalendarImport).where(CalendarImport.profile_id == profile_id))


def get_receipt_asset(profile_id, filename):
    from sqlalchemy import select

    with Session(_engine(database_url())) as session:
        return session.scalar(select(ReceiptAsset).where(
            ReceiptAsset.profile_id == profile_id,
            ReceiptAsset.filename == filename,
        ))


def delete_receipt_asset(profile_id, filename):
    from sqlalchemy import delete

    with Session(_engine(database_url())) as session:
        session.execute(delete(ReceiptAsset).where(
            ReceiptAsset.profile_id == profile_id,
            ReceiptAsset.filename == filename,
        ))
        session.commit()


def receipt_assets_for_profile(profile_id):
    from sqlalchemy import select

    with Session(_engine(database_url())) as session:
        return session.scalars(select(ReceiptAsset).where(ReceiptAsset.profile_id == profile_id)).all()


def import_documents(documents, replace=False):
    """Import keyed JSON documents atomically, skipping existing keys by default."""
    imported = 0
    skipped = 0
    with Session(_engine(database_url())) as session:
        for key, payload in documents.items():
            if _document_exists(session, key) and not replace:
                skipped += 1
                continue
            _store_document(session, key, payload)
            imported += 1
        session.commit()
    return imported, skipped


def legacy_document_keys():
    """Keys still stored as whole JSON documents in ``finance_documents``."""
    from sqlalchemy import select

    with Session(_engine(database_url())) as session:
        return session.scalars(select(FinanceDocument.key).order_by(FinanceDocument.key)).all()

"""SQLAlchemy-backed document storage for Finance JSON stores."""
import os
from functools import lru_cache

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, func
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


@lru_cache(maxsize=4)
def _engine(url):
    engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine


def configured():
    return bool(database_url())


def require_postgres():
    url = database_url()
    if not url.startswith("postgresql+"):
        raise RuntimeError("Render requires DATABASE_URL to point to PostgreSQL; local-file storage is disabled.")
    _engine(url)


def load_document(key, default):
    with Session(_engine(database_url())) as session:
        document = session.get(FinanceDocument, key)
        return default if document is None else document.payload


def save_document(key, payload):
    with Session(_engine(database_url())) as session:
        document = session.get(FinanceDocument, key)
        if document is None:
            document = FinanceDocument(key=key, payload=payload)
            session.add(document)
        else:
            document.payload = payload
        session.commit()


def delete_documents(prefix):
    with Session(_engine(database_url())) as session:
        session.query(FinanceDocument).filter(FinanceDocument.key.like(prefix + "%")).delete(synchronize_session=False)
        session.commit()


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
            document = session.get(FinanceDocument, key)
            if document is not None:
                if replace:
                    document.payload = payload
                    imported += 1
                else:
                    skipped += 1
                continue
            session.add(FinanceDocument(key=key, payload=payload))
            imported += 1
        session.commit()
    return imported, skipped

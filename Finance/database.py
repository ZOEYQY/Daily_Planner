"""SQLAlchemy-backed document storage for Finance JSON stores."""
import os
from functools import lru_cache

from sqlalchemy import JSON, DateTime, String, create_engine, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class FinanceDocument(Base):
    __tablename__ = "finance_documents"

    key: Mapped[str] = mapped_column(String(768), primary_key=True)
    payload: Mapped[object] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


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

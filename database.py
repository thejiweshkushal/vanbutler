"""SQLAlchemy engine, session, and declarative Base (Neon / Postgres)."""

import os
from collections.abc import Generator
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()


def _sync_database_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    return create_engine(
        _sync_database_url(url),
        pool_pre_ping=True,
    )


engine = _make_engine()
SessionLocal = (
    sessionmaker(bind=engine, autocommit=False, autoflush=False) if engine else None
)


def check_connection() -> bool:
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@contextmanager
def get_session() -> Generator[Session, None, None]:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not set")
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

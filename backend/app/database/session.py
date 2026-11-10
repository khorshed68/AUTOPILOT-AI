"""Engine and session factory."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings


def _build_engine() -> Engine:
    if settings.database_url.startswith("sqlite"):
        engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 30}, poolclass=NullPool)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(conn, _record):  # noqa: ANN001
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        return engine
    return create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

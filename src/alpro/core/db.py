"""Database bootstrap (SQLAlchemy 2.0).

SQLite in the sprint, Postgres+Timescale later — only DATABASE_URL changes.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from alpro.config import settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        kwargs: dict = {"future": True}
        if settings.database_url.startswith("sqlite"):
            # Eşzamanlı okuma/yazma toleransı: bekleme süresi + WAL modu
            kwargs["connect_args"] = {"timeout": 30}
        _engine = create_engine(settings.database_url, **kwargs)
        if settings.database_url.startswith("sqlite"):
            from sqlalchemy import event

            @event.listens_for(_engine, "connect")
            def _set_wal(dbapi_conn, _record):  # pragma: no cover
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def dispose_engine() -> None:
    """Havuzdaki tüm bağlantıları kapat — SQLite dosyası yerinde değiştirilmeden
    (yedekten geri yükleme) önce çağrılır. Engine yeniden kullanılabilir kalır:
    bir sonraki bağlantı dosyayı yeniden açar."""
    if _engine is not None:
        _engine.dispose()


def _ensure_sqlite_columns(engine) -> None:
    """Minimal column guard for pre-multiuser SQLite files.

    Alembic bilinçli ertelendi (bkz. core/models.py notu): create_all yeni
    tabloları açar ama VAR OLAN tabloya kolon eklemez. Tek gereken ek şu an
    transactions.user_id — Postgres'e geçişte bu bekçi Alembic'e devrolur.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.connect() as conn:
        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(transactions)")]
        if cols and "user_id" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE transactions ADD COLUMN user_id INTEGER REFERENCES users(id)"
            )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_transactions_user_id ON transactions (user_id)"
        )
        conn.commit()


def init_db() -> None:
    """Create all tables (idempotent)."""
    from alpro.core import models  # noqa: F401 — register mappings

    Base.metadata.create_all(get_engine())
    _ensure_sqlite_columns(get_engine())


@contextmanager
def session() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

"""Canonical data model — the single schema every connector normalizes into.

Design principles (see roadmap doc §5.1, §6.3):
- Canonical model: upper layers never see provider-specific shapes.
- License registry: every price point carries `source`, `license_note`
  and `asof`, so the UI/AI can always disclose freshness and licensing.
- Numbers only from tools: the AI layer reads these tables through typed
  tools; it never invents figures.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from alpro.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssetClass(str, enum.Enum):
    EQUITY_BIST = "equity_bist"
    FUND_TEFAS = "fund_tefas"
    PENSION_BES = "pension_bes"
    FX = "fx"
    GOLD = "gold"
    CRYPTO = "crypto"
    BOND = "bond"
    EUROBOND = "eurobond"
    COMMODITY = "commodity"


class TxSide(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class Instrument(Base):
    """Master record for anything a user can hold or watch."""

    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("symbol", name="uq_instrument_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)  # e.g. THYAO, BTC, USD
    name: Mapped[str] = mapped_column(String(160))
    asset_class: Mapped[AssetClass] = mapped_column(Enum(AssetClass), index=True)
    currency: Mapped[str] = mapped_column(String(8))  # quote currency of prices
    # Provider hooks (nullable by design — one instrument, many sources)
    coingecko_id: Mapped[str | None] = mapped_column(String(64), default=None)
    tefas_code: Mapped[str | None] = mapped_column(String(16), default=None)
    isin: Mapped[str | None] = mapped_column(String(16), default=None)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="instrument")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Instrument {self.symbol} ({self.asset_class.value})>"


class PricePoint(Base):
    """A dated price observation for an instrument, with full provenance."""

    __tablename__ = "price_points"
    __table_args__ = (
        UniqueConstraint("instrument_id", "asof", "source", name="uq_price_obs"),
        Index("ix_price_lookup", "instrument_id", "asof"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    asof: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8))
    day_change_pct: Mapped[float | None] = mapped_column(Float, default=None)
    source: Mapped[str] = mapped_column(String(64))  # e.g. "coingecko", "demo-fixture"
    license_note: Mapped[str] = mapped_column(String(120), default="")  # e.g. "15 dk gecikmeli"
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FxRate(Base):
    """Base-currency conversion rates (e.g. USD→TRY), same provenance rules."""

    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint("pair", "asof", "source", name="uq_fx_obs"),
        Index("ix_fx_lookup", "pair", "asof"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pair: Mapped[str] = mapped_column(String(16), index=True)  # "USDTRY"
    asof: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rate: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(64))
    license_note: Mapped[str] = mapped_column(String(120), default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Transaction(Base):
    """Ledger entry — the portfolio engine is ledger-first (doc §6.4).

    `user_id` is nullable on purpose: legacy single-user rows are adopted
    by the founder at startup (see api.app._startup)."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True, default=None
    )
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    side: Mapped[TxSide] = mapped_column(Enum(TxSide))
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)  # unit price in `currency`
    currency: Mapped[str] = mapped_column(String(8))
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str] = mapped_column(String(200), default="")

    instrument: Mapped[Instrument] = relationship(back_populates="transactions")


class NewsItem(Base):
    """A headline attached to a watched symbol, with provenance.

    Same disclosure rules as prices: `source` is either the live feed
    ("google-news-rss") or "demo" for labeled fixture headlines — the
    briefing/UI must surface which one it is."""

    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("symbol", "link", name="uq_news_symbol_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(300))
    link: Mapped[str] = mapped_column(String(500))
    published: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(64))  # "google-news-rss" | "demo"
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConnectorRun(Base):
    """Audit log of data pulls — feeds the data-status view."""

    __tablename__ = "connector_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ok: Mapped[bool] = mapped_column(default=False)
    mode: Mapped[str] = mapped_column(String(16), default="live")  # live | fixture
    points_written: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(400), default="")


# ------------------------------------------------------------ multi-user core
# Şema yönetimi NOTU: Alembic bilinçli olarak ertelendi (SPRINT-PLAN-FAZ1,
# tek instance SQLite beta). Yeni tablolar create_all ile gelir; VAR OLAN
# tabloya kolon ekleme (transactions.user_id) için db.init_db içinde küçük
# bir SQLite kolon bekçisi var. Postgres'e geçişte ilk iş Alembic'tir.


class User(Base):
    """An account — identified by e-mail only (magic link, no password)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)  # stored lowercase
    name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # KVKK explicit consent timestamp — required at first signup, nullable
    # only because the column predates enforcement for the founder account.
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    is_founder: Mapped[bool] = mapped_column(default=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email}{' (founder)' if self.is_founder else ''}>"


class AuthSession(Base):
    """Server-side login session. Only the sha256 of the cookie token is stored."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 hex
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MagicLink(Base):
    """One-shot login link. Raw token is never persisted — hash only."""

    __tablename__ = "magic_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 hex
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class UserBackup(Base):
    """Per-user copy of the browser app's data file (was a flat file pre-multiuser)."""

    __tablename__ = "user_backups"
    __table_args__ = (UniqueConstraint("user_id", name="uq_backup_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    payload: Mapped[str] = mapped_column(Text)  # JSON as text
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    """Server-side price alert — one-shot: firing deactivates the alert.

    Notification channel today is the briefing ("TETİKLENEN ALARMLAR") and
    the panel; e-mail delivery lands with the SMTP/Resend integration."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))  # "above" | "below"
    level: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    fired_price: Mapped[float | None] = mapped_column(Float, default=None)


class BriefingRecord(Base):
    """Daily briefing per user (day is YYYY-MM-DD in Europe/Istanbul)."""

    __tablename__ = "briefing_records"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_briefing_user_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[str] = mapped_column(String(10))  # "2026-08-03"
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rating: Mapped[int | None] = mapped_column(Integer, default=None)  # 1=beğendi, 0=beğenmedi

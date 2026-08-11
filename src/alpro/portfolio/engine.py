"""Portfolio engine — ledger-first (roadmap §6.4).

Positions are derived from the transaction ledger with weighted-average
cost. Realized P/L uses average cost at the moment of each sell.
Valuation converts every position into the base currency through the
pricing service. TWR/MWR need a valuation history and land later in
Faz 1 (tracked in ROADMAP.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from alpro.config import settings
from alpro.core.models import Instrument, Transaction, TxSide
from alpro.pricing.service import PricingError, Quote, convert, latest_quote


class LedgerError(Exception):
    pass


@dataclass
class Position:
    instrument: Instrument
    quantity: float
    avg_cost: float          # per unit, in instrument currency
    invested: float          # quantity * avg_cost
    realized_pl: float       # cumulative, in instrument currency
    quote: Quote | None = None
    market_value_ccy: float | None = None      # in instrument currency
    market_value_base: float | None = None     # in base currency
    unrealized_pl: float | None = None         # in instrument currency
    unrealized_pl_pct: float | None = None
    weight_pct: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class PortfolioSummary:
    base_currency: str
    total_value_base: float
    total_invested_base: float
    total_unrealized_pl_base: float
    positions: list[Position]
    asof: datetime
    data_notes: list[str]  # freshness/licensing disclosures for the AI layer


def _positions_from_ledger(
    s: Session, user_id: int | None = None, strict: bool = False
) -> list[Position]:
    """Kronolojik replay. strict=True: satış-aşımı hatası fırlatır (yazma yolu);
    strict=False: okuma yolu kırılmasın diye aşım kırpılır ve uyarı eklenir
    (kod inceleme bulgusu #2 — okuma uçları kalıcı 500'e düşmemeli).

    user_id verilirse yalnız o kullanıcının defteri okunur; None = tüm defter
    (CLI/tek kullanıcı geri uyumluluğu)."""
    stmt = select(Transaction).order_by(Transaction.executed_at, Transaction.id)
    if user_id is not None:
        stmt = stmt.where(Transaction.user_id == user_id)
    txs = list(s.scalars(stmt))
    book: dict[int, Position] = {}
    for tx in txs:
        pos = book.get(tx.instrument_id)
        if pos is None:
            pos = Position(
                instrument=tx.instrument, quantity=0.0, avg_cost=0.0,
                invested=0.0, realized_pl=0.0,
            )
            book[tx.instrument_id] = pos
        if tx.side == TxSide.BUY:
            total_cost = pos.avg_cost * pos.quantity + tx.price * tx.quantity + tx.fee
            pos.quantity += tx.quantity
            pos.avg_cost = total_cost / pos.quantity if pos.quantity else 0.0
        else:  # SELL
            sell_qty = tx.quantity
            if sell_qty > pos.quantity + 1e-9:
                if strict:
                    raise LedgerError(
                        f"{tx.instrument.symbol} ({tx.executed_at.date()}): "
                        f"satış {tx.quantity}, o tarihteki eldekini ({pos.quantity}) aşıyor"
                    )
                pos.warnings.append(
                    f"defter tutarsızlığı: {tx.executed_at.date()} satışı kırpıldı"
                )
                sell_qty = pos.quantity
            pos.realized_pl += (tx.price - pos.avg_cost) * sell_qty - tx.fee
            pos.quantity -= sell_qty
            if pos.quantity <= 1e-9:
                pos.quantity = 0.0  # avg_cost kept for history
        pos.invested = pos.avg_cost * pos.quantity
    return [p for p in book.values()]


def portfolio_summary(
    s: Session, user_id: int | None = None, include_closed: bool = False
) -> PortfolioSummary:
    positions = _positions_from_ledger(s, user_id)
    if not include_closed:
        positions = [p for p in positions if p.quantity > 0]

    base = settings.base_currency
    total_value = 0.0
    total_invested = 0.0
    data_notes: set[str] = set()

    for pos in positions:
        quote = latest_quote(s, pos.instrument)
        pos.quote = quote
        if quote is None:
            pos.warnings.append("fiyat verisi yok")
            continue
        pos.market_value_ccy = pos.quantity * quote.price
        pos.unrealized_pl = (quote.price - pos.avg_cost) * pos.quantity
        pos.unrealized_pl_pct = (
            (quote.price / pos.avg_cost - 1.0) * 100.0 if pos.avg_cost else None
        )
        try:
            pos.market_value_base = convert(s, pos.market_value_ccy, quote.currency, base)
            total_invested += convert(s, pos.invested, quote.currency, base)
            total_value += pos.market_value_base
        except PricingError as exc:
            pos.warnings.append(str(exc))
            continue
        note = quote.license_note or quote.source
        if note:
            data_notes.add(f"{pos.instrument.symbol}: {note}")

    for pos in positions:
        if pos.market_value_base is not None and total_value > 0:
            pos.weight_pct = pos.market_value_base / total_value * 100.0

    return PortfolioSummary(
        base_currency=base,
        total_value_base=total_value,
        total_invested_base=total_invested,
        total_unrealized_pl_base=total_value - total_invested,
        positions=sorted(
            positions, key=lambda p: p.market_value_base or 0.0, reverse=True
        ),
        asof=datetime.now().astimezone(),
        data_notes=sorted(data_notes),
    )


def add_transaction(
    s: Session,
    symbol: str,
    side: TxSide,
    quantity: float,
    price: float,
    fee: float = 0.0,
    executed_at: datetime | None = None,
    note: str = "",
    user_id: int | None = None,
) -> Transaction:
    import math

    inst = s.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if inst is None:
        raise LedgerError(f"unknown instrument: {symbol}")
    for name, value in (("quantity", quantity), ("price", price), ("fee", fee)):
        if not math.isfinite(value):
            raise LedgerError(f"{name} must be a finite number")
    if quantity <= 0 or price <= 0 or fee < 0:
        raise LedgerError("quantity/price must be positive, fee non-negative")
    tx = Transaction(
        instrument_id=inst.id,
        user_id=user_id,
        side=side,
        quantity=quantity,
        price=price,
        currency=inst.currency,
        fee=fee,
        note=note,
    )
    if executed_at is not None:
        tx.executed_at = executed_at
    s.add(tx)
    s.flush()
    # Doğrulama: yeni işlem DAHİL kronolojik tam replay — geçmiş tarihli satış
    # da yakalanır; hata fırlarsa session rollback eder, kayıt kalmaz
    # (kod inceleme bulgusu #2). Replay, işlemin sahibi olan defterle sınırlı.
    _positions_from_ledger(s, user_id, strict=True)
    return tx

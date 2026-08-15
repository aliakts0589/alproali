"""Portfolio engine — ledger-first (roadmap §6.4).

Positions are derived from the transaction ledger with weighted-average
cost. Realized P/L uses average cost at the moment of each sell.
Valuation converts every position into the base currency through the
pricing service. Daily valuation snapshots feed the time-weighted
return (TWR) calculation below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from alpro.config import settings
from alpro.core.models import Instrument, Transaction, TxSide, ValuationSnapshot
from alpro.pricing.service import PricingError, Quote, convert, latest_quote

IST_TZ = ZoneInfo("Europe/Istanbul")


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


# ------------------------------------------------- valuation history / TWR

def record_valuation_snapshot(s: Session, user_id: int, day: str) -> None:
    """Günün portföy değerini kaydeder (upsert: aynı gün ikinci çağrı günceller).
    Cron ve brifing üretimi çağırır — gün Europe/Istanbul takvimindedir."""
    ps = portfolio_summary(s, user_id)
    snap = s.scalar(
        select(ValuationSnapshot).where(
            ValuationSnapshot.user_id == user_id, ValuationSnapshot.day == day
        )
    )
    if snap is None:
        s.add(
            ValuationSnapshot(
                user_id=user_id,
                day=day,
                total_value=ps.total_value_base,
                invested=ps.total_invested_base,
                base_currency=ps.base_currency,
            )
        )
    else:
        snap.total_value = ps.total_value_base
        snap.invested = ps.total_invested_base
        snap.base_currency = ps.base_currency


def _tx_day_ist(dt: datetime) -> str:
    """İşlem zamanını IST gününe eşler (SQLite naive-UTC döndürür — normalize)."""
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(IST_TZ).strftime("%Y-%m-%d")


def _daily_flows(s: Session, user_id: int) -> dict[str, float]:
    """Gün başına net dış akış (baz para varsayımı: işlem para birimi ≈ baz;
    farklı para birimli işlemlerde akış o günkü kur olmadan yaklaşık kalır).
    Alış = +maliyet (fiyat*adet+komisyon), satış = -(hasılat-komisyon)."""
    flows: dict[str, float] = {}
    for tx in s.scalars(select(Transaction).where(Transaction.user_id == user_id)):
        day = _tx_day_ist(tx.executed_at)
        if tx.side == TxSide.BUY:
            amount = tx.price * tx.quantity + tx.fee
        else:
            amount = -(tx.price * tx.quantity - tx.fee)
        try:
            amount = convert(s, amount, tx.currency, settings.base_currency)
        except PricingError:
            pass  # kur yoksa nominal tutarla devam — yaklaşık akış sıfırdan iyidir
        flows[day] = flows.get(day, 0.0) + amount
    return flows


def compute_returns(s: Session, user_id: int) -> dict:
    """Zaman-ağırlıklı getiri (TWR): r_t = (V_t - F_t) / V_{t-1} - 1, zincirlenir.
    F_t = (önceki kayıt günü, bu kayıt günü] aralığındaki net dış akış — para
    yatırıp çıkarmak getiri sayılmaz. Dönemler: 1g / 7g / 30g / başlangıç."""
    snaps = list(
        s.scalars(
            select(ValuationSnapshot)
            .where(ValuationSnapshot.user_id == user_id)
            .order_by(ValuationSnapshot.day)
        )
    )
    series = [
        {"day": sn.day, "total_value": round(sn.total_value, 2), "invested": round(sn.invested, 2)}
        for sn in snaps
    ]
    result: dict = {
        "days": len(snaps),
        "base_currency": snaps[-1].base_currency if snaps else settings.base_currency,
        "series": series,
        "returns": {"d1": None, "d7": None, "d30": None, "inception": None},
    }
    if len(snaps) < 2:
        return result

    flows = _daily_flows(s, user_id)

    def flow_between(start_day: str, end_day: str) -> float:
        # (start_day, end_day] — gün dizgileri YYYY-MM-DD, sözlük sırası = tarih sırası
        return sum(v for d, v in flows.items() if start_day < d <= end_day)

    # Zincir halkaları + kümülatif endeks (100'den başlar)
    index = [100.0]
    for prev, cur in zip(snaps, snaps[1:]):
        if prev.total_value <= 0:
            index.append(index[-1])  # boş portföyden başlayan halka: getiri tanımsız, nötr geç
            continue
        r = (cur.total_value - flow_between(prev.day, cur.day)) / prev.total_value - 1.0
        index.append(index[-1] * (1.0 + r))

    def window_return(days_back: int) -> float | None:
        cutoff = (
            date.fromisoformat(snaps[-1].day) - timedelta(days=days_back)
        ).strftime("%Y-%m-%d")
        base_i = None
        for i, sn in enumerate(snaps):
            if sn.day <= cutoff:
                base_i = i
        if base_i is None or index[base_i] <= 0:
            return None
        return (index[-1] / index[base_i] - 1.0) * 100.0

    d1 = (index[-1] / index[-2] - 1.0) * 100.0 if index[-2] > 0 else None
    result["returns"] = {
        "d1": round(d1, 2) if d1 is not None else None,
        "d7": (lambda v: round(v, 2) if v is not None else None)(window_return(7)),
        "d30": (lambda v: round(v, 2) if v is not None else None)(window_return(30)),
        "inception": round((index[-1] / 100.0 - 1.0) * 100.0, 2),
    }
    return result

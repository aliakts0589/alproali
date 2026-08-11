"""Demo seed — Ali's example portfolio (all figures are demo placeholders)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from alpro.core.models import AssetClass, Instrument, TxSide
from alpro.portfolio.engine import add_transaction

INSTRUMENTS = [
    # symbol, name, asset_class, currency, coingecko_id, tefas_code
    ("THYAO", "Türk Hava Yolları", AssetClass.EQUITY_BIST, "TRY", None, None),
    ("ASELS", "Aselsan", AssetClass.EQUITY_BIST, "TRY", None, None),
    ("TCELL", "Turkcell", AssetClass.EQUITY_BIST, "TRY", None, None),
    ("XAU-GRAM", "Gram Altın", AssetClass.GOLD, "TRY", None, None),
    ("BTC", "Bitcoin", AssetClass.CRYPTO, "TRY", "bitcoin", None),
    ("ETH", "Ethereum", AssetClass.CRYPTO, "TRY", "ethereum", None),
    ("USD", "ABD Doları", AssetClass.FX, "TRY", None, None),
    ("DMO-FON", "Demo Serbest Fon", AssetClass.FUND_TEFAS, "TRY", None, "DMO"),
]

# side, symbol, qty, price, fee, date
TRANSACTIONS = [
    (TxSide.BUY, "THYAO", 500, 285.00, 45.0, "2026-03-10"),
    (TxSide.BUY, "ASELS", 300, 92.40, 25.0, "2026-04-02"),
    (TxSide.BUY, "XAU-GRAM", 150, 2450.00, 0.0, "2026-02-20"),
    (TxSide.BUY, "BTC", 0.15, 2600000.00, 390.0, "2026-05-15"),
    (TxSide.BUY, "USD", 5000, 38.60, 0.0, "2026-01-25"),
    (TxSide.BUY, "DMO-FON", 40000, 8.9120, 0.0, "2026-06-01"),
    # a partial take-profit to exercise realized P/L
    (TxSide.SELL, "THYAO", 100, 305.00, 12.0, "2026-07-21"),
]


def seed_demo(s: Session) -> dict[str, int]:
    created_i = 0
    for symbol, name, ac, ccy, cg, tefas in INSTRUMENTS:
        if s.scalar(select(Instrument).where(Instrument.symbol == symbol)):
            continue
        s.add(
            Instrument(
                symbol=symbol, name=name, asset_class=ac, currency=ccy,
                coingecko_id=cg, tefas_code=tefas,
            )
        )
        created_i += 1
    s.flush()

    created_t = 0
    from alpro.core.models import Transaction

    if s.scalar(select(Transaction.id).limit(1)) is None:
        for side, symbol, qty, price, fee, day in TRANSACTIONS:
            add_transaction(
                s, symbol, side, qty, price, fee,
                executed_at=datetime.fromisoformat(day).replace(tzinfo=timezone.utc),
                note="demo seed",
            )
            created_t += 1
    return {"instruments": created_i, "transactions": created_t}

"""Clearly-labeled offline demo data.

These numbers are PLACEHOLDERS for development and demos — they are not
market data and are always stored with source="demo-fixture" and the
license note "DEMO verisi — gerçek piyasa verisi değildir", which the
briefing/UI must surface. Live connectors replace them the moment real
sources (EVDS key, vendor feed, network) are available.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpro.data.base import FxObs, PriceObs

SOURCE = "demo-fixture"
NOTE = "DEMO verisi — gerçek piyasa verisi değildir"


def _yesterday_close() -> datetime:
    d = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
    return d - timedelta(days=1)


# symbol -> (price, prev_price, currency)
_PRICES: dict[str, tuple[float, float, str]] = {
    # BIST equities (vendor agreement pending — Faz 0 action item)
    "THYAO": (312.50, 308.75, "TRY"),
    "ASELS": (101.20, 102.10, "TRY"),
    "TCELL": (118.40, 117.20, "TRY"),
    # Gold (gram)
    "XAU-GRAM": (2735.00, 2718.00, "TRY"),
    # Crypto fallbacks (used only if CoinGecko is unreachable)
    "BTC": (2845000.0, 2801000.0, "TRY"),
    "ETH": (142000.0, 143900.0, "TRY"),
    # Demo TEFAS fund
    "DMO-FON": (9.4812, 9.4310, "TRY"),
}

_FX: dict[str, tuple[float, float]] = {
    "USDTRY": (43.20, 43.05),
    "EURTRY": (46.85, 46.90),
}


def fixture_prices(symbols: list[str] | None = None) -> list[PriceObs]:
    asof = _yesterday_close()
    out: list[PriceObs] = []
    for symbol, (price, prev, ccy) in _PRICES.items():
        if symbols is not None and symbol not in symbols:
            continue
        change = (price / prev - 1.0) * 100.0
        out.append(
            PriceObs(
                symbol=symbol,
                asof=asof,
                price=price,
                currency=ccy,
                day_change_pct=round(change, 2),
                source=SOURCE,
                license_note=NOTE,
            )
        )
    return out


def fixture_fx() -> list[FxObs]:
    asof = _yesterday_close()
    return [
        FxObs(pair=pair, asof=asof, rate=rate, source=SOURCE, license_note=NOTE)
        for pair, (rate, _prev) in _FX.items()
    ]

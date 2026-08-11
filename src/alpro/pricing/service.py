"""Pricing service — latest observations + base-currency conversion.

Single gateway for "what is X worth right now": everything above it
(portfolio engine, AI tools) gets prices ONLY through this service, so
provenance (source/asof/license) is never lost on the way up.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from alpro.core.models import FxRate, Instrument, PricePoint


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    currency: str
    asof: datetime
    source: str
    license_note: str
    day_change_pct: float | None


class PricingError(Exception):
    pass


def latest_quote(s: Session, instrument: Instrument) -> Quote | None:
    pp = s.scalar(
        select(PricePoint)
        .where(PricePoint.instrument_id == instrument.id)
        .order_by(PricePoint.asof.desc(), PricePoint.fetched_at.desc())
        .limit(1)
    )
    if pp is None:
        # FX instruments (e.g. USD held against TRY) are priced from the
        # FX table: 1 unit of the symbol in the instrument's quote currency.
        from alpro.core.models import AssetClass

        if instrument.asset_class == AssetClass.FX:
            fx = latest_fx(s, f"{instrument.symbol}{instrument.currency}")
            if fx is not None:
                return Quote(
                    symbol=instrument.symbol,
                    price=fx.rate,
                    currency=instrument.currency,
                    asof=fx.asof,
                    source=fx.source,
                    license_note=fx.license_note,
                    day_change_pct=None,
                )
        return None
    return Quote(
        symbol=instrument.symbol,
        price=pp.price,
        currency=pp.currency,
        asof=pp.asof,
        source=pp.source,
        license_note=pp.license_note,
        day_change_pct=pp.day_change_pct,
    )


def latest_fx(s: Session, pair: str) -> FxRate | None:
    return s.scalar(
        select(FxRate)
        .where(FxRate.pair == pair)
        .order_by(FxRate.asof.desc(), FxRate.fetched_at.desc())
        .limit(1)
    )


def convert(s: Session, amount: float, from_ccy: str, to_ccy: str) -> float:
    """Convert via stored FX pairs (direct or inverse). Raises if no path."""
    from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
    if from_ccy == to_ccy:
        return amount
    direct = latest_fx(s, f"{from_ccy}{to_ccy}")
    if direct is not None:
        return amount * direct.rate
    inverse = latest_fx(s, f"{to_ccy}{from_ccy}")
    if inverse is not None and inverse.rate != 0:
        return amount / inverse.rate
    raise PricingError(f"no FX path {from_ccy}->{to_ccy}")

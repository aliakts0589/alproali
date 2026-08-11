"""Connector contract: fetch → validate → normalize → store → report.

Every source implements `Connector`. Two hard rules:
1. Whatever happens upstream, the app keeps working — if live fetch fails
   (or network is disabled) a connector may fall back to clearly-labeled
   fixture data (`source="demo-fixture"`), never to silent staleness.
2. Every stored observation carries source + asof + license note, so the
   AI layer can disclose freshness ("15 dk gecikmeli", "DEMO verisi").
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from alpro.core.models import ConnectorRun, FxRate, Instrument, PricePoint

log = logging.getLogger(__name__)


@dataclass
class PriceObs:
    symbol: str
    asof: datetime
    price: float
    currency: str
    day_change_pct: float | None = None
    source: str = "unknown"
    license_note: str = ""


@dataclass
class FxObs:
    pair: str  # "USDTRY"
    asof: datetime
    rate: float
    source: str = "unknown"
    license_note: str = ""


@dataclass
class RunReport:
    connector: str
    ok: bool
    mode: str  # "live" | "fixture" | "skipped"
    points_written: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)


class ValidationError(Exception):
    pass


def validate_price(obs: PriceObs) -> None:
    if not (obs.price > 0):
        raise ValidationError(f"{obs.symbol}: non-positive price {obs.price}")
    if obs.day_change_pct is not None and abs(obs.day_change_pct) > 60:
        raise ValidationError(
            f"{obs.symbol}: implausible daily change {obs.day_change_pct:.1f}%"
        )


def store_prices(s: Session, observations: list[PriceObs]) -> int:
    """Upsert-by-uniqueness; returns number of rows written."""
    written = 0
    for obs in observations:
        validate_price(obs)
        inst = s.scalar(select(Instrument).where(Instrument.symbol == obs.symbol))
        if inst is None:
            log.warning("unknown instrument %s — skipped", obs.symbol)
            continue
        exists = s.scalar(
            select(PricePoint.id).where(
                PricePoint.instrument_id == inst.id,
                PricePoint.asof == obs.asof,
                PricePoint.source == obs.source,
            )
        )
        if exists:
            continue
        s.add(
            PricePoint(
                instrument_id=inst.id,
                asof=obs.asof,
                price=obs.price,
                currency=obs.currency,
                day_change_pct=obs.day_change_pct,
                source=obs.source,
                license_note=obs.license_note,
            )
        )
        written += 1
    return written


def store_fx(s: Session, observations: list[FxObs]) -> int:
    written = 0
    for obs in observations:
        if not (obs.rate > 0):
            raise ValidationError(f"{obs.pair}: non-positive rate {obs.rate}")
        exists = s.scalar(
            select(FxRate.id).where(
                FxRate.pair == obs.pair,
                FxRate.asof == obs.asof,
                FxRate.source == obs.source,
            )
        )
        if exists:
            continue
        s.add(
            FxRate(
                pair=obs.pair,
                asof=obs.asof,
                rate=obs.rate,
                source=obs.source,
                license_note=obs.license_note,
            )
        )
        written += 1
    return written


def record_run(s: Session, report: RunReport) -> None:
    s.add(
        ConnectorRun(
            connector=report.connector,
            ok=report.ok,
            mode=report.mode,
            points_written=report.points_written,
            message=report.message[:400],
        )
    )


class Connector:
    """Base class — subclasses implement `run(session) -> RunReport`."""

    name = "base"

    def run(self, s: Session) -> RunReport:  # pragma: no cover — interface
        raise NotImplementedError

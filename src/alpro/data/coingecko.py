"""CoinGecko connector — crypto spot prices (free tier, no key).

Live endpoint: /api/v3/simple/price with 24h change. Falls back to the
labeled fixture set when the network is unavailable or disabled.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from alpro.config import settings
from alpro.core.models import AssetClass, Instrument
from alpro.data.base import Connector, PriceObs, RunReport, record_run, store_prices
from alpro.data.fixtures import fixture_prices

log = logging.getLogger(__name__)

API = "https://api.coingecko.com/api/v3/simple/price"


class CoinGeckoConnector(Connector):
    name = "coingecko"

    def run(self, s: Session) -> RunReport:
        instruments = list(
            s.scalars(
                select(Instrument).where(
                    Instrument.asset_class == AssetClass.CRYPTO,
                    Instrument.coingecko_id.is_not(None),
                )
            )
        )
        if not instruments:
            report = RunReport(self.name, ok=True, mode="skipped", message="no crypto instruments")
            record_run(s, report)
            return report

        if settings.allow_network:
            try:
                observations = self._fetch_live(instruments)
                written = store_prices(s, observations)
                report = RunReport(self.name, ok=True, mode="live", points_written=written)
                record_run(s, report)
                return report
            except Exception as exc:  # graceful degradation, never silent
                log.warning("coingecko live fetch failed: %s", exc)
                message = f"live failed ({type(exc).__name__}); fixture fallback"
        else:
            message = "network disabled; fixture fallback"

        symbols = [i.symbol for i in instruments]
        written = store_prices(s, fixture_prices(symbols))
        report = RunReport(
            self.name, ok=True, mode="fixture", points_written=written, message=message
        )
        record_run(s, report)
        return report

    def _fetch_live(self, instruments: list[Instrument]) -> list[PriceObs]:
        ids = ",".join(i.coingecko_id for i in instruments if i.coingecko_id)
        vs = {i.currency.lower() for i in instruments}
        params = {
            "ids": ids,
            "vs_currencies": ",".join(sorted(vs)),
            "include_24hr_change": "true",
        }
        resp = httpx.get(API, params=params, timeout=settings.http_timeout_s)
        resp.raise_for_status()
        payload = resp.json()
        asof = datetime.now(timezone.utc).replace(microsecond=0)
        out: list[PriceObs] = []
        for inst in instruments:
            data = payload.get(inst.coingecko_id or "", {})
            ccy = inst.currency.lower()
            if ccy not in data:
                log.warning("coingecko: %s missing %s quote", inst.symbol, ccy)
                continue
            out.append(
                PriceObs(
                    symbol=inst.symbol,
                    asof=asof,
                    price=float(data[ccy]),
                    currency=inst.currency,
                    day_change_pct=(
                        round(float(data[f"{ccy}_24h_change"]), 2)
                        if data.get(f"{ccy}_24h_change") is not None
                        else None
                    ),
                    source="coingecko",
                    license_note="CoinGecko ücretsiz katman",
                )
            )
        if not out:
            raise RuntimeError("coingecko returned no usable quotes")
        return out

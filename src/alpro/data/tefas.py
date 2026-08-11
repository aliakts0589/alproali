"""TEFAS connector — Turkish mutual/pension fund NAVs (discovery version).

Sprint-1 scope: pull latest NAV for configured fund codes from the public
TEFAS endpoint. Commercial-use terms are a Faz 0 action item (Takasbank
data service), so this connector is behind an explicit opt-in: it only
goes live when ALPRO_TEFAS_FUNDS is configured; otherwise it uses the
demo fixture fund.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from alpro.config import settings
from alpro.core.models import AssetClass, Instrument
from alpro.data.base import Connector, PriceObs, RunReport, record_run, store_prices
from alpro.data.fixtures import fixture_prices

log = logging.getLogger(__name__)

API = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"


class TefasConnector(Connector):
    name = "tefas"

    def run(self, s: Session) -> RunReport:
        live_codes = settings.tefas_funds
        if live_codes and settings.allow_network:
            try:
                observations = self._fetch_live(list(live_codes))
                written = store_prices(s, observations)
                report = RunReport(self.name, ok=True, mode="live", points_written=written)
                record_run(s, report)
                return report
            except Exception as exc:
                log.warning("tefas live fetch failed: %s", exc)
                message = f"live failed ({type(exc).__name__}); fixture fallback"
        elif not live_codes:
            message = "ALPRO_TEFAS_FUNDS tanımlı değil; demo fon kullanılıyor"
        else:
            message = "network disabled; fixture fallback"

        fund_symbols = [
            i.symbol
            for i in s.scalars(
                select(Instrument).where(Instrument.asset_class == AssetClass.FUND_TEFAS)
            )
        ]
        written = store_prices(s, fixture_prices(fund_symbols))
        report = RunReport(
            self.name, ok=True, mode="fixture", points_written=written, message=message
        )
        record_run(s, report)
        return report

    def _fetch_live(self, codes: list[str]) -> list[PriceObs]:
        # Son 7 günü sorgula: NAV gün sonu/ertesi iş günü yayımlanır; hafta
        # sonu-tatilde "bugün" boş döner (kod inceleme bulgusu #7).
        today = datetime.now(timezone.utc)
        start = today - timedelta(days=7)
        out: list[PriceObs] = []
        for code in codes:
            payload = {
                "fontip": "YAT",
                "fonkod": code,
                "bastarih": start.strftime("%d.%m.%Y"),
                "bittarih": today.strftime("%d.%m.%Y"),
            }
            resp = httpx.post(API, data=payload, timeout=settings.http_timeout_s)
            resp.raise_for_status()
            rows = resp.json().get("data", [])
            if not rows:
                log.warning("tefas: no rows for %s", code)
                continue
            row = rows[-1]
            # TARIH arrives as epoch-millis string; FIYAT as float
            asof = datetime.fromtimestamp(int(row["TARIH"]) / 1000, tz=timezone.utc)
            out.append(
                PriceObs(
                    symbol=code,
                    asof=asof,
                    price=float(row["FIYAT"]),
                    currency="TRY",
                    source="tefas",
                    license_note="TEFAS gün sonu birim fiyat",
                )
            )
        if not out:
            raise RuntimeError("tefas returned no usable rows")
        return out

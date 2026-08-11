"""TCMB EVDS connector — official FX (and later macro series).

EVDS requires a free API key (https://evds2.tcmb.gov.tr). With
EVDS_API_KEY set and network available it pulls official USD/TRY and
EUR/TRY selling rates; otherwise it falls back to labeled fixtures so
the rest of the product keeps working.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from alpro.config import settings
from alpro.data.base import Connector, FxObs, RunReport, record_run, store_fx
from alpro.data.fixtures import fixture_fx

log = logging.getLogger(__name__)

API = "https://evds2.tcmb.gov.tr/service/evds/"
SERIES = {
    "USDTRY": "TP.DK.USD.S.YTL",
    "EURTRY": "TP.DK.EUR.S.YTL",
}


class EvdsConnector(Connector):
    name = "evds"

    def run(self, s: Session) -> RunReport:
        if settings.evds_api_key and settings.allow_network:
            try:
                observations = self._fetch_live()
                written = store_fx(s, observations)
                report = RunReport(self.name, ok=True, mode="live", points_written=written)
                record_run(s, report)
                return report
            except Exception as exc:
                log.warning("evds live fetch failed: %s", exc)
                message = f"live failed ({type(exc).__name__}); fixture fallback"
        elif not settings.evds_api_key:
            message = "EVDS_API_KEY yok"
        else:
            message = "network disabled; fixture fallback"

        # Anahtarsız canlı yedek: open.er-api.com (ücretsiz, anahtar istemez)
        if settings.allow_network:
            try:
                observations = self._fetch_erapi()
                written = store_fx(s, observations)
                report = RunReport(
                    self.name, ok=True, mode="live", points_written=written,
                    message=message + "; er-api canlı yedeği kullanıldı",
                )
                record_run(s, report)
                return report
            except Exception as exc:
                log.warning("er-api fallback failed: %s", exc)
                message += "; er-api başarısız"

        written = store_fx(s, fixture_fx())
        report = RunReport(
            self.name, ok=True, mode="fixture", points_written=written, message=message
        )
        record_run(s, report)
        return report

    def _fetch_erapi(self) -> list[FxObs]:
        resp = httpx.get("https://open.er-api.com/v6/latest/USD", timeout=settings.http_timeout_s)
        resp.raise_for_status()
        j = resp.json()
        rates = j.get("rates") or {}
        if not rates.get("TRY") or not rates.get("EUR"):
            raise RuntimeError("er-api: TRY/EUR yok")
        asof = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        return [
            FxObs(pair="USDTRY", asof=asof, rate=float(rates["TRY"]),
                  source="open.er-api.com", license_note="ücretsiz açık API"),
            FxObs(pair="EURTRY", asof=asof, rate=float(rates["TRY"]) / float(rates["EUR"]),
                  source="open.er-api.com", license_note="ücretsiz açık API"),
        ]

    def _fetch_live(self) -> list[FxObs]:
        # Ask for the last 7 days and keep the most recent published value
        # (weekends/holidays have no observation).
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=7)
        series = "-".join(SERIES.values())
        url = (
            f"{API}series={series}"
            f"&startDate={start.strftime('%d-%m-%Y')}&endDate={end.strftime('%d-%m-%Y')}"
            "&type=json"
        )
        resp = httpx.get(
            url, headers={"key": settings.evds_api_key or ""}, timeout=settings.http_timeout_s
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        out: list[FxObs] = []
        for pair, code in SERIES.items():
            field = code.replace(".", "_")
            latest_value: float | None = None
            latest_date: datetime | None = None
            for row in items:
                raw = row.get(field)
                if raw in (None, ""):
                    continue
                latest_value = float(raw)
                latest_date = datetime.strptime(row["Tarih"], "%d-%m-%Y").replace(
                    tzinfo=timezone.utc
                )
            if latest_value is None or latest_date is None:
                log.warning("evds: no observation for %s", pair)
                continue
            out.append(
                FxObs(
                    pair=pair,
                    asof=latest_date,
                    rate=latest_value,
                    source="tcmb-evds",
                    license_note="TCMB gösterge kuru (satış)",
                )
            )
        if not out:
            raise RuntimeError("evds returned no usable observations")
        return out

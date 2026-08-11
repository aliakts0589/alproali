"""BIST demo connector — placeholder until the vendor agreement lands.

Roadmap §5.3: real BIST data arrives through a licensed vendor
(Matriks / Foreks / iDeal — Faz 0 quote requests). Until that contract
is signed this connector serves ONLY labeled fixture prices, so the
product experience (portfolio, briefing) can be built and tested today
without misrepresenting demo numbers as market data.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from alpro.core.models import AssetClass, Instrument
from alpro.data.base import Connector, RunReport, record_run, store_prices
from alpro.data.fixtures import fixture_prices


class BistDemoConnector(Connector):
    name = "bist-demo"

    def run(self, s: Session) -> RunReport:
        symbols = [
            i.symbol
            for i in s.scalars(
                select(Instrument).where(
                    Instrument.asset_class.in_(
                        [AssetClass.EQUITY_BIST, AssetClass.GOLD]
                    )
                )
            )
        ]
        written = store_prices(s, fixture_prices(symbols))
        report = RunReport(
            self.name,
            ok=True,
            mode="fixture",
            points_written=written,
            message="vendör anlaşması bekleniyor (Faz 0) — DEMO fiyatlar",
        )
        record_run(s, report)
        return report


def run_all(s: Session) -> list[RunReport]:
    """Convenience: run every registered connector in order."""
    from alpro.data.coingecko import CoinGeckoConnector
    from alpro.data.evds import EvdsConnector
    from alpro.data.news import NewsConnector
    from alpro.data.tefas import TefasConnector

    connectors: list[Connector] = [
        EvdsConnector(),
        CoinGeckoConnector(),
        TefasConnector(),
        BistDemoConnector(),
        NewsConnector(),
    ]
    return [c.run(s) for c in connectors]

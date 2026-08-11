"""Google News RSS connector — headlines for watched BIST equities.

Live source is the public Google News RSS search feed (no key, stdlib
XML parsing — no new dependency). Same contract as every connector:
fetch → validate → normalize → store → report, and when the network is
unavailable it falls back to clearly-labeled DEMO headlines
(source="demo") instead of going silent.
"""
from __future__ import annotations

import html
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from alpro.config import settings
from alpro.core.models import AssetClass, Instrument, NewsItem, utcnow
from alpro.data.base import Connector, RunReport, record_run

log = logging.getLogger(__name__)

RSS_URL = "https://news.google.com/rss/search"
LIVE_SOURCE = "google-news-rss"
DEMO_SOURCE = "demo"
MAX_SYMBOLS = 8          # watchlist cap — keeps the fetch cheap and polite
PER_SYMBOL_KEEP = 5      # newest N headlines stored per symbol
TITLE_MAX = 300
LINK_MAX = 500


@dataclass
class NewsObs:
    symbol: str
    title: str
    link: str
    published: datetime
    source: str


def _store_news(s: Session, observations: list[NewsObs]) -> int:
    """Upsert-by-uniqueness on (symbol, link); returns rows written."""
    written = 0
    seen: set[tuple[str, str]] = set()
    for obs in observations:
        title = html.unescape(obs.title).strip()[:TITLE_MAX]
        link = obs.link.strip()[:LINK_MAX]
        if not title or not link:
            continue
        key = (obs.symbol, link)
        if key in seen:  # duplicate inside the same batch
            continue
        seen.add(key)
        exists = s.scalar(
            select(NewsItem.id).where(
                NewsItem.symbol == obs.symbol, NewsItem.link == link
            )
        )
        if exists:  # unique-constraint hit → skip, keep the original row
            continue
        s.add(
            NewsItem(
                symbol=obs.symbol,
                title=title,
                link=link,
                published=obs.published,
                source=obs.source,
            )
        )
        written += 1
    return written


def _fixture_news(symbols: list[str]) -> list[NewsObs]:
    """Labeled DEMO headlines — stable links make repeated runs idempotent."""
    now = utcnow()
    out: list[NewsObs] = []
    for symbol in symbols:
        out.append(
            NewsObs(
                symbol=symbol,
                title=f"{symbol} hakkında örnek haber başlığı (DEMO)",
                link=f"https://alpro.local/demo-news/{symbol.lower()}-guncel",
                published=now - timedelta(hours=2),
                source=DEMO_SOURCE,
            )
        )
        out.append(
            NewsObs(
                symbol=symbol,
                title=f"{symbol} için örnek piyasa değerlendirmesi (DEMO)",
                link=f"https://alpro.local/demo-news/{symbol.lower()}-analiz",
                published=now - timedelta(hours=26),
                source=DEMO_SOURCE,
            )
        )
    return out


class NewsConnector(Connector):
    name = "google-news"

    def run(self, s: Session) -> RunReport:
        symbols = [
            i.symbol
            for i in s.scalars(
                select(Instrument)
                .where(Instrument.asset_class == AssetClass.EQUITY_BIST)
                .order_by(Instrument.id)
                .limit(MAX_SYMBOLS)
            )
        ]
        if not symbols:
            report = RunReport(
                self.name, ok=True, mode="skipped", message="izlenecek BIST hissesi yok"
            )
            record_run(s, report)
            return report

        if settings.allow_network:
            try:
                observations = self._fetch_live(symbols)
                written = _store_news(s, observations)
                report = RunReport(
                    self.name, ok=True, mode="live", points_written=written
                )
                record_run(s, report)
                return report
            except Exception as exc:  # graceful degradation, never silent
                log.warning("google news live fetch failed: %s", exc)
                message = f"live failed ({type(exc).__name__}); fixture fallback"
        else:
            message = "network disabled; fixture fallback"

        written = _store_news(s, _fixture_news(symbols))
        report = RunReport(
            self.name,
            ok=True,
            mode="fixture",
            points_written=written,
            message=message + " — DEMO haber başlıkları",
        )
        record_run(s, report)
        return report

    def _fetch_live(self, symbols: list[str]) -> list[NewsObs]:
        out: list[NewsObs] = []
        for symbol in symbols:
            try:
                out.extend(self._fetch_symbol(symbol))
            except Exception as exc:  # one bad feed must not sink the rest
                log.warning("google news: %s feed failed: %s", symbol, exc)
        if not out:
            raise RuntimeError("google news returned no usable items")
        return out

    def _fetch_symbol(self, symbol: str) -> list[NewsObs]:
        params = {"q": f"{symbol} hisse", "hl": "tr", "gl": "TR", "ceid": "TR:tr"}
        resp = httpx.get(
            RSS_URL, params=params, timeout=settings.http_timeout_s,
            follow_redirects=True,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items: list[NewsObs] = []
        for node in root.findall(".//item"):
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            if not title or not link:
                continue
            pub_raw = (node.findtext("pubDate") or "").strip()
            try:
                published = parsedate_to_datetime(pub_raw)  # RFC822
            except (TypeError, ValueError):
                published = utcnow()
            items.append(
                NewsObs(
                    symbol=symbol,
                    title=title,
                    link=link,
                    published=published,
                    source=LIVE_SOURCE,
                )
            )
        items.sort(key=lambda n: n.published, reverse=True)
        return items[:PER_SYMBOL_KEEP]

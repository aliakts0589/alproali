"""Tool registry — the "Program" half of "AI + Program".

Every capability the AI (or any client) can use is a typed tool with a
JSON-serializable result. The orchestrator and the briefing pipeline
consume ONLY these tools; the LLM never computes numbers itself.

Sprint-1 tools:
- get_portfolio_summary
- get_market_overview
- get_quote
- get_data_status
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from alpro.core.models import ConnectorRun, Instrument, NewsItem
from alpro.portfolio.engine import portfolio_summary
from alpro.pricing.service import latest_fx, latest_quote


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: Callable[..., dict[str, Any]]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------- tools

def get_portfolio_summary(s: Session, user_id: int | None = None) -> dict[str, Any]:
    ps = portfolio_summary(s, user_id)
    return {
        "base_currency": ps.base_currency,
        "total_value": round(ps.total_value_base, 2),
        "total_invested": round(ps.total_invested_base, 2),
        "total_unrealized_pl": round(ps.total_unrealized_pl_base, 2),
        "total_unrealized_pl_pct": (
            round(ps.total_unrealized_pl_base / ps.total_invested_base * 100.0, 2)
            if ps.total_invested_base
            else None
        ),
        "asof": _iso(ps.asof),
        "positions": [
            {
                "symbol": p.instrument.symbol,
                "name": p.instrument.name,
                "asset_class": p.instrument.asset_class.value,
                "quantity": p.quantity,
                "avg_cost": round(p.avg_cost, 4),
                "price": p.quote.price if p.quote else None,
                "currency": p.quote.currency if p.quote else None,
                "price_asof": _iso(p.quote.asof) if p.quote else None,
                "price_source": p.quote.source if p.quote else None,
                "license_note": p.quote.license_note if p.quote else None,
                "day_change_pct": p.quote.day_change_pct if p.quote else None,
                "market_value_base": (
                    round(p.market_value_base, 2) if p.market_value_base is not None else None
                ),
                "unrealized_pl": (
                    round(p.unrealized_pl, 2) if p.unrealized_pl is not None else None
                ),
                "unrealized_pl_pct": (
                    round(p.unrealized_pl_pct, 2) if p.unrealized_pl_pct is not None else None
                ),
                "weight_pct": round(p.weight_pct, 2) if p.weight_pct is not None else None,
                "warnings": p.warnings,
            }
            for p in ps.positions
        ],
        "data_notes": ps.data_notes,
    }


def get_market_overview(s: Session) -> dict[str, Any]:
    """FX + every instrument that has a quote — the briefing's market block."""
    out: dict[str, Any] = {"fx": [], "instruments": []}
    for pair in ("USDTRY", "EURTRY"):
        fx = latest_fx(s, pair)
        if fx:
            out["fx"].append(
                {
                    "pair": pair,
                    "rate": fx.rate,
                    "asof": _iso(fx.asof),
                    "source": fx.source,
                    "license_note": fx.license_note,
                }
            )
    for inst in s.scalars(select(Instrument).order_by(Instrument.symbol)):
        q = latest_quote(s, inst)
        if q is None:
            continue
        out["instruments"].append(
            {
                "symbol": inst.symbol,
                "name": inst.name,
                "asset_class": inst.asset_class.value,
                "price": q.price,
                "currency": q.currency,
                "day_change_pct": q.day_change_pct,
                "asof": _iso(q.asof),
                "source": q.source,
                "license_note": q.license_note,
            }
        )
    return out


def get_quote(s: Session, symbol: str) -> dict[str, Any]:
    inst = s.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
    if inst is None:
        return {"error": f"unknown symbol: {symbol}"}
    q = latest_quote(s, inst)
    if q is None:
        return {"error": f"no price data for {symbol}"}
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "price": q.price,
        "currency": q.currency,
        "day_change_pct": q.day_change_pct,
        "asof": _iso(q.asof),
        "source": q.source,
        "license_note": q.license_note,
    }


def _as_utc(dt: datetime) -> datetime:
    """SQLite hands back naive UTC datetimes; normalize before comparing."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _news_dict(n: NewsItem) -> dict[str, Any]:
    return {
        "symbol": n.symbol,
        "title": n.title,
        "link": n.link,
        "published": _iso(n.published),
        "source": n.source,
        "fetched_at": _iso(n.fetched_at),
    }


def get_news(s: Session, symbol: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Newest stored headlines, optionally filtered by symbol (global data)."""
    limit = max(1, min(int(limit), 50))
    stmt = select(NewsItem)
    if symbol:
        stmt = stmt.where(NewsItem.symbol == symbol.upper())
    stmt = stmt.order_by(NewsItem.published.desc(), NewsItem.id.desc()).limit(limit)
    return {"news": [_news_dict(n) for n in s.scalars(stmt)]}


def get_watchlist_news(
    s: Session,
    symbols: list[str],
    now: datetime | None = None,
    hours: int = 48,
    per_symbol: int = 2,
) -> list[dict[str, Any]]:
    """Briefing feed: newest headlines of the given symbols within `hours`,
    capped at `per_symbol` each. The recency filter runs in Python so naive
    (SQLite) and aware timestamps compare safely."""
    if not symbols:
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=hours)
    out: list[dict[str, Any]] = []
    for sym in symbols:
        rows = s.scalars(
            select(NewsItem)
            .where(NewsItem.symbol == sym)
            .order_by(NewsItem.published.desc(), NewsItem.id.desc())
            .limit(per_symbol * 5)  # small headroom before the recency filter
        )
        kept = [n for n in rows if _as_utc(n.published) >= cutoff][:per_symbol]
        out.extend(_news_dict(n) for n in kept)
    return out


def get_data_status(s: Session) -> dict[str, Any]:
    """Last run per connector — powers the data-status disclosure."""
    runs: dict[str, dict[str, Any]] = {}
    for run in s.scalars(select(ConnectorRun).order_by(ConnectorRun.started_at)):
        runs[run.connector] = {
            "connector": run.connector,
            "ok": run.ok,
            "mode": run.mode,
            "points_written": run.points_written,
            "message": run.message,
            "at": _iso(run.started_at),
        }
    return {"connectors": list(runs.values())}


REGISTRY: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool("get_portfolio_summary", "Kullanıcının portföy özeti (değer, K/Z, ağırlıklar).", get_portfolio_summary),
        Tool("get_market_overview", "Piyasa görünümü: kurlar ve izlenen enstrümanlar.", get_market_overview),
        Tool("get_quote", "Tek enstrüman için son fiyat.", get_quote),
        Tool("get_news", "İzlenen semboller için en yeni haber başlıkları.", get_news),
        Tool("get_data_status", "Veri bağlayıcılarının son çalışma durumu.", get_data_status),
    ]
}

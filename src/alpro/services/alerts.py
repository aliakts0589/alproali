"""Price alerts — server-side evaluation against the pricing service.

`check_alerts` runs after every data refresh (background loop) and inside
the daily cron, so alerts fire even when no client is connected. Firing
is one-shot: the alert flips to inactive and records when/at what price
it fired; the user sees it in the briefing ("TETİKLENEN ALARMLAR") and
on the panel.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from alpro.core.models import Alert, Instrument, utcnow
from alpro.pricing.service import latest_quote

log = logging.getLogger(__name__)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def check_alerts(s: Session, now: datetime | None = None) -> int:
    """Evaluate every active alert against the latest quote; returns fired count.

    Prices come ONLY from the pricing service (FX fallback included), so
    provenance rules hold here too. Missing quotes are skipped silently —
    the alert simply stays armed until data exists.
    """
    now = now or utcnow()
    fired = 0
    for alert in s.scalars(select(Alert).where(Alert.active.is_(True))):
        inst = s.scalar(select(Instrument).where(Instrument.symbol == alert.symbol))
        if inst is None:
            continue
        quote = latest_quote(s, inst)
        if quote is None:
            continue
        hit = (
            quote.price >= alert.level
            if alert.direction == "above"
            else quote.price <= alert.level
        )
        if not hit:
            continue
        alert.active = False
        alert.fired_at = now
        alert.fired_price = quote.price
        fired += 1
        log.info(
            "alert fired: user=%s %s %s %.6g → price %.6g (%s)",
            alert.user_id, alert.symbol, alert.direction, alert.level,
            quote.price, quote.source,
        )
        # TODO(Faz 2 devamı): e-posta bildirimi — email_mode "smtp"/"resend"
        # entegrasyonu gelince tetiklenen alarm buradan mail atacak.
    return fired


def recent_fired_alerts(
    s: Session, user_id: int, now: datetime | None = None, hours: int = 24
) -> list[dict[str, Any]]:
    """User's alerts that fired within the last `hours` — briefing feed."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=hours)
    out: list[dict[str, Any]] = []
    rows = s.scalars(
        select(Alert)
        .where(Alert.user_id == user_id, Alert.fired_at.is_not(None))
        .order_by(Alert.fired_at.desc())
    )
    for a in rows:
        if _as_utc(a.fired_at) < cutoff:
            continue
        out.append(
            {
                "symbol": a.symbol,
                "direction": a.direction,
                "level": a.level,
                "fired_price": a.fired_price,
                "fired_at": a.fired_at.isoformat(),
            }
        )
    return out

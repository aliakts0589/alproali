"""Morning briefing pipeline — the "Günaydın Ali" experience (roadmap §6.5).

Deterministic template first: every figure comes from the tool layer.
If an LLM key is configured the wording is polished by the adapter, then
AUDITED — polished output with ungrounded numbers or advice patterns is
rejected and the template ships instead. Trust beats eloquence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from alpro.config import settings
from alpro.core.formatting import fmt_money, fmt_num, fmt_pct
from alpro.ai.guardrails import (
    contains_personal_advice,
    ensure_disclaimer,
    ungrounded_numbers,
)
from alpro.ai.llm import LLMAdapter
from alpro.ai.tools import (
    get_data_status,
    get_market_overview,
    get_portfolio_summary,
    get_watchlist_news,
)
from alpro.services.alerts import recent_fired_alerts

_DAYS_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
_MONTHS_TR = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]

_CLASS_TR = {
    "equity_bist": "BIST hissesi",
    "fund_tefas": "yatırım fonu",
    "pension_bes": "BES",
    "fx": "döviz",
    "gold": "altın",
    "crypto": "kripto",
    "bond": "tahvil",
    "eurobond": "eurobond",
    "commodity": "emtia",
}


@dataclass
class Briefing:
    text: str
    generated_at: datetime
    used_llm: bool
    tool_payload: dict[str, Any]
    audit_notes: list[str]


def _date_line_tr(now: datetime) -> str:
    return f"{now.day} {_MONTHS_TR[now.month - 1]} {now.year} {_DAYS_TR[now.weekday()]}"


def _fmt_asof(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        return f"{dt.day} {_MONTHS_TR[dt.month - 1]}"
    except ValueError:
        return ""


def _market_lines(market: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if market["fx"]:
        parts = []
        for fx in market["fx"]:
            pair = f"{fx['pair'][:3]}/{fx['pair'][3:]}"
            parts.append(f"{pair} {fmt_num(fx['rate'])}")
        src = market["fx"][0]
        note = src["license_note"] or src["source"]
        asof = _fmt_asof(src["asof"])
        lines.append(f"• Kurlar: {' · '.join(parts)}  ({note}; {asof})")
    for item in market["instruments"]:
        if item["asset_class"] not in ("crypto", "gold"):
            continue
        chg = (
            f", günlük {fmt_pct(item['day_change_pct'])}"
            if item.get("day_change_pct") is not None
            else ""
        )
        lines.append(
            f"• {item['name']}: {fmt_money(item['price'], item['currency'])}{chg}"
            f"  ({item['license_note'] or item['source']})"
        )
    return lines


def _portfolio_lines(portfolio: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    total = portfolio["total_value"]
    pl = portfolio["total_unrealized_pl"]
    pl_pct = portfolio["total_unrealized_pl_pct"]
    lines.append(
        f"• Toplam değer: {fmt_money(total, portfolio['base_currency'])}"
        + (
            f" · Açık K/Z: {fmt_money(pl, portfolio['base_currency'])}"
            f" ({fmt_pct(pl_pct)})"
            if pl_pct is not None
            else ""
        )
    )
    for p in portfolio["positions"]:
        if p["price"] is None:
            lines.append(f"• {p['symbol']}: fiyat verisi yok ({', '.join(p['warnings'])})")
            continue
        chg = (
            f" · günlük {fmt_pct(p['day_change_pct'])}"
            if p.get("day_change_pct") is not None
            else ""
        )
        plp = (
            f" · K/Z {fmt_pct(p['unrealized_pl_pct'])}"
            if p.get("unrealized_pl_pct") is not None
            else ""
        )
        weight = (
            f" · ağırlık {fmt_pct(p['weight_pct'], signed=False)}"
            if p.get("weight_pct") is not None
            else ""
        )
        lines.append(
            f"• {p['symbol']} ({_CLASS_TR.get(p['asset_class'], p['asset_class'])}): "
            f"{fmt_num(p['quantity'], 4).rstrip('0').rstrip(',')} adet · "
            f"{fmt_money(p['price'], p.get('currency') or 'TRY')}"
            f"{chg}{plp}{weight}"
        )
    return lines


def _highlight_lines(portfolio: dict[str, Any]) -> list[str]:
    """Descriptive highlights — never directive (SPK boundary)."""
    lines: list[str] = []
    movers = [
        p for p in portfolio["positions"] if p.get("day_change_pct") is not None
    ]
    if movers:
        top = max(movers, key=lambda p: abs(p["day_change_pct"]))
        if abs(top["day_change_pct"]) >= 1.0:
            lines.append(
                f"• Günün en hareketlisi {top['symbol']}: {fmt_pct(top['day_change_pct'])}."
            )
    # concentration by asset class
    weights: dict[str, float] = {}
    for p in portfolio["positions"]:
        if p.get("weight_pct"):
            weights[p["asset_class"]] = weights.get(p["asset_class"], 0.0) + p["weight_pct"]
    if weights:
        cls, w = max(weights.items(), key=lambda kv: kv[1])
        if w >= 50.0:
            lines.append(
                f"• Portföyünün {fmt_pct(w, signed=False)} kadarı tek varlık sınıfında "
                f"({_CLASS_TR.get(cls, cls)}) yoğunlaşmış durumda."
            )
    big_pl = [
        p
        for p in portfolio["positions"]
        if p.get("unrealized_pl_pct") is not None and abs(p["unrealized_pl_pct"]) >= 10.0
    ]
    for p in big_pl[:2]:
        yon = "üzerinde" if p["unrealized_pl_pct"] > 0 else "altında"
        lines.append(
            f"• {p['symbol']} pozisyonu ortalama maliyetinin "
            f"{fmt_pct(abs(p['unrealized_pl_pct']), signed=False)} {yon}."
        )
    return lines


def _alert_lines(alerts: list[dict[str, Any]]) -> list[str]:
    """Fired alerts (last 24h) — factual, never directive."""
    lines: list[str] = []
    for a in alerts:
        cond = "üzeri" if a["direction"] == "above" else "altı"
        price = (
            f" — son fiyat {fmt_num(a['fired_price'])}"
            if a.get("fired_price") is not None
            else ""
        )
        lines.append(
            f"• {a['symbol']}: {fmt_num(a['level'])} {cond} alarmı tetiklendi{price}."
        )
    return lines


def _news_lines(news: list[dict[str, Any]]) -> list[str]:
    """Watchlist headlines with their source label; DEMO stays visible."""
    lines: list[str] = []
    for n in news:
        label = "DEMO — örnek haber" if n["source"] == "demo" else n["source"]
        lines.append(f"• {n['symbol']}: {n['title']}  ({label})")
    return lines


def _data_note_lines(portfolio: dict[str, Any], status: dict[str, Any]) -> list[str]:
    lines = [f"• {note}" for note in portfolio["data_notes"]]
    fixture_connectors = [
        c["connector"] for c in status["connectors"] if c["mode"] == "fixture"
    ]
    if fixture_connectors:
        lines.append(
            "• DEMO modundaki kaynaklar: "
            + ", ".join(sorted(fixture_connectors))
            + " — canlı bağlantı/lisans eklendiğinde otomatik gerçek veriye geçer."
        )
    return lines


def build_briefing(
    s: Session, user_id: int | None = None, now: datetime | None = None
) -> Briefing:
    now = now or datetime.now().astimezone()
    # Çok kullanıcılı selamlama: kullanıcının adı, yoksa e-posta yerel kısmı;
    # user_id verilmemişse (CLI/tek kullanıcı) eski davranış korunur.
    display_name = settings.user_name
    if user_id is not None:
        from alpro.core.models import User

        user = s.get(User, user_id)
        if user is not None:
            display_name = user.name or user.email.split("@", 1)[0]
    portfolio = get_portfolio_summary(s, user_id)
    market = get_market_overview(s)
    status = get_data_status(s)
    # Watchlist = the user's HELD BIST equities; headlines from the last 48h,
    # at most two per symbol. Alerts are per-user, so CLI mode (no user) skips.
    held_bist = [
        p["symbol"] for p in portfolio["positions"] if p["asset_class"] == "equity_bist"
    ]
    news = get_watchlist_news(s, held_bist, now=now)
    fired_alerts = recent_fired_alerts(s, user_id, now=now) if user_id is not None else []
    # news/alerts ride in the tool payload: number-grounding walks strings too,
    # so figures inside headlines are auto-whitelisted for the LLM audit.
    payload = {
        "portfolio": portfolio,
        "market": market,
        "status": status,
        "news": news,
        "alerts": fired_alerts,
    }

    sections: list[str] = []
    sections.append(f"Günaydın {display_name} — {_date_line_tr(now)}")
    sections.append("")
    sections.append("PİYASA GÖRÜNÜMÜ")
    sections.extend(_market_lines(market) or ["• Bugün için piyasa verisi bulunamadı."])
    sections.append("")
    sections.append("PORTFÖYÜN")
    if portfolio["positions"]:
        sections.extend(_portfolio_lines(portfolio))
    else:
        sections.append("• Henüz işlem kaydı yok. İlk işlemini `alpro demo` ile veya API'den ekleyebilirsin.")
    highlights = _highlight_lines(portfolio)
    if highlights:
        sections.append("")
        sections.append("DİKKAT ÇEKENLER")
        sections.extend(highlights)
    if fired_alerts:
        sections.append("")
        sections.append("TETİKLENEN ALARMLAR")
        sections.extend(_alert_lines(fired_alerts))
    if news:
        sections.append("")
        sections.append("İZLEME LİSTESİ HABERLERİ")
        sections.extend(_news_lines(news))
    notes = _data_note_lines(portfolio, status)
    if notes:
        sections.append("")
        sections.append("VERİ NOTLARI")
        sections.extend(notes)

    draft = "\n".join(sections)
    audit_notes: list[str] = []
    used_llm = False

    adapter = LLMAdapter()
    if adapter.available():
        polished = adapter.polish(draft, payload)
        if polished:
            bad_numbers = ungrounded_numbers(polished, payload)
            advice = contains_personal_advice(polished)
            if bad_numbers:
                audit_notes.append(f"LLM çıktısı reddedildi: kaynaksız sayılar {bad_numbers[:5]}")
            elif advice:
                audit_notes.append(f"LLM çıktısı reddedildi: tavsiye kalıbı {advice}")
            else:
                draft = polished
                used_llm = True

    # The template itself must also pass the advice filter — belt and braces.
    advice_in_draft = contains_personal_advice(draft)
    if advice_in_draft:
        audit_notes.append(f"UYARI: şablon tavsiye filtresine takıldı: {advice_in_draft}")

    text = ensure_disclaimer(draft)
    return Briefing(
        text=text,
        generated_at=now,
        used_llm=used_llm,
        tool_payload=payload,
        audit_notes=audit_notes,
    )

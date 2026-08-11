"""Faz 2 / dilim 1 testleri — haber katmanı + sunucu tarafı alarmlar.

Offline (ağ kapalı): haber bağlayıcısı etiketli DEMO fixture'a düşer.
Senaryolar: fixture haber → tablo + /api/news + brifing bölümü; sayı
topraklaması (başlık içi sayılar payload'dan beyaz-listelenir); alarm
CRUD + kullanıcı izolasyonu; tetikleme (check_alerts + cron) ve brifing
"TETİKLENEN ALARMLAR" bölümü; geçersiz sembol 404.
"""
from __future__ import annotations

import logging
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from alpro.ai.briefing import build_briefing
from alpro.ai.guardrails import contains_personal_advice, ungrounded_numbers
from alpro.core.db import session
from alpro.core.models import (
    Alert,
    AuthSession,
    BriefingRecord,
    MagicLink,
    NewsItem,
    Transaction,
    User,
    UserBackup,
    utcnow,
)
from alpro.data.news import NewsConnector
from alpro.services.alerts import check_alerts

ADMIN_HEADERS = {"X-API-Key": "test-admin-token"}  # conftest ALPRO_API_TOKEN
FOUNDER_EMAIL = "founder@alpro.test"
TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")

THYAO_FIXTURE_PRICE = 312.50  # data/fixtures.py — DEMO fiyat


@pytest.fixture(scope="module")
def client(_db):
    from alpro.api.app import app

    with TestClient(app) as c:
        yield c
    # Temizlik: modülün yarattığı alarmlar, sentetik haberler ve kullanıcılar
    # silinir ki alfabetik olarak sonra koşan test_portfolio/test_validation
    # orijinal (yalnız kurucuya ait) demo defteri görsün.
    with session() as s:
        s.execute(delete(Alert))
        s.execute(delete(NewsItem).where(NewsItem.source == "test"))
        extras = list(s.scalars(select(User).where(User.email != FOUNDER_EMAIL)))
        for u in extras:
            s.execute(delete(Transaction).where(Transaction.user_id == u.id))
            s.execute(delete(UserBackup).where(UserBackup.user_id == u.id))
            s.execute(delete(BriefingRecord).where(BriefingRecord.user_id == u.id))
            s.execute(delete(AuthSession).where(AuthSession.user_id == u.id))
            s.execute(delete(MagicLink).where(MagicLink.email == u.email))
            s.delete(u)
        founder = s.scalar(select(User).where(User.email == FOUNDER_EMAIL))
        if founder is not None:  # cron/brifing kayıtları
            s.execute(delete(BriefingRecord).where(BriefingRecord.user_id == founder.id))


def _login(client: TestClient, caplog, email: str) -> None:
    client.cookies.clear()
    with caplog.at_level(logging.INFO, logger="alpro.api.auth"):
        r = client.post("/auth/request-link", json={"email": email, "consent": True})
    assert r.status_code == 200, r.text
    token = TOKEN_RE.findall(caplog.text)[-1]
    r = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 303
    assert "alpro_session" in client.cookies


def _founder_id() -> int:
    with session() as s:
        return s.scalar(select(User.id).where(User.email == FOUNDER_EMAIL))


# ------------------------------------------------------------ haber katmanı

def test_news_connector_fixture_populates_table(db_session):
    # conftest'teki run_all bir kez koştu → tablo dolu; ikinci koşu idempotent
    report = NewsConnector().run(db_session)
    assert report.ok is True and report.mode == "fixture"
    assert report.points_written == 0  # (symbol, link) benzersizliği → atlandı
    assert "DEMO" in report.message

    for symbol in ("THYAO", "ASELS", "TCELL"):
        rows = list(
            db_session.scalars(select(NewsItem).where(NewsItem.symbol == symbol))
        )
        assert len(rows) == 2  # sembol başına 2 etiketli DEMO haber
        for n in rows:
            assert n.source == "demo"
            assert "(DEMO)" in n.title
            assert len(n.title) <= 300 and len(n.link) <= 500
            assert n.published is not None


def test_news_endpoint_returns_and_filters(client):
    client.cookies.clear()
    assert client.get("/api/news").status_code == 401  # auth şart

    r = client.get("/api/news", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    items = r.json()["news"]
    assert len(items) >= 6  # 3 BIST sembolü × 2 demo haber
    assert {"symbol", "title", "link", "published", "source"} <= set(items[0])

    # sembol filtresi (küçük harf girdisi normalize edilir) + limit
    r = client.get("/api/news?symbol=thyao", headers=ADMIN_HEADERS)
    got = r.json()["news"]
    assert got and all(n["symbol"] == "THYAO" for n in got)
    r = client.get("/api/news?symbol=THYAO&limit=1", headers=ADMIN_HEADERS)
    assert len(r.json()["news"]) == 1
    # limit sınırı: max 50
    assert client.get("/api/news?limit=100", headers=ADMIN_HEADERS).status_code == 422


def test_briefing_includes_watchlist_news(client):
    # Kurucu THYAO ve ASELS tutuyor (demo defter) → son 48 saatin DEMO
    # haberleri "İZLEME LİSTESİ HABERLERİ" bölümünde, kaynak etiketiyle.
    with session() as s:
        b = build_briefing(s, _founder_id())
    assert "İZLEME LİSTESİ HABERLERİ" in b.text
    news_section = b.text.split("İZLEME LİSTESİ HABERLERİ", 1)[1]
    assert "THYAO" in news_section and "ASELS" in news_section
    assert "DEMO — örnek haber" in news_section  # kaynak etiketi korunur
    assert b.tool_payload["news"]  # haberler LLM payload'ına girer
    # sembol başına en çok 2 başlık
    assert sum(1 for n in b.tool_payload["news"] if n["symbol"] == "THYAO") <= 2
    assert contains_personal_advice(b.text) == []


def test_news_titles_ground_numbers_in_payload(client):
    # Başlıktaki sayılar payload üzerinden otomatik beyaz-listelenir
    # (_collect_numbers string'leri de tarar) — doğrulama.
    with session() as s:
        s.add(
            NewsItem(
                symbol="THYAO",
                title="THYAO için 847 milyon TL tutarında örnek sözleşme (TEST)",
                link="https://alpro.local/test-news/thyao-847",
                published=utcnow(),
                source="test",
            )
        )
    with session() as s:
        b = build_briefing(s, _founder_id())
    titles = [n["title"] for n in b.tool_payload["news"]]
    assert any("847" in t for t in titles)
    assert ungrounded_numbers("THYAO haberinde 847 rakamı geçiyor.", b.tool_payload) == []
    # kontrol: payload'da olmayan sayı hâlâ yakalanıyor
    assert ungrounded_numbers("Uydurma 98765 sayısı.", b.tool_payload) != []


# ------------------------------------------------------------ alarmlar

def test_alert_crud_and_isolation(client, caplog):
    client.cookies.clear()
    assert client.get("/api/alerts").status_code == 401  # auth şart

    # A alarm kurar (tetiklenmeyecek kadar yüksek eşik)
    _login(client, caplog, "a@alpro.test")
    r = client.post(
        "/api/alerts", json={"symbol": "THYAO", "direction": "above", "level": 999999}
    )
    assert r.status_code == 200
    a_alert_id = r.json()["id"]
    mine = client.get("/api/alerts").json()["alerts"]
    assert [a["id"] for a in mine] == [a_alert_id]
    assert mine[0]["active"] is True and mine[0]["fired_at"] is None

    # şema doğrulama: level<=0, sonsuz/NaN, geçersiz yön → 422
    assert (
        client.post(
            "/api/alerts", json={"symbol": "THYAO", "direction": "above", "level": -5}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/alerts", json={"symbol": "THYAO", "direction": "sideways", "level": 10}
        ).status_code
        == 422
    )

    # B, A'nın alarmını GÖREMEZ ve SİLEMEZ (404 — id sızdırılmaz)
    _login(client, caplog, "b@alpro.test")
    assert client.get("/api/alerts").json()["alerts"] == []
    assert client.delete(f"/api/alerts/{a_alert_id}").status_code == 404

    # B kendi alarmını kurup silebilir
    r = client.post(
        "/api/alerts", json={"symbol": "ASELS", "direction": "below", "level": 1}
    )
    b_alert_id = r.json()["id"]
    assert client.delete(f"/api/alerts/{b_alert_id}").status_code == 200
    assert client.get("/api/alerts").json()["alerts"] == []

    # A'nın alarmı yerinde duruyor
    _login(client, caplog, "a@alpro.test")
    assert [a["id"] for a in client.get("/api/alerts").json()["alerts"]] == [a_alert_id]


def test_alert_unknown_symbol_404(client):
    client.cookies.clear()
    r = client.post(
        "/api/alerts",
        headers=ADMIN_HEADERS,
        json={"symbol": "YOKBU", "direction": "above", "level": 10},
    )
    assert r.status_code == 404
    assert "bilinmeyen sembol" in r.json()["detail"]


def test_alert_trigger_check_and_briefing(client):
    client.cookies.clear()
    # Fixture fiyatı 312,50'nin ALTINDA "above" eşiği → koşul sağlanır
    r = client.post(
        "/api/alerts",
        headers=ADMIN_HEADERS,
        json={"symbol": "THYAO", "direction": "above", "level": 300.0},
    )
    assert r.status_code == 200
    # kontrol alarmı: koşulu sağlanmayan "below" → tetiklenmemeli
    client.post(
        "/api/alerts",
        headers=ADMIN_HEADERS,
        json={"symbol": "THYAO", "direction": "below", "level": 100.0},
    )

    with session() as s:
        fired = check_alerts(s)
    assert fired == 1

    alerts = client.get("/api/alerts", headers=ADMIN_HEADERS).json()["alerts"]
    by_level = {a["level"]: a for a in alerts}
    hit = by_level[300.0]
    assert hit["active"] is False
    assert hit["fired_at"] is not None
    assert hit["fired_price"] == pytest.approx(THYAO_FIXTURE_PRICE)
    assert by_level[100.0]["active"] is True  # koşulsuz alarm kurulu kalır

    # Brifing: son 24 saatte tetiklenen alarm bölümü + sayıların topraklanması
    with session() as s:
        b = build_briefing(s, _founder_id())
    assert "TETİKLENEN ALARMLAR" in b.text
    alert_section = b.text.split("TETİKLENEN ALARMLAR", 1)[1]
    assert "THYAO" in alert_section and "312,50" in alert_section
    assert b.tool_payload["alerts"]
    assert contains_personal_advice(b.text) == []


def test_cron_daily_runs_check_alerts(client):
    client.cookies.clear()
    # 312,50 <= 999999 → "below" koşulu cron'daki check_alerts ile tetiklenir
    r = client.post(
        "/api/alerts",
        headers=ADMIN_HEADERS,
        json={"symbol": "THYAO", "direction": "below", "level": 999999},
    )
    alert_id = r.json()["id"]

    r = client.post("/internal/cron/daily", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert set(r.json()) == {"generated", "day"}  # yanıt sözleşmesi değişmedi

    with session() as s:
        a = s.get(Alert, alert_id)
        assert a.active is False and a.fired_at is not None
        assert a.fired_price == pytest.approx(THYAO_FIXTURE_PRICE)

    # cron'un ürettiği kurucu brifingi tetiklenen alarmı içerir (cache'ten)
    b = client.get("/api/briefing", headers=ADMIN_HEADERS).json()
    assert b["cached"] is True
    assert "TETİKLENEN ALARMLAR" in b["text"]


def test_full_app_served(client):
    """/app: tam AL PRO istemcisi sunucudan gelir (statik, auth'suz sayfa)."""
    r = client.get("/app")
    assert r.status_code == 200
    assert "AL PRO" in r.text and "Finans İşletim Sistemi" in r.text

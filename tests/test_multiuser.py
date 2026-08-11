"""Çok kullanıcılı çekirdek testleri — offline, starlette TestClient.

Kritik senaryolar: davet kapısı, KVKK rızası, magic link akışı,
cross-user veri izolasyonu, X-API-Key köprüsü (regresyon), günlük
brifing cron'u + geri bildirim metriği, KVKK hesap silme.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from alpro.core.db import session
from alpro.core.models import (
    AuthSession,
    BriefingRecord,
    MagicLink,
    Transaction,
    User,
    UserBackup,
)

ADMIN_HEADERS = {"X-API-Key": "test-admin-token"}  # conftest ALPRO_API_TOKEN
FOUNDER_EMAIL = "founder@alpro.test"
TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")


def _today_ist() -> str:
    return datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d")


@pytest.fixture(scope="module")
def client(_db):
    from alpro.api.app import app

    with TestClient(app) as c:
        yield c
    # Temizlik: bu modülün yarattığı kullanıcılar ve TÜM verileri silinir ki
    # alfabetik olarak sonra koşan test_portfolio/test_validation orijinal
    # (yalnız kurucuya ait) demo defteri görsün.
    with session() as s:
        extras = list(s.scalars(select(User).where(User.email != FOUNDER_EMAIL)))
        for u in extras:
            s.execute(delete(Transaction).where(Transaction.user_id == u.id))
            s.execute(delete(UserBackup).where(UserBackup.user_id == u.id))
            s.execute(delete(BriefingRecord).where(BriefingRecord.user_id == u.id))
            s.execute(delete(AuthSession).where(AuthSession.user_id == u.id))
            s.execute(delete(MagicLink).where(MagicLink.email == u.email))
            s.delete(u)
        founder = s.scalar(select(User).where(User.email == FOUNDER_EMAIL))
        if founder is not None:  # cron'un ürettiği kurucu brifing kayıtları
            s.execute(delete(BriefingRecord).where(BriefingRecord.user_id == founder.id))


def _login(client: TestClient, caplog, email: str) -> None:
    """request-link → linki logdan yakala → verify → cookie jar'da oturum."""
    client.cookies.clear()
    with caplog.at_level(logging.INFO, logger="alpro.api.auth"):
        r = client.post("/auth/request-link", json={"email": email, "consent": True})
    assert r.status_code == 200, r.text
    token = TOKEN_RE.findall(caplog.text)[-1]
    r = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 303
    assert "alpro_session" in client.cookies


# ------------------------------------------------------------ davet kapısı

def test_uninvited_email_rejected(client):
    r = client.post(
        "/auth/request-link", json={"email": "davetsiz@ornek.com", "consent": True}
    )
    assert r.status_code == 403
    assert "davet listesinde değil" in r.json()["detail"]


def test_invalid_email_rejected(client):
    r = client.post("/auth/request-link", json={"email": "gecersiz-adres", "consent": True})
    assert r.status_code == 422


def test_first_signup_requires_consent(client):
    # c@ ilk kez geliyor ve consent=false → 422, kullanıcı YARATILMAZ
    r = client.post("/auth/request-link", json={"email": "c@alpro.test", "consent": False})
    assert r.status_code == 422
    assert "KVKK" in r.json()["detail"]
    with session() as s:
        assert s.scalar(select(User).where(User.email == "c@alpro.test")) is None


# ------------------------------------------------------------ magic link

def test_magic_link_flow(client, caplog):
    client.cookies.clear()
    with caplog.at_level(logging.INFO, logger="alpro.api.auth"):
        r = client.post("/auth/request-link", json={"email": "A@Alpro.Test", "consent": True})
    assert r.status_code == 200
    assert r.json()["message"] == "Giriş bağlantısı e-postana gönderildi."
    token = TOKEN_RE.findall(caplog.text)[-1]

    # ham token DB'de tutulmaz — yalnız sha256 hash'i
    with session() as s:
        link = s.scalar(select(MagicLink).where(MagicLink.email == "a@alpro.test"))
        assert link is not None and link.token_hash != token and len(link.token_hash) == 64

    r = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"

    me = client.get("/api/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "a@alpro.test"  # normalize edilmiş (lower)
    assert body["is_founder"] is False and body["consent_at"] is not None

    # link tek kullanımlık — ikinci tıklama geçersiz
    r = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 400

    # bozuk token da geçersiz sayfası döner
    r = client.get("/auth/verify?token=sahte-token", follow_redirects=False)
    assert r.status_code == 400


def test_logout_kills_session(client, caplog):
    _login(client, caplog, "a@alpro.test")
    assert client.get("/api/me").status_code == 200
    r = client.post("/auth/logout")
    assert r.status_code == 200
    assert client.get("/api/me").status_code == 401


# ------------------------------------------------------ cross-user izolasyon

def test_cross_user_transaction_isolation(client, caplog):
    # A, TCELL alır (demo enstrüman, işlemi olmayan sembol)
    _login(client, caplog, "a@alpro.test")
    r = client.post(
        "/api/transactions",
        json={"symbol": "TCELL", "side": "buy", "quantity": 10, "price": 50.0},
    )
    assert r.status_code == 200
    a_tx_id = r.json()["id"]
    a_list = client.get("/api/transactions").json()["transactions"]
    assert any(t["id"] == a_tx_id for t in a_list)

    # B'nin listesinde A'nın işlemi GÖRÜNMEZ
    _login(client, caplog, "b@alpro.test")
    b_list = client.get("/api/transactions").json()["transactions"]
    assert b_list == []

    # B, A'nın işlemini SİLEMEZ (id sızdırmamak için 404)
    r = client.delete(f"/api/transactions/{a_tx_id}")
    assert r.status_code == 404

    # B'nin portföyü boş; A'nınki TCELL içerir ve işlem hâlâ yerinde
    assert client.get("/api/portfolio/summary").json()["positions"] == []
    _login(client, caplog, "a@alpro.test")
    assert any(t["id"] == a_tx_id for t in client.get("/api/transactions").json()["transactions"])
    symbols = [p["symbol"] for p in client.get("/api/portfolio/summary").json()["positions"]]
    assert symbols == ["TCELL"]


def test_backup_isolation(client, caplog):
    _login(client, caplog, "a@alpro.test")
    r = client.post("/api/backup", json={"app": "alpro", "owner": "A", "savedAt": "t1"})
    assert r.status_code == 200

    _login(client, caplog, "b@alpro.test")
    assert client.get("/api/backup").status_code == 404  # B'nin yedeği yok
    r = client.post("/api/backup", json={"app": "alpro", "owner": "B", "savedAt": "t2"})
    assert r.status_code == 200
    assert client.get("/api/backup").json()["owner"] == "B"

    _login(client, caplog, "a@alpro.test")
    assert client.get("/api/backup").json()["owner"] == "A"  # üzerine yazılmadı


# ------------------------------------------------------ X-API-Key köprüsü

def test_api_key_bridge_maps_to_founder(client):
    """Regresyon: tek-dosya HTML uygulaması X-API-Key ile çalışmayı sürdürür."""
    client.cookies.clear()
    r = client.get("/api/portfolio/summary", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    symbols = [p["symbol"] for p in r.json()["positions"]]
    assert "THYAO" in symbols and "BTC" in symbols  # demo veri kurucuya benimsendi

    me = client.get("/api/me", headers=ADMIN_HEADERS).json()
    assert me["email"] == FOUNDER_EMAIL and me["is_founder"] is True

    # köprüsüz ve oturumsuz istek → 401 (fail-closed)
    assert client.get("/api/portfolio/summary").status_code == 401
    assert client.get("/api/portfolio/summary", headers={"X-API-Key": "yanlis"}).status_code == 401


# ------------------------------------------------- günlük brifing + metrik

def test_cron_daily_generates_and_is_idempotent(client, caplog):
    client.cookies.clear()
    # cron yalnız X-API-Key kabul eder
    assert client.post("/internal/cron/daily").status_code == 401

    with session() as s:
        expected = len(list(s.scalars(select(User.id))))
    r = client.post("/internal/cron/daily", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json() == {"generated": expected, "day": _today_ist()}

    # idempotent: ikinci çağrı aynı sayıyı üretir, kayıt çoğaltmaz
    r2 = client.post("/internal/cron/daily", headers=ADMIN_HEADERS)
    assert r2.json()["generated"] == expected
    with session() as s:
        a = s.scalar(select(User).where(User.email == "a@alpro.test"))
        recs = list(
            s.scalars(
                select(BriefingRecord).where(
                    BriefingRecord.user_id == a.id, BriefingRecord.day == _today_ist()
                )
            )
        )
    assert len(recs) == 1

    # A bugünün brifingini cache'ten alır — selamlama kişiselleştirilmiş
    _login(client, caplog, "a@alpro.test")
    b = client.get("/api/briefing").json()
    assert b["cached"] is True and b["day"] == _today_ist()
    assert b["text"].startswith("Günaydın a ")  # a@alpro.test → "a"


def test_feedback_and_metrics_flow(client, caplog):
    today = _today_ist()
    _login(client, caplog, "a@alpro.test")
    assert (
        client.post("/api/briefing/feedback", json={"day": today, "rating": 1}).status_code
        == 200
    )
    # kaydı olmayan güne geri bildirim → 404
    assert (
        client.post(
            "/api/briefing/feedback", json={"day": "1999-01-01", "rating": 1}
        ).status_code
        == 404
    )

    _login(client, caplog, "b@alpro.test")
    assert (
        client.post("/api/briefing/feedback", json={"day": today, "rating": 0}).status_code
        == 200
    )
    # metrik ucu kurucuya özel
    assert client.get("/api/metrics/briefing").status_code == 403

    client.cookies.clear()
    m = client.get("/api/metrics/briefing", headers=ADMIN_HEADERS).json()
    assert m == {"total_rated": 2, "positive": 1, "satisfaction_pct": 50.0}


# ------------------------------------------------------------ KVKK

def test_kvkk_page_public(client):
    r = client.get("/kvkk")
    assert r.status_code == 200 and "KVKK" in r.text and "silme" in r.text.lower()


def test_account_deletion_erases_everything(client, caplog):
    _login(client, caplog, "c@alpro.test")  # consent=True ile ilk kayıt
    client.post(
        "/api/transactions",
        json={"symbol": "ASELS", "side": "buy", "quantity": 5, "price": 90.0},
    )
    client.post("/api/backup", json={"app": "alpro", "owner": "C"})
    with session() as s:
        c_id = s.scalar(select(User.id).where(User.email == "c@alpro.test"))

    r = client.delete("/api/me")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/me").status_code == 401  # oturum da silindi

    with session() as s:
        assert s.scalar(select(User).where(User.email == "c@alpro.test")) is None
        assert list(s.scalars(select(Transaction).where(Transaction.user_id == c_id))) == []
        assert s.scalar(select(UserBackup).where(UserBackup.user_id == c_id)) is None
        assert s.scalar(select(MagicLink).where(MagicLink.email == "c@alpro.test")) is None

    # kurucu hesap silinemez
    client.cookies.clear()
    r = client.delete("/api/me", headers=ADMIN_HEADERS)
    assert r.status_code == 400

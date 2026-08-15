"""Deploy-günü hazırlık testleri — offline.

Kapsam: Resend magic-link gönderimi (mod seçimi, başarı, fail-open),
request-link'in gönderim başarısına göre log davranışı, kurucuya özel
SQLite yedek indirme ucu.
"""
from __future__ import annotations

import dataclasses
import logging
import re

import pytest
from fastapi.testclient import TestClient

from alpro.api import mailer
from alpro.config import settings

ADMIN_HEADERS = {"X-API-Key": "test-admin-token"}  # conftest ALPRO_API_TOKEN
TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")


@pytest.fixture(scope="module")
def client(_db):
    from alpro.api.app import app

    with TestClient(app) as c:
        yield c


def _settings_with(**overrides):
    return dataclasses.replace(settings, **overrides)


# ------------------------------------------------------------ mod seçimi

def test_auto_mode_uses_resend_only_with_key(monkeypatch):
    monkeypatch.setattr(mailer, "settings", _settings_with(email_mode="auto"))
    assert mailer.effective_mode() == "console"
    monkeypatch.setattr(
        mailer, "settings", _settings_with(email_mode="auto", resend_api_key="re_x")
    )
    assert mailer.effective_mode() == "resend"


def test_console_mode_never_sends(monkeypatch):
    monkeypatch.setattr(
        mailer, "settings", _settings_with(email_mode="console", resend_api_key="re_x")
    )

    def _boom(*a, **k):  # pragma: no cover — çağrılmamalı
        raise AssertionError("console modunda HTTP çağrısı yapılmamalı")

    monkeypatch.setattr(mailer.httpx, "post", _boom)
    assert mailer.send_magic_link("a@alpro.test", "https://x/auth/verify?token=t") is False


# ------------------------------------------------------------ gönderim

class _FakeResponse:
    def __init__(self, fail: bool = False):
        self._fail = fail

    def raise_for_status(self):
        if self._fail:
            raise RuntimeError("500 Internal Server Error")


def test_resend_send_success(monkeypatch):
    monkeypatch.setattr(
        mailer,
        "settings",
        _settings_with(email_mode="resend", resend_api_key="re_test", allow_network=True),
    )
    calls = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls.update(url=url, headers=headers, json=json)
        return _FakeResponse()

    monkeypatch.setattr(mailer.httpx, "post", _fake_post)
    link = "https://alpro.example/auth/verify?token=abc123"
    assert mailer.send_magic_link("a@alpro.test", link) is True
    assert calls["url"] == mailer.RESEND_API_URL
    assert calls["headers"]["Authorization"] == "Bearer re_test"
    assert calls["json"]["to"] == ["a@alpro.test"]
    assert link in calls["json"]["html"]  # link e-postanın içinde


def test_resend_failure_falls_open(monkeypatch):
    """Sağlayıcı hatası girişi kilitlemez: False döner, link loga düşecek."""
    monkeypatch.setattr(
        mailer,
        "settings",
        _settings_with(email_mode="resend", resend_api_key="re_test", allow_network=True),
    )
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: _FakeResponse(fail=True))
    assert mailer.send_magic_link("a@alpro.test", "https://x/auth/verify?token=t") is False


def test_network_off_blocks_send(monkeypatch):
    monkeypatch.setattr(
        mailer,
        "settings",
        _settings_with(email_mode="resend", resend_api_key="re_test", allow_network=False),
    )

    def _boom(*a, **k):  # pragma: no cover — çağrılmamalı
        raise AssertionError("ağ kapalıyken HTTP çağrısı yapılmamalı")

    monkeypatch.setattr(mailer.httpx, "post", _boom)
    assert mailer.send_magic_link("a@alpro.test", "https://x/auth/verify?token=t") is False


# ------------------------------------------- request-link entegrasyonu

def test_request_link_skips_log_when_email_sent(client, caplog, monkeypatch):
    """Gerçek e-posta gittiyse link loga YAZILMAZ (tek kopya ilkesi)."""
    monkeypatch.setattr("alpro.api.mailer.send_magic_link", lambda email, url: True)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="alpro.api.auth"):
        r = client.post(
            "/auth/request-link", json={"email": "a@alpro.test", "consent": True}
        )
    assert r.status_code == 200
    assert "MAGIC LINK" not in caplog.text


def test_request_link_logs_link_on_send_failure(client, caplog, monkeypatch):
    monkeypatch.setattr("alpro.api.mailer.send_magic_link", lambda email, url: False)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="alpro.api.auth"):
        r = client.post(
            "/auth/request-link", json={"email": "a@alpro.test", "consent": True}
        )
    assert r.status_code == 200
    assert TOKEN_RE.findall(caplog.text)  # link loga düştü — giriş kilitlenmedi


# ------------------------------------------------- anahtarla kalıcı giriş

def test_token_login_sets_persistent_session(client):
    client.cookies.clear()
    # yanlış anahtar → 401, çerez yok
    r = client.post("/auth/token-login", json={"token": "yanlis-anahtar"})
    assert r.status_code == 401
    assert "alpro_session" not in client.cookies

    # doğru anahtar → çerez oturumu; sonraki istekler başlıksız çalışır
    r = client.post("/auth/token-login", json={"token": "test-admin-token"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "alpro_session" in client.cookies
    me = client.get("/api/me")  # X-API-Key YOK — çerez yetiyor
    assert me.status_code == 200 and me.json()["is_founder"] is True
    client.cookies.clear()


# ------------------------------------------------------ off-site yedek

def test_db_backup_download_founder_only(client, caplog):
    # oturumsuz → 401
    client.cookies.clear()
    assert client.get("/internal/backup/db").status_code == 401

    # kurucu olmayan (a@ magic link ile girer) → 403
    with caplog.at_level(logging.INFO, logger="alpro.api.auth"):
        r = client.post(
            "/auth/request-link", json={"email": "a@alpro.test", "consent": True}
        )
        assert r.status_code == 200
    token = TOKEN_RE.findall(caplog.text)[-1]
    assert client.get(f"/auth/verify?token={token}", follow_redirects=False).status_code == 303
    assert client.get("/internal/backup/db").status_code == 403

    # kurucu (X-API-Key köprüsü) → gerçek SQLite dosyası iner
    client.cookies.clear()
    r = client.get("/internal/backup/db", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.content.startswith(b"SQLite format 3\x00")
    assert 'attachment; filename="alpro-yedek-' in r.headers["content-disposition"]


def test_db_backup_restore_roundtrip(client):
    """İndir → geri yükle → veri yerinde. Free-plan veri kaybı senaryosunun
    kurtarma yolu: panel 'Veri Yedeği' kartının kullandığı uçlar."""
    client.cookies.clear()
    snapshot = client.get("/internal/backup/db", headers=ADMIN_HEADERS).content

    r = client.post(
        "/internal/backup/db",
        headers={**ADMIN_HEADERS, "Content-Type": "application/octet-stream"},
        content=snapshot,
    )
    assert r.status_code == 200 and r.json()["ok"] is True

    # geri yükleme sonrası portföy hâlâ okunuyor (kurucu demo verisi yerinde)
    p = client.get("/api/portfolio/summary", headers=ADMIN_HEADERS).json()
    symbols = [x["symbol"] for x in p["positions"]]
    assert "THYAO" in symbols


def test_db_restore_rejects_garbage(client):
    client.cookies.clear()
    r = client.post(
        "/internal/backup/db",
        headers={**ADMIN_HEADERS, "Content-Type": "application/octet-stream"},
        content=b"bu bir sqlite dosyasi degil",
    )
    assert r.status_code == 422

    # SQLite başlıklı ama AL PRO şeması olmayan dosya da reddedilir
    import sqlite3 as _sq
    import tempfile as _tmp
    import os as _os

    fd, path = _tmp.mkstemp(suffix=".db")
    _os.close(fd)
    try:
        con = _sq.connect(path)
        con.execute("CREATE TABLE bambaska (x INTEGER)")
        con.commit()
        con.close()
        with open(path, "rb") as f:
            fake = f.read()
    finally:
        _os.unlink(path)
    r = client.post(
        "/internal/backup/db",
        headers={**ADMIN_HEADERS, "Content-Type": "application/octet-stream"},
        content=fake,
    )
    assert r.status_code == 422

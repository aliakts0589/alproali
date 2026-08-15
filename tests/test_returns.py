"""Getiri hesabı (TWR) testleri — offline.

Kapsam: snapshot upsert, akış-düzeltmeli zincirleme getiri matematiği,
/api/portfolio/history ucunun yetkilendirmesi ve şekli. Sentetik kullanıcı
test sonunda tamamen silinir ki alfabetik olarak sonra koşan test_validation
orijinal defteri görsün.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from alpro.core.db import session
from alpro.core.models import Transaction, User, ValuationSnapshot, utcnow
from alpro.portfolio.engine import compute_returns, record_valuation_snapshot

ADMIN_HEADERS = {"X-API-Key": "test-admin-token"}


@pytest.fixture()
def twr_user():
    with session() as s:
        u = User(email="twr@alpro.test", consent_at=utcnow())
        s.add(u)
        s.flush()
        uid = u.id
    yield uid
    with session() as s:
        s.execute(delete(ValuationSnapshot).where(ValuationSnapshot.user_id == uid))
        s.execute(delete(Transaction).where(Transaction.user_id == uid))
        u = s.get(User, uid)
        if u is not None:
            s.delete(u)


def _snap(s, uid: int, day: str, value: float, invested: float = 0.0) -> None:
    s.add(
        ValuationSnapshot(
            user_id=uid, day=day, total_value=value, invested=invested, base_currency="TRY"
        )
    )


def test_snapshot_upsert_is_idempotent(twr_user):
    with session() as s:
        founder_day = "2026-08-15"
        record_valuation_snapshot(s, twr_user, founder_day)
        record_valuation_snapshot(s, twr_user, founder_day)  # ikinci çağrı günceller
    with session() as s:
        rows = list(
            s.scalars(select(ValuationSnapshot).where(ValuationSnapshot.user_id == twr_user))
        )
    assert len(rows) == 1 and rows[0].day == founder_day


def test_twr_no_flows(twr_user):
    """Akış yokken TWR düz değer değişimidir: 1000 → 1100 = +%10."""
    with session() as s:
        _snap(s, twr_user, "2026-08-10", 1000.0)
        _snap(s, twr_user, "2026-08-11", 1100.0)
    with session() as s:
        r = compute_returns(s, twr_user)
    assert r["days"] == 2
    assert r["returns"]["d1"] == 10.0
    assert r["returns"]["inception"] == 10.0


def test_twr_deposit_is_not_return(twr_user):
    """Para yatırmak getiri değildir: 1000 → (500 alış) → 1600.
    TWR halkası: (1600-500)/1000 - 1 = +%10 (basit fark %60 olurdu)."""
    with session() as s:
        _snap(s, twr_user, "2026-08-10", 1000.0)
        _snap(s, twr_user, "2026-08-11", 1600.0)
        # 11 Ağu IST içinde bir alış: 10 adet * 50 TRY = 500 TRY akış
        from alpro.portfolio.engine import add_transaction
        from alpro.core.models import TxSide

        add_transaction(
            s,
            symbol="TCELL",
            side=TxSide.BUY,
            quantity=10,
            price=50.0,
            executed_at=datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc),
            user_id=twr_user,
        )
    with session() as s:
        r = compute_returns(s, twr_user)
    assert r["returns"]["d1"] == 10.0
    assert r["returns"]["inception"] == 10.0


def test_twr_multi_day_chain_and_windows(twr_user):
    """Zincir: +%10 sonra -%5 → kümülatif (1.10*0.95-1) = +%4,5."""
    with session() as s:
        _snap(s, twr_user, "2026-08-10", 1000.0)
        _snap(s, twr_user, "2026-08-11", 1100.0)
        _snap(s, twr_user, "2026-08-12", 1045.0)
    with session() as s:
        r = compute_returns(s, twr_user)
    assert r["returns"]["d1"] == -5.0
    assert r["returns"]["inception"] == 4.5
    # 7 günlük pencere: taban 2026-08-05 ve öncesi — kayıt yok → None
    assert r["returns"]["d7"] is None


def test_single_snapshot_returns_none(twr_user):
    with session() as s:
        _snap(s, twr_user, "2026-08-10", 1000.0)
    with session() as s:
        r = compute_returns(s, twr_user)
    assert r["days"] == 1
    assert all(v is None for v in r["returns"].values())


# ------------------------------------------------------------ endpoint

@pytest.fixture(scope="module")
def client(_db):
    from alpro.api.app import app

    with TestClient(app) as c:
        yield c


def test_history_endpoint_auth_and_shape(client):
    client.cookies.clear()
    assert client.get("/api/portfolio/history").status_code == 401

    r = client.get("/api/portfolio/history", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"days", "series", "returns"}
    assert set(body["returns"]) == {"d1", "d7", "d30", "inception"}

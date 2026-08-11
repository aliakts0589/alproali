"""Kod inceleme bulgularının regresyon testleri (agency-agents Code Reviewer)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpro.core.db import session
from alpro.core.models import TxSide
from alpro.portfolio.engine import LedgerError, add_transaction


def test_backdated_sell_rejected(db_session):
    # Bulgu #2: geçmiş tarihli satış, o tarihte elde yokken kabul edilmemeli
    with pytest.raises(LedgerError):
        add_transaction(
            db_session, "ASELS", TxSide.SELL, 10, 100.0,
            executed_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )


def test_nan_and_infinity_rejected(db_session):
    # Bulgu #3: NaN/Infinity defteri zehirlememeli
    for bad in (float("nan"), float("inf")):
        with pytest.raises(LedgerError):
            add_transaction(db_session, "ASELS", TxSide.BUY, bad, 100.0)
        with pytest.raises(LedgerError):
            add_transaction(db_session, "ASELS", TxSide.BUY, 10, bad)


def test_negative_fee_rejected(db_session):
    with pytest.raises(LedgerError):
        add_transaction(db_session, "ASELS", TxSide.BUY, 10, 100.0, fee=-5)


def test_ungrounded_percent_caught():
    # Bulgu #8: 0-100 beyaz listesi daraltıldı — uydurma %45 artık yakalanır
    from alpro.ai.guardrails import ungrounded_numbers

    payload = {"portfolio": {"total_value": 1000.0}}
    assert ungrounded_numbers("Hisse %45 düşebilir.", payload) != []

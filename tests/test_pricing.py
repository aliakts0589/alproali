from __future__ import annotations

import pytest

from alpro.pricing.service import PricingError, convert


def test_identity_conversion(db_session):
    assert convert(db_session, 100.0, "TRY", "TRY") == 100.0


def test_direct_and_inverse_fx(db_session):
    usd_in_try = convert(db_session, 100.0, "USD", "TRY")
    assert usd_in_try > 100.0  # TRY per USD is > 1
    back = convert(db_session, usd_in_try, "TRY", "USD")
    assert back == pytest.approx(100.0, rel=1e-9)


def test_missing_pair_raises(db_session):
    with pytest.raises(PricingError):
        convert(db_session, 1.0, "GBP", "JPY")

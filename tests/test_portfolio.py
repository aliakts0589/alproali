from __future__ import annotations

import pytest

from alpro.core.models import TxSide
from alpro.portfolio.engine import LedgerError, _positions_from_ledger, portfolio_summary


def test_positions_derived_from_ledger(db_session):
    positions = {p.instrument.symbol: p for p in _positions_from_ledger(db_session)}
    thyao = positions["THYAO"]
    # 500 bought @285 with 45 fee -> avg cost (500*285+45)/500 = 285.09
    assert thyao.quantity == pytest.approx(400)  # 100 sold later
    assert thyao.avg_cost == pytest.approx(285.09, abs=0.01)
    # realized: (305 - 285.09) * 100 - 12 fee
    assert thyao.realized_pl == pytest.approx((305 - 285.09) * 100 - 12, abs=0.5)


def test_sell_more_than_held_raises(db_session):
    from alpro.portfolio.engine import add_transaction

    with pytest.raises(LedgerError):
        add_transaction(db_session, "ASELS", TxSide.SELL, 10_000, 100.0)
        _positions_from_ledger(db_session)


def test_summary_valuation_in_base_currency(db_session):
    ps = portfolio_summary(db_session)
    assert ps.base_currency == "TRY"
    assert ps.total_value_base > 0
    open_positions = {p.instrument.symbol for p in ps.positions}
    assert "THYAO" in open_positions and "BTC" in open_positions
    # weights sum to ~100 for priced positions
    total_weight = sum(p.weight_pct or 0 for p in ps.positions)
    assert total_weight == pytest.approx(100.0, abs=0.5)
    # every priced position carries provenance for the AI layer
    for p in ps.positions:
        if p.quote:
            assert p.quote.source
            assert p.quote.asof is not None

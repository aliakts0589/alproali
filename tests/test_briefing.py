from __future__ import annotations

from alpro.ai.briefing import build_briefing
from alpro.ai.guardrails import DISCLAIMER_TR, contains_personal_advice


def test_briefing_end_to_end(db_session):
    b = build_briefing(db_session)
    assert b.text.startswith("Günaydın Ali")
    # sections present
    for header in ("PİYASA GÖRÜNÜMÜ", "PORTFÖYÜN", "VERİ NOTLARI"):
        assert header in b.text
    # compliance: disclaimer always present, no advice patterns ever
    assert DISCLAIMER_TR[:40] in b.text
    assert contains_personal_advice(b.text) == []
    # no LLM key in tests -> deterministic template
    assert b.used_llm is False


def test_briefing_discloses_demo_sources(db_session):
    b = build_briefing(db_session)
    assert "DEMO" in b.text  # fixture data must never masquerade as market data

from __future__ import annotations

from alpro.ai.guardrails import (
    DISCLAIMER_TR,
    contains_personal_advice,
    ensure_disclaimer,
    ungrounded_numbers,
)


def test_advice_patterns_caught():
    assert contains_personal_advice("Bence THYAO almalısın, kesin çıkar.")
    assert contains_personal_advice("Bunu sat! Hemen.")
    assert contains_personal_advice("Sana ASELS almanı öneririm.")


def test_descriptive_text_is_clean():
    clean = (
        "Portföyünün %62'si tek varlık sınıfında yoğunlaşmış durumda. "
        "THYAO bugün %+1,21 değişimle günün en hareketlisi."
    )
    assert contains_personal_advice(clean) == []


def test_disclaimer_appended_once():
    once = ensure_disclaimer("Merhaba.")
    assert DISCLAIMER_TR[:40] in once
    twice = ensure_disclaimer(once)
    assert twice.count(DISCLAIMER_TR[:40]) == 1


def test_number_grounding():
    payload = {"portfolio": {"total_value": 1245300.5, "positions": [{"price": 312.5}]}}
    ok_text = "Toplam değer 1.245.300,50 ₺; THYAO 312,50 ₺."
    assert ungrounded_numbers(ok_text, payload) == []
    bad_text = "Toplam değer 9.999.999 ₺ oldu."
    assert ungrounded_numbers(bad_text, payload) != []

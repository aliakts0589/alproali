"""Guardrails — the compliance layer (roadmap §4.2, §6.5).

Three jobs:
1. DISCLAIMER: every user-facing analysis ends with the standard notice.
2. ADVICE FILTER: block personalized buy/sell advice patterns (SPK
   boundary — AL PRO describes, it does not direct).
3. NUMBER GROUNDING: in LLM mode, any number in the output must exist in
   the tool payload that produced it ("sayı yalnızca araçtan gelir").
"""
from __future__ import annotations

import re
from typing import Any

DISCLAIMER_TR = (
    "Bu içerik bilgilendirme amaçlıdır; yatırım tavsiyesi değildir. "
    "Yatırım kararlarınızı kendi araştırmanız ve gerekiyorsa yetkili bir "
    "yatırım kuruluşunun danışmanlığıyla veriniz."
)

# Personalized-advice patterns (Turkish, case-insensitive).
_ADVICE_PATTERNS = [
    r"\balmal[ıi]s[ıi]n\b",
    r"\bsatmal[ıi]s[ıi]n\b",
    r"\bal\s*!\B",
    r"\bsat\s*!\B",
    r"\bkesinlikle\s+(al|sat)\b",
    r"\bsana\s+.{0,40}\b(al|sat)man[ıi]\s+öneririm\b",
    r"\b(bunu|şunu)\s+(al|sat)\b",
    r"\btavsiye\s+ederim\b",
    r"\byatırman[ıi]\s+öneririm\b",
]
_ADVICE_RE = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _ADVICE_PATTERNS]

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def contains_personal_advice(text: str) -> list[str]:
    """Return the matched advice patterns (empty list = clean)."""
    return [rx.pattern for rx in _ADVICE_RE if rx.search(text)]


def ensure_disclaimer(text: str, locale: str = "tr") -> str:
    if DISCLAIMER_TR[:40] in text:
        return text
    return f"{text.rstrip()}\n\n---\n{DISCLAIMER_TR}"


def _canon(value: float) -> str:
    """Canonical string for a numeric value (1245300.50 -> '1245300.5')."""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _token_candidates(token: str) -> set[str]:
    """All plausible readings of a textual number token.

    '1.245.300,50' (TR) and '1,245,300.50' (EN) both canonicalize to
    '1245300.5'; ambiguous tokens like '312.50' produce every reading.
    """
    out: set[str] = set()
    tr = token.replace(".", "").replace(",", ".")  # TR: dots=thousands, comma=decimal
    en = token.replace(",", "")                    # EN: commas=thousands, dot=decimal
    for cleaned in (tr, en):
        try:
            out.add(_canon(float(cleaned)))
        except ValueError:
            pass
    return out


def _collect_numbers(payload: Any, acc: set[str]) -> None:
    if isinstance(payload, bool):
        return
    if isinstance(payload, dict):
        for v in payload.values():
            _collect_numbers(v, acc)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            _collect_numbers(v, acc)
    elif isinstance(payload, (int, float)):
        value = float(payload)
        for v in (value, abs(value), round(value, 2), round(abs(value), 2), round(value)):
            acc.add(_canon(float(v)))
    elif isinstance(payload, str):
        # ground numbers embedded in strings too (ISO timestamps, codes)
        for token in _NUM_RE.findall(payload):
            acc |= _token_candidates(token)


def ungrounded_numbers(text: str, tool_payload: Any, tolerance: int = 0) -> list[str]:
    """Numbers present in `text` but absent from `tool_payload`.

    Used to audit LLM-polished output before it may replace the
    deterministic template ("sayı yalnızca araçtan gelir").
    """
    allowed: set[str] = set()
    _collect_numbers(tool_payload, allowed)
    # structural small numbers ("24 saat", list ordinals) and years
    allowed |= {str(n) for n in range(0, 32)}  # yalnızca yapısal küçük sayılar (inceleme #8)
    allowed |= {str(n) for n in range(1990, 2101)}

    bad: list[str] = []
    for token in _NUM_RE.findall(text):
        if not (_token_candidates(token) & allowed):
            bad.append(token)
    return bad[tolerance:] if tolerance else bad

"""Provider-agnostic LLM adapter (roadmap §6.5 "model stratejisi").

Without any API key the adapter reports `available() == False` and the
briefing pipeline uses the deterministic template — the product never
depends on a key. With ANTHROPIC_API_KEY or OPENAI_API_KEY set, the LLM
POLISHES the wording of the template; it receives the tool payload and
is explicitly instructed not to introduce numbers. Output is audited by
guardrails.ungrounded_numbers before use.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from alpro.config import settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Sen AL PRO adlı finans platformunun brifing editörüsün. Görevin, sana "
    "verilen ARAÇ VERİLERİ ve TASLAK METNİ daha akıcı, sıcak ve kısa bir "
    "Türkçe sabah brifingine dönüştürmek. KURALLAR: (1) Yalnızca araç "
    "verilerindeki sayıları kullan; yeni sayı, oran veya fiyat üretme. "
    "(2) Kişiye özel al/sat tavsiyesi verme; 'almalısın', 'satmalısın', "
    "'tavsiye ederim' gibi ifadeler yasak. (3) Veri kaynağı notlarını ve "
    "DEMO uyarılarını koru. (4) Yanıt yalnızca brifing metni olsun."
)


class LLMAdapter:
    def __init__(self) -> None:
        self.provider: str | None = None
        if settings.anthropic_api_key:
            self.provider = "anthropic"
        elif settings.openai_api_key:
            self.provider = "openai"

    def available(self) -> bool:
        return self.provider is not None and settings.allow_network

    def polish(self, draft: str, tool_payload: dict[str, Any]) -> str | None:
        """Return polished text, or None on any failure (caller keeps draft)."""
        if not self.available():
            return None
        user_msg = (
            "ARAÇ VERİLERİ (JSON):\n"
            + json.dumps(tool_payload, ensure_ascii=False, default=str)
            + "\n\nTASLAK METİN:\n"
            + draft
        )
        try:
            if self.provider == "anthropic":
                return self._anthropic(user_msg)
            if self.provider == "openai":
                return self._openai(user_msg)
        except Exception as exc:  # never break the briefing over polish
            log.warning("llm polish failed: %s", exc)
        return None

    def _anthropic(self, user_msg: str) -> str:
        model = settings.llm_model or "claude-sonnet-4-5"
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.anthropic_api_key or "",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 1200,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        blocks = resp.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    def _openai(self, user_msg: str) -> str:
        model = settings.llm_model or "gpt-4o-mini"
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 1200,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

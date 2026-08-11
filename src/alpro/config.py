"""Application settings.

All configuration comes from environment variables (or a local .env file).
Principle: the code base is English; user-facing strings live in the i18n
catalog (alpro.ai.i18n) with Turkish as the first language.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

APP_DIR = Path(os.environ.get("ALPRO_HOME", Path.cwd())).resolve()


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Identity / locale
    user_name: str = os.environ.get("ALPRO_USER_NAME", "Ali")
    locale: str = os.environ.get("ALPRO_LOCALE", "tr")
    base_currency: str = os.environ.get("ALPRO_BASE_CURRENCY", "TRY")

    # Storage — SQLite for the sprint; swap DATABASE_URL for Postgres later.
    database_url: str = os.environ.get(
        "DATABASE_URL", f"sqlite:///{APP_DIR / 'alpro.db'}"
    )

    # Data providers
    evds_api_key: str | None = os.environ.get("EVDS_API_KEY") or None
    allow_network: bool = _bool("ALPRO_ALLOW_NETWORK", True)
    http_timeout_s: float = float(os.environ.get("ALPRO_HTTP_TIMEOUT", "10"))

    # API protection (optional): when set, all API calls require this token
    api_token: str | None = os.environ.get("ALPRO_API_TOKEN") or None

    # Multi-user auth (Faz 1): e-posta + magic link, parola yok.
    # The invite list is the beta gate; the founder is always allowed.
    founder_email: str = os.environ.get("ALPRO_FOUNDER_EMAIL", "founder@local").strip().lower()
    invites: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            e.strip().lower()
            for e in os.environ.get("ALPRO_INVITES", "").split(",")
            if e.strip()
        )
    )
    session_days: int = int(os.environ.get("ALPRO_SESSION_DAYS", "30"))
    link_minutes: int = int(os.environ.get("ALPRO_LINK_MINUTES", "15"))
    # "console": magic link is written to the server log (dev/beta).
    # "smtp"/"resend": real e-mail delivery — lands with the Resend/Brevo integration.
    email_mode: str = os.environ.get("ALPRO_EMAIL_MODE", "console")

    # LLM (optional — the product works without any key: template briefing)
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
    openai_api_key: str | None = os.environ.get("OPENAI_API_KEY") or None
    llm_model: str = os.environ.get("ALPRO_LLM_MODEL", "")

    # Watchlist funds for the TEFAS connector (comma separated fund codes)
    tefas_funds: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            c.strip().upper()
            for c in os.environ.get("ALPRO_TEFAS_FUNDS", "").split(",")
            if c.strip()
        )
    )


settings = Settings()

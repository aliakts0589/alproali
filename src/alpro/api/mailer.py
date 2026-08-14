"""Magic-link e-posta gönderimi (Faz 1 kapanışı: Resend entegrasyonu).

Kanal seçimi (ALPRO_EMAIL_MODE):
- "auto" (varsayılan): RESEND_API_KEY tanımlıysa Resend, değilse console.
- "resend": her zaman Resend dener.
- "console": hiç gönderilmez — link sunucu loguna yazılır (dev/beta).

Fail-open ilkesi: Resend gönderimi hangi sebeple olursa olsun başarısızsa
`send_magic_link` False döndürür ve çağıran linki loga yazar. Giriş akışı
e-posta sağlayıcısı çökse bile kilitlenmez — link logdan iletilebilir.
"""
from __future__ import annotations

import logging

import httpx

from alpro.config import settings

log = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


def effective_mode() -> str:
    if settings.email_mode == "auto":
        return "resend" if settings.resend_api_key else "console"
    return settings.email_mode


def _link_email_html(url: str, minutes: int) -> str:
    return (
        '<div style="font-family:system-ui,sans-serif;max-width:480px;margin:0 auto">'
        "<h2>AL PRO — giriş bağlantın</h2>"
        "<p>Aşağıdaki düğmeye tıklayarak giriş yapabilirsin. Bağlantı tek "
        f"kullanımlıktır ve {minutes} dakika geçerlidir.</p>"
        f'<p><a href="{url}" style="display:inline-block;background:#3987e5;'
        'color:#fff;padding:12px 22px;border-radius:8px;text-decoration:none;'
        'font-weight:600">Giriş yap</a></p>'
        f'<p style="color:#888;font-size:12px">Düğme çalışmazsa: <a href="{url}">{url}</a><br>'
        "Bu e-postayı sen istemediysen görmezden gelebilirsin.</p></div>"
    )


def send_magic_link(email: str, url: str) -> bool:
    """True → gerçek e-posta gönderildi; False → çağıran linki loglamalı."""
    if effective_mode() != "resend":
        return False
    if not settings.resend_api_key:
        log.warning("ALPRO_EMAIL_MODE=resend ama RESEND_API_KEY tanımlı değil")
        return False
    if not settings.allow_network:
        log.info("ağ kapalı (ALPRO_ALLOW_NETWORK=0) — magic link loga yazılacak")
        return False
    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.email_from,
                "to": [email],
                "subject": "AL PRO — giriş bağlantın",
                "html": _link_email_html(url, settings.link_minutes),
            },
            timeout=settings.http_timeout_s,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — sağlayıcı hatası girişi kilitlemesin
        log.warning("Resend gönderimi başarısız (%s) — link loga yazılacak", exc)
        return False
    log.info("magic link e-postası gönderildi (resend): %s", email)
    return True

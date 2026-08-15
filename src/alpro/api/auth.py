"""Authentication — e-posta + magic link (Faz 1 kararı: parola YOK).

Flow: invited e-mail requests a link → we store ONLY the sha256 of the
one-shot token → user clicks /auth/verify → server-side AuthSession is
created and the raw session token travels in an httpOnly cookie.

Two identity paths coexist by design:
1. Cookie session ("alpro_session") — the multi-user path.
2. X-API-Key == RUNTIME_TOKEN — the legacy single-file app / admin-cron
   bridge; it resolves to the FOUNDER account and must keep working.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from alpro.api import mailer
from alpro.config import settings
from alpro.core.db import session
from alpro.core.models import (
    AuthSession,
    BriefingRecord,
    MagicLink,
    Transaction,
    User,
    UserBackup,
    utcnow,
)

log = logging.getLogger(__name__)
# email_mode="console" iken magic link BU logger'dan okunur. Uvicorn doğrudan
# başlatıldığında (ör. Render start command) root logger yapılandırılmaz ve
# INFO kaybolurdu — kendi handler'ımızı garanti ederiz (çift kayıt yok:
# root'ta handler varsa propagation zaten basar).
log.setLevel(logging.INFO)
if not logging.getLogger().handlers:  # pragma: no cover — launch-mode dependent
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    log.addHandler(_handler)

router = APIRouter()

SESSION_COOKIE = "alpro_session"

# Fail-closed auth: ALPRO_API_TOKEN tanımlı değilse açılışta rastgele anahtar
# üretilir ve loglanır — API hiçbir modda anahtarsız çalışmaz (kod inceleme
# bulgusu #1: aksi halde halka açık kurulumda tüm finansal veri ifşa olurdu).
RUNTIME_TOKEN: str = settings.api_token or secrets.token_urlsafe(24)
if not settings.api_token:
    log.warning(
        "ALPRO_API_TOKEN tanımlı değil — geçici anahtar üretildi (panel girişi için): %s",
        RUNTIME_TOKEN,
    )

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _as_utc(dt: datetime) -> datetime:
    """SQLite returns naive UTC datetimes; Postgres returns aware — normalize."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _is_invited(email: str) -> bool:
    return email == settings.founder_email or email in settings.invites


def get_or_create_founder(s) -> User:
    user = s.scalar(select(User).where(User.email == settings.founder_email))
    if user is None:
        user = User(
            email=settings.founder_email,
            name=settings.user_name,
            is_founder=True,
            consent_at=utcnow(),  # operator account — consent implicit at creation
        )
        s.add(user)
        s.flush()
    return user


# ------------------------------------------------------------ dependencies

def require_token(request: Request) -> None:
    """Shared-secret auth — sabit zamanlı karşılaştırma, yalnızca başlıktan.
    Admin/cron uçları için: cookie oturumu burada GEÇERLİ DEĞİLDİR."""
    provided = request.headers.get("x-api-key") or ""
    if not hmac.compare_digest(provided, RUNTIME_TOKEN):
        raise HTTPException(status_code=401, detail="geçersiz veya eksik API anahtarı")


def get_current_user(request: Request) -> User:
    """Resolve the caller: cookie session first, X-API-Key bridge second.

    The bridge keeps the existing single-file HTML app working unchanged:
    a valid RUNTIME_TOKEN maps to the founder account.
    """
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        token_hash = _hash_token(raw)
        with session() as s:
            sess = s.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash))
            if sess is not None:
                if _as_utc(sess.expires_at) <= utcnow():
                    s.delete(sess)  # hygiene: drop the expired row
                else:
                    user = s.get(User, sess.user_id)
                    if user is not None:
                        return user
    provided = request.headers.get("x-api-key") or ""
    if provided and hmac.compare_digest(provided, RUNTIME_TOKEN):
        with session() as s:
            return get_or_create_founder(s)
    raise HTTPException(status_code=401, detail="kimlik doğrulaması gerekli")


# ------------------------------------------------------------ magic link

class RequestLinkIn(BaseModel):
    email: str = Field(min_length=6, max_length=255)
    consent: bool = False  # KVKK — first signup must send true


@router.post("/auth/request-link")
def request_link(payload: RequestLinkIn, request: Request) -> dict:
    email = payload.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="geçersiz e-posta adresi")
    if not _is_invited(email):
        raise HTTPException(status_code=403, detail="davet listesinde değil")

    with session() as s:
        user = s.scalar(select(User).where(User.email == email))
        if user is None:
            if not payload.consent:
                raise HTTPException(status_code=422, detail="KVKK onayı gerekli")
            user = User(
                email=email,
                is_founder=(email == settings.founder_email),
                consent_at=utcnow(),
            )
            s.add(user)
        elif user.consent_at is None and payload.consent:
            user.consent_at = utcnow()

        raw_token = secrets.token_urlsafe(32)
        s.add(
            MagicLink(
                email=email,
                token_hash=_hash_token(raw_token),  # raw token asla saklanmaz
                expires_at=utcnow() + timedelta(minutes=settings.link_minutes),
            )
        )

    url = f"{str(request.base_url).rstrip('/')}/auth/verify?token={raw_token}"
    if not mailer.send_magic_link(email, url):
        # console modu ya da gönderim hatası: link BU logger'dan okunur,
        # operatör iletir (beta). Testler de linki buradan yakalar.
        log.info("MAGIC LINK for %s: %s", email, url)
    return {"ok": True, "message": "Giriş bağlantısı e-postana gönderildi."}


_INVALID_LINK_HTML = """<!DOCTYPE html>
<html lang="tr"><head><meta charset="UTF-8"><title>AL PRO — Giriş</title></head>
<body style="font-family:system-ui;background:#0d0d0d;color:#fff;display:grid;place-items:center;height:100vh">
<div style="text-align:center"><h2>Bağlantı geçersiz ya da süresi dolmuş</h2>
<p><a href="/" style="color:#3987e5">Panele dön</a> ve yeni bir giriş bağlantısı iste.</p></div>
</body></html>"""


@router.get("/auth/verify")
def verify(token: str, request: Request):
    token_hash = _hash_token(token)
    with session() as s:
        link = s.scalar(select(MagicLink).where(MagicLink.token_hash == token_hash))
        if (
            link is None
            or link.used_at is not None
            or _as_utc(link.expires_at) <= utcnow()
        ):
            return HTMLResponse(_INVALID_LINK_HTML, status_code=400)
        link.used_at = utcnow()  # single use

        user = s.scalar(select(User).where(User.email == link.email))
        if user is None:  # deleted between request and click
            return HTMLResponse(_INVALID_LINK_HTML, status_code=400)

        raw_session = secrets.token_urlsafe(32)
        s.add(
            AuthSession(
                user_id=user.id,
                token_hash=_hash_token(raw_session),
                expires_at=utcnow() + timedelta(days=settings.session_days),
            )
        )

    resp = RedirectResponse(url="/", status_code=303)
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    resp.set_cookie(
        SESSION_COOKIE,
        raw_session,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=(forwarded_proto == "https"),
        path="/",
    )
    return resp


class TokenLoginIn(BaseModel):
    token: str = Field(min_length=8, max_length=256)


@router.post("/auth/token-login")
def token_login(payload: TokenLoginIn, request: Request) -> JSONResponse:
    """Erişim anahtarını BİR KEZ girip kalıcı oturum açma (kurucu).

    Panel eskiden anahtarı yalnızca sayfa içinde tutuyordu — her ziyarette
    yeniden soruluyordu. Bu uç, anahtar doğruysa magic-link ile aynı çerez
    oturumunu kurar: aynı cihazda 30 gün boyunca bir daha sorulmaz.
    Güvenlik değişmez: aynı sır, X-API-Key köprüsüyle eşdeğer."""
    if not hmac.compare_digest(payload.token, RUNTIME_TOKEN):
        raise HTTPException(status_code=401, detail="geçersiz erişim anahtarı")
    with session() as s:
        user = get_or_create_founder(s)
        raw_session = secrets.token_urlsafe(32)
        s.add(
            AuthSession(
                user_id=user.id,
                token_hash=_hash_token(raw_session),
                expires_at=utcnow() + timedelta(days=settings.session_days),
            )
        )
    resp = JSONResponse({"ok": True, "message": "Giriş yapıldı — bu cihazda hatırlanacak."})
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    resp.set_cookie(
        SESSION_COOKIE,
        raw_session,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=(forwarded_proto == "https"),
        path="/",
    )
    return resp


@router.post("/auth/logout")
def logout(request: Request) -> JSONResponse:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        with session() as s:
            s.execute(delete(AuthSession).where(AuthSession.token_hash == _hash_token(raw)))
    resp = JSONResponse({"ok": True, "message": "Çıkış yapıldı."})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# ------------------------------------------------------------ account

@router.get("/api/me")
def me(request: Request) -> dict:
    user = get_current_user(request)
    return {
        "email": user.email,
        "name": user.name,
        "is_founder": user.is_founder,
        "consent_at": user.consent_at.isoformat() if user.consent_at else None,
    }


@router.delete("/api/me")
def delete_me(request: Request) -> dict:
    """KVKK silme hakkı: kullanıcının TÜM verileri kalıcı olarak silinir."""
    user = get_current_user(request)
    if user.is_founder:
        raise HTTPException(status_code=400, detail="kurucu hesap silinemez")
    with session() as s:
        s.execute(delete(Transaction).where(Transaction.user_id == user.id))
        s.execute(delete(UserBackup).where(UserBackup.user_id == user.id))
        s.execute(delete(BriefingRecord).where(BriefingRecord.user_id == user.id))
        s.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
        s.execute(delete(MagicLink).where(MagicLink.email == user.email))
        db_user = s.get(User, user.id)
        if db_user is not None:
            s.delete(db_user)
    resp_msg = "Hesabın ve tüm verilerin silindi."
    log.info("KVKK erasure completed for user id=%s", user.id)
    return {"ok": True, "message": resp_msg}


# ------------------------------------------------------------ KVKK notice

_KVKK_HTML = """<!DOCTYPE html>
<html lang="tr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AL PRO — KVKK Aydınlatma Metni</title>
<style>body{font-family:system-ui;background:#0d0d0d;color:#eee;max-width:720px;
margin:0 auto;padding:32px 20px;line-height:1.6}h1{font-size:20px}h2{font-size:15px;color:#c3c2b7}
a{color:#3987e5}</style></head><body>
<h1>KVKK Aydınlatma Metni (özet)</h1>
<p>6698 sayılı Kişisel Verilerin Korunması Kanunu kapsamında, AL PRO beta
hizmetinde işlenen kişisel verilerine ilişkin bilgilendirme:</p>
<h2>Toplanan veriler</h2>
<p>Yalnızca <b>e-posta adresin</b>, istersen adın ve uygulamaya kendi girdiğin
<b>portföy kayıtların</b> (işlemler, yedek dosyası, günlük brifing kayıtları).
Parola toplanmaz; giriş e-postana gönderilen tek kullanımlık bağlantıyla yapılır.</p>
<h2>İşleme amacı</h2>
<p>Hesabına giriş yapabilmen, portföyünü takip edebilmen ve sana özel günlük
brifingin üretilmesi. Verilerin üçüncü taraflarla paylaşılmaz, reklam/profilleme
için kullanılmaz.</p>
<h2>Saklama ve silme hakkın</h2>
<p>Verilerin hesabın açık olduğu sürece saklanır. Dilediğin an hesabını ve tüm
verilerini kalıcı olarak silebilirsin (panelden ya da <code>DELETE /api/me</code>
ucuyla). Erişme, düzeltme ve silme taleplerin için hizmet sahibine e-posta ile
ulaşabilirsin.</p>
<p><a href="/">← Panele dön</a></p>
</body></html>"""


@router.get("/kvkk", response_class=HTMLResponse)
def kvkk() -> str:
    return _KVKK_HTML

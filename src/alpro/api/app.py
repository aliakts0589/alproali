"""Public API + minimal web panel — the Faz-1 server seed.

Same tools the AI uses, exposed over HTTP (API-first). Identity (Faz 1
multi-user core):
- Browser panel: e-posta + magic link → httpOnly cookie session.
- Legacy single-file app / cron: X-API-Key == RUNTIME_TOKEN → founder.
User data (transactions, backup, briefing) is scoped by user_id; market
data stays global (cost is flat regardless of user count).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import asyncio
import json
import logging
import os
import sqlite3
import tempfile
import threading

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from alpro.ai.briefing import build_briefing
from alpro.ai.tools import (
    REGISTRY,
    get_data_status,
    get_market_overview,
    get_news,
    get_portfolio_summary,
    get_quote,
)
from alpro.api.auth import get_current_user, get_or_create_founder, require_token
from alpro.api.auth import router as auth_router
from alpro.config import settings
from alpro.core.db import dispose_engine, init_db, session
from alpro.core.models import (
    Alert,
    BriefingRecord,
    Instrument,
    Transaction,
    TxSide,
    User,
    UserBackup,
    utcnow,
)
from alpro.portfolio.engine import LedgerError, add_transaction
from alpro.services.alerts import check_alerts

log = logging.getLogger(__name__)

# Sentry (isteğe bağlı): SENTRY_DSN tanımlıysa hatalar raporlanır. SDK "ops"
# ekstrasıyla gelir (Dockerfile kurar); yoksa uygulama normal çalışmayı sürdürür.
_SENTRY_DSN = os.environ.get("SENTRY_DSN")
if _SENTRY_DSN:
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=_SENTRY_DSN, traces_sample_rate=0.0, send_default_pii=False)
        log.info("Sentry hata izleme aktif")
    except ImportError:  # pragma: no cover — kurulum moduna bağlı
        log.warning("SENTRY_DSN tanımlı ama sentry-sdk kurulu değil (pip install 'alpro[ops]')")

app = FastAPI(title="AL PRO API", version="0.4.0")
app.include_router(auth_router)

# Tarayıcıdaki AL PRO uygulamasının (file:// dahil) bağlanabilmesi için CORS.
# Kimlik her koşulda token/oturumla sağlanır (fail-closed); origin/metot/başlık dar tutulur.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("ALPRO_ALLOWED_ORIGINS", "*").split(",")],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["X-API-Key", "Content-Type"],
)

_write_lock = threading.Lock()  # SQLite beta: yazmaları serileştir

IST_TZ = ZoneInfo("Europe/Istanbul")


def _today_ist() -> str:
    return datetime.now(IST_TZ).strftime("%Y-%m-%d")


def _do_refresh() -> None:
    from alpro.data.bist_demo import run_all

    with session() as s:
        run_all(s)
        # Taze veri geldi → sunucu tarafı alarmlar hemen değerlendirilir.
        check_alerts(s)


_refresh_task: asyncio.Task | None = None  # referans tutulmazsa task GC ile ölebilir


async def _refresh_loop() -> None:
    """30 dakikada bir veri tazele — senkron iş thread'e taşınır ki
    event loop (ve /health) bloke olmasın (kod inceleme bulgusu #4)."""
    while True:
        try:
            await asyncio.to_thread(_do_refresh)
            log.info("background refresh ok")
        except Exception as exc:  # asla süreci düşürme
            log.warning("background refresh failed: %s", exc)
        await asyncio.sleep(30 * 60)


@app.on_event("startup")
async def _startup() -> None:
    global _refresh_task
    init_db()
    # Tek seferlik benimseme: çok-kullanıcı öncesi (user_id IS NULL) işlemler
    # kurucuya bağlanır — mevcut demo/tek kullanıcı verisi kaybolmaz.
    with session() as s:
        founder = get_or_create_founder(s)
        s.execute(
            update(Transaction)
            .where(Transaction.user_id.is_(None))
            .values(user_id=founder.id)
        )
    _refresh_task = asyncio.create_task(_refresh_loop())


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": "alpro", "version": "0.4.0", "auth": bool(settings.api_token)}


@app.get("/api/tools")
def tools(user: User = Depends(get_current_user)) -> dict:
    return {"tools": [{"name": t.name, "description": t.description} for t in REGISTRY.values()]}


# ------------------------------------------------------- user-scoped reads

@app.get("/api/portfolio/summary")
def portfolio(user: User = Depends(get_current_user)) -> dict:
    with session() as s:
        return get_portfolio_summary(s, user.id)


# ------------------------------------------------- global market data (auth,
# ama kullanıcıya göre filtrelenmez — piyasa verisi herkes için aynı)

@app.get("/api/market/overview")
def market(user: User = Depends(get_current_user)) -> dict:
    with session() as s:
        return get_market_overview(s)


@app.get("/api/quotes/{symbol}")
def quote(symbol: str, user: User = Depends(get_current_user)) -> dict:
    with session() as s:
        result = get_quote(s, symbol)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/api/data/status")
def data_status(user: User = Depends(get_current_user)) -> dict:
    with session() as s:
        return get_data_status(s)


@app.get("/api/news")
def news(
    symbol: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
) -> dict:
    """En yeni haber başlıkları — piyasa verisi gibi global (kullanıcıya göre
    filtrelenmez); kaynak etiketi (google-news-rss | demo) her kayıtta taşınır."""
    with session() as s:
        return get_news(s, symbol=symbol, limit=limit)


# ------------------------------------------------------------ briefing

def _upsert_briefing_record(s, user_id: int, day: str, text: str) -> None:
    """Insert-or-update; mevcut kaydın rating'i korunur (idempotent cron)."""
    rec = s.scalar(
        select(BriefingRecord).where(
            BriefingRecord.user_id == user_id, BriefingRecord.day == day
        )
    )
    if rec is None:
        s.add(BriefingRecord(user_id=user_id, day=day, text=text))
    else:
        rec.text = text


@app.get("/api/briefing")
def briefing(user: User = Depends(get_current_user)) -> dict:
    day = _today_ist()
    with session() as s:
        rec = s.scalar(
            select(BriefingRecord).where(
                BriefingRecord.user_id == user.id, BriefingRecord.day == day
            )
        )
        if rec is not None:
            return {
                "text": rec.text,
                "day": day,
                "cached": True,
                "generated_at": rec.created_at.isoformat(),
            }
        b = build_briefing(s, user.id)
    with _write_lock, session() as s:
        _upsert_briefing_record(s, user.id, day, b.text)
    return {
        "text": b.text,
        "day": day,
        "cached": False,
        "generated_at": b.generated_at.isoformat(),
        "used_llm": b.used_llm,
        "audit_notes": b.audit_notes,
    }


class FeedbackIn(BaseModel):
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    rating: int = Field(ge=0, le=1)  # 1=beğendi, 0=beğenmedi


@app.post("/api/briefing/feedback")
def briefing_feedback(payload: FeedbackIn, user: User = Depends(get_current_user)) -> dict:
    with _write_lock, session() as s:
        rec = s.scalar(
            select(BriefingRecord).where(
                BriefingRecord.user_id == user.id, BriefingRecord.day == payload.day
            )
        )
        if rec is None:
            raise HTTPException(status_code=404, detail="o güne ait brifing kaydı yok")
        rec.rating = payload.rating
    return {"ok": True, "message": "Geri bildirimin kaydedildi, teşekkürler."}


@app.get("/api/metrics/briefing")
def briefing_metrics(user: User = Depends(get_current_user)) -> dict:
    """Kuzey Yıldızı metriği: brifing memnuniyeti (hedef ≥ %60). Yalnız kurucu."""
    if not user.is_founder:
        raise HTTPException(status_code=403, detail="yalnızca kurucu erişebilir")
    with session() as s:
        total_rated = s.scalar(
            select(func.count(BriefingRecord.id)).where(BriefingRecord.rating.is_not(None))
        ) or 0
        positive = s.scalar(
            select(func.count(BriefingRecord.id)).where(BriefingRecord.rating == 1)
        ) or 0
    return {
        "total_rated": total_rated,
        "positive": positive,
        "satisfaction_pct": round(positive / total_rated * 100.0, 1) if total_rated else None,
    }


@app.post("/internal/cron/daily", dependencies=[Depends(require_token)])
def cron_daily() -> dict:
    """Günlük brifing üretimi — cron-job.org buraya vurur (SPRINT-PLAN karar #4).
    Yalnız X-API-Key; idempotent: aynı gün ikinci çağrı metni tazeler,
    rating'lere dokunmaz."""
    day = _today_ist()
    generated = 0
    # Önce alarmlar: yeni tetiklenenler aynı günün brifinglerinde görünsün.
    with _write_lock, session() as s:
        check_alerts(s)
    with session() as s:
        user_ids = list(s.scalars(select(User.id).order_by(User.id)))
    for uid in user_ids:
        with session() as s:
            b = build_briefing(s, uid)
        with _write_lock, session() as s:
            _upsert_briefing_record(s, uid, day, b.text)
        generated += 1
    return {"generated": generated, "day": day}


# ------------------------------------------------------------ alerts

def _alert_dict(a: Alert) -> dict:
    return {
        "id": a.id,
        "symbol": a.symbol,
        "direction": a.direction,
        "level": a.level,
        "active": a.active,
        "created_at": a.created_at.isoformat(),
        "fired_at": a.fired_at.isoformat() if a.fired_at else None,
        "fired_price": a.fired_price,
    }


@app.get("/api/alerts")
def list_alerts(user: User = Depends(get_current_user)) -> dict:
    with session() as s:
        alerts = [
            _alert_dict(a)
            for a in s.scalars(
                select(Alert)
                .where(Alert.user_id == user.id)
                .order_by(Alert.created_at.desc(), Alert.id.desc())
            )
        ]
    return {"alerts": alerts}


class AlertIn(BaseModel):
    symbol: str = Field(min_length=2, max_length=10)
    direction: Literal["above", "below"]
    level: float = Field(gt=0, allow_inf_nan=False)


@app.post("/api/alerts")
def create_alert(payload: AlertIn, user: User = Depends(get_current_user)) -> dict:
    symbol = payload.symbol.strip().upper()
    with _write_lock, session() as s:
        # Sunucuda enstrüman kaynağı DB'deki Instrument tablosudur.
        inst = s.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if inst is None:
            raise HTTPException(status_code=404, detail=f"bilinmeyen sembol: {symbol}")
        alert = Alert(
            user_id=user.id,
            symbol=symbol,
            direction=payload.direction,
            level=payload.level,
        )
        s.add(alert)
        s.flush()
        alert_id = alert.id
    return {"ok": True, "id": alert_id}


@app.delete("/api/alerts/{alert_id}")
def delete_alert(alert_id: int, user: User = Depends(get_current_user)) -> dict:
    with _write_lock, session() as s:
        alert = s.get(Alert, alert_id)
        # Başkasının alarmı da "yok" gibi davranır — id'ler sızdırılmaz.
        if alert is None or alert.user_id != user.id:
            raise HTTPException(status_code=404, detail="alarm bulunamadı")
        s.delete(alert)
    return {"ok": True}


# ------------------------------------------------------------ transactions

@app.get("/api/transactions")
def list_transactions(user: User = Depends(get_current_user)) -> dict:
    with session() as s:
        txs = [
            {
                "id": t.id,
                "symbol": t.instrument.symbol,
                "side": t.side.value,
                "quantity": t.quantity,
                "price": t.price,
                "fee": t.fee,
                "executed_at": t.executed_at.isoformat(),
                "note": t.note,
            }
            for t in s.scalars(
                select(Transaction)
                .where(Transaction.user_id == user.id)
                .order_by(Transaction.executed_at.desc())
            )
        ]
    return {"transactions": txs}


class TxIn(BaseModel):
    """Şemalı işlem girdisi — NaN/Infinity/negatif değerler kapıda reddedilir
    (kod inceleme bulgusu #3)."""

    symbol: str = Field(min_length=2, max_length=10)
    side: TxSide = TxSide.BUY
    quantity: float = Field(gt=0, allow_inf_nan=False)
    price: float = Field(gt=0, allow_inf_nan=False)
    fee: float = Field(ge=0, allow_inf_nan=False, default=0.0)
    executed_at: datetime | None = None
    note: str = Field(default="", max_length=200)


@app.post("/api/transactions")
def create_transaction(payload: TxIn, user: User = Depends(get_current_user)) -> dict:
    try:
        with _write_lock, session() as s:
            tx = add_transaction(
                s,
                symbol=payload.symbol.upper(),
                side=payload.side,
                quantity=payload.quantity,
                price=payload.price,
                fee=payload.fee,
                executed_at=payload.executed_at,
                note=payload.note,
                user_id=user.id,
            )
            tx_id = tx.id
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id": tx_id}


@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: int, user: User = Depends(get_current_user)) -> dict:
    from alpro.portfolio.engine import _positions_from_ledger

    try:
        with _write_lock, session() as s:
            tx = s.get(Transaction, tx_id)
            # Başkasının işlemi de "yok" gibi davranır — id'ler sızdırılmaz.
            if tx is None or tx.user_id != user.id:
                raise HTTPException(status_code=404, detail="işlem bulunamadı")
            s.delete(tx)
            s.flush()
            _positions_from_ledger(s, user.id, strict=True)  # silme defteri bozuyorsa geri al
    except LedgerError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"silinemez — sonraki bir satış bu işleme dayanıyor: {exc}",
        )
    return {"ok": True}


# ------------------------------------------- off-site yedek (yalnız kurucu)
# SPRINT-PLAN madde 6'nın kod ayağı: kurucu, panelde oturum açıkken tarayıcıdan
# /internal/backup/db adresini açar → SQLite dosyasının tutarlı anlık görüntüsü
# iner. Haftada bir indirip saklamak off-site yedek provasıdır.

@app.get("/internal/backup/db")
def download_db_backup(user: User = Depends(get_current_user)) -> Response:
    if not user.is_founder:
        raise HTTPException(status_code=403, detail="yalnızca kurucu erişebilir")
    url = settings.database_url
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise HTTPException(
            status_code=400, detail="dosya yedeği yalnızca SQLite için desteklenir"
        )
    src_path = url[len(prefix):]
    fd, snapshot_path = tempfile.mkstemp(prefix="alpro-backup-", suffix=".db")
    os.close(fd)
    try:
        # sqlite3.backup: yazma sürerken bile sayfa-tutarlı kopya (WAL dahil).
        with _write_lock:
            src = sqlite3.connect(src_path)
            dst = sqlite3.connect(snapshot_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        data = _Path(snapshot_path).read_bytes()
    finally:
        os.unlink(snapshot_path)
    stamp = datetime.now(IST_TZ).strftime("%Y%m%d-%H%M")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="alpro-yedek-{stamp}.db"'
        },
    )


MAX_DB_RESTORE_BYTES = 100 * 1024 * 1024  # 100 MB


@app.post("/internal/backup/db")
async def restore_db_backup(request: Request, user: User = Depends(get_current_user)) -> dict:
    """İndirilen .db yedeğini sunucuya geri yükler (yalnız kurucu).

    Ücretsiz planda kalıcı disk yoktur: yeniden kurulumda veriler sıfırlanır.
    Bu uç, paneldeki "Geri yükle" düğmesiyle son yedeğin dakikalar içinde
    geri gelmesini sağlar — off-site yedek provasının ikinci yarısı.
    """
    if not user.is_founder:
        raise HTTPException(status_code=403, detail="yalnızca kurucu erişebilir")
    url = settings.database_url
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise HTTPException(
            status_code=400, detail="dosya geri yükleme yalnızca SQLite için desteklenir"
        )
    dst_path = url[len(prefix):]
    raw = await request.body()
    if len(raw) > MAX_DB_RESTORE_BYTES:
        raise HTTPException(status_code=413, detail="yedek dosyası çok büyük")
    if not raw.startswith(b"SQLite format 3\x00"):
        raise HTTPException(status_code=422, detail="geçerli bir SQLite yedeği değil")

    fd, tmp_path = tempfile.mkstemp(prefix="alpro-restore-", suffix=".db")
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    try:
        # Bütünlük + "gerçekten AL PRO yedeği mi" denetimi dosya devreye
        # alınmadan yapılır — bozuk/yanlış dosya mevcut veriyi ezemez.
        conn = sqlite3.connect(tmp_path)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()
        if ok != "ok" or "users" not in tables or "transactions" not in tables:
            raise HTTPException(status_code=422, detail="geçerli bir AL PRO yedeği değil")

        with _write_lock:
            dispose_engine()  # açık bağlantılar kapanmadan dosya değiştirilmez
            os.replace(tmp_path, dst_path)
            for sidecar in (dst_path + "-wal", dst_path + "-shm"):
                if os.path.exists(sidecar):
                    os.unlink(sidecar)
            init_db()  # yeni sürümün ekleyeceği tablo/kolonlar tamamlanır
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    log.info("DB yedeği geri yüklendi (%d bayt)", len(raw))
    return {"ok": True, "bytes": len(raw)}


# ------------------------------------------------------------ yedekleme
# AL PRO masaüstü/tarayıcı uygulamasının veri dosyası artık kullanıcı hesabına
# bağlı olarak user_backups tablosunda tutulur (dosya tabanlı eski yol kalktı).

MAX_BACKUP_BYTES = 2 * 1024 * 1024  # 2 MB fazlasıyla yeter


@app.post("/api/backup")
async def save_backup(request: Request, user: User = Depends(get_current_user)) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BACKUP_BYTES:
        raise HTTPException(status_code=413, detail="yedek çok büyük")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="geçersiz JSON")
    if payload.get("app") != "alpro":
        raise HTTPException(status_code=422, detail="AL PRO veri dosyası değil")
    serialized = json.dumps(payload, ensure_ascii=False)
    with _write_lock, session() as s:
        row = s.scalar(select(UserBackup).where(UserBackup.user_id == user.id))
        if row is None:
            s.add(UserBackup(user_id=user.id, payload=serialized, saved_at=utcnow()))
        else:  # upsert: tek satır/kullanıcı — önceki kopyanın üzerine yazılır
            row.payload = serialized
            row.saved_at = utcnow()
    return {"ok": True, "bytes": len(raw), "savedAt": payload.get("savedAt")}


@app.get("/api/backup")
def load_backup(user: User = Depends(get_current_user)) -> dict:
    with session() as s:
        row = s.scalar(select(UserBackup).where(UserBackup.user_id == user.id))
    if row is None:
        raise HTTPException(status_code=404, detail="sunucuda yedek yok")
    return json.loads(row.payload)


# ---------------------------------------------------------------- panel

PANEL_HTML = """<!DOCTYPE html>
<html lang="tr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AL PRO — Sunucu Paneli</title>
<style>
:root{color-scheme:dark}
body{margin:0;background:#0d0d0d;color:#fff;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:14px;line-height:1.5}
.wrap{max-width:980px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 2px}
.sub{color:#898781;font-size:12px;margin-bottom:18px}
.card{background:#1a1a19;border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:16px 18px;margin-bottom:14px}
.card h3{font-size:13px;color:#c3c2b7;margin:0 0 10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:14px}
.tile .l{color:#898781;font-size:12px}.tile .v{font-size:21px;font-weight:700}
table{width:100%;border-collapse:collapse}
th{color:#898781;font-weight:500;font-size:11.5px;text-align:left;padding:6px 8px;border-bottom:1px solid #2c2c2a}
td{padding:7px 8px;border-bottom:1px solid #2c2c2a;font-size:13px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
pre{white-space:pre-wrap;background:#222221;border:1px solid rgba(255,255,255,.1);border-radius:10px;padding:16px;font-family:inherit;font-size:13px}
input,select,button{background:#222221;border:1px solid rgba(255,255,255,.12);color:#fff;border-radius:8px;padding:7px 10px;font-size:13px;font-family:inherit}
button{background:#3987e5;border:none;font-weight:600;cursor:pointer}
button.ghost{background:transparent;border:1px solid rgba(255,255,255,.15)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:end}
label{display:flex;flex-direction:column;gap:4px;font-size:11.5px;color:#898781}
a{color:#3987e5}
.up{color:#0ca30c}.down{color:#d03b3b}
.muted{color:#898781;font-size:12px}
#who{text-align:right;margin-bottom:10px}
</style></head><body><div class="wrap">
<h1>AL PRO <span style="color:#0e7490;font-size:14px">sunucu paneli</span></h1>
<div class="sub">Faz 1 — çok kullanıcılı beta. Giriş yaptıktan sonra <a href="/app" style="color:#8ab8ee">🚀 Tam Uygulamayı Aç</a> — grafikler, alarmlar, senaryolar; telefon dahil.</div>
<div class="muted" id="who" style="display:none"></div>
<div class="card" id="authBox" style="display:none">
  <h3>Giriş — e-posta ile</h3>
  <div class="row">
    <label>E-posta<input id="email" type="email" style="width:230px" placeholder="ornek@eposta.com"></label>
    <button onclick="sendLink()">Giriş bağlantısı gönder</button>
  </div>
  <label style="flex-direction:row;align-items:center;gap:7px;margin-top:10px;font-size:12.5px;color:#c3c2b7">
    <input id="consent" type="checkbox" style="width:auto">
    <span><a href="/kvkk" target="_blank">KVKK aydınlatma metnini</a> okudum, kişisel verilerimin işlenmesini onaylıyorum.</span>
  </label>
  <div class="muted" id="authMsg" style="margin-top:8px"></div>
  <details style="margin-top:12px"><summary class="muted" style="cursor:pointer">Erişim anahtarıyla bağlan (eski yöntem)</summary>
    <div class="row" style="margin-top:8px">
      <label>ALPRO_API_TOKEN<input id="tok" type="password" style="width:220px"></label>
      <button onclick="saveTok()">Bağlan</button>
    </div>
    <div class="muted">Sunucu sahibi .env dosyasında belirler; yalnızca bilenler erişebilir.</div>
  </details>
</div>
<div id="main" style="display:none">
<div class="tiles" id="tiles"></div>
<div class="card"><h3>Sabah Brifingi</h3><pre id="brief">Yükleniyor…</pre>
<button class="ghost" onclick="load()">Yenile</button></div>
<div class="card"><h3>Pozisyonlar</h3><table id="pos"></table></div>
<div class="card"><h3>Alarmlar</h3><table id="alerts"></table>
<div class="row" style="margin-top:10px">
<label>Sembol<input id="aSym" style="width:90px" placeholder="THYAO"></label>
<label>Koşul<select id="aDir"><option value="above">üzerine çıkarsa</option><option value="below">altına inerse</option></select></label>
<label>Eşik<input id="aLevel" type="number" step="any" style="width:100px"></label>
<button onclick="addAlert()">Alarm Kur</button></div>
<div class="muted" id="aMsg"></div></div>
<div class="card" id="bkCard" style="display:none"><h3>Veri Yedeği (kurucu)</h3>
<div class="row">
<button class="ghost" onclick="dlBackup()">Yedeği İndir (.db)</button>
<label>Geri yükle<input id="bkFile" type="file" accept=".db"></label>
<button onclick="restoreBackup()">Yükle</button></div>
<div class="muted" id="bkMsg">Ücretsiz sunucuda veriler yeniden kurulumda silinebilir — haftada bir yedeğini indir; gerekirse buradan geri yükle.</div></div>
<div class="card"><h3>İşlem Ekle</h3>
<div class="row">
<label>Sembol<input id="fSym" style="width:90px" placeholder="THYAO"></label>
<label>Tür<select id="fSide"><option value="buy">Alış</option><option value="sell">Satış</option></select></label>
<label>Adet<input id="fQty" type="number" step="any" style="width:90px"></label>
<label>Fiyat<input id="fPrice" type="number" step="any" style="width:100px"></label>
<label>Komisyon<input id="fFee" type="number" step="any" value="0" style="width:80px"></label>
<button onclick="addTx()">Ekle</button></div>
<div class="muted" id="msg"></div></div>
</div>
<div class="muted">Bu içerik bilgilendirme amaçlıdır; yatırım tavsiyesi değildir. · <a href="/kvkk">KVKK</a> · AL PRO API v0.4</div>
</div>
<script>
let TOKEN = "";
const $ = id => document.getElementById(id);
const fmt = (n,d=2)=>Number(n).toLocaleString("tr-TR",{minimumFractionDigits:d,maximumFractionDigits:d});
async function api(path, opts={}){
  const h = Object.assign({"Content-Type":"application/json"}, TOKEN?{"X-API-Key":TOKEN}:{});
  const r = await fetch(path, Object.assign({headers:h}, opts));
  if(r.status===401){$("authBox").style.display="";$("main").style.display="none";throw new Error("auth");}
  if(!r.ok) throw new Error((await r.json()).detail||r.status);
  return r.json();
}
async function boot(){
  try{
    const me = await api("/api/me");
    $("who").style.display="";
    $("who").innerHTML = me.email + ' · <a href="#" onclick="logout();return false">Çıkış</a>';
    $("authBox").style.display="none"; $("main").style.display="";
    if(me.is_founder) $("bkCard").style.display="";
    load();
  }catch(e){ /* 401 → giriş formu görünür kalır */ }
}
async function sendLink(){
  const m = $("authMsg");
  try{
    const j = await fetch("/auth/request-link",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({email:$("email").value.trim(), consent:$("consent").checked})});
    const d = await j.json();
    m.textContent = j.ok ? d.message : ("Hata: " + (d.detail || j.status));
  }catch(e){ m.textContent = "Hata: " + e.message; }
}
async function logout(){ await fetch("/auth/logout",{method:"POST"}); TOKEN=""; location.reload(); }
function saveTok(){ TOKEN=$("tok").value.trim(); boot(); }
async function load(){
  try{
    const p = await api("/api/portfolio/summary");
    const b = await api("/api/briefing");
    $("tiles").innerHTML = `
      <div class="card tile"><div class="l">Toplam Değer</div><div class="v">${fmt(p.total_value)} ₺</div></div>
      <div class="card tile"><div class="l">Açık K/Z</div><div class="v ${p.total_unrealized_pl>=0?"up":"down"}">${fmt(p.total_unrealized_pl)} ₺</div></div>
      <div class="card tile"><div class="l">Pozisyon</div><div class="v">${p.positions.length}</div></div>`;
    $("brief").textContent = b.text;
    $("pos").innerHTML =
      "<tr><th>Sembol</th><th class=num>Adet</th><th class=num>Fiyat</th><th class=num>Değer</th><th class=num>K/Z %</th><th class=num>Ağırlık</th></tr>" +
      p.positions.map(x=>`<tr><td><b>${x.symbol}</b> <span class=muted>${x.name||""}</span></td>
        <td class=num>${x.quantity}</td><td class=num>${x.price!=null?fmt(x.price):"—"}</td>
        <td class=num>${x.market_value_base!=null?fmt(x.market_value_base,0):"—"}</td>
        <td class="num ${x.unrealized_pl_pct>=0?"up":"down"}">${x.unrealized_pl_pct!=null?fmt(x.unrealized_pl_pct):"—"}</td>
        <td class=num>${x.weight_pct!=null?fmt(x.weight_pct,1):"—"}</td></tr>`).join("");
    loadAlerts();
  }catch(e){ if(e.message!=="auth") $("msg").textContent="Hata: "+e.message; }
}
async function loadAlerts(){
  try{
    const a = await api("/api/alerts");
    $("alerts").innerHTML =
      "<tr><th>Sembol</th><th>Koşul</th><th class=num>Eşik</th><th>Durum</th><th></th></tr>" +
      (a.alerts.length ? a.alerts.map(x=>`<tr><td><b>${x.symbol}</b></td>
        <td>${x.direction==="above"?"üzerine çıkarsa":"altına inerse"}</td>
        <td class=num>${fmt(x.level)}</td>
        <td>${x.active?"aktif":(x.fired_price!=null?`<span class=up>tetiklendi @ ${fmt(x.fired_price)}</span>`:"pasif")}</td>
        <td><button class=ghost onclick="delAlert(${x.id})">Sil</button></td></tr>`).join("")
      : "<tr><td class=muted colspan=5>Henüz alarm yok.</td></tr>");
  }catch(e){ if(e.message!=="auth") $("aMsg").textContent="Hata: "+e.message; }
}
async function addAlert(){
  try{
    await api("/api/alerts",{method:"POST",body:JSON.stringify({
      symbol:$("aSym").value.trim(), direction:$("aDir").value,
      level:parseFloat($("aLevel").value)})});
    $("aMsg").textContent="Alarm kuruldu ✓"; loadAlerts();
  }catch(e){ $("aMsg").textContent="Hata: "+e.message; }
}
async function delAlert(id){
  try{ await api("/api/alerts/"+id,{method:"DELETE"}); $("aMsg").textContent=""; loadAlerts(); }
  catch(e){ $("aMsg").textContent="Hata: "+e.message; }
}
async function dlBackup(){
  try{
    const r = await fetch("/internal/backup/db",{headers:TOKEN?{"X-API-Key":TOKEN}:{}});
    if(!r.ok) throw new Error((await r.json()).detail||r.status);
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    const cd = r.headers.get("content-disposition")||"";
    a.download = (cd.match(/filename="([^"]+)"/)||[])[1] || "alpro-yedek.db";
    a.click(); URL.revokeObjectURL(a.href);
    $("bkMsg").textContent = "Yedek indirildi ✓ Dosyayı güvenli bir yerde sakla.";
  }catch(e){ $("bkMsg").textContent = "Hata: " + e.message; }
}
async function restoreBackup(){
  const f = $("bkFile").files[0];
  if(!f){ $("bkMsg").textContent = "Önce bir .db yedek dosyası seç."; return; }
  if(!confirm("Sunucudaki TÜM veriler bu yedekle DEĞİŞTİRİLECEK. Devam edilsin mi?")) return;
  try{
    const h = Object.assign({"Content-Type":"application/octet-stream"}, TOKEN?{"X-API-Key":TOKEN}:{});
    const r = await fetch("/internal/backup/db",{method:"POST",headers:h,body:f});
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail||r.status);
    $("bkMsg").textContent = "Geri yükleme tamam ✓"; load();
  }catch(e){ $("bkMsg").textContent = "Hata: " + e.message; }
}
async function addTx(){
  try{
    await api("/api/transactions",{method:"POST",body:JSON.stringify({
      symbol:$("fSym").value, side:$("fSide").value,
      quantity:parseFloat($("fQty").value), price:parseFloat($("fPrice").value),
      fee:parseFloat($("fFee").value)||0})});
    $("msg").textContent="İşlem eklendi ✓"; load();
  }catch(e){ $("msg").textContent="Hata: "+e.message; }
}
boot();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def panel() -> str:
    return PANEL_HTML


# ------------------------------------------------ tam uygulama (/app)
# Tek dosyalık AL PRO istemcisi sunucudan sunulur: aynı origin + giriş
# çerezi sayesinde "Sunucu Bağlantısı" kutusu boş bırakılarak otomatik
# bağlanır (istemci srvBase() bunu destekler). Statik dosya, auth istemez;
# içindeki tüm /api çağrıları oturum/cookie ile korunur.
from pathlib import Path as _Path  # noqa: E402

_WEBUI_PATH = _Path(__file__).resolve().parent / "webui_app.html"
_webui_cache: str | None = None


@app.get("/app", response_class=HTMLResponse)
def full_app() -> str:
    global _webui_cache
    if _webui_cache is None:
        try:
            _webui_cache = _WEBUI_PATH.read_text(encoding="utf-8")
        except OSError:
            raise HTTPException(status_code=404, detail="uygulama dosyası pakette yok")
    return _webui_cache

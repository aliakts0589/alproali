# AL PRO — Faz 1 Kapanış Planı (Backend Architect değerlendirmesi, 3 Ağu 2026)

## Dört mimari karar
1. **Kimlik: e-posta + magic link** (parola yok). Davet listesi = beta kapısı.
   Mevcut token admin/cron ucu olarak kalır. E-posta: Resend/Brevo ücretsiz katman.
2. **Veri modeli: tek DB + user_id.** users/sessions/backups tabloları; piyasa
   verisi global kalır (100 kullanıcıda da veri maliyeti sabit). Alembic şimdi girer.
3. **SQLite betaya yeter** (WAL + tek instance + az yazma). Postgres tetikleyicileri:
   "database is locked" logları, ikinci instance ihtiyacı, yönetilen yedek ihtiyacı.
4. **Barındırma: geliştirmede Render free, beta açılışında Starter (~$7/ay).**
   Brifing üretimi sunucu içi döngüye değil cron-job.org → /internal/cron ucuna bağlanır.

## Sprint sırası (efor: S/M/L)
1. Alembic + çok kullanıcılı şema (M)
2. Magic link auth + davet listesi (L)
3. Uçların user-scope'lanması + cross-user erişim testleri (M)
4. Günlük brifing üretimi + LLM maliyet tavanı (M)
5. Brifing geri bildirimi 👍/👎 → %60 memnuniyet ölçümü (S)
6. Sentry + uptime + gece off-site yedek + restore provası (S)
7. Onboarding + KVKK aydınlatma/rıza kaydı (S)

## Canlıya çıkış kontrol listesi (özet)
Cross-user testler CI'da · CORS daraltıldı · secrets env'de + rotasyon yazılı ·
rate limit + LLM tavanı · yedek + restore provası · Sentry/uptime alarmı ·
guardrail regresyonu CI'da · KVKK rıza + hesap silme ucu · 100 eşzamanlı GET
p95<2sn · rollback denenmiş.

## Bilinçli YAPMA listesi
Celery/Redis yok · WebSocket/gerçek zamanlı yok · parola altyapısı yok ·
SPA/mobil yeniden yazım yok · erken Postgres/mikroservis yok.

## DURUM GÜNCELLEMESİ (8 Ağu 2026 — sunucu v0.4)
✅ 1. Çok kullanıcılı şema (users/sessions/backups/briefings + transactions.user_id;
      Alembic yerine SQLite kolon bekçisi — Postgres'e geçişte Alembic ilk iş)
✅ 2. Magic link auth + davet listesi (ALPRO_INVITES; e-posta modu: console —
      gerçek SMTP/Resend deploy günü 15 dk'lık iş)
✅ 3. Uçlar user-scoped + cross-user erişim testleri (28/28 test)
✅ 4. Günlük brifing cron ucu (/internal/cron/daily) + kayıt/önbellek
✅ 5. Brifing geri bildirimi (👍/👎) + /api/metrics/briefing
✅ 7. KVKK: /kvkk aydınlatma + kayıtta zorunlu rıza + hesap/veri silme ucu
⏳ 6. Sentry/uptime/off-site yedek → hesaplar açılınca (deploy günü)
⏳ Deploy: GitHub + Render + (ops.) EVDS → kullanıcı hazır olduğunda

## FAZ 2 — DİLİM A (10 Ağu 2026, sunucu v0.5) ✅
✅ Haber katmanı: Google News RSS bağlayıcısı (ağ yoksa etiketli DEMO), /api/news,
   brifingde "İZLEME LİSTESİ HABERLERİ" bölümü (elde tutulan hisselere göre)
✅ Sunucu tarafı alarmlar: kullanıcı bazlı CRUD, her veri tazelemede + günlük cron'da
   kontrol, brifingde "TETİKLENEN ALARMLAR" bölümü, panelde alarm kartı
   (e-posta bildirimi SMTP/Resend ile deploy sonrası)
Test: 36/36 · Doğrulama: THYAO 300-üzeri alarmı fixture fiyat 312,50'de tetiklendi ✓

## v0.6 (10 Ağu 2026): PROGRAM = /app ✅
Sunucu artık tam AL PRO istemcisini kendisi sunuyor: adres/app → 8 sekmeli uygulama
(aynı origin + oturum çerezi ile otomatik bağlanır; adres kutusu boş kalabilir).
Panel giriş kapısı olarak kaldı; 37/37 test; tarayıcı doğrulaması: sunucudan yüklenen
uygulama boş adresle sunucuya yedekledi/geri yükledi ✓

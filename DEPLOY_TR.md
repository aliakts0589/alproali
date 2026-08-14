# AL PRO Yayına Alma Rehberi (Faz 1 kapanışı)

Kod tarafındaki her şey hazır. Senin payına düşen yalnızca **hesap açıp
düğmelere basmak** — aşağıdaki adımları sırayla yap, takıldığın yerde ekran
görüntüsüyle bana gel. Zorunlu kısım (1. Bölüm) ~15 dakika sürer.

> ✅ **GitHub adımı bitti.** Kod zaten GitHub hesabında
> (`aliakts0589/alproali`). Doğrudan Render'dan başlıyorsun.

---

## BÖLÜM 1 — Zorunlu: Render'da yayına al (~15 dk)

1. **render.com** adresine git → "Get Started" → **GitHub ile kaydol**
   (aliakts0589 hesabınla). E-posta doğrulamasını yap.
2. Panelde **New +** → **Blueprint** → GitHub bağlantısına izin ver →
   listeden **alproali** reposunu seç.
3. Karşına çıkan formda **hiçbir alanı doldurmak zorunda değilsin**:
   - Giriş anahtarını (ALPRO_API_TOKEN) Render kendisi üretir.
   - EVDS / Resend / Sentry alanlarını **boş bırak** — hepsi isteğe bağlı,
     sonradan eklenir.
4. **Apply**'a bas. Birkaç dakika içinde adresin hazır:
   `https://alpro-XXXX.onrender.com` (Render panelinde görünür).
5. **Panele giriş:** adresi telefonda/bilgisayarda aç. İki yol var:
   - **E-posta ile (önerilen):** e-posta kutusuna `aliakts0589@gmail.com`
     yaz, KVKK kutusunu işaretle, "Giriş bağlantısı gönder"e bas. Resend
     kurulmadıysa bağlantı e-postana DEĞİL sunucu **loguna** yazılır:
     Render panelinde alpro servisi → **Logs** sekmesi → `MAGIC LINK`
     satırındaki adresi kopyala, tarayıcıya yapıştır → içerdesin.
   - **Anahtar ile:** Render'da alpro servisi → **Environment** sekmesi →
     `ALPRO_API_TOKEN` değerini kopyala → paneldeki "Erişim anahtarıyla
     bağlan" kutusuna yapıştır.
6. Giriş yaptıktan sonra **/app** bağlantısına tıkla → tam uygulama
   (grafikler, alarmlar, işlem ekleme) her cihazda.

## BÖLÜM 2 — Önerilen: sunucu uyumasın + günlük brifing (~10 dk)

Ücretsiz Render planında sunucu 15 dk hareketsizlikte uyur; ilk istek onu
uyandırır ama ~1 dk bekletir. Çözüm: **cron-job.org** (ücretsiz).

1. cron-job.org'da hesap aç → **Create cronjob**.
2. **Uyandırma görevi:** URL = `https://ADRESIN.onrender.com/health`,
   sıklık = her 10 dakikada bir. Kaydet — bu kadar.
3. *(İsteğe bağlı)* **Günlük brifing görevi:** ikinci bir cronjob aç:
   - URL = `https://ADRESIN.onrender.com/internal/cron/daily`
   - Zaman = her gün 07:00 (Europe/Istanbul)
   - Ayarlarda **Request method: POST** seç ve **Headers** bölümüne şunu
     ekle: isim `X-API-Key`, değer = Render Environment'taki
     `ALPRO_API_TOKEN`.
   Böylece sabah brifingin sen uygulamayı açmadan hazırlanmış olur.

## BÖLÜM 3 — İsteğe bağlı: magic link gerçek e-postayla gelsin (~10 dk)

1. **resend.com**'da hesap aç — **aliakts0589@gmail.com ile kaydol**
   (önemli: ücretsiz test göndericisi yalnızca kendi adresine gönderebilir).
2. Panelde **API Keys** → **Create API Key** → çıkan `re_...` değerini kopyala.
3. Render'da alpro servisi → **Environment** → `RESEND_API_KEY` alanına
   yapıştır → **Save** (servis kendini yeniden başlatır).
4. Artık giriş bağlantısı doğrudan e-postana gelir; loglara bakmak gerekmez.
   Gönderim herhangi bir nedenle başarısız olursa sistem eski yönteme döner
   (link loga yazılır) — giriş asla kilitlenmez.

> Davet listesine başka kullanıcılar ekleyeceğin gün: Resend'de kendi alan
> adını doğrulaman ve Render'a `ALPRO_EMAIL_FROM` + `ALPRO_INVITES`
> eklemen gerekir — o gün geldiğinde birlikte yaparız.

## BÖLÜM 4 — İsteğe bağlı: hata izleme + yedek (~10 dk)

- **Sentry (hata alarmı):** sentry.io'da hesap aç → yeni proje (platform:
  Python/FastAPI) → sana verilen **DSN** adresini kopyala → Render
  **Environment** → `SENTRY_DSN` alanına yapıştır → Save. Sunucuda bir hata
  olursa e-postana bildirim gelir.
- **Off-site yedek (haftalık alışkanlık):** panelde kurucu olarak
  girişliyken tarayıcıya `https://ADRESIN.onrender.com/internal/backup/db`
  yaz → tüm veritabanının o anki kopyası (`alpro-yedek-TARIH.db`) iner.
  Haftada bir indirip bilgisayarında saklaman yeterli.
- **TCMB kurları (canlı döviz):** evds2.tcmb.gov.tr'de üye ol → profil
  sayfasından "API Anahtarı" → Render **Environment** → `EVDS_API_KEY`.

## Sık sorulanlar

**Verilerim nerede?** Render'daki 1 GB kalıcı diskte (`/data/alpro.db`),
yalnızca senin sunucunda. Panele giriş yapmadan kimse erişemez.

**Ücret?** GitHub + Render free + cron-job.org + Resend free + Sentry free
= 0 ₺/ay. Sunucu hiç uyumasın istersen Render Starter (~7 $/ay) yeterli.

**Sorun çıkarsa?** Render'da alpro servisi → **Logs** sekmesindeki son
satırları kopyala, bana getir — birlikte çözeriz.

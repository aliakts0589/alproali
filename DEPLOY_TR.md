# AL PRO Yayına Alma Rehberi (Faz 1 kapanışı)

## Mevcut durum (14 Ağu 2026)

- ✅ Kod GitHub'da: `aliakts0589/alproali`
- ✅ Render hesabın **zaten açık** (aliakts0589@gmail.com) ve depo bir
  Blueprint olarak bağlı.
- ❌ 11 Ağustos'taki ilk kurulum "Blueprint Sync Failed" hatasıyla durmuştu.
  Sebep: eski `render.yaml` ücretsiz planda **kalıcı disk** istiyordu —
  Render free planda disk vermez. Bu düzeltildi (disk kaldırıldı; veri
  güvenliği paneldeki "Veri Yedeği" kartıyla sağlanıyor).
- 🔁 Render, depoya her push'ta Blueprint'i yeniden kurmayı dener. Düzeltme
  ana dala geçtiği için kurulumun **kendiliğinden** tamamlanması beklenir.

## Kurulum kendiliğinden olmadıysa (tek tıklama)

Gelen kutundaki "Blueprint Sync Failed" e-postasındaki bağlantıya tıkla
(https://dashboard.render.com/blueprint/exs-d9tembdbedkc7394qgf0) →
sayfada **"Manual Sync"** (elle eşitle) düğmesine bas. Hepsi bu.

## Panele giriş

Kurulum bitince adresin şuna benzer: `https://alpro-XXXX.onrender.com`
(Render panelinde görünür; e-postana da bildirim gelebilir).

1. Adresi telefonda/bilgisayarda aç.
2. **E-posta ile giriş:** `aliakts0589@gmail.com` yaz, KVKK kutusunu
   işaretle, "Giriş bağlantısı gönder"e bas.
   - Resend anahtarı girilmediyse bağlantı e-postana değil **sunucu
     loguna** yazılır: Render panelinde alpro servisi → **Logs** →
     `MAGIC LINK` satırındaki adresi kopyala, tarayıcıya yapıştır.
   - Alternatif: Render'da alpro servisi → **Environment** →
     `ALPRO_API_TOKEN` değerini kopyala → paneldeki "Erişim anahtarıyla
     bağlan" kutusuna yapıştır.
3. Girişten sonra **/app** → tam uygulama (grafikler, alarmlar, işlemler).

## Ücretsiz planda verilerin güvenliği (ÖNEMLİ)

Free planda kalıcı disk olmadığı için sunucu **yeniden kurulduğunda**
(kod güncellemesi, Render bakımı) kayıtlı işlemler silinebilir. Çözüm
panelde hazır: **Veri Yedeği** kartı →

- **"Yedeği İndir (.db)"** — haftada bir bas, inen dosyayı sakla.
- **"Geri yükle"** — veri sıfırlanırsa son yedeği seç, "Yükle"ye bas;
  her şey dakikalar içinde geri gelir.

Verilerin hiç silinmemesini istediğin gün Render'da planı **Starter**'a
(~$7/ay) yükseltip `render.yaml` içindeki disk bloğunun yorumunu
kaldırıyoruz — o gün geldiğinde birlikte yaparız.

## Sunucu uyumasın (otomatik — bir şey yapman gerekmiyor)

Depodaki GitHub Actions görevi (`keep-alive`) 10 dakikada bir sunucuya
istek atarak onu uyanık tutar. Sunucu adresi `RENDER_URL.txt` dosyasına
yazıldığı anda çalışmaya başlar (adres belli olunca Claude'a söylemen
yeterli — dosyayı o günceller). cron-job.org'a gerek kalmadı.

## İsteğe bağlı iyileştirmeler (hazır olduğunda, her biri ~10 dk)

- **Magic link e-postayla gelsin:** resend.com'da **aliakts0589@gmail.com
  ile** hesap aç (ücretsiz test göndericisi yalnızca kendi adresine
  gönderebilir) → API Keys → Create → `re_...` değerini Render'da alpro
  servisi → Environment → `RESEND_API_KEY` alanına yapıştır → Save.
  Gönderim aksarsa sistem otomatik log yöntemine döner; giriş kilitlenmez.
- **Hata alarmı (Sentry):** sentry.io'da hesap aç → yeni proje
  (Python/FastAPI) → DSN'i kopyala → Render Environment → `SENTRY_DSN`.
- **Resmi TCMB kurları:** evds2.tcmb.gov.tr → üye ol → profilden
  "API Anahtarı" → Render Environment → `EVDS_API_KEY`.

## Sık sorulanlar

**Ücret?** Şu an her şey 0 ₺/ay (Render free + GitHub Actions + Resend
free + Sentry free). Kalıcı disk istersen Render Starter ~$7/ay.

**Sorun çıkarsa?** Render'da alpro servisi → **Logs** sekmesindeki son
satırları kopyala, Claude'a getir — birlikte çözülür.

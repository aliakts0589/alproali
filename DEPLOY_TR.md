# AL PRO Sunucu Kurulum Rehberi (Faz 1)

Bu rehber, AL PRO'yu internete — telefonundan da açabileceğin bir adrese —
taşımanın adımlarını anlatır. Kod hazır; senin yapman gerekenler yalnızca
hesap açmak ve düğmelere basmak. Toplam süre: ~30-45 dakika.

## Neye ihtiyacın var?

1. **GitHub hesabı** (ücretsiz) — kodu barındırmak için: github.com
2. **Render hesabı** (ücretsiz plan yeter) — sunucu için: render.com
3. *(İsteğe bağlı)* **EVDS anahtarı** (ücretsiz) — resmi TCMB kurları için:
   evds2.tcmb.gov.tr → üye ol → profil sayfasından "API Anahtarı"

## Adımlar

### 1) Kodu GitHub'a yükle
- GitHub'da "New repository" → ad: `alpro` → Private seç → Create.
- Bu klasördeki dosyaları "uploading an existing file" bağlantısıyla
  sürükleyip bırak (ya da bana "GitHub'a yükleme adımlarında yardım et" de).

### 2) Render'da yayına al
- render.com → New + → **Blueprint** → GitHub hesabını bağla → `alpro`
  reposunu seç. Render, `render.yaml` dosyasını okuyup her şeyi kurar.
- Sorduğunda **ALPRO_API_TOKEN** için uzun, tahmin edilemez bir şifre yaz
  (örn. bir parola üreticiden 24+ karakter). Bu, paneline giriş anahtarındır.
- (İsteğe bağlı) **EVDS_API_KEY** alanına TCMB anahtarını yapıştır.
- "Apply" → birkaç dakika içinde `https://alpro-XXXX.onrender.com` hazır.

### 3) Panele gir
- Adresi telefonunda/bilgisayarında aç → "Erişim Anahtarı" kutusuna
  ALPRO_API_TOKEN değerini gir → portföyün, brifingin ve işlem ekleme
  ekranın her cihazda.

### 4) Sabah brifingini sunucudan tazele (isteğe bağlı)
Ücretsiz planda sunucu boşta uyur. cron-job.org'da (ücretsiz) 5 dakikada
bir `https://ADRESIN/health` adresine istek atan bir görev kur — sunucu
uyanık kalır; `/api/briefing` her açılışta güncel brifingi üretir.

## Sık sorulanlar

**Verilerim nerede?** Render'daki 1 GB kalıcı diskte (`/data/alpro.db`),
yalnızca senin sunucunda. Panel şifresiz erişilemez (token zorunlu).

**Ücret?** GitHub + Render free + cron-job.org = 0 ₺/ay. Uyumadan çalışsın
istersen Render Starter (~7 $/ay) yeterli.

**Sorun çıkarsa?** Render "Logs" sekmesindeki son satırları kopyala,
bana getir — birlikte çözeriz.

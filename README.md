# Kamu İlan Takip Sistemi (Playwright + Groq + GitHub Actions)

## Mimari
- **GitHub Actions**: her gün TR saati ~08:00'de otomatik çalışır (bulutta, bilgisayarın açık olmasına gerek yok)
- **Playwright**: JS ile içerik yükleyen siteler için gerçek (headless) tarayıcı açar
- **Groq (llama-3.3-70b-versatile)**: her ilgili ilanın metnini okuyup KPSS türü, ek sınav, tarih, ek şart gibi alanları yapılandırılmış JSON'a çevirir
- **GitHub Pages** (`docs/index.html`): sonucu her gün aynı linkte günceller — tarayıcıda açıp bakarsın
- **logs/log.csv**: her günün her kaynak için kaç ilan bulduğunu, hata olup olmadığını kalıcı olarak kaydeder — doğrulama katmanı budur

## Kaynakların durumu (dürüstçe)
| Kaynak | Durum | Not |
|---|---|---|
| İlan.gov.tr | Doğrulanmış | Gerçek JSON API kullanıyor |
| Resmî Gazete | Doğrulanmış | Tarihe göre öngörülebilir statik sayfa |
| Kariyer Kapısı | Kısmi | Sayfa adresi doğru, ilan kartı CSS seçicisi tahmini — ilk çalıştırmada muhtemelen 0 dönecek, birlikte düzeltmemiz gerekecek |
| İŞKUR | Kısmi | Gerçek bağlantı sayfaları doğru, ama sayfa yapısı test edilmedi |
| ÇŞB | Doğrulanmadı | Kodda boş — hangi sayfayı kastettiğini netleştirelim |
| SBB Kamu İlan | Doğrulanmadı | Kodda boş — hangi sayfayı kastettiğini netleştirelim |

**Neden hepsini "çalışıyor" diye yazmadım:** Kariyer Kapısı ve İŞKUR gibi
JS ile yüklenen sitelerde doğru CSS seçiciyi tahmin ederek yazmak, "çalışıyor
gibi görünüp aslında 0 veya yanlış veri döndürme" riski taşır — bu tam senin
istemediğin şey. Log dosyası (`logs/log.csv`) bu yüzden var: her sabah oraya
bakarak hangi kaynağın gerçekten veri getirdiğini göreceksin.

## Kurulum
1. Bu dosyaları bir GitHub reposuna yükle (repo private olabilir, sorun değil).
2. Settings → Secrets and variables → Actions → New repository secret
   → İsim: `GROQ_API_KEY`, değer: kendi Groq anahtarın (console.groq.com/keys, ücretsiz).
3. Settings → Pages → Source: "Deploy from a branch" → Branch: `main`, klasör: `/docs`.
   Kaydettikten sonra GitHub sana bir link verir (örn. `https://kullaniciadi.github.io/repo-adi/`) —
   bu link her gün otomatik güncellenen raporun.
4. Actions sekmesine git → "Günlük İlan Taraması" workflow'unu seç →
   "Run workflow" ile ilk denemeyi elle tetikle.
5. Çalışma bitince `docs/index.html` ve `logs/log.csv` otomatik commit'lenir.
   Log dosyasına bak: hangi kaynak kaç ilan getirmiş, hata var mı gör.

## Sıradaki adım — birlikte tamamlayalım
Kariyer Kapısı ve İŞKUR'un gerçek ilan kartı yapısını netleştirmek için:

1. Yerelinde `python -m playwright install chromium` yap, sonra şunu çalıştır:
```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    page = p.chromium.launch().new_page()
    page.goto("https://isealimkariyerkapisi.cbiko.gov.tr/")
    page.wait_for_load_state("networkidle")
    print(page.content())
```
2. Çıkan HTML'i bana gönder (veya kendin inceleyip ilan kartlarının gerçek
   `class`/`href` desenini bul), `fetch_kariyer_kapisi` fonksiyonundaki
   seçiciyi ona göre güncelleyelim.
3. Aynısını İŞKUR sayfaları için yapalım.
4. ÇŞB ve SBB için: hangi tam sayfayı (URL) kastettiğini bulup gönderirsen,
   onlar için de fonksiyon yazarım.

Her kaynağı tek tek doğrulaya doğrulaya ekleyeceğiz — böylece "hepsi var ama
bazıları sessizce yanlış" değil, gerçekten güvendiğin bir sistem çıkar.

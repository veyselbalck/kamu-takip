
# Kamu İlan Takip

Scraper her çalıştırmada kaynaklardaki ilanları toplar; ilan ayrıntılarını ve
analiz sonuçlarını `data/ilanlar.sqlite3` içinde saklar. Daha önce ayrıntı
metni veritabanına kaydedilmiş bir ilan için sayfa tekrar açılmaz. Metni
okunamayan veya henüz analiz edilemeyen ilanlar sonraki çalıştırmalarda
yeniden denenir.

`data/profiles.json` mevcut iki rapor profilinin puan ve kişisel uygunluk
ayarlarını tutar. Yeni bir puan veya yaş şartı geldiğinde ilan metni yeniden
okunmadan rapor uygunluğu tekrar hesaplanır. `data/state.json` eski
çalıştırmalarla uyumluluk ve kurtarma için korunur; ilan metinlerinin asıl
kalıcı deposu SQLite veritabanıdır.

İlk çalıştırmada eski `state.json` kayıtları SQLite'a aktarılır. Eski scraper
analizden sonra bazı ilan metinlerini sildiği için, bu eski metinler geriye
dönük kurtarılamaz; kaydı/analizi tamamlanmış ilanlar yine de yeniden okunmaz.
Yeni okunan metinler artık kalıcı olarak saklanır.

Günlük çıktı `docs/index.html` ve `docs/onlisans.html` dosyalarına yazılır.
Kaynak bazında okuma hataları, analiz kuyruğu ve uygun bulunmayan ilanlar
raporlarda ayrı gösterilir. Çalıştırma ve kaynak özetleri de SQLite'ta
`scrape_runs` ve `source_runs` tablolarında tutulur.

İŞKUR'un e-Şube kamu aramasına ek olarak, [Kamu Memur Alım İlanları](https://www.iskur.gov.tr/ilanlar/kamu-memur-alim-ilanlari/)
ve [Kurumdışı Kamu İşçi Alım İlanları](https://www.iskur.gov.tr/ilanlar/kurumdisi-kamu-isci-alim-ilanlari/)
sayfalarındaki haritalar da ayrı kaynaklar olarak taranır. İlan bulunan illerin PDF
bağlantıları toplanır; PDF metni çıkarılır, gerektiğinde mevcut Gemini OCR yolu
veya Groq Vision OCR denenir ve ilanlar iki profilin rapor akışına dahil edilir.
Görüntü PDF'leri için PDFium sayfaları resme çevirir; Tesseract Türkçe OCR önce
yerel CPU'da çalışır, yeterli metin çıkaramazsa Gemini ve Groq denenir. Yerel OCR
API kotası tüketmez ve GPU gerektirmez. Self-hosted Windows runner'a Tesseract
kurulmalıdır; Türkçe dil modeli ilk çalıştırmada indirip yerel önbelleğe alınır.
İŞKUR PDF'si gerçek PDF olarak alınamazsa
tarayıcı isteğine ek olarak HTTP yedek yolu denenir ve iki yanıtın türü raporlanır.
Self-hosted Windows makinesine kurmak için PowerShell'de:

```powershell
winget install --id UB-Mannheim.TesseractOCR --exact
```

GitHub Actions, `GROQ_API_KEY` ve numaralı `GROQ_API_KEY_2`–`GROQ_API_KEY_5`
depolarını sırayla kullanır; kota veya hız sınırına takılan anahtar/model
çifti atlanıp sıradaki denenir. Ek hesap eklemek için GitHub deposunda
**Settings → Secrets and variables → Actions** altında `GROQ_API_KEY_3`,
`GROQ_API_KEY_4` ve `GROQ_API_KEY_5` adlarıyla secret oluşturun. `GEMINI_API_KEY`
ayrı bir yedektir; Gemini kotasının dolması tek başına Groq kotalarının da
dolduğu anlamına gelmez.

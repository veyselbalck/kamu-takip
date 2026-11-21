# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

İki kişilik kişisel kullanım: V (bilgisayar/yazılım mühendisliği, lisans düzeyi kadrolar) ve B (önlisans, anestezi/sağlık, engelli kadro ve EKPSS ilanları). Her biri günlük raporu kendi profiliyle okur. Rapor telefonda ve masaüstünde eşit sıklıkta açılır (kullanıcı onayı); iki cihaz da birinci sınıftır. Amaç: kendi şartlarına uyan kamu ilanını son başvuru tarihini kaçırmadan görmek.

## Product Purpose

Günlük çalışan bir tarayıcı (scraper) kamu ilan kaynaklarını toplar, her ilanı yapay zekâ ile okuyup iki profile göre süzer ve statik HTML raporlar üretir (`docs/index.html`, `docs/onlisans.html`, `docs/debug.html`). Başarı: sayfayı açan kişi ilk bakışta "bana en uygun ilan hangisi, ne zaman bitiyor" sorusunun cevabını görür ve yanlış-pozitif kartlarla vakit kaybetmez.

## Positioning

Genel ilan sitelerinden farkı: ilanlar kişinin gerçek şartlarına (KPSS puanı/türü, bölüm, yaş, tecrübe, boy-kilo) göre elenir, aynı ilanın farklı kaynaklardaki kopyaları tek kartta birleşir ve her karar ilan metninden alıntılanan kanıtla doğrulanabilir.

## Operating Context

- Rapor GitHub Actions / self-hosted Windows runner üzerinde günde bir kez (ve elle) yeniden üretilir; her çalıştırma `docs/` dosyalarını baştan yazar.
- Çıktı tek dosyalık statik HTML'dir (harici font/kütüphane yok), `report_ui.py` içindeki stil ve küçük betikle üretilir; tasarım değişiklikleri bu dosyada yapılır, veri mantığına dokunulmaz.
- Kaynaklar: İlan.gov.tr, Resmî Gazete, Kariyer Kapısı, SBB, ÇŞB, İŞKUR, Memurlar.net.

## Capabilities and Constraints

- Statik sayfa; sunucu yok. Etkileşim yalnızca satır içi betikle: arama, filtre düğmeleri, tema anahtarı.
- Ekran dili Türkçe. Tarihler `gg.aa.yyyy`.
- Kartlar ilan metninden gelen veri içerir; kaçış (escape) zorunludur.
- İkinci profilin sayfası (`onlisans.html`) aynı bileşenleri paylaşır.
- Açık karar: görsel kimlik henüz resmî olarak kayda geçmedi (DESIGN.md yok).

## Brand Commitments

Ad: "Kamu İlan Takip". Profiller baş harfle anılır (V, B). Başka marka taahhüdü yok.

## Evidence on Hand

Gerçek ilan verisi `data/ilanlar.sqlite3` içinde (yaklaşık 10 bin kayıt). Kullanıcı yorumu, müşteri ya da ölçüm verisi yoktur; uydurulmamalıdır.

## Product Principles

1. Kişiye uyum önce gelir: sayfa açılınca profile en uygun ilanlar öne çıkar; son başvuru tarihi ikinci sıradadır ama kaçırılmamalıdır.
2. Güven görünür olmalı: kanıt alıntısı, kaynak ve "doğrula" uyarıları gizlenmez.
3. Tekrar yok: aynı ilan tek kartta, diğer kaynaklar bağlantı olarak.
4. Telefon ve masaüstü eşit: aynı bilgi iki ekranda da taranabilir kalır.
5. Sessiz arayüz: günlük okunan bir araçtır; süs değil, tarama hızı ve netlik kazanır.

## Accessibility & Inclusion

Okunaklı kontrast (WCAG AA), klavye ile erişilebilir filtre ve arama, açık/koyu tema, hareket azaltma tercihine saygı. Kullanıcıların biri engelli kadro ilanlarını takip ediyor; erişilebilirlik bu nedenle ürünün kendisi için de önemlidir.

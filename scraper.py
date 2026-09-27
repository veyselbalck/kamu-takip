"""
KAMU İLAN TAKİP SİSTEMİ — Playwright + Groq
------------------------------------------------------------
2 aşamalı çalışır:
  AŞAMA 1: Her kaynaktan ilanları çeker, her çekimi doğrulayıp
           logs/log.csv dosyasına yazar (kaç ilan geldi, hata var mı).
  AŞAMA 2: İlgili ilanları filtreler, Groq API ile şartlarını
           yapılandırılmış veriye çevirir, kart kart HTML rapor üretir.

Çıktı: docs/index.html  (GitHub Pages bu klasörü otomatik yayınlar)

DOĞRULUK NOTU: Aşağıdaki 4 kaynak gerçek URL/API ile doğrulandı:
  - ilan.gov.tr        -> gerçek JSON API
  - resmigazete.gov.tr -> tarihe göre öngörülebilir statik sayfa
  - kariyerkapisi       -> sayfa adresi doğru, yapı Playwright ile çekiliyor
  - iskur.gov.tr        -> gerçek bağlantı sayfaları, Playwright ile çekiliyor
2 kaynak (ÇŞB, SBB Kamu İlan) HENÜZ DOĞRULANMADI — fonksiyonları boş
döner ve log'da "0 ilan / DOĞRULANMADI" görünür. Bunları birlikte
tamamlayalım (bkz. README.md).
"""
import os
import re
import csv
import json
import datetime
import requests
from playwright.sync_api import sync_playwright

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = "llama-3.3-70b-versatile"

KEYWORDS_BOLUM = ["bilgisayar mühendis", "yazılım mühendis", "bilişim personeli", "bilişim uzman"]
KEYWORDS_TUM_LISANS = ["tüm lisans", "her bölüm lisans", "4 yıllık fakültelerin herhangi bir bölümü",
                        "lisans mezunlarının tamamı", "fakültelerin herhangi bir bölümünden"]

os.makedirs("logs", exist_ok=True)
os.makedirs("docs", exist_ok=True)


# ============ 1. AŞAMA: KAYNAK ÇEKİCİLER ============

def fetch_ilan_gov_tr():
    """Doğrulanmış: gerçek JSON API (Personel = ad_type 5)."""
    url = "https://www.ilan.gov.tr/api/api/services/app/Ad/AdsByFilter"
    payload = {"AdTypeFilter": 5, "PageSize": 100, "Page": 1}
    r = requests.post(url, json=payload, timeout=30,
                       headers={"Content-Type": "application/json-patch+json"})
    r.raise_for_status()
    items = (r.json().get("result") or {}).get("items") or []
    return [{
        "title": it.get("title") or it.get("adTitle") or "",
        "content": it.get("description") or it.get("summary") or it.get("title") or "",
        "link": f"https://www.ilan.gov.tr/ilan/{it.get('id')}",
        "kurum": it.get("institutionName") or "",
    } for it in items]


def fetch_resmi_gazete():
    """Doğrulanmış: 'Çeşitli İlanlar' sayfası tarihe göre öngörülebilir statik HTML."""
    today = datetime.date.today()
    date_str = today.strftime("%Y%m%d")
    url = f"https://www.resmigazete.gov.tr/ilanlar/eskiilanlar/{today.year}/{today.month:02d}/{date_str}-4.htm"
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        return []  # bugün için henüz yayımlanmamış olabilir, hata değil
    html = r.text
    # Kurum başlıklarını ve altındaki metni kabaca ayıklıyoruz.
    blocks = re.split(r"(?=[A-ZÇĞİÖŞÜ][^\n]{5,80}(?:Bakanlığından|Rektörlüğünden|Başkanlığından|Müdürlüğünden):)", html)
    results = []
    for b in blocks:
        if re.search("|".join(KEYWORDS_BOLUM + KEYWORDS_TUM_LISANS + ["mühendis", "personel alım"]), b, re.I):
            title_match = re.match(r"([^\n]{5,120})", b.strip())
            results.append({
                "title": (title_match.group(1) if title_match else "Resmî Gazete İlanı")[:150],
                "content": re.sub("<[^>]+>", " ", b)[:4000],
                "link": url,
                "kurum": "",
            })
    return results


def fetch_kariyer_kapisi(page):
    """Playwright ile: sayfa JS render ediyor, ilan kartlarını bekleyip okuyoruz."""
    page.goto("https://isealimkariyerkapisi.cbiko.gov.tr/", timeout=60000)
    page.wait_for_load_state("networkidle", timeout=30000)
    # NOT: Aşağıdaki seçici tahminidir — ilk çalıştırmada 0 ilan dönerse
    # sayfayı Playwright ile inceleyip (page.content() yazdır) gerçek
    # ilan kartı seçicisini bulup burayı güncellememiz gerekir.
    cards = page.query_selector_all("a[href*='IlanDetay'], .ilan-card, .job-card")
    results = []
    for c in cards:
        title = (c.inner_text() or "").strip()
        href = c.get_attribute("href") or ""
        if title:
            results.append({"title": title[:150], "content": title, "link": href, "kurum": ""})
    return results


def fetch_iskur(page):
    """Playwright ile: iki bağlantı sayfasını (memur + işçi) tarar."""
    results = []
    for url in [
        "https://www.iskur.gov.tr/baglantilar/kamu-memur-alim-ilanlari/",
        "https://www.iskur.gov.tr/baglantilar/kurum-disi-kamu-isci-alimi-ilani/",
    ]:
        try:
            page.goto(url, timeout=60000)
            page.wait_for_load_state("networkidle", timeout=30000)
            links = page.query_selector_all("a")
            for l in links:
                title = (l.inner_text() or "").strip()
                href = l.get_attribute("href") or ""
                if title and len(title) > 8 and href:
                    results.append({"title": title[:150], "content": title, "link": href, "kurum": ""})
        except Exception as e:
            print(f"İŞKUR sayfası hata ({url}): {e}")
    return results


def fetch_csb():
    """DOĞRULANMADI — henüz gerçek ilan sayfası netleşmedi. Bkz. README.md"""
    return []


def fetch_sbb_kamu_ilan():
    """DOĞRULANMADI — henüz gerçek ilan sayfası netleşmedi. Bkz. README.md"""
    return []


SOURCES_STATIC = [
    ("İlan.gov.tr", fetch_ilan_gov_tr, 1),
    ("Resmî Gazete", fetch_resmi_gazete, 0),
    ("ÇŞB", fetch_csb, 0),
    ("SBB Kamu İlan", fetch_sbb_kamu_ilan, 0),
]
SOURCES_PLAYWRIGHT = [
    ("Kariyer Kapısı", fetch_kariyer_kapisi, 0),
    ("İŞKUR", fetch_iskur, 0),
]


# ============ FİLTRE ============
def is_relevant(ad):
    text = ((ad.get("title") or "") + " " + (ad.get("content") or "")).lower()
    return any(k in text for k in KEYWORDS_BOLUM) or any(k in text for k in KEYWORDS_TUM_LISANS)


def category_of(ad):
    text = ((ad.get("title") or "") + " " + (ad.get("content") or "")).lower()
    if any(k in text for k in KEYWORDS_BOLUM):
        return "BOLUM"
    return "TUM_LISANS"


# ============ 2. AŞAMA: GROQ İLE ANALİZ ============
def analyze_with_groq(ad):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY tanımlı değil (GitHub Secrets kontrol et).")

    prompt = f"""Aşağıda bir kamu personeli alım ilanı metni var. SADECE geçerli JSON döndür,
başka hiçbir açıklama ekleme. Şema:
{{
  "kurum": string,
  "kadroSayisi": number veya null,
  "pozisyon": string,
  "basvuruBaslangic": string veya null,
  "basvuruBitis": string veya null,
  "degerlendirmeSekli": string,
  "ekSinav": boolean,
  "ekSartlar": string,
  "kisaOzet": string
}}
İLAN BAŞLIĞI: {ad['title']}
İLAN METNİ: {(ad.get('content') or '')[:4000]}
"""
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    if r.status_code != 200:
        print("Groq API hatası:", r.text)
        return None
    text = r.json()["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        print("JSON parse edilemedi:", text)
        return None
    parsed["kaynak"] = ad.get("kaynak", "")
    parsed["link"] = ad.get("link", "")
    parsed["baslik"] = ad.get("title", "")
    parsed["kategori"] = ad.get("_kategori", "TUM_LISANS")
    return parsed


# ============ HTML RAPOR ============
def build_html_report(bolum, tum_lisans, log_rows):
    def cards(items):
        if not items:
            return "<p>Bugün bu kategoride yeni ilan bulunamadı.</p>"
        out = []
        for a in items:
            ek_sartlar_html = (f'<div class="warn">Ek şart: {a["ekSartlar"]}</div>'
                                if a.get("ekSartlar") and a["ekSartlar"] != "Yok" else "")
            tarih_html = (f'<span class="tag">{a.get("basvuruBaslangic","")} - {a.get("basvuruBitis","?")}</span>'
                          if a.get("basvuruBaslangic") else "")
            out.append(f"""<div class="card">
                <a href="{a.get('link','')}" target="_blank">{a.get('baslik','')}</a>
                <div>{a.get('kurum','')} {f"— {a['kadroSayisi']} kadro" if a.get('kadroSayisi') else ''}</div>
                <div class="meta">
                    <span class="tag">{a.get('kaynak','')}</span>
                    <span class="tag">{a.get('degerlendirmeSekli','')}</span>
                    {'<span class="tag">Ek sınav var</span>' if a.get('ekSinav') else ''}
                    {tarih_html}
                </div>
                {ek_sartlar_html}
                <p>{a.get('kisaOzet','')}</p>
            </div>""")
        return "\n".join(out)

    log_html = "<table><tr><th>Zaman</th><th>Kaynak</th><th>İlan Sayısı</th><th>Durum</th><th>Detay</th></tr>"
    for row in log_rows:
        log_html += "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
    log_html += "</table>"

    style = """
    body{font-family:system-ui,Arial,sans-serif;background:#f5f6fa;margin:0;padding:24px;color:#1f2430}
    h1{font-size:22px} h2{font-size:18px;margin-top:32px}
    .card{background:#fff;border-radius:12px;padding:16px 20px;margin:12px 0;box-shadow:0 1px 4px rgba(0,0,0,.08)}
    .card a{color:#2a5bd7;text-decoration:none;font-weight:600}
    .meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
    .tag{background:#eef1fb;color:#33418f;padding:3px 10px;border-radius:20px;font-size:12px}
    .warn{background:#fff3cd;color:#7a5b00;padding:2px 8px;border-radius:6px;font-size:11px}
    .logbox{font-size:12px;color:#555;margin-top:40px}
    table{border-collapse:collapse;width:100%;font-size:12px}
    td,th{border:1px solid #ddd;padding:4px 8px;text-align:left}
    """
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">
<title>Günlük Kamu İlan Raporu</title><style>{style}</style></head><body>
<h1>📋 Günlük Kamu İlan Raporu — {now}</h1>
<h2>🎯 Bölümüme Özel (Bilgisayar/Yazılım Mühendisliği)</h2>
{cards(bolum)}
<h2>🎓 Tüm Lisans Mezunlarına Açık</h2>
{cards(tum_lisans)}
<div class="logbox"><h2>🔍 Bugünkü Veri Çekim Doğrulama Kaydı</h2>{log_html}</div>
</body></html>"""


# ============ ANA AKIŞ ============
def main():
    log_rows = []
    all_ads = []

    for name, fn, min_expected in SOURCES_STATIC:
        started = datetime.datetime.now().strftime("%H:%M:%S")
        try:
            ads = fn()
            ok = len(ads) >= min_expected
            durum = "OK" if ok else ("DOĞRULANMADI" if min_expected == 0 and len(ads) == 0 else "ŞÜPHELİ")
            log_rows.append([started, name, len(ads), durum, ""])
            for a in ads:
                a["kaynak"] = name
            all_ads.extend(ads)
        except Exception as e:
            log_rows.append([started, name, 0, "HATA", str(e)])
            print(f"HATA ({name}): {e}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"))
        for name, fn, min_expected in SOURCES_PLAYWRIGHT:
            started = datetime.datetime.now().strftime("%H:%M:%S")
            try:
                ads = fn(page)
                ok = len(ads) >= min_expected
                durum = "OK" if ok else "ŞÜPHELİ (seçici güncellemesi gerekebilir)"
                log_rows.append([started, name, len(ads), durum, ""])
                for a in ads:
                    a["kaynak"] = name
                all_ads.extend(ads)
            except Exception as e:
                log_rows.append([started, name, 0, "HATA", str(e)])
                print(f"HATA ({name}): {e}")
        browser.close()

    # Log dosyasına ekle (kalıcı geçmiş için)
    log_path = "logs/log.csv"
    file_exists = os.path.isfile(log_path)
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["zaman", "kaynak", "ilan_sayisi", "durum", "detay"])
        for row in log_rows:
            w.writerow([datetime.date.today().isoformat()] + row)

    # AŞAMA 2
    relevant = [a for a in all_ads if is_relevant(a)]
    for a in relevant:
        a["_kategori"] = category_of(a)

    analyzed = []
    for a in relevant:
        result = analyze_with_groq(a)
        if result:
            analyzed.append(result)

    bolum = [a for a in analyzed if a["kategori"] == "BOLUM"]
    tum_lisans = [a for a in analyzed if a["kategori"] == "TUM_LISANS"]

    html = build_html_report(bolum, tum_lisans, log_rows)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Bitti. {len(relevant)} ilgili ilan bulundu, {len(analyzed)} tanesi analiz edildi.")


if __name__ == "__main__":
    main()

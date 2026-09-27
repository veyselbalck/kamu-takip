"""
KAMU İLAN TAKİP SİSTEMİ — Playwright + Groq
============================================================
AKIŞ
  AŞAMA 1  Her kaynağın ilan listesi çekilir. Kaç ham kayıt bulunduğu
           logs/log.csv'ye yazılır (0 ise sayfa okunamamıştır).
  DETAY    Daha önce görülmemiş HER ilanın detay sayfası Playwright ile
           açılıp tam metni okunur. Görülmüş ilanlar data/state.json'dan
           gelir (tekrar okunmaz) -> sadece YENİ ilanlar rozetlenir.
  AŞAMA 2  Aday ilanlar Groq'a verilir: kategori (BOLUM / TUM_LISANS /
           ILGISIZ), kanıt cümlesi, tarihler, değerlendirme şekli,
           ikamet şartı, ek şartlar çıkarılır. Kart kart HTML üretilir.

DAYANIKLILIK
  - Süre sınırı (MAX_RUNTIME_MIN): yetişmezse işlenmeyenler state'e
    yazılmaz, ertesi gün kaldığı yerden devam eder.
  - Groq başarısız olursa ilan "yeniden dene" olarak saklanır.
  - Bir kaynak çökerse diğerleri devam eder; hata log'a yazılır.

KAYNAKLAR
  1 ilan.gov.tr  2 Resmî Gazete  3 Kariyer Kapısı
  4 SBB Kamu İlan  5 İŞKUR e-şube  6 ÇŞB Yerel Yönetimler duyurular
"""
import os
import re
import csv
import json
import time
import hashlib
import html as htmllib
import datetime
from urllib.parse import urljoin

import requests
import urllib3
from playwright.sync_api import sync_playwright

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------- AYARLAR ----------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_RUNTIME_MIN = 300            # bu süreden sonra yeni detay okumayı bırak
MAX_LINKS_PER_SOURCE = 400       # bir kaynaktan en fazla kaç ilan detayı
GROQ_SLEEP_SEC = 3               # ücretsiz kotayı aşmamak için bekleme
STATE_PATH = "data/state.json"
LOG_PATH = "logs/log.csv"

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Detay metninde bunlardan biri geçiyorsa Groq'a gönderilir (geniş ön eleme)
ADAY_ANAHTAR = re.compile(
    r"lisans|mühendis|bilişim|bilgisayar|yazılım|4 yıllık|dört yıllık|fakülte", re.I)

SOSYAL = ("twitter.com", "facebook.com", "instagram.com", "linkedin.com",
          "youtube.com", "x.com", "wa.me", "t.me")

START = time.time()
TODAY = datetime.date.today().isoformat()

os.makedirs("logs", exist_ok=True)
os.makedirs("docs", exist_ok=True)
os.makedirs("data", exist_ok=True)


def zaman_doldu():
    return (time.time() - START) / 60 > MAX_RUNTIME_MIN


def request_with_retry(method, url, retries=3, timeout=60, **kwargs):
    """Bazı .gov.tr sitelerinde sertifika zinciri eksik / yanıt yavaş."""
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", BROWSER_UA)
    last = None
    for i in range(1, retries + 1):
        try:
            return requests.request(method, url, timeout=timeout, verify=False,
                                    headers=headers, **kwargs)
        except requests.exceptions.RequestException as e:
            last = e
            print(f"Deneme {i}/{retries} başarısız ({url}): {e}")
            time.sleep(5)
    raise last


# ---------------- STATE ----------------
def load_state():
    if os.path.isfile(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"first_run": TODAY, "ads": {}}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


# ---------------- ORTAK YARDIMCILAR ----------------
def collect_links(page, url, must_contain=None, wait_extra=0):
    """Sayfadaki tüm anlamlı linkleri (ilan adayları) toplar."""
    page.goto(url, timeout=90000)
    try:
        page.wait_for_load_state("networkidle", timeout=40000)
    except Exception:
        pass
    if wait_extra:
        time.sleep(wait_extra)
    seen, out = set(), []
    for a in page.query_selector_all("a"):
        href = (a.get_attribute("href") or "").strip()
        text = (a.inner_text() or "").strip().replace("\n", " ")
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(url, href)
        if any(s in full for s in SOSYAL) or full in seen:
            continue
        if must_contain and must_contain not in full:
            continue
        if len(text) < 8:
            continue
        seen.add(full)
        out.append({"title": text[:200], "link": full})
    return out[:MAX_LINKS_PER_SOURCE]


def read_detail(page, url):
    """Bir ilanın detay sayfasını açıp tüm görünen metni döndürür."""
    try:
        page.goto(url, timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=25000)
        except Exception:
            pass
        return (page.inner_text("body") or "")[:14000]
    except Exception as e:
        print(f"Detay okunamadı ({url}): {e}")
        return ""


# ---------------- KAYNAK ÇEKİCİLER ----------------
# Her biri (entries, ham_kayit, not) döndürür.
# entry: {title, link, kurum?, content?}   content varsa detay açılmaz.

def fetch_ilan_gov_tr(page):
    """Önce JSON API denenir (sayfalamalı); olmazsa sayfa linkleri okunur."""
    url = "https://www.ilan.gov.tr/api/api/services/app/Ad/AdsByFilter"
    entries, toplam_bildirilen = [], None
    try:
        skip, size = 0, 100
        for _ in range(60):
            payload = {"AdTypeFilter": 5, "PageSize": size, "Page": skip // size + 1,
                       "SkipCount": skip, "MaxResultCount": size}
            r = request_with_retry("post", url, json=payload,
                                   headers={"Content-Type": "application/json-patch+json"})
            r.raise_for_status()
            res = r.json().get("result") or {}
            items = res.get("items") or []
            toplam_bildirilen = res.get("totalCount", toplam_bildirilen)
            for it in items:
                entries.append({
                    "title": it.get("title") or it.get("adTitle") or "",
                    "link": f"https://www.ilan.gov.tr/ilan/{it.get('id')}",
                    "kurum": it.get("institutionName") or "",
                })
            if len(items) < size:
                break
            skip += size
        if entries:
            not_ = f"API; sitenin bildirdiği toplam: {toplam_bildirilen}"
            if toplam_bildirilen and len(entries) < toplam_bildirilen:
                not_ += " (EKSİK OKUNDU!)"
            return entries, len(entries), not_
    except Exception as e:
        print(f"ilan.gov.tr API başarısız, sayfa okunacak: {e}")
    entries = collect_links(page, "https://www.ilan.gov.tr/ilan/tum-ilanlar/personel-alimi?ats=5",
                            must_contain="/ilan/", wait_extra=3)
    return entries, len(entries), "API çalışmadı, sayfa linkleri okundu"


def fetch_resmi_gazete(page):
    today = datetime.date.today()
    url = (f"https://www.resmigazete.gov.tr/ilanlar/eskiilanlar/{today.year}/"
           f"{today.month:02d}/{today.strftime('%Y%m%d')}-4.htm")
    r = request_with_retry("get", url)
    if r.status_code != 200:
        return [], 0, f"bugünkü ilan sayfası yok (HTTP {r.status_code})"
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = htmllib.unescape(re.sub(r"\n\s*\n+", "\n", text))
    blocks = re.split(
        r"(?=\n[^\n]{5,90}(?:Bakanlığından|Rektörlüğünden|Başkanlığından|Müdürlüğünden|"
        r"Genel Müdürlüğünden|Kurumundan|Belediyesinden|Valiliğinden)[:\s])", text)
    entries = []
    for b in blocks:
        b = b.strip()
        if len(b) < 120:
            continue
        title = b.split("\n")[0][:150]
        key = hashlib.md5(b[:400].encode()).hexdigest()[:10]
        entries.append({"title": title, "link": f"{url}#{key}", "content": b[:14000],
                        "kurum": title})
    return entries, len(entries), "tek sayfa, bloklara bölündü"


def fetch_kariyer_kapisi(page):
    url = "https://kariyerkapisi.gov.tr/isealim"
    page.goto(url, timeout=90000)
    try:
        page.wait_for_load_state("networkidle", timeout=40000)
    except Exception:
        pass
    body = (page.inner_text("body") or "").lower()
    entries = []
    for tr in page.query_selector_all("table tr"):
        a = tr.query_selector("a")
        if not a:
            continue
        href = a.get_attribute("href") or ""
        row_text = (tr.inner_text() or "").strip().replace("\n", " | ")
        if href and not href.startswith(("#", "javascript:")):
            entries.append({"title": row_text[:200], "link": urljoin(url, href),
                            "kurum": row_text.split("|")[0].strip()})
    if not entries and "aktif bir ilan bulunmamaktadır" in body:
        return [], 0, "site 'aktif ilan yok' diyor (normal)"
    if not entries:
        entries = collect_links(page, url)  # yedek: tüm linkler
        return entries, len(entries), "tablo bulunamadı, yedek link toplama"
    return entries, len(entries), "tablo satırları"


def fetch_sbb_kamu_ilan(page):
    entries = collect_links(page, "https://kamuilan.sbb.gov.tr/", wait_extra=3)
    return entries, len(entries), "sayfa linkleri (yapı doğrulanmadı)"


def fetch_csb_yerel(page):
    entries = collect_links(page, "https://yerelyonetimler.csb.gov.tr/duyurular", wait_extra=3)
    return entries, len(entries), "sayfa linkleri (yapı doğrulanmadı)"


def fetch_iskur_esube(page):
    """ASP.NET postback sayfası: Kamu seç, Ara'ya bas, sonuç satırlarını oku.
    Detay penceresi postback ile açıldığı için satır metni içerik olarak alınır."""
    page.goto("https://esube.iskur.gov.tr/Istihdam/AcikIsIlanAra.aspx", timeout=90000)
    try:
        page.wait_for_load_state("networkidle", timeout=40000)
    except Exception:
        pass
    try:
        page.get_by_text("Kamu", exact=True).first.click(timeout=8000)
    except Exception as e:
        print(f"İŞKUR 'Kamu' tıklanamadı: {e}")
    try:
        page.get_by_text("Ara", exact=True).first.click(timeout=8000)
        page.wait_for_load_state("networkidle", timeout=40000)
    except Exception as e:
        print(f"İŞKUR 'Ara' tıklanamadı: {e}")
    entries = []
    for i, tr in enumerate(page.query_selector_all("table tr")):
        t = (tr.inner_text() or "").strip().replace("\n", " | ")
        if len(t) > 25:
            key = hashlib.md5(t.encode()).hexdigest()[:10]
            entries.append({"title": t[:150], "content": t,
                            "link": f"https://esube.iskur.gov.tr/Istihdam/AcikIsIlanAra.aspx#{key}"})
    return entries[:MAX_LINKS_PER_SOURCE], len(entries), "sonuç tablosu satırları (yapı doğrulanmadı)"


SOURCES = [
    ("İlan.gov.tr", fetch_ilan_gov_tr),
    ("Resmî Gazete", fetch_resmi_gazete),
    ("Kariyer Kapısı", fetch_kariyer_kapisi),
    ("SBB Kamu İlan", fetch_sbb_kamu_ilan),
    ("İŞKUR (e-şube)", fetch_iskur_esube),
    ("ÇŞB Yerel Yönetimler", fetch_csb_yerel),
]


# ---------------- GROQ ANALİZİ ----------------
SEMA = """{
  "kategori": "BOLUM" | "TUM_LISANS" | "ILGISIZ",
  "kanit": "kategoriyi destekleyen ilan metninden KISA birebir alıntı (max 200 karakter)",
  "kurum": string,
  "pozisyon": string,
  "kadroSayisi": number | null,
  "basvuruBaslangic": string | null,
  "basvuruBitis": string | null,
  "degerlendirmeSekli": "örn: %100 KPSS | KPSS + sözlü mülakat | KPSS + yazılı sınav | sadece yazılı/sözlü | belirtilmemiş",
  "kpssTuru": "örn: KPSS-P3 en az 70 puan | belirtilmemiş",
  "ekSinav": boolean,
  "ikametSarti": "Yok" | "Belirtilmemiş" | "Var: <şehir/il/ilçe>",
  "ekSartlar": "boy/kilo/yaş sınırı, yabancı dil puanı, ehliyet, deneyim, askerlik vb. ya da 'Yok'",
  "kisaOzet": "en fazla 2 cümle"
}"""


def groq_analiz(baslik, metin):
    prompt = (
        "Aşağıda bir Türkiye kamu personeli alım ilanının tam metni var.\n"
        "Görevin: ilanı bilgisayar mühendisliği mezunu bir aday açısından sınıflandırıp "
        "bilgileri çıkarmak. SADECE geçerli JSON döndür.\n\n"
        "KATEGORİ KURALLARI:\n"
        "- BOLUM: ilanda Bilgisayar Mühendisliği / Yazılım Mühendisliği / Bilişim ile ilgili "
        "bölümler özellikle sayılıyor (ör. 'Bilişim Personeli' için kabul edilen bölümler arasında "
        "Bilgisayar Mühendisliği geçiyor).\n"
        "- TUM_LISANS: bölüm kısıtı YOK; herhangi bir lisans (4 yıllık) mezunu başvurabiliyor.\n"
        "- ILGISIZ: sadece başka bölümler isteniyor, lise/önlisans, işçi alımı vb. ya da bir "
        "bilgisayar mühendisi başvuramıyor.\n"
        "Bir ilanda birden çok kadro varsa bilgisayar mühendisinin başvurabildiği kadroyu esas al "
        "ve kisaOzet'te belirt.\n\n"
        "KESİN KURALLAR: Metinde yazmayan hiçbir bilgiyi UYDURMA. Bilgi yoksa null veya "
        "'Belirtilmemiş' yaz. 'kanit' alanı metinden birebir alıntı olmalı. ikametSarti için "
        "'ikamet', 'oturmak', 'ilinde ikamet eden' gibi ifadelere bak; varsa şehri yaz.\n\n"
        "JSON ŞEMASI:\n" + SEMA + "\n\n"
        "İLAN BAŞLIĞI: " + baslik + "\n\nİLAN METNİ:\n" + metin[:9000]
    )
    for deneme in range(1, 5):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": GROQ_MODEL,
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.0,
                      "response_format": {"type": "json_object"}},
                timeout=90)
        except requests.exceptions.RequestException as e:
            print(f"Groq bağlantı hatası: {e}")
            time.sleep(10)
            continue
        if r.status_code == 429:
            bekle = int(float(r.headers.get("retry-after", "20"))) + 2
            print(f"Groq kota (429), {bekle}s bekleniyor ({deneme}/4)")
            time.sleep(bekle)
            continue
        if r.status_code != 200:
            print("Groq hata:", r.status_code, r.text[:200])
            return None
        try:
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            print("Groq JSON okunamadı:", e)
            return None
    return None


# ---------------- HTML RAPOR ----------------
E = lambda x: htmllib.escape(str(x)) if x not in (None, "") else ""


def card_html(a):
    yeni = '<span class="new">🆕 Bugün eklendi</span>' if a.get("_yeni") else ""
    ikamet = a.get("ikametSarti") or "Belirtilmemiş"
    ikamet_cls = "tag red" if ikamet.startswith("Var") else "tag"
    ek = a.get("ekSartlar") or "Yok"
    ek_html = "" if ek.strip().lower() == "yok" else f'<div class="warn">⚠ Ek şart: {E(ek)}</div>'
    tarih = ""
    if a.get("basvuruBaslangic") or a.get("basvuruBitis"):
        tarih = f'<span class="tag">📅 {E(a.get("basvuruBaslangic") or "?")} → {E(a.get("basvuruBitis") or "?")}</span>'
    kadro = f' — {E(a["kadroSayisi"])} kadro' if a.get("kadroSayisi") else ""
    return f"""<div class="card">
  <div class="top"><a href="{E(a.get('link'))}" target="_blank">{E(a.get('pozisyon') or a.get('_baslik'))}</a>{yeni}</div>
  <div class="kurum">{E(a.get('kurum'))}{kadro}</div>
  <div class="meta">
    <span class="tag src">{E(a.get('_kaynak'))}</span>
    {tarih}
    <span class="tag">🧾 {E(a.get('degerlendirmeSekli'))}</span>
    <span class="tag">KPSS: {E(a.get('kpssTuru') or 'belirtilmemiş')}</span>
    {'<span class="tag">Ek sınav var</span>' if a.get('ekSinav') else ''}
    <span class="{ikamet_cls}">📍 İkamet: {E(ikamet)}</span>
  </div>
  {ek_html}
  <p>{E(a.get('kisaOzet'))}</p>
  <div class="kanit">Kanıt: “{E(a.get('kanit'))}”</div>
  <a class="btn" href="{E(a.get('link'))}" target="_blank">İlana git ↗</a>
</div>"""


def build_html_report(bolum, tum, log_rows, yeni_sayisi):
    def section(items):
        if not items:
            return "<p>Şu an bu kategoride aktif ilan bulunamadı.</p>"
        items = sorted(items, key=lambda x: (not x.get("_yeni"), x.get("_kaynak", "")))
        return "\n".join(card_html(a) for a in items)

    log_html = ("<table><tr><th>Saat</th><th>Kaynak</th><th>Ham kayıt</th><th>Detay okunan</th>"
                "<th>İlgili</th><th>Durum</th><th>Not</th></tr>")
    for row in log_rows:
        log_html += "<tr>" + "".join(f"<td>{E(c)}</td>" for c in row) + "</tr>"
    log_html += "</table>"

    style = """
    body{font-family:system-ui,Arial,sans-serif;background:#f5f6fa;margin:0;padding:24px;color:#1f2430;max-width:1000px;margin:auto}
    h1{font-size:22px} h2{font-size:18px;margin-top:32px}
    .card{background:#fff;border-radius:12px;padding:16px 20px;margin:12px 0;box-shadow:0 1px 4px rgba(0,0,0,.08)}
    .top a{color:#2a5bd7;text-decoration:none;font-weight:700;font-size:16px}
    .new{background:#16a34a;color:#fff;border-radius:6px;padding:2px 8px;font-size:12px;margin-left:8px}
    .kurum{color:#555;margin-top:2px}
    .meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
    .tag{background:#eef1fb;color:#33418f;padding:3px 10px;border-radius:20px;font-size:12px}
    .tag.red{background:#fde8e8;color:#9b1c1c} .tag.src{background:#e8f5e9;color:#1b5e20}
    .warn{background:#fff3cd;color:#7a5b00;padding:4px 10px;border-radius:6px;font-size:12px;margin-top:8px}
    .kanit{font-size:11px;color:#777;font-style:italic;margin-top:4px}
    .btn{display:inline-block;margin-top:8px;font-size:12px;color:#2a5bd7}
    .logbox{font-size:12px;color:#555;margin-top:40px;overflow-x:auto}
    table{border-collapse:collapse;width:100%;font-size:12px}
    td,th{border:1px solid #ddd;padding:4px 8px;text-align:left}
    """
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Günlük Kamu İlan Raporu</title><style>{style}</style></head><body>
<h1>📋 Günlük Kamu İlan Raporu — {now}</h1>
<p>Bugün yeni eklenen ilgili ilan: <b>{yeni_sayisi}</b></p>
<h2>🎯 Bölümüme Özel (Bilgisayar/Yazılım Mühendisliği) — {len(bolum)} ilan</h2>
{section(bolum)}
<h2>🎓 Tüm Lisans Mezunlarına Açık — {len(tum)} ilan</h2>
{section(tum)}
<div class="logbox"><h2>🔍 Veri Çekim Doğrulama Kaydı</h2>{log_html}</div>
</body></html>"""


# ---------------- ANA AKIŞ ----------------
def main():
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY tanımlı değil (GitHub Secrets kontrol et).")

    state = load_state()
    ads_state = state["ads"]
    log_rows = []
    bugun_gorulen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(user_agent=BROWSER_UA, ignore_https_errors=True, locale="tr-TR")
        list_page = ctx.new_page()
        detail_page = ctx.new_page()

        for name, fn in SOURCES:
            saat = datetime.datetime.now().strftime("%H:%M:%S")
            try:
                entries, ham, note = fn(list_page)
            except Exception as e:
                log_rows.append([saat, name, 0, 0, 0, "HATA", str(e)[:300]])
                print(f"HATA ({name}): {e}")
                continue

            okunan = ilgili = 0
            for e in entries:
                key = e["link"]
                bugun_gorulen.add(key)
                kayit = ads_state.get(key)

                if kayit and (kayit.get("analiz") or not kayit.get("aday")):
                    kayit["last_seen"] = TODAY
                    continue  # zaten işlenmiş

                if zaman_doldu():
                    bugun_gorulen.discard(key)  # yarın işlenecek
                    continue

                # Detay metni
                metin = e.get("content") or (kayit or {}).get("metin") or read_detail(detail_page, key)
                okunan += 1
                aday = bool(metin) and bool(ADAY_ANAHTAR.search(metin + " " + e.get("title", "")))
                kayit = kayit or {"first_seen": TODAY, "kaynak": name, "baslik": e.get("title", ""),
                                  "kurum": e.get("kurum", "")}
                kayit.update({"last_seen": TODAY, "aday": aday})

                if not metin:
                    # detay okunamadı: kaydetme ki yarın tekrar denensin
                    bugun_gorulen.discard(key)
                    continue

                if aday:
                    sonuc = groq_analiz(e.get("title", ""), metin)
                    time.sleep(GROQ_SLEEP_SEC)
                    if sonuc:
                        kayit["analiz"] = sonuc
                        kayit.pop("metin", None)
                    else:
                        kayit["metin"] = metin[:9000]  # yarın yeniden denenecek
                ads_state[key] = kayit
                if kayit.get("analiz") and kayit["analiz"].get("kategori") in ("BOLUM", "TUM_LISANS"):
                    ilgili += 1

            durum = "OK" if ham > 0 or "normal" in note else "ŞÜPHELİ (0 ham kayıt)"
            log_rows.append([saat, name, ham, okunan, ilgili, durum, note])
            save_state(state)  # her kaynaktan sonra ilerlemeyi kaydet

        browser.close()

    # Rapor: bugün listede görülen + ilgili ilanlar
    bolum, tum, yeni_sayisi = [], [], 0
    for key, k in ads_state.items():
        an = k.get("analiz")
        if key not in bugun_gorulen or not an or an.get("kategori") not in ("BOLUM", "TUM_LISANS"):
            continue
        a = dict(an)
        a.update({"link": key, "_kaynak": k.get("kaynak"), "_baslik": k.get("baslik"),
                  "_yeni": (k.get("first_seen") == TODAY and TODAY != state.get("first_run"))})
        yeni_sayisi += 1 if a["_yeni"] else 0
        (bolum if a.get("kategori") == "BOLUM" else tum).append(a)

    # Log CSV
    yeni_dosya = not os.path.isfile(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if yeni_dosya:
            w.writerow(["tarih", "saat", "kaynak", "ham_kayit", "detay_okunan", "ilgili", "durum", "not"])
        for row in log_rows:
            w.writerow([TODAY] + row)

    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(build_html_report(bolum, tum, log_rows, yeni_sayisi))
    save_state(state)
    print(f"Bitti. Bölüm: {len(bolum)}, Tüm lisans: {len(tum)}, Yeni: {yeni_sayisi}")


if __name__ == "__main__":
    main()

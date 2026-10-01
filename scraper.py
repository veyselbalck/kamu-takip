"""
KAMU İLAN TAKİP SİSTEMİ v3 — Playwright + Groq
============================================================
AKIŞ
  1) Her kaynağın ilan listesi (sayfalama dahil) çekilir.
  2) Görülmemiş her ilanın detay sayfası açılıp tam metni okunur.
  3) Kademeli analiz (Groq ücretsiz kotasını korumak için):
       a) ön eleme  : küçük/hızlı model -> BOLUM / TUM_LISANS / ILGISIZ
       b) ayrıntı   : büyük model -> tarihler, KPSS, ikamet, ek şartlar...
  4) docs/index.html   : kart kart rapor + "okunan tüm ilanlar" tablosu
     docs/debug.html   : her sitenin ekran görüntüsü, link örnekleri,
                         arka plan (XHR) istekleri  -> sorun tespiti için

DOĞRULAMA
  - "ham_kayit" sitenin gösterdiği ilan sayısıyla karşılaştırılır
    (ilan.gov.tr "Toplam N ilan" yazıyor). Eksikse durum ŞÜPHELİ olur.
  - "okunan tüm ilanlar" tablosunda her ilan için metin uzunluğu ve
    sonuç görünür; çok kısa metin = detay sayfası okunamamış demektir.

DAYANIKLILIK
  - Süre sınırı MAX_RUNTIME_MIN; yetişmeyenler ertesi gün devam eder.
  - Groq kotası biterse kalanlar "beklemede" kalır, ertesi gün işlenir.
  - Bir kaynak çökerse diğerleri devam eder.
"""
import os
import re
import csv
import json
import time
import io
import hashlib
import html as htmllib
import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlparse

# Windows'ta antivirüs/proxy HTTPS'i araya girip tarıyorsa Python'un kendi sertifika
# listesi güvenmez. truststore, Windows sertifika deposunu kullanmasını sağlar.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import requests
import urllib3
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------- AYARLAR ----------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODELS = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
GROQ_MODEL = GROQ_MODELS[0]                 # ana model; kotası dolarsa sıradakine geçilir
GROQ_DEAD_MODELS = set()                    # bugün kotası bitmiş / kullanılamayan modeller
DIAGNOSE = os.environ.get("DIAGNOSE", "1") == "1"

MAX_RUNTIME_MIN = 300
MAX_LINKS_PER_SOURCE = 400
GROQ_SLEEP_SEC = 2.5
STATE_VERSION = 2
STATE_PATH = "data/state.json"
LOG_PATH = "logs/log.csv"

TR_NOW = datetime.datetime.now(ZoneInfo("Europe/Istanbul"))
TODAY = TR_NOW.date().isoformat()
START = time.time()

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

ADAY_ANAHTAR = re.compile(
    r"bilgisayar|yazılım|bilişim|programcı|programlama|bilgi işlem|siber güvenlik|"
    r"herhangi bir.{0,30}lisans|tüm lisans|lisans mezunu|"
    r"(?:4|dört) yıllık lisans|lisans programlarının.{0,60}birinden mezun", re.I | re.S)
AKADEMIK_BASLIK = re.compile(
    r"öğretim üyesi alım|öğretim elemanı alım|öğretim görevlisi alım|araştırma görevlisi", re.I)
SOSYAL = ("twitter.com", "facebook.com", "instagram.com", "linkedin.com",
          "youtube.com", "//x.com", "wa.me", "t.me")

for d in ("logs", "docs", "docs/debug", "data"):
    os.makedirs(d, exist_ok=True)

GROQ_DEAD = False            # günlük kota bittiyse True
CAPTURE = {"on": False, "name": ""}
XHR = {}                     # kaynak adı -> [istekler]
DEBUG = []                   # tanı sayfası kayıtları


def zaman_doldu():
    return (time.time() - START) / 60 > MAX_RUNTIME_MIN


def request_with_retry(method, url, retries=3, timeout=60, **kwargs):
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
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                st = json.load(f)
            if st.get("version") == STATE_VERSION:
                return st
            print("State şeması eski, sıfırlanıyor.")
        except Exception as e:
            print("State okunamadı, sıfırlanıyor:", e)
    return {"version": STATE_VERSION, "first_run": TODAY, "ads": {}}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


# ---------------- SAYFA YARDIMCILARI ----------------
def goto_safe(page, url, timeout=60000, settle=2.0):
    page.goto(url, timeout=timeout, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=25000)
    except Exception:
        pass
    if settle:
        time.sleep(settle)


def links_matching(page, base, pattern=None, exclude=None, min_text=0):
    """Sayfadaki anchor'lardan (url,metin) toplar; pattern/exclude regex."""
    out, seen = [], set()
    for a in page.query_selector_all("a"):
        href = (a.get_attribute("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(base, href)
        if full in seen or any(s in full for s in SOSYAL):
            continue
        if pattern and not re.search(pattern, full, re.I):
            continue
        if exclude and re.search(exclude, full, re.I):
            continue
        text = (a.inner_text() or "").strip().replace("\n", " | ")
        if len(text) < min_text:
            continue
        seen.add(full)
        out.append({"title": text[:220] or full, "link": full})
    return out


def read_detail(page, url):
    """Detay sayfasının görünen metnini döndürür (SPA'lar için ek bekleme)."""
    try:
        page.goto(url, timeout=60000, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        txt = page.inner_text("body") or ""
        if len(txt) < 400:
            page.wait_for_timeout(4500)
            txt = page.inner_text("body") or ""
        return txt[:14000]
    except Exception as e:
        print(f"Detay okunamadı ({url}): {str(e)[:120]}")
        return ""


# ---------------- KAYNAK ÇEKİCİLER ----------------
# (entries, ham_kayit, not) döndürür. entry: {title, link, kurum?, content?}

def fetch_ilan_gov_tr(page):
    base = "https://www.ilan.gov.tr/ilan/tum-ilanlar/personel-alimi?ats=5"
    pat, exc = r"/ilan/\d+", r"/ilan/tum-ilanlar"
    goto_safe(page, base, settle=3)
    m = re.search(r"Toplam\s+(\d+)\s+ilan", page.inner_text("body") or "", re.I)
    toplam = int(m.group(1)) if m else None
    tum = {}
    for e in links_matching(page, base, pat, exc):
        tum[e["link"]] = e

    param_used = None
    for param in ("currentPage", "page", "pageNumber", "pageIndex", "p"):
        goto_safe(page, f"{base}&{param}=2", settle=2)
        yeni = [e for e in links_matching(page, base, pat, exc) if e["link"] not in tum]
        if yeni:
            param_used = param
            for e in yeni:
                tum[e["link"]] = e
            break
    if param_used:
        for n in range(3, 60):
            if toplam and len(tum) >= toplam:
                break
            goto_safe(page, f"{base}&{param_used}={n}", settle=1.5)
            yeni = [e for e in links_matching(page, base, pat, exc) if e["link"] not in tum]
            if not yeni:
                break
            for e in yeni:
                tum[e["link"]] = e
    else:
        # Yedek: "sonraki" düğmesiyle sayfalama
        goto_safe(page, base, settle=2)
        for _ in range(60):
            btn = page.locator("a,button", has_text=re.compile(r"^\s*(›|»|>|Sonraki|İleri|Next)\s*$", re.I))
            if btn.count() == 0:
                break
            try:
                btn.first.click(timeout=4000)
                page.wait_for_timeout(2000)
            except Exception:
                break
            yeni = [e for e in links_matching(page, base, pat, exc) if e["link"] not in tum]
            if not yeni:
                break
            for e in yeni:
                tum[e["link"]] = e

    entries = list(tum.values())
    note = f"sitede toplam: {toplam}, okunan: {len(entries)}, sayfalama: {param_used or 'düğme/yok'}"
    if toplam and len(entries) < toplam:
        note += " (EKSİK!)"
    return entries[:MAX_LINKS_PER_SOURCE * 2], len(entries), note


RG_PERSONEL = re.compile(r"alım|alin|alınacak|personel|kadro|sözleşmeli|işçi|memur|KPSS|öğretim", re.I)


def _rg_pdf_metni(url):
    r = request_with_retry("GET", url, retries=2, timeout=60)
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return None
    reader = PdfReader(io.BytesIO(r.content))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages)


def fetch_resmi_gazete(page):
    d = TR_NOW.date()
    ymd = d.strftime("%Y%m%d")
    klasor = f"https://www.resmigazete.gov.tr/ilanlar/eskiilanlar/{d.year}/{d.month:02d}/"
    index = f"{klasor}{ymd}-4.htm"
    response = page.goto(index, timeout=60000, wait_until="domcontentloaded")
    if response and response.status != 200:
        return [], 0, f"bugünkü ilan sayfası yok (HTTP {response.status}) (normal olabilir)"
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # 1) Dizin sayfasındaki PDF linkleri
    pdfs = {e["link"] for e in links_matching(page, index, rf"{ymd}-4-\d+\.pdf$")}
    kaynak = "dizin linkleri"

    # 2) Link bulunamazsa numarayı sırayla dene (art arda 3 boş = son)
    if not pdfs:
        kaynak = "numara denemesi"
        bos = 0
        for n in range(1, 500):
            u = f"{klasor}{ymd}-4-{n}.pdf"
            try:
                if request_with_retry("GET", u, retries=1, timeout=30).status_code == 200:
                    pdfs.add(u); bos = 0
                else:
                    bos += 1
            except Exception:
                bos += 1
            if bos >= 3:
                break

    entries, okunan = [], 0
    for u in sorted(pdfs, key=lambda x: int(re.search(r"-4-(\d+)\.pdf", x).group(1))):
        try:
            txt = _rg_pdf_metni(u)
        except Exception as e:
            print("Resmî Gazete PDF okunamadı:", u, str(e)[:100]); continue
        if not txt or len(txt.strip()) < 100:      # taranmış/boş PDF: okunamadı say
            continue
        okunan += 1
        if not RG_PERSONEL.search(txt[:3000]):
            continue
        satirlar = [s.strip() for s in txt.split("\n") if s.strip()]
        baslik = " ".join(satirlar[:2])[:150]
        entries.append({"title": baslik, "link": u, "content": txt[:14000], "kurum": baslik,
                        "detail_level": "Resmî Gazete ilan PDF'si (tam metin)"})
    note = (f"sitede toplam: {len(pdfs)}, okunan: {okunan}, personel ilanı: {len(entries)}, "
            f"PDF bulma: {kaynak}")
    return entries, len(pdfs), note


def fetch_kariyer_kapisi(page):
    url = "https://kariyerkapisi.gov.tr/isealim"
    goto_safe(page, url, settle=3)
    try:
        page.get_by_role("button", name=re.compile(r"^(ara|listele|getir)$", re.I)).first.click(timeout=3000)
        page.wait_for_timeout(3000)
    except Exception:
        pass
    detay = links_matching(page, url, r"IlanDetay")
    if detay:
        body = page.inner_text("body") or ""
        sayilar = re.findall(r"\((\d+)\s*İLAN\)", body)
        harici = sum(1 for a in page.query_selector_all("a") if (a.inner_text() or "").strip() == "İlana Git")
        if sayilar:
            toplam = sum(int(x) for x in sayilar) - harici
            note = (f"IlanDetay linkleri doğrudan bulundu; sitede toplam: {toplam}, okunan: {len(detay)}, "
                    f"harici siteye giden (İlana Git): {harici}")
        else:
            note = "IlanDetay linkleri doğrudan bulundu"
        return detay, len(detay), note
    kurum_satirlari = []
    for tr in page.query_selector_all("table tr"):
        a = tr.query_selector("a")
        href = (a.get_attribute("href") or "") if a else ""
        if href and not href.startswith(("#", "javascript:")):
            kurum_satirlari.append(urljoin(url, href))
    if kurum_satirlari:
        toplanan = {}
        for u in kurum_satirlari[:150]:
            try:
                goto_safe(page, u, settle=1.5)
                for e in links_matching(page, u, r"IlanDetay"):
                    toplanan[e["link"]] = e
            except Exception as ex:
                print("Kariyer Kapısı kurum sayfası hatası:", str(ex)[:100])
        return list(toplanan.values()), len(toplanan), f"{len(kurum_satirlari)} kurum satırı gezildi"
    goto_safe(page, url, settle=1)
    body = (page.inner_text("body") or "").lower()
    if "aktif bir ilan bulunmamaktadır" in body:
        return [], 0, "site 'aktif ilan yok' diyor (normal) — tanı sayfasındaki ekran görüntüsünü kontrol et"
    if "aktif ilanlar" in body and "kurum/birim" in body:
        return [], 0, "aktif ilan tablosu boş (normal olabilir); sayfada yalnızca tablo başlıkları var"
    return [], 0, "ilan/tablo bulunamadı — tanı sayfasına bak"


def fetch_sbb_kamu_ilan(page):
    url = "https://kamuilan.sbb.gov.tr/"
    goto_safe(page, url, settle=4)
    body = (page.inner_text("body") or "").lower()
    if "geographic restriction" in body or "access denied" in body or "access restricted" in body:
        raise Exception("SBB sitesi bu çalıştırma ortamını coğrafi erişim kuralıyla engelledi")
    host = urlparse(url).hostname
    entries = [e for e in links_matching(page, url, min_text=8)
               if urlparse(e["link"]).hostname == host]
    # Manşet/kaydırıcıdaki tekrarları ele: aynı metinli bağlantı aynı ilandır
    tekil, gorulen = [], set()
    for e in entries:
        t = re.sub(r"\s+", " ", e["title"]).strip().lower()
        if t in gorulen:
            continue
        gorulen.add(t)
        tekil.append(e)
    m = re.search(r"TÜM İLANLAR\s*(\d+)\s*ilan", page.inner_text("body") or "", re.I)
    if m:
        note = (f"sitede toplam: {m.group(1)}, okunan: {len(tekil)} "
                f"(ham bağlantı: {len(entries)}, tekrarlar elendi)")
    else:
        note = "aynı alan adındaki sayfa linkleri (yapı doğrulanmadı)"
    return tekil[:MAX_LINKS_PER_SOURCE], len(tekil), note


def fetch_csb_yerel(page):
    url = "https://yerelyonetimler.csb.gov.tr/duyurular"
    son = None
    for _ in range(2):
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            son = None
            break
        except Exception as e:
            son = e
    if son:
        raise Exception("erişilemedi (zaman aşımı). GitHub sunucusunun yurt dışı IP'si engelleniyor "
                        "olabilir. Çözüm: Türkiye'deki bilgisayarında self-hosted runner. Ayrıntı: "
                        + str(son)[:120])
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    time.sleep(2)
    entries = links_matching(page, url, min_text=12)
    ilanli = [e for e in entries if re.search(r"ilan|alım|alim|personel|memur", e["title"], re.I)]
    entries = ilanli or entries
    return entries[:MAX_LINKS_PER_SOURCE], len(entries), "duyuru linkleri (yapı doğrulanmadı — tanı sayfasına bak)"


def fetch_iskur_memur(page):
    url = "https://www.iskur.gov.tr/ilanlar/kamu-memur-alim-ilanlari/"
    goto_safe(page, url, settle=3)
    entries = links_matching(page, url, min_text=10)
    ilanli = [e for e in entries if re.search(r"alım|alim|ilan|personel|memur|sözleşmeli", e["title"], re.I)]
    entries = ilanli or entries
    sayilar = [int(x) for x in re.findall(r"^\((\d+)\)\s*$", page.inner_text("body") or "", re.M)]
    note = "İŞKUR kamu memur ilan linkleri (yapı doğrulanmadı)"
    if sayilar and sum(sayilar) > len(entries):
        note = (f"EKSİK: haritada {sum(sayilar)} ilan görünüyor ({len(sayilar)} il), "
                f"{len(entries)} bağlantı okundu — iller tek tek gezilmeli. " + note)
    return entries[:MAX_LINKS_PER_SOURCE], len(entries), note


def fetch_iskur_esube(page):
    url = "https://esube.iskur.gov.tr/Istihdam/AcikIsIlanAra.aspx"
    goto_safe(page, url, settle=3)
    body = (page.inner_text("body") or "").lower()
    if "reddedildi" in body or "istek id" in body:
        raise Exception("İŞKUR güvenlik duvarı isteği reddetti (bot / yurt dışı IP). "
                        "Self-hosted runner çözebilir.")
    for txt in ("Kamu",):
        try:
            page.get_by_text(txt, exact=True).first.click(timeout=6000)
        except Exception as e:
            print(f"İŞKUR '{txt}' tıklanamadı: {str(e)[:80]}")
    try:
        page.get_by_text("Ara", exact=True).first.click(timeout=6000)
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception as e:
        print(f"İŞKUR 'Ara' tıklanamadı: {str(e)[:80]}")
    entries = []
    for tr in page.query_selector_all("table tr"):
        t = (tr.inner_text() or "").strip().replace("\n", " | ")
        if len(t) > 25:
            key = hashlib.md5(t.encode()).hexdigest()[:10]
            href = next((a.get_attribute("href") for a in tr.query_selector_all("a")
                         if a.get_attribute("href") and not a.get_attribute("href").startswith(
                             ("#", "javascript:"))), None)
            if href:
                entries.append({"title": t[:150], "link": urljoin(url, href),
                                "detail_level": "İlan detay bağlantısı"})
            else:
                entries.append({"title": t[:150], "content": t, "link": f"{url}#row-{key}",
                                "detail_level": "Satır düzeyi; detay bağlantısı bulunamadı"})
    detail_count = sum(1 for e in entries if e["detail_level"] == "İlan detay bağlantısı")
    note = (f"sonuç tablosu: {len(entries)} satır, {detail_count} ilan detay bağlantısı; "
            f"{len(entries) - detail_count} satır düzeyi kayıt")
    return entries[:MAX_LINKS_PER_SOURCE], len(entries), note


SOURCES = [
    ("İlan.gov.tr", fetch_ilan_gov_tr),
    ("Resmî Gazete", fetch_resmi_gazete),
    ("Kariyer Kapısı", fetch_kariyer_kapisi),
    ("SBB Kamu İlan", fetch_sbb_kamu_ilan),
    ("ÇŞB Yerel Yönetimler", fetch_csb_yerel),
    ("İŞKUR (kamu memur ilanları)", fetch_iskur_memur),
    ("İŞKUR (e-şube)", fetch_iskur_esube),
]


# ---------------- GROQ ----------------
ONCELIK_YUKSEK = re.compile(r"bilişim|bilgisayar|yazılım|bilgi işlem|programcı|siber|veri |sistem|mühendis", re.I)
ONCELIK_ORTA = re.compile(r"uzman yardımcısı|sözleşmeli|memur|meslek personeli|uzman|müfettiş|denetçi|kurum", re.I)


def oncelik(baslik, metin=""):
    """Küçük sayı = önce analiz edilir (kota sınırlıyken en umut verici ilanlar öne)."""
    if ONCELIK_YUKSEK.search(baslik):
        return 0
    if ONCELIK_ORTA.search(baslik):
        return 1
    return 2


def groq_call(model, prompt):
    """JSON dict | None (geçici/kalıcı hata) | 'MODEL_HATA'."""
    if not GROQ_API_KEY:
        return None
    if model in GROQ_DEAD_MODELS:
        return "KOTA"
    for deneme in range(1, 4):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.0, "response_format": {"type": "json_object"}},
                timeout=90)
        except requests.exceptions.RequestException as e:
            print("Groq bağlantı hatası:", e)
            time.sleep(8)
            continue
        if r.status_code == 429:
            try:
                bekle = float(r.headers.get("retry-after", "20"))
            except ValueError:
                bekle = 20
            if bekle > 90:
                GROQ_DEAD_MODELS.add(model)
                print(f"Groq günlük kota doldu ({model}, retry-after {bekle:.0f}s).")
                return "KOTA"
            print(f"Groq 429, {bekle:.0f}s bekleniyor")
            time.sleep(bekle + 2)
            continue
        if r.status_code in (400, 404):
            print("Groq model/istek hatası:", model, r.status_code, r.text[:150])
            return "MODEL_HATA"
        if r.status_code != 200:
            print("Groq hata:", r.status_code, r.text[:200])
            return None
        try:
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            print("Groq JSON okunamadı:", e)
            return None
    return None


def groq_zincir(prompt):
    """Modelleri sırayla dener; kotası biten/kullanılamayan modeli atlar."""
    global GROQ_DEAD
    for model in GROQ_MODELS:
        if model in GROQ_DEAD_MODELS:
            continue
        res = groq_call(model, prompt)
        if res == "MODEL_HATA":
            GROQ_DEAD_MODELS.add(model)
            continue
        if res == "KOTA":
            continue
        if isinstance(res, dict):
            res["_model"] = model
        return res
    if all(m in GROQ_DEAD_MODELS for m in GROQ_MODELS):
        GROQ_DEAD = True
        print("Tüm Groq modellerinin bugünkü kotası doldu; kalanlar yarına.")
    return None


KATEGORI_KURALI = (
    "KATEGORİLER (aday bilgisayar mühendisliği mezunu):\n"
    "- BOLUM: kabul edilen bölümler arasında Bilgisayar/Yazılım Mühendisliği veya bilişim bölümleri "
    "açıkça sayılıyor ('Bilişim Personeli' kadrosu dahil).\n"
    "- TUM_LISANS: bölüm kısıtı YOK, herhangi bir lisans (4 yıllık) mezunu başvurabiliyor.\n"
    "- ILGISIZ: yalnızca başka bölümler, akademik kadro (öğretim üyesi/görevlisi), lise/önlisans, "
    "işçi, iptal/düzeltme ilanı vb.; bilgisayar mühendisi başvuramıyor.\n"
    "Bir metinde birden çok kadro varsa bilgisayar mühendisinin başvurabildiği kadroyu esas al.\n"
)


SEMA = """{
  "kategori": "BOLUM" | "TUM_LISANS" | "ILGISIZ",
  "kanit": "kategoriyi destekleyen METİNDEN birebir kısa alıntı (max 200 karakter)",
  "kurum": string,
  "pozisyon": string,
  "kadroSayisi": number | null,
  "basvuruBaslangic": string | null,
  "basvuruBitis": string | null,
  "degerlendirmeSekli": "örn: %100 KPSS | KPSS + sözlü mülakat | KPSS + yazılı sınav | belirtilmemiş",
    "kpssDurumu": "Zorunlu" | "Tercih sebebi" | "Aranmıyor" | "Belirtilmemiş",
  "kpssTuru": "örn: KPSS-P3 en az 70 puan | belirtilmemiş",
  "ekSinav": boolean,
    "ekSinavDetay": "sınav türü ve puan şartı; yoksa Yok veya Belirtilmemiş",
  "ikametSarti": "Yok" | "Belirtilmemiş" | "Var: <il/ilçe>",
  "ekSartlar": "boy/kilo/yaş sınırı, yabancı dil puanı, ehliyet, deneyim, askerlik vb. yoksa 'Yok'",
  "kisaOzet": "en fazla 2 cümle"
}"""


KIRP_ANAHTAR = re.compile(
    r"KPSS|lisans|mezun|bölüm|mühendis|bilgisayar|bilişim|yazılım|ikamet|oturan|başvuru|"
    r"tarih|sınav|mülakat|puan|yaş|boy|kilo|deneyim|kadro|pozisyon|unvan", re.I)


def kirp_metin(metin, limit=3500, bas=1000, pencere=350):
    """Groq token tüketimini düşürmek için: ilan başı + anahtar kelime çevresindeki pasajlar."""
    metin = re.sub(r"[ \t]+", " ", metin)
    metin = re.sub(r"\n{2,}", "\n", metin)
    if len(metin) <= limit:
        return metin
    araliklar = [[0, bas]]
    for m in KIRP_ANAHTAR.finditer(metin):
        s, e = max(0, m.start() - pencere // 2), min(len(metin), m.end() + pencere)
        if s <= araliklar[-1][1]:
            araliklar[-1][1] = max(araliklar[-1][1], e)
        else:
            araliklar.append([s, e])
    parca, toplam = [], 0
    for s, e in araliklar:
        p = metin[s:e]
        if toplam + len(p) > limit:
            p = p[:max(0, limit - toplam)]
        if p:
            parca.append(p)
            toplam += len(p)
        if toplam >= limit:
            break
    return "\n[...]\n".join(parca)


def analiz(baslik, metin):
    prompt = ("Aşağıda bir Türkiye kamu personeli alım ilanının tam metni var. Bilgisayar mühendisliği "
              "mezunu bir aday açısından sınıflandır ve bilgileri çıkar. SADECE geçerli JSON döndür.\n"
              + KATEGORI_KURALI +
              "\nKESİN KURALLAR: Metinde yazmayan bilgiyi UYDURMA; yoksa null / 'Belirtilmemiş' yaz. "
              "'kanit' metinden birebir alıntı olmalı. KPSS için zorunlu, tercih sebebi, aranmıyor "
              "ve belirtilmemiş durumlarını ayır; türü ve taban puanı yaz. Ek sınavın türünü ve "
              "varsa puanını belirt. İkamet şartı için 'ikamet', 'oturmak', 'ilinde ikamet eden' "
              "gibi ifadelere bak; şart varsa il/ilçeyi yaz.\n\nJSON ŞEMASI:\n" + SEMA +
              "\n\nBAŞLIK: " + baslik + "\n\nMETİN (ilgili bölümler):\n" + kirp_metin(metin))
    res = groq_zincir(prompt)
    return res if isinstance(res, dict) else None


# ---------------- RAPOR ----------------
E = lambda x: htmllib.escape(str(x)) if x not in (None, "") else ""


def card_html(a):
    yeni = '<span class="new">🆕 Bugün eklendi</span>' if a.get("_yeni") else ""
    ikamet = a.get("ikametSarti") or "Belirtilmemiş"
    cls = "tag red" if str(ikamet).startswith("Var") else "tag"
    ek = a.get("ekSartlar") or "Yok"
    ek_html = "" if str(ek).strip().lower() == "yok" else f'<div class="warn">⚠ Ek şart: {E(ek)}</div>'
    ek_sinav = a.get("ekSinavDetay") or ("Var (ayrıntı belirtilmemiş)" if a.get("ekSinav") else "Yok")
    tarih = ""
    if a.get("basvuruBaslangic") or a.get("basvuruBitis"):
        tarih = f'<span class="tag">📅 {E(a.get("basvuruBaslangic") or "?")} → {E(a.get("basvuruBitis") or "?")}</span>'
    kadro = f' — {E(a["kadroSayisi"])} kadro' if a.get("kadroSayisi") else ""
    return f"""<div class="card">
  <div class="top"><a href="{E(a.get('link'))}" target="_blank">{E(a.get('pozisyon') or a.get('_baslik'))}</a>{yeni}</div>
  <div class="kurum">{E(a.get('kurum'))}{kadro}</div>
  <div class="meta">
    <span class="tag src">{E(a.get('_kaynak'))}</span>{tarih}
    <span class="tag">🧾 {E(a.get('degerlendirmeSekli'))}</span>
        <span class="tag">KPSS {E(a.get('kpssDurumu') or 'Belirtilmemiş')}: {E(a.get('kpssTuru') or 'belirtilmemiş')}</span>
        <span class="tag">Ek sınav: {E(ek_sinav)}</span>
    <span class="{cls}">📍 İkamet: {E(ikamet)}</span>
    {'<span class="tag">🤖 yedek model: ' + E(a.get('_model')) + ' (doğrula)</span>' if a.get('_model') and a.get('_model') != GROQ_MODELS[0] else ''}
  </div>
  {ek_html}
  <p>{E(a.get('kisaOzet'))}</p>
  <div class="kanit">Kanıt: “{E(a.get('kanit'))}”</div>
  <a class="btn" href="{E(a.get('link'))}" target="_blank">İlana git ↗</a>
</div>"""


STYLE = """
body{font-family:system-ui,Arial,sans-serif;background:#f5f6fa;padding:24px;color:#1f2430;max-width:1000px;margin:auto}
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
.box{font-size:12px;color:#444;margin-top:30px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12px}
td,th{border:1px solid #ddd;padding:4px 8px;text-align:left;vertical-align:top}
.bad{background:#fde8e8} img{max-width:100%;border:1px solid #ccc}
pre{white-space:pre-wrap;background:#fff;padding:8px;border:1px solid #ddd;font-size:11px}
"""


def build_report(bolum, tum, log_rows, yeni_sayisi, okunanlar, groq_notu):
    def section(items):
        if not items:
            return "<p>Şu an bu kategoride aktif ilan bulunamadı.</p>"
        items = sorted(items, key=lambda x: (not x.get("_yeni"), x.get("_kaynak", "")))
        return "\n".join(card_html(a) for a in items)

    log_html = ("<table><tr><th>Saat</th><th>Kaynak</th><th>Ham kayıt</th><th>Detay okunan (bugün)</th>"
                "<th>İlgili</th><th>Durum</th><th>Not</th></tr>")
    for r in log_rows:
        cls = ' class="bad"' if str(r[5]).startswith(("HATA", "ŞÜPHELİ", "KISMİ")) else ""
        log_html += f"<tr{cls}>" + "".join(f"<td>{E(c)}</td>" for c in r) + "</tr>"
    log_html += "</table>"

    okunan_html = ("<table><tr><th>Kaynak</th><th>Başlık</th><th>Metin uzunluğu</th>"
                   "<th>Okuma düzeyi</th><th>Sonuç</th></tr>")
    for o in okunanlar:
        kisa = isinstance(o["len"], int) and o["len"] < 300
        cls = ' class="bad"' if kisa or o["sonuc"].startswith("BEKLEMEDE") else ""
        yeni = '<span class="new">🆕 Bugün eklendi</span>' if o.get("yeni") else ""
        okunan_html += (f"<tr{cls}><td>{E(o['kaynak'])}</td>"
                        f"<td><a href=\"{E(o['link'])}\" target=\"_blank\">{E(o['baslik'])[:140]}</a>{yeni}</td>"
                        f"<td>{E(o['len'])}{' ⚠ kısa' if kisa else ''}</td>"
                        f"<td>{E(o['duzey'])}</td><td>{E(o['sonuc'])}</td></tr>")
    okunan_html += "</table>"

    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Günlük Kamu İlan Raporu</title><style>{STYLE}</style></head><body>
<h1>📋 Günlük Kamu İlan Raporu — {TR_NOW.strftime('%d.%m.%Y %H:%M')}</h1>
<p>Bugün yeni eklenen ilgili ilan: <b>{yeni_sayisi}</b> {E(groq_notu)}</p>
<h2>🎯 Bölümüme Özel (Bilgisayar/Yazılım Mühendisliği) — {len(bolum)} ilan</h2>
{section(bolum)}
<h2>🎓 Tüm Lisans Mezunlarına Açık — {len(tum)} ilan</h2>
{section(tum)}
<div class="box"><h2>🔍 Veri Çekim Doğrulama Kaydı</h2>{log_html}
<p><a href="debug.html">Tanı sayfası (ekran görüntüleri, arka plan istekleri)</a></p></div>
<div class="box"><details><summary><b>📑 Bugün listede görülen tüm ilanlar ({len(okunanlar)}) — okundu mu, sonuç ne?</b></summary>
{okunan_html}</details></div>
</body></html>"""


def build_debug():
    parts = []
    for d in DEBUG:
        img = f'<img src="{d["img"]}" alt="ekran görüntüsü">' if d.get("img") else "(ekran görüntüsü yok)"
        xhr = "\n".join(f'{x["method"]} {x["status"]} {x["url"]}'
                        + (f'\n   gövde: {x["post"]}' if x.get("post") else "")
                        + (f'\n   yanıt: {x["preview"]}' if x.get("preview") else "")
                        for x in XHR.get(d["name"], [])[:25]) or "(yok)"
        parts.append(f"""<h2>{E(d['name'])}</h2>
<p><b>Not:</b> {E(d['note'])}<br><b>Son URL:</b> {E(d.get('url'))}<br><b>Başlık:</b> {E(d.get('title'))}</p>
{img}
<h3>Sayfa metni (ilk 1500 karakter)</h3><pre>{E(d.get('body'))}</pre>
<h3>Bulunan linkler (ilk 40)</h3><pre>{E(d.get('links'))}</pre>
<h3>Arka plan (XHR/fetch) istekleri</h3><pre>{E(xhr)}</pre><hr>""")
    return (f'<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8"><title>Tanı</title>'
            f'<style>{STYLE}</style></head><body><h1>🛠 Tanı sayfası — {TR_NOW.strftime("%d.%m.%Y %H:%M")}</h1>'
            f'<p><a href="index.html">← Rapora dön</a></p>{"".join(parts)}</body></html>')


def snapshot(page, idx, name, note):
    """Tanı sayfası için ekran görüntüsü + metin + link örnekleri."""
    if not DIAGNOSE:
        return
    rec = {"name": name, "note": note}
    try:
        rec["url"] = page.url
        rec["title"] = page.title()
        rec["body"] = (page.inner_text("body") or "")[:1500]
        rec["links"] = "\n".join(
            f'{(a.inner_text() or "").strip()[:70]!r} -> {(a.get_attribute("href") or "")[:110]}'
            for a in page.query_selector_all("a")[:40])
        path = f"docs/debug/s{idx}.jpg"
        page.screenshot(path=path, type="jpeg", quality=45)
        rec["img"] = f"debug/s{idx}.jpg"
    except Exception as e:
        rec["body"] = f"(tanı alınamadı: {str(e)[:150]})"
    DEBUG.append(rec)


# ---------------- ANA AKIŞ ----------------
def sonuc_metni(k):
    if not k.get("detail_checked"):
        if k.get("sonuc") == "DETAY OKUNAMADI":
            return "DETAY OKUNAMADI (yeniden denenecek)"
        if k.get("sonuc") == "DETAY LİNKİ BULUNAMADI":
            return "DETAY LİNKİ BULUNAMADI (manuel kontrol gerekli)"
        return "BEKLEMEDE (detay kuyruğu)"
    an = k.get("analiz")
    if an:
        return an.get("kategori", "?")
    if k.get("bitti"):
        return k.get("sonuc", "bitti")
    if k.get("sonuc") == "DETAY OKUNAMADI":
        return "DETAY OKUNAMADI (yeniden denenecek)"
    if k.get("metin"):
        return "BEKLEMEDE (Groq analizi)"
    return "BEKLEMEDE (detay kuyruğu)"


def ilan_hash(baslik, metin):
    return hashlib.sha256(f"{baslik}\n{metin}".encode("utf-8")).hexdigest()


def kaynak_durumu(ham, note):
    if note.startswith("HATA:"):
        return "HATA"
    if "EKSİK" in note:
        return "ŞÜPHELİ (eksik okundu)"
    match = re.search(r"sitede toplam:\s*(\d+), okunan:\s*(\d+)", note)
    if match:
        return "OK" if int(match.group(2)) >= int(match.group(1)) else "ŞÜPHELİ (eksik okundu)"
    if "bugünkü ilan sayfası yok" in note:
        return "BOŞ (sayfa yok; kontrol edin)"
    if "aktif ilan tablosu boş" in note:
        return "KISMİ (boş tablo; doğrulanmalı)"
    if "aktif ilan yok" in note:
        return "BOŞ (site ilan olmadığını bildiriyor)"
    if ham == 0:
        return "ŞÜPHELİ (0 ham kayıt)"
    return "KISMİ (tamlık doğrulanmadı)"


def main():
    state = load_state()
    ads = state["ads"]
    log_rows, bugun = [], set()

    def on_resp(resp):
        if not CAPTURE["on"]:
            return
        try:
            req = resp.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            lst = XHR.setdefault(CAPTURE["name"], [])
            if len(lst) >= 40:
                return
            item = {"url": resp.url[:200], "status": resp.status, "method": req.method,
                    "post": (req.post_data or "")[:200]}
            if "json" in (resp.headers.get("content-type", "")) and len([x for x in lst if x.get("preview")]) < 12:
                try:
                    item["preview"] = resp.text()[:300]
                except Exception:
                    pass
            lst.append(item)
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(user_agent=BROWSER_UA, ignore_https_errors=True, locale="tr-TR")
        ctx.on("response", on_resp)
        list_page, detail_page = ctx.new_page(), ctx.new_page()
        source_batches = []
        analiz_kuyrugu, log_by_name = [], {}

        # Stage 1: collect every source before spending time or quota on analysis.
        for idx, (name, fn) in enumerate(SOURCES):
            saat = datetime.datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%H:%M:%S")
            CAPTURE.update(on=True, name=name)
            try:
                entries, ham, note = fn(list_page)
            except Exception as e:
                CAPTURE["on"] = False
                entries, ham = [], 0
                note = f"HATA: {str(e)[:200]}"
                print(f"HATA ({name}): {e}")
            CAPTURE["on"] = False
            snapshot(list_page, idx, name, note)
            source_batches.append({"idx": idx, "name": name, "entries": entries,
                                  "ham": ham, "note": note, "saat": saat})
            for e in entries:
                key = e["link"]
                bugun.add(key)
                k = ads.get(key) or {"first_seen": TODAY, "kaynak": name,
                                     "baslik": e.get("title", ""), "kurum": e.get("kurum", "")}
                k.update(last_seen=TODAY, kaynak=name, baslik=e.get("title", k.get("baslik", "")))
                k["detail_level"] = e.get("detail_level", "İlan detay sayfası")
                k["detail_checked"] = False
                if e.get("kurum"):
                    k["kurum"] = e["kurum"]
                if e.get("content") and not (k.get("bitti") or k.get("analiz")):
                    k["metin"] = e["content"][:8000]
                ads[key] = k
            save_state(state)

        # Stage 2: read details and classify the already-collected listings.
        for batch in source_batches:
            idx, name, entries = batch["idx"], batch["name"], batch["entries"]
            okunan = ilgili = 0
            for e in entries:
                key = e["link"]
                k = ads.get(key)
                baslik = e.get("title", "")
                if zaman_doldu():
                    continue
                if e.get("detail_level", "").startswith("Satır düzeyi"):
                    k = ads[key]
                    k.update(detail_checked=False, sonuc="DETAY LİNKİ BULUNAMADI")
                    continue

                metin = e.get("content") or read_detail(detail_page, key)
                if not metin:
                    k = k or {"first_seen": TODAY, "kaynak": name, "baslik": baslik}
                    k.update(last_seen=TODAY, detail_checked=False, sonuc="DETAY OKUNAMADI")
                    ads[key] = k
                    continue
                okunan += 1
                k = k or {"first_seen": TODAY, "kaynak": name, "baslik": baslik,
                          "kurum": e.get("kurum", "")}
                metin_hash = ilan_hash(baslik, metin)
                degisti = k.get("metin_hash") != metin_hash
                k.update({"last_seen": TODAY, "metin_len": len(metin), "metin_hash": metin_hash,
                          "detail_checked": True})
                k.pop("detail_error", None)
                if not degisti and (k.get("bitti") or k.get("analiz")):
                    continue
                k.pop("analiz", None)
                k.pop("bitti", None)
                k.pop("sonuc", None)
                ads[key] = k

                if AKADEMIK_BASLIK.search(baslik):
                    k.update(bitti=True, sonuc="ILGISIZ (akademik kadro)")
                    k.pop("metin", None)
                    continue
                if not ADAY_ANAHTAR.search(metin + " " + e.get("title", "")):
                    k.update(bitti=True, sonuc="ADAY DEĞİL (anahtar kelime yok)")
                    k.pop("metin", None)
                    continue

                k["metin"] = metin[:8000]          # analiz edilene kadar saklanır
                analiz_kuyrugu.append((oncelik(baslik, metin), key, baslik, metin, name))

            durum = kaynak_durumu(batch["ham"], batch["note"])
            log_rows.append([batch["saat"], name, batch["ham"], okunan, ilgili, durum, batch["note"]])
            log_by_name[name] = log_rows[-1]
            save_state(state)

        # Stage 3: kota sınırlıyken en umut verici ilanlardan başlayarak analiz et
        if GROQ_API_KEY:
            analiz_kuyrugu.sort(key=lambda x: x[0])
            print(f"Analiz kuyruğu: {len(analiz_kuyrugu)} ilan")
            for sayac, (_, key, baslik, metin, name) in enumerate(analiz_kuyrugu, 1):
                if GROQ_DEAD or zaman_doldu():
                    break
                sonuc = analiz(baslik, metin)
                if not GROQ_DEAD:
                    time.sleep(GROQ_SLEEP_SEC)
                if not sonuc:
                    continue
                k = ads[key]
                k["analiz"] = sonuc
                k["bitti"] = True
                k.pop("metin", None)
                if sonuc.get("kategori") in ("BOLUM", "TUM_LISANS") and name in log_by_name:
                    log_by_name[name][4] += 1
                if sayac % 10 == 0:
                    save_state(state)
            save_state(state)

        browser.close()

    # Rapor verileri
    bolum, tum, yeni_sayisi, okunanlar = [], [], 0, []
    for key in bugun:
        k = ads.get(key)
        if not k:
            continue
        okunanlar.append({"kaynak": k.get("kaynak"), "baslik": k.get("baslik"), "link": key,
                          "len": k.get("metin_len", "?"), "duzey": k.get("detail_level", "?"),
                          "sonuc": sonuc_metni(k),
                          "yeni": k.get("first_seen") == TODAY and TODAY != state.get("first_run")})
        an = k.get("analiz")
        if k.get("detail_checked") and an and an.get("kategori") in ("BOLUM", "TUM_LISANS"):
            a = dict(an)
            a.update({"link": key, "_kaynak": k.get("kaynak"), "_baslik": k.get("baslik"),
                      "_yeni": k.get("first_seen") == TODAY and TODAY != state.get("first_run")})
            yeni_sayisi += 1 if a["_yeni"] else 0
            (bolum if a["kategori"] == "BOLUM" else tum).append(a)
    okunanlar.sort(key=lambda o: (o["kaynak"] or "", o["sonuc"]))
    bekleyen = sum(1 for o in okunanlar if o["sonuc"].startswith("BEKLEMEDE"))
    if bekleyen and not GROQ_API_KEY:
        groq_notu = f"— ⏳ {bekleyen} ilan analiz kuyruğunda; GROQ_API_KEY tanımlı değil."
    elif bekleyen:
        groq_notu = f"— ⏳ {bekleyen} ilan Groq kotası/hatası nedeniyle analiz kuyruğunda."
    else:
        groq_notu = ""

    yeni_dosya = not os.path.isfile(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if yeni_dosya:
            w.writerow(["tarih", "saat", "kaynak", "ham_kayit", "detay_okunan", "ilgili", "durum", "not"])
        for r in log_rows:
            w.writerow([TODAY] + r)

    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(build_report(bolum, tum, log_rows, yeni_sayisi, okunanlar, groq_notu))
    if DIAGNOSE:
        with open("docs/debug.html", "w", encoding="utf-8") as f:
            f.write(build_debug())
    save_state(state)
    print(f"Bitti. Bölüm: {len(bolum)}, Tüm lisans: {len(tum)}, Yeni: {yeni_sayisi}, Beklemede: {bekleyen}")


if __name__ == "__main__":
    main()

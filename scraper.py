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
GROQ_DEAD_NEDEN = {}                        # model -> neden (rapor başlığında gösterilir)
GROQ_ZAYIF = {"openai/gpt-oss-20b", "llama-3.1-8b-instant"}   # kartta "doğrula" uyarısı yalnız bunlar için
SBB_ADI = "SBB Kamu İlan"
# Groq'ta kaldırılmış model olursa listeden elenir; bu adaylar (varsa) 120b'den sonra zincire eklenir
GROQ_ADAY_MODELLER = ["meta-llama/llama-4-maverick-17b-128e-instruct", "meta-llama/llama-4-scout-17b-16e-instruct",
                      "moonshotai/kimi-k2-instruct"]
GROQ_MODEL_KONTROL = {"yapildi": False}

# Kişisel profil (uygunluk filtresi için). Değişirse buradan güncelle.
PROFIL = {"kpss": 71, "kpss_turu": "P3", "yas": 26, "boy": 173, "kilo": 105, "tecrube_gun": 150}   # tecrübe: yalnız ~150 gün staj
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
    r"öğretim\s+(?:üyesi|görevlisi|elemanı)|öğr\.?\s*(?:üyesi|görevlisi|gör\b)|"
    r"araştırma\s+görevlisi|okutman|profesör|doçent", re.I)
PROMPT_VERSION = 2

# ---- İkinci profil (önlisans mezunu, engelli kadro/EKPSS) — ayrı rapor sayfası: docs/onlisans.html ----
P2_VERSION = 1
P2_MAX_ANALIZ = 40          # bir çalıştırmada ikinci profil için en çok bu kadar Groq analizi
ADAY2_ANAHTAR = re.compile(
    r"engelli|ekpss|ön\s?lisans|meslek\s+yüksekokul|anestezi|sağlık\s+teknik|tıbbi\s+hizmetler|"
    r"tıbbi\s+(?:laboratuvar|görüntüleme)|ilk\s+ve\s+acil|ameliyathane", re.I)
ENGEL_BASLIK = re.compile(r"engelli|ekpss|ön\s?lisans|anestezi|sağlık\s+teknik|tekniker|teknisyen|"
                          r"büro|veri hazırlama|memur", re.I)
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
        if len(txt) < 400:                      # içerik iframe içinde olabilir
            for fr in page.frames[1:]:
                try:
                    txt += "\n" + (fr.inner_text("body") or "")
                except Exception:
                    pass
        return txt[:40000]
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
    CSB_ILAN = re.compile(r"(?:ALIM|İLANI|İLAN)\b", re.I)
    CSB_KURUM = re.compile(r"BELEDİY|ÜNİVERSİT|İL ÖZEL|BAŞKANLIĞ|MÜDÜRLÜĞ|KURUM|BİRLİĞ|İDARESİ|ODASI", re.I)

    def duyurular():
        out = []
        for e in links_matching(page, url, min_text=25):
            t = e["title"].replace("|", " ")
            # menü/rehber bağlantıları değil, gerçek duyuru başlıkları: kurum adı + "ALIM/İLAN"
            if CSB_ILAN.search(t) and CSB_KURUM.search(t) and urlparse(e["link"]).hostname == urlparse(url).hostname:
                out.append(e)
        return out

    entries = duyurular()
    # sayfalama varsa (2, 3, 4 … numaralı bağlantılar) en çok 4 ek sayfa gez
    sayfa_linkleri = []
    for a in page.query_selector_all("a"):
        tx = (a.inner_text() or "").strip()
        href = (a.get_attribute("href") or "").strip()
        if tx.isdigit() and 2 <= int(tx) <= 9 and href and not href.startswith(("#", "javascript:")):
            full = urljoin(url, href)
            if full not in sayfa_linkleri and full != url:
                sayfa_linkleri.append(full)
    gezilen = 1
    for sl in sayfa_linkleri[:4]:
        try:
            page.goto(sl, timeout=60000, wait_until="domcontentloaded")
            time.sleep(2)
            mevcut = {e["link"] for e in entries}
            entries += [e for e in duyurular() if e["link"] not in mevcut]
            gezilen += 1
        except Exception:
            break
    if not entries:
        return [], 0, "duyuru listesinde ilan başlığı bulunamadı — tanı sayfasına bak"
    return (entries[:MAX_LINKS_PER_SOURCE], len(entries),
            f"sitede toplam: {len(entries)}, okunan: {len(entries)} (yalnız ilan/alım başlıklı duyurular; {gezilen} sayfa gezildi)")


def fetch_iskur_esube(page):
    url = "https://esube.iskur.gov.tr/Istihdam/AcikIsIlanAra.aspx"
    goto_safe(page, url, settle=3)
    body = (page.inner_text("body") or "").lower()
    if "reddedildi" in body or "istek id" in body:
        raise Exception("İŞKUR güvenlik duvarı isteği reddetti (bot / yurt dışı IP). "
                        "Self-hosted runner çözebilir.")

    kamu = page.locator("#ctl04_kamuRadio")
    if not kamu.is_checked():
        kamu.check(timeout=6000)
    try:
        page.locator("a[href*='CommandItem_Search']").click(timeout=6000)
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception as e:
        raise Exception(f"İŞKUR e-Şube kamu araması başarısız: {str(e)[:120]}") from e

    entries, gorulen_no = [], set()
    sayfa, tamam = 1, False
    while sayfa <= 15:
        grid = page.locator("#ctl04_ctlGridAcikIslerListeDetail")
        yeni_say = 0
        for tr in grid.locator("tr").all():
            detail_link = tr.locator("a[href^='javascript:PopupJobDetails']")
            if detail_link.count() == 0:
                continue
            t = (tr.inner_text() or "").strip().replace("\n", " | ")
            href = detail_link.first.get_attribute("href") or ""
            match = re.search(r"PopupJobDetails\('([^']+)'\s*,\s*'Kamu'", href)
            if not match or match.group(1) in gorulen_no:
                continue
            ilan_no = match.group(1)
            gorulen_no.add(ilan_no)
            yeni_say += 1
            entries.append({"title": t[:220],
                            "link": urljoin(url, f"AcikIsIlanDetay.aspx?uiID={ilan_no}&isyeriTuru=Kamu"),
                            "detail_level": "İlan detay sayfası"})
        # ASP.NET grid sayfalaması: __doPostBack(...,'Page$N')
        sonraki = page.locator(f"a[href*=\"Page${sayfa + 1}'\"]")
        if yeni_say == 0 or sonraki.count() == 0:
            tamam = True
            break
        try:
            sonraki.first.click(timeout=6000)
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            break
        sayfa += 1

    if entries and tamam:
        note = (f"sitede toplam: {len(entries)}, okunan: {len(entries)} "
                f"(Kamu seçilerek arandı; {sayfa} sayfa gezildi, sonraki sayfa yok)")
        return entries[:MAX_LINKS_PER_SOURCE], len(entries), note
    note = f"Kamu seçilerek arandı; {len(entries)} ilan bulundu, detay sayfaları açılacak"
    return entries[:MAX_LINKS_PER_SOURCE], len(entries), note


SOURCES = [
    ("İlan.gov.tr", fetch_ilan_gov_tr),
    ("Resmî Gazete", fetch_resmi_gazete),
    ("Kariyer Kapısı", fetch_kariyer_kapisi),
    ("SBB Kamu İlan", fetch_sbb_kamu_ilan),
    ("ÇŞB Yerel Yönetimler", fetch_csb_yerel),
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


# ---------------- SONUÇ DOĞRULAMA / TARİH / TEKRAR BİRLEŞTİRME ----------------
OGRENCI_SARTI = re.compile(
    r"[34]\.?\s*(?:veya|ya da|ve)\s*[34]\.?\s*sınıf|öğrenim görecek|öğrenim görmekte", re.I)
IC_TERFI = re.compile(r"yeterlik\s+sınav|görevde\s+yükselme|unvan\s+değişikliği", re.I)
LISE_ONLISANS_POZ = re.compile(
    r"teknisyen|tekniker|hizmetli|şoför|aşçı|bekçi|temizlik|koruma ve güvenlik|"
    r"güvenlik görevlisi|işçi|çaycı|odacı", re.I)
AYLAR = {"ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6, "temmuz": 7,
         "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12}


def tarih_ayrisir(s):
    """'19.10.2026 17:00' | '12/10/2026' | '12 Ekim 2026' | '2026-10-12' -> date | None"""
    if not s:
        return None
    s = str(s).replace("İ", "i").replace("I", "ı").lower()
    try:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.search(r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4})", s)
        if m:
            return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = re.search(r"(\d{1,2})\s+([a-zçğıöşü]+)\s+(\d{4})", s)
        if m and m.group(2) in AYLAR:
            return datetime.date(int(m.group(3)), AYLAR[m.group(2)], int(m.group(1)))
    except ValueError:
        return None
    return None


def dogrula(an, baslik=""):
    """LLM 'BOLUM/TUM_LISANS' demiş ama kurallara aykırıysa ILGISIZ'e çevirir. (analiz, neden)"""
    if not an or an.get("kategori") not in ("BOLUM", "TUM_LISANS"):
        return an, None
    poz = an.get("pozisyon") or ""
    metin = " ".join(str(an.get(x) or "") for x in ("kanit", "kisaOzet", "ekSartlar", "ekSinavDetay"))
    neden = None
    if AKADEMIK_BASLIK.search(poz) or AKADEMIK_BASLIK.search(baslik or ""):
        neden = "akademik kadro"
    elif OGRENCI_SARTI.search(metin):
        neden = "öğrenciye yönelik ilan"
    elif IC_TERFI.search(metin):
        neden = "iç terfi / yeterlik sınavı"
    elif LISE_ONLISANS_POZ.search(poz):
        neden = "lise/önlisans kadrosu"
    if neden:
        an = dict(an)
        an["kategori"] = "ILGISIZ"
        an["_neden"] = neden
    return an, neden


KURUM_GENEL = {"ve", "ile", "başkanlığı", "başkanlığına", "müdürlüğü", "müdürlüğüne", "genel", "bakanlığı",
               "rektörlüğü", "rektörlüğünden", "belediye", "belediyesi", "daire", "il", "kurumu",
               "türkiye", "cumhuriyeti", "kurum", "üniversitesi", "üniversite", "valiliği", "valilik",
               "belediyesi", "büyükşehir", "kalkınma", "ajansı"}


def _kelimeler(s, genel=()):
    s = re.sub(r"\(.*?\)", " ", (s or "").replace("İ", "i").replace("I", "ı").lower())
    return {w for w in re.findall(r"[a-zçğıöşü0-9]+", s) if len(w) > 1 and w not in genel}


def _kapsama(x, y):
    if not x or not y:
        return 0.0
    return len(x & y) / min(len(x), len(y))


BILISIM_POZ = re.compile(r"bilişim|bilgi\s*işlem|yazılım|programc|sistem|veri", re.I)


def ayni_ilan(a, b):
    ka, kb = a.get("kadroSayisi"), b.get("kadroSayisi")
    celisir = bool(ka and kb and str(ka) != str(kb))
    ku = _kapsama(_kelimeler(a.get("kurum"), KURUM_GENEL), _kelimeler(b.get("kurum"), KURUM_GENEL))
    poz_a = a.get("pozisyon") or a.get("_baslik") or ""
    poz_b = b.get("pozisyon") or b.get("_baslik") or ""
    # 1) kurum ve pozisyon adı benzer
    if not celisir and ku >= 0.6 and _kapsama(_kelimeler(poz_a), _kelimeler(poz_b)) >= 0.6:
        return True
    # 2) aynı kurum, aynı (birden büyük) kadro sayısı, başvuru bitişi çelişmiyor (başlıklar farklı yazılmış olabilir)
    ba, bb = a.get("_bitis"), b.get("_bitis")
    if (ku >= 0.8 and ka and kb and not celisir and str(ka) not in ("0", "1")
            and not (ba and bb and ba != bb)):
        return True
    # 3) aynı kurum, ikisi de bilişim kadrosu ve aynı gün ilk kez görüldü (aynı ilanın farklı yayımı)
    if (ku >= 0.8 and BILISIM_POZ.search(poz_a) and BILISIM_POZ.search(poz_b)
            and a.get("_ilk") and a.get("_ilk") == b.get("_ilk") and a.get("_kaynak") != b.get("_kaynak")):
        return True
    return False


def birlestir(items):
    """Aynı ilanın farklı kaynaklardaki kopyalarını tek karta indirir (diğer linkler korunur)."""
    gruplar = []
    for a in items:
        for g in gruplar:
            if ayni_ilan(g[0], a):
                g.append(a)
                break
        else:
            gruplar.append([a])
    out = []
    for g in gruplar:
        g.sort(key=lambda x: -sum(1 for v in x.values() if v not in (None, "", "Belirtilmemiş", "belirtilmemiş")))
        ana = dict(g[0])
        ana["_digerleri"] = [(x.get("_kaynak"), x.get("link")) for x in g[1:]]
        ana["_ilk"] = min((x.get("_ilk") or "9999") for x in g)       # kart, en eski görülme tarihine göre "yeni" sayılır
        out.append(ana)
    return out


TECRUBE_RX = re.compile(r"(\d+)(?:\s*[-–]\s*\d+)?\s*yıl\w*\s+(?:\w+\s+){0,3}?(?:tecrübe|deneyim)", re.I)
FIZIKSEL = re.compile(r"zabıta|itfaiye|bekçi|güvenlik görevlisi|\bboy\b|\bkilo", re.I)
ILGI_BASLIK = re.compile(r"uzman|bilişim|bilgi\s*işlem|yazılım|mühendis|memur|meslek personeli|sözleşmeli|"
                         r"müfettiş|denetçi|kontrolör|analist|programcı|personel", re.I)
DISLA_BASLIK = re.compile(r"iptal|düzeltme|süre\s*uzat|subay|pilot|tabip|hakim|savcı|hemşire|işçi|sağlık|"
                          r"öğretmen|bilirkişi|tercüman|zabıta|itfaiye|bekçi", re.I)


def tecrube_yili(a):
    """Kartta istenen asgari mesleki tecrübe yılı (yoksa 0). 'Kıdemli' unvanı en az 3 yıl sayılır."""
    metin = " ".join(str(a.get(x) or "") for x in ("ekSartlar", "kisaOzet", "pozisyon", "_baslik"))
    yillar = [int(m.group(1)) for m in TECRUBE_RX.finditer(metin)]
    if yillar:
        return min(yillar)
    return 3 if re.search(r"kıdemli", metin, re.I) else 0


def uygunluk(a):
    """Kişisel profile göre kesin elenen ilan için neden (yoksa None). Emin olunmayan durumda None döner."""
    kt = str(a.get("kpssTuru") or "")
    ek = " ".join(str(a.get(x) or "") for x in ("ekSartlar", "kisaOzet", "kpssTuru"))
    if re.search(r"\(\s*[BC]\s*\)\s*grubu|\b[BC]\s*grubu", kt, re.I):
        return "KPSS (B/C) grubu — önlisans/lise düzeyi"
    m = re.search(r"(?:en az|asgari|minimum)\s*(\d{2,3})(?:[.,]\d+)?|(\d{2,3})(?:[.,]\d+)?\s*(?:puan|ve üzeri|ve üstü|ve yukarı)", kt, re.I)
    if m:
        puan = int(m.group(1) or m.group(2))
        if 40 <= puan <= 100 and puan > PROFIL["kpss"]:
            return f"KPSS puanın ({PROFIL['kpss']}) yetmez — en az {puan} isteniyor"
    turler = set(re.findall(r"P\s*-?\s*(\d{1,2})\b", kt))
    if turler and PROFIL["kpss_turu"].lstrip("P") not in turler:
        return (f"puan türün (KPSS-{PROFIL['kpss_turu']}) istenen türler arasında yok "
                f"({', '.join('P' + t for t in sorted(turler, key=int))})")
    bk = re.search(r"boy[\s\-‑–]*kilo[^.;]{0,40}?[±+]\s*/?-?\s*(\d{1,2})\s*kg", ek, re.I)
    if bk and abs(PROFIL["kilo"] - (PROFIL["boy"] - 100)) > int(bk.group(1)):
        return f"boy-kilo şartı (±{bk.group(1)} kg) — {PROFIL['boy']} cm için ideal ~{PROFIL['boy']-100} kg"
    ty = tecrube_yili(a)
    if ty >= 1:
        return f"{ty}+ yıl mesleki tecrübe şartı (sende ~{PROFIL['tecrube_gun']} gün staj var)"
    y = re.search(r"(\d{2})\s*yaş\w*\s+(?:\w+\s+){0,2}?(?:doldurmamış|aşmamış)", ek, re.I)
    if y and PROFIL["yas"] >= int(y.group(1)):
        return f"yaş sınırı ({y.group(1)}) aşılmış"
    return None


def _sbb_parcala(baslik):
    parcalar = [p.strip() for p in re.split(r"\||\n", baslik or "") if p.strip()]
    return (parcalar[0] if parcalar else ""), (parcalar[1] if len(parcalar) > 1 else "")


def sbb_yalniz(ads, bugun):
    """SBB'de listelenen ama başka hiçbir kaynakta kurum karşılığı bulunmayan, başlığı ilgili ilanlar."""
    diger = []
    for key in bugun:
        k = ads.get(key)
        if k and k.get("kaynak") != SBB_ADI:
            diger.append(_kelimeler((k.get("baslik") or "") + " " + str((k.get("analiz") or {}).get("kurum") or ""),
                                    KURUM_GENEL))
    out, gorulen = [], set()
    for key in bugun:
        k = ads.get(key)
        if not k or k.get("kaynak") != SBB_ADI:
            continue
        kurum, ilan = _sbb_parcala(k.get("baslik"))
        ks = _kelimeler(kurum, KURUM_GENEL)
        if not ks or not ILGI_BASLIK.search(ilan or kurum) or DISLA_BASLIK.search(kurum + " " + ilan) \
                or AKADEMIK_BASLIK.search(ilan):
            continue
        if any(len(ks & d) / len(ks) >= 0.5 for d in diger):
            continue
        anahtar = (frozenset(ks), " ".join(re.sub(r"\(.*", "", ilan).lower().split()))
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        out.append({"kaynak": SBB_ADI, "baslik": f"{kurum} — {ilan}".strip(" —"), "link": key,
                    "neden": "yalnız SBB'de görüldü, içeriği okunamıyor"})
    return out


def kisa_metinliler(ads, bugun):
    out = []
    for key in bugun:
        k = ads.get(key)
        if k and str(k.get("sonuc") or "").startswith("METİN KISA") and k.get("kaynak") != SBB_ADI:
            b = (k.get("baslik") or "").replace("\n", " | ")
            if ILGI_BASLIK.search(b) and not DISLA_BASLIK.search(b) and not AKADEMIK_BASLIK.search(b):
                out.append({"kaynak": k.get("kaynak"), "baslik": b[:160], "link": key,
                            "neden": "sayfa metni alınamadı (çok kısa)"})
    return out


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
                GROQ_DEAD_NEDEN[model] = "günlük kota doldu"
                print(f"Groq günlük kota doldu ({model}, retry-after {bekle:.0f}s).")
                return "KOTA"
            print(f"Groq 429, {bekle:.0f}s bekleniyor")
            time.sleep(bekle + 2)
            continue
        if r.status_code in (400, 404):
            print("Groq model/istek hatası:", model, r.status_code, r.text[:150])
            GROQ_DEAD_NEDEN[model] = f"model/istek hatası {r.status_code}: {r.text[:90]}"
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


def groq_modelleri_dogrula():
    """Groq'un güncel model listesini çekip zincirden kaldırılmış modelleri çıkarır, yeni adayları ekler."""
    global GROQ_MODELS
    if GROQ_MODEL_KONTROL["yapildi"] or not GROQ_API_KEY:
        return
    GROQ_MODEL_KONTROL["yapildi"] = True
    try:
        r = requests.get("https://api.groq.com/openai/v1/models",
                         headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, timeout=30)
        if r.status_code != 200:
            print("Groq model listesi alınamadı:", r.status_code)
            return
        mevcut = {m.get("id") for m in r.json().get("data", []) if m.get("active", True)}
    except Exception as e:
        print("Groq model listesi hatası:", e)
        return
    if not mevcut:
        return
    yeni = []
    for m in GROQ_MODELS:
        if m in mevcut:
            yeni.append(m)
        else:
            GROQ_DEAD_NEDEN[m] = "Groq model listesinde yok (kaldırılmış) — zincirden çıkarıldı"
            print("Groq'ta olmayan model çıkarıldı:", m)
    guclu = [m for m in GROQ_ADAY_MODELLER if m in mevcut and m not in yeni]
    if guclu:
        i = 1 if yeni[:1] else 0
        yeni[i:i] = guclu
        print("Zincire eklenen yeni modeller:", guclu)
    if yeni:
        GROQ_MODELS = yeni
    print("Groq model zinciri:", GROQ_MODELS)


def groq_zincir(prompt):
    """Modelleri sırayla dener; kotası biten/kullanılamayan modeli atlar."""
    global GROQ_DEAD
    groq_modelleri_dogrula()
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
    "- ILGISIZ: yalnızca başka bölümler, akademik kadro (öğretim üyesi/görevlisi/araştırma görevlisi), "
    "lise/önlisans/meslek lisesi kadroları (teknisyen, tekniker, hizmetli, şoför...), işçi, iptal/düzeltme ilanı; "
    "öğrencilere yönelik (3./4. sınıf öğrenimi sürenler) ilanlar; yalnızca belirli bir kadroda görev yapmış "
    "kişilere açık yeterlik sınavı / iç terfi ilanları; yüksek lisans veya doktora zorunlu ilanlar. "
    "Bu durumlarda bilgisayar mühendisi lisans mezunu başvuramaz.\n"
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


KIRP_GUCLU = re.compile(
    r"bilgisayar|bilişim|yazılım|mühendis|herhangi bir.{0,30}lisans|lisans mezun|bölümlerinden|"
    r"mezun olmak|KPSS|ikamet|oturan|tecrübe|deneyim", re.I | re.S)


def kirp_metin(metin, limit=3500, bas=900):
    """Groq token tüketimini düşürmek için: ilan başı + önce güçlü, sonra genel anahtar kelime pasajları."""
    metin = re.sub(r"[ \t]+", " ", metin)
    metin = re.sub(r"\n{2,}", "\n", metin)
    if len(metin) <= limit:
        return metin
    secili = bytearray(len(metin))
    for i in range(min(bas, len(metin))):
        secili[i] = 1
    butce = limit - min(bas, len(metin))
    for rx, once, sonra in ((KIRP_GUCLU, 150, 450), (KIRP_ANAHTAR, 100, 250)):
        for m in rx.finditer(metin):
            for i in range(max(0, m.start() - once), min(len(metin), m.end() + sonra)):
                if butce <= 0:
                    break
                if not secili[i]:
                    secili[i] = 1
                    butce -= 1
    parca, bas_i = [], None
    for i, v in enumerate(secili):
        if v and bas_i is None:
            bas_i = i
        elif not v and bas_i is not None:
            parca.append(metin[bas_i:i])
            bas_i = None
    if bas_i is not None:
        parca.append(metin[bas_i:])
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
    if isinstance(res, dict):
        res["_pv"] = PROMPT_VERSION
        return res
    return None


KATEGORI2_KURALI = (
    "KATEGORİLER (aday: ÖNLİSANS mezunu, anestezi alanı; ayrıca işitme engelli, EKPSS'si var):\n"
    "- ENGELLI_KADRO: ilan engelli adaylara özel VEYA ilanda engelli kadrosu/kontenjanı (EKPSS) ayrıca belirtilmiş; "
    "eğitim düzeyi önlisans, lise veya ilkokul/ortaokul olabilir (önlisans mezunu başvurabilir). Sürekli işçi, memur, "
    "sözleşmeli personel, hepsi dahil.\n"
    "- ANESTEZI_SAGLIK: anestezi teknikeri/teknisyeni veya önlisans düzeyi sağlık/tıbbi hizmetler kadrosu "
    "(engelli kontenjanı yoksa).\n"
    "- ONLISANS: önlisans mezunlarının başvurabildiği genel kadro (memur, sözleşmeli, tekniker, büro, VHKİ...), "
    "engelli kontenjanı yok.\n"
    "- ILGISIZ: yalnız lisans/yüksek lisans/doktora şartı, akademik kadro, belirli mesleklere kapalı (öğretmen, hâkim...), "
    "sağlık dışı özel meslek ya da önlisans mezununun başvuramadığı, iptal/düzeltme ilanı.\n"
    "Bir metinde birden çok kadro varsa önlisans/engelli adayın başvurabildiği kadroyu esas al; "
    "engelli kadrosu varsa ENGELLI_KADRO seç.\n"
)

SEMA2 = """{
  "kategori": "ENGELLI_KADRO" | "ANESTEZI_SAGLIK" | "ONLISANS" | "ILGISIZ",
  "kanit": "kategoriyi destekleyen METİNDEN birebir kısa alıntı (max 200 karakter)",
  "kurum": string,
  "pozisyon": string,
  "kadroSayisi": number | null,
  "engelliBilgi": "engelli kadro sayısı, engel derecesi/grubu (örn. %40 ve üzeri, işitme) ve EKPSS şartı; yoksa Yok",
  "basvuruBaslangic": string | null,
  "basvuruBitis": string | null,
  "degerlendirmeSekli": "örn: %100 KPSS | EKPSS | KPSS + sözlü mülakat | kura | belirtilmemiş",
  "kpssDurumu": "Zorunlu" | "Tercih sebebi" | "Aranmıyor" | "Belirtilmemiş",
  "kpssTuru": "örn: KPSS-B grubu en az 60 puan | EKPSS | belirtilmemiş",
  "ekSinav": boolean,
  "ekSinavDetay": "sınav türü ve puan şartı; yoksa Yok veya Belirtilmemiş",
  "ikametSarti": "Yok" | "Belirtilmemiş" | "Var: <il/ilçe>",
  "ekSartlar": "yaş sınırı, sağlık şartı, ehliyet, deneyim, askerlik vb. yoksa 'Yok'",
  "kisaOzet": "en fazla 2 cümle"
}"""


def analiz2(baslik, metin):
    prompt = ("Aşağıda bir Türkiye kamu personeli alım ilanının tam metni var. Önlisans mezunu, engelli (işitme) "
              "bir aday açısından sınıflandır ve bilgileri çıkar. SADECE geçerli JSON döndür.\n"
              + KATEGORI2_KURALI +
              "\nKESİN KURALLAR: Metinde yazmayan bilgiyi UYDURMA; yoksa null / 'Belirtilmemiş' yaz. "
              "'kanit' metinden birebir alıntı olmalı. Engelli kadrosu/kontenjanını ve istenen engel derecesini "
              "özellikle ara. KPSS/EKPSS durumunu ve taban puanı yaz. İkamet şartı için 'ikamet', 'oturmak' "
              "ifadelerine bak.\n\nJSON ŞEMASI:\n" + SEMA2 +
              "\n\nBAŞLIK: " + baslik + "\n\nMETİN (ilgili bölümler):\n" + kirp_metin(metin))
    res = groq_zincir(prompt)
    if isinstance(res, dict) and res.get("kategori") in ("ENGELLI_KADRO", "ANESTEZI_SAGLIK", "ONLISANS", "ILGISIZ"):
        res["_pv"] = P2_VERSION
        return res
    return None


def oncelik2(baslik, metin):
    t = f"{baslik} {metin[:3000]}"
    if re.search(r"engelli|ekpss", t, re.I):
        return 0
    if re.search(r"anestezi|sağlık\s+teknik|tıbbi", t, re.I):
        return 1
    return 2


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
    kalan = ""
    if a.get("_bitis"):
        g = (a["_bitis"] - TR_NOW.date()).days
        kalan = (f'<span class="tag red">⏰ bugün son gün!</span>' if g == 0 else
                 f'<span class="tag{" red" if g <= 3 else ""}">⏰ {g} gün kaldı</span>')
    tecr = ""
    if a.get("_tecrube"):
        tecr = (f'<span class="tag{" red" if a["_tecrube"] >= 3 else ""}">'
                f'🧑‍💼 {a["_tecrube"]}+ yıl tecrübe şartı</span>')
    zayif = ""
    eng = ""
    if a.get("engelliBilgi") and str(a["engelliBilgi"]).strip().lower() not in ("yok", "belirtilmemiş", "none"):
        eng = f'<span class="tag src">♿ Engelli: {E(a["engelliBilgi"])}</span>'
    if not a.get("_p2") and not re.search(r"lisans|bilgisayar|bilişim|yazılım|herhangi|mühendis", str(a.get("kanit") or ""), re.I):
        zayif = '<span class="tag red">⚠ kanıt zayıf — ilanı elle kontrol et</span>'
    diger = ""
    if a.get("_digerleri"):
        diger = ('<div class="kanit">🔗 Aynı ilan başka kaynakta da: '
                 + " · ".join(f'<a href="{E(l)}" target="_blank">{E(k)}</a>' for k, l in a["_digerleri"])
                 + "</div>")
    return f"""<div class="card">
  <div class="top"><a href="{E(a.get('link'))}" target="_blank">{E(a.get('pozisyon') or a.get('_baslik'))}</a>{yeni}</div>
  <div class="kurum">{E(a.get('kurum'))}{kadro}</div>
  <div class="meta">
    <span class="tag src">{E(a.get('_kaynak'))}</span>{tarih}
    <span class="tag">🧾 {E(a.get('degerlendirmeSekli'))}</span>
        <span class="tag">KPSS {E(a.get('kpssDurumu') or 'Belirtilmemiş')}: {E(a.get('kpssTuru') or 'belirtilmemiş')}</span>
        <span class="tag">Ek sınav: {E(ek_sinav)}</span>
    <span class="{cls}">📍 İkamet: {E(ikamet)}</span>{kalan}{tecr}{zayif}{eng}
    {'<span class="tag">🤖 yedek model: ' + E(a.get('_model')) + ' (doğrula)</span>' if a.get('_model') in GROQ_ZAYIF else ''}
  </div>
  {ek_html}
  <p>{E(a.get('kisaOzet'))}</p>
  <div class="kanit">Kanıt: “{E(a.get('kanit'))}”</div>{diger}
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


def build_report(bolum, tum, log_rows, yeni_sayisi, okunanlar, groq_notu, dolmus=(), elle=(), yetmeyen=()):
    def section(items):
        if not items:
            return "<p>Şu an bu kategoride aktif ilan bulunamadı.</p>"
        items = sorted(items, key=lambda x: ((x.get("_tecrube") or 0) >= 3, x.get("_bitis") is None,
                                             x.get("_bitis") or datetime.date.max))
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

    tum_fiz = [a for a in tum if FIZIKSEL.search(str(a.get("pozisyon") or "") + " " + str(a.get("ekSartlar") or ""))]
    tum_n = [a for a in tum if a not in tum_fiz]
    fiz_html = ""
    if tum_fiz:
        fiz_html = (f'<div class="box"><details><summary><b>🚓 Fiziksel/yaş şartı aranan kadrolar — zabıta vb. '
                    f'({len(tum_fiz)})</b></summary>{section(tum_fiz)}</details></div>')
    elle_html = ""
    if elle:
        satir = "".join(f'<li><a href="{E(x["link"])}" target="_blank">{E(x["baslik"])}</a> '
                        f'<span class="tag">{E(x["kaynak"])}</span> — {E(x["neden"])}</li>' for x in elle)
        elle_html = (f'<div class="box"><details open><summary><b>🔎 Elle kontrol listesi ({len(elle)})</b> — '
                     f'içeriği okunamadı ama başlığı ilgili görünüyor</summary><ul>{satir}</ul></details></div>')
    yetmeyen_html = ""
    if yetmeyen:
        satirlar = "".join(
            f'<li><a href="{E(a.get("link"))}" target="_blank">{E(a.get("pozisyon") or a.get("_baslik"))}</a>'
            f' — {E(a.get("kurum"))} <span class="tag red">{E(a.get("_uygunsuz"))}</span></li>' for a in yetmeyen)
        yetmeyen_html = (f'<div class="box"><details><summary><b>🚫 Profilinle uyuşmadığı için gizlenen ilanlar '
                         f'({len(yetmeyen)})</b> — KPSS {PROFIL["kpss"]} ({PROFIL["kpss_turu"]}), {PROFIL["yas"]} yaş'
                         f'</summary><ul>{satirlar}</ul></details></div>')
    dolmus_html = ""
    if dolmus:
        satirlar = "".join(
            f'<li><a href="{E(a.get("link"))}" target="_blank">{E(a.get("pozisyon") or a.get("_baslik"))}</a>'
            f' — {E(a.get("kurum"))} (son gün: {a["_bitis"].strftime("%d.%m.%Y")})</li>' for a in dolmus)
        dolmus_html = (f'<div class="box"><details><summary><b>⌛ Süresi dolmuş, gizlenen ilanlar '
                       f'({len(dolmus)})</b></summary><ul>{satirlar}</ul></details></div>')
    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Günlük Kamu İlan Raporu</title><style>{STYLE}</style></head><body>
<h1>📋 Günlük Kamu İlan Raporu — {TR_NOW.strftime('%d.%m.%Y %H:%M')}</h1>
<p>Bugün yeni eklenen ilgili ilan: <b>{yeni_sayisi}</b> {E(groq_notu)}</p>
<h2>🎯 Bölümüme Özel (Bilgisayar/Yazılım Mühendisliği) — {len(bolum)} ilan</h2>
{section(bolum)}
<h2>🎓 Tüm Lisans Mezunlarına Açık — {len(tum_n)} ilan</h2>
{section(tum_n)}
{fiz_html}
{elle_html}
{yetmeyen_html}
{dolmus_html}
<div class="box"><h2>🔍 Veri Çekim Doğrulama Kaydı</h2>{log_html}
<p><a href="debug.html">Tanı sayfası (ekran görüntüleri, arka plan istekleri)</a></p>
<p><a href="onlisans.html">♿ İkinci rapor: önlisans &amp; engelli kadro ilanları</a></p></div>
<div class="box"><details><summary><b>📑 Bugün listede görülen tüm ilanlar ({len(okunanlar)}) — okundu mu, sonuç ne?</b></summary>
{okunan_html}</details></div>
</body></html>"""


def p2_rapor(ads, bugun, state):
    """İkinci profil (önlisans/engelli) için bugün listede olan ilanları kategorilere ayırır."""
    bugun_tarih = TR_NOW.date()
    gruplar = {"ENGELLI_KADRO": [], "ANESTEZI_SAGLIK": [], "ONLISANS": []}
    dolmus, bekleyen = [], 0
    for key in bugun:
        k = ads.get(key)
        if not k or k.get("kaynak") == SBB_ADI:
            continue
        p2 = k.get("p2") or {}
        if p2.get("aday") and not p2.get("analiz"):
            bekleyen += 1
        an = p2.get("analiz")
        if not an or an.get("kategori") not in gruplar:
            continue
        bitis = tarih_ayrisir(an.get("basvuruBitis"))
        a = dict(an)
        a.update({"link": key, "_kaynak": k.get("kaynak"), "_baslik": k.get("baslik"), "_p2": True,
                  "_bitis": bitis, "_ilk": k.get("first_seen")})
        if bitis and bitis < bugun_tarih:
            dolmus.append(a)
        else:
            gruplar[an["kategori"]].append(a)
    for kat in gruplar:
        gruplar[kat] = birlestir(gruplar[kat])
        for c in gruplar[kat]:
            c["_yeni"] = c.get("_ilk") == TODAY and TODAY != state.get("first_run")
    # SBB'de içerik okunmadığı için başlığı uygun görünenler elle kontrol listesi olur
    elle, gorulen = [], set()
    for key in bugun:
        k = ads.get(key)
        if not k or k.get("kaynak") != SBB_ADI:
            continue
        kurum, ilan = _sbb_parcala(k.get("baslik"))
        t = f"{kurum} {ilan}"
        if ENGEL_BASLIK.search(t) and not re.search(r"iptal|düzeltme|süre\s*uzat", t, re.I) and t.lower() not in gorulen:
            gorulen.add(t.lower())
            elle.append({"link": key, "baslik": t[:160], "kaynak": SBB_ADI})
    return gruplar, dolmus, elle[:40], bekleyen


def build_report2(gruplar, dolmus, elle, bekleyen, groq_notu):
    def section(items):
        if not items:
            return "<p>Şu an bu kategoride aktif ilan bulunamadı.</p>"
        items = sorted(items, key=lambda x: (x.get("_bitis") is None, x.get("_bitis") or datetime.date.max))
        return "\n".join(card_html(a) for a in items)

    yeni = sum(1 for g in gruplar.values() for c in g if c.get("_yeni"))
    elle_html = ""
    if elle:
        satir = "".join(f'<li><a href="{E(x["link"])}" target="_blank">{E(x["baslik"])}</a> '
                        f'<span class="tag">{E(x["kaynak"])}</span></li>' for x in elle)
        elle_html = (f'<div class="box"><details><summary><b>🔎 SBB\'de görülen, elle kontrol edilecek ({len(elle)})</b>'
                     f' — içeriği okunamıyor</summary><ul>{satir}</ul></details></div>')
    dolmus_html = ""
    if dolmus:
        satir = "".join(f'<li><a href="{E(a["link"])}" target="_blank">{E(a.get("pozisyon") or a.get("_baslik"))}</a> '
                        f'— {E(a.get("kurum"))} (son gün: {a["_bitis"].strftime("%d.%m.%Y")})</li>' for a in dolmus)
        dolmus_html = (f'<div class="box"><details><summary><b>⌛ Süresi dolmuş, gizlenen ilanlar ({len(dolmus)})'
                       f'</b></summary><ul>{satir}</ul></details></div>')
    bekle = f" — ⏳ {bekleyen} ilan analiz kuyruğunda (sonraki çalıştırmalarda işlenecek)" if bekleyen else ""
    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Önlisans ve Engelli Kadro İlanları</title><style>{STYLE}</style></head><body>
<h1>♿ Önlisans &amp; Engelli Kadro İlanları — {TR_NOW.strftime('%d.%m.%Y %H:%M')}</h1>
<p><a href="index.html">← Ana rapora dön</a> · Bugün yeni eklenen ilgili ilan: <b>{yeni}</b>{E(bekle)}</p>
<p style="font-size:12px;color:#555">Puana göre eleme yapılmaz; ilanlar son başvuru tarihine göre sıralıdır.
Aynı ilanlar ana kaynaklardan (İlan.gov.tr, Resmî Gazete, Kariyer Kapısı, ÇŞB, İŞKUR) taranır.</p>
<h2>♿ Engelli kadrosu / EKPSS'li ilanlar — {len(gruplar['ENGELLI_KADRO'])} ilan</h2>
{section(gruplar['ENGELLI_KADRO'])}
<h2>🩺 Anestezi / sağlık teknikeri kadroları — {len(gruplar['ANESTEZI_SAGLIK'])} ilan</h2>
{section(gruplar['ANESTEZI_SAGLIK'])}
<h2>🎓 Önlisans mezunlarına açık kadrolar — {len(gruplar['ONLISANS'])} ilan</h2>
{section(gruplar['ONLISANS'])}
{elle_html}
{dolmus_html}
<div class="box"><p>Not: Sağlık Bakanlığı'nın kendi KPSS tercih alımları bu kaynaklarda yer almaz; ayrıca takip edilmelidir.</p></div>
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
    if k.get("kaynak") == SBB_ADI:
        return "SBB: yalnız liste kaydı (detay okunmuyor)"
    if not k.get("detail_checked") and not k.get("analiz"):
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
        analiz_kuyrugu, log_by_name, analiz2_kuyrugu = [], {}, []

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
                if name == SBB_ADI:
                    # SBB detay sayfaları 133 karakterlik iskelet metin dönüyor (içerik okunamıyor) ve her ilan
                    # birkaç kez listeleniyor. Sayfa açmak yerine yalnız liste kaydı tutulur; başka kaynakta
                    # karşılığı olmayanlar raporda "elle kontrol" listesine düşer.
                    k = k or {"first_seen": TODAY, "kaynak": name, "baslik": baslik}
                    k.update(last_seen=TODAY, detail_checked=False, bitti=True,
                             sonuc="SBB: yalnız liste (detay okunmuyor)")
                    k.pop("metin", None)
                    ads[key] = k
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
                # İkinci profil (önlisans/engelli): metin burada elde olduğu için anahtar kelime süzgeci uygulanır
                p2 = k.get("p2") or {}
                if degisti or p2.get("v") != P2_VERSION:
                    aday2 = bool(not AKADEMIK_BASLIK.search(baslik) and ADAY2_ANAHTAR.search(metin + " " + baslik))
                    p2 = {"v": P2_VERSION, "aday": aday2}
                    k["p2"] = p2
                    k.pop("p2metin", None)
                if p2.get("aday") and not p2.get("analiz"):
                    k["p2metin"] = metin[:8000]
                    analiz2_kuyrugu.append((oncelik2(baslik, metin), key, baslik, metin))
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
                    if len(metin) < 300 and not e.get("content"):
                        # çok kısa metin = sayfa gerçekten okunamadı; "ilgisiz" demek yanlış olur
                        k.update(bitti=True, sonuc="METİN KISA — OKUNAMADI")
                    else:
                        k.update(bitti=True, sonuc="ADAY DEĞİL (anahtar kelime yok)")
                    k.pop("metin", None)
                    continue

                k["metin"] = metin[:8000]          # analiz edilene kadar saklanır
                analiz_kuyrugu.append((oncelik(baslik, metin), key, baslik, metin, name))

            if name == SBB_ADI:
                batch["note"] += " — detay okunmuyor (yalnız liste); başka kaynaklarla eşleştirildi"
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

        # Stage 3b: ikinci profil analizi (birinci profilden artan kotayla)
        p2_denenen = 0
        if GROQ_API_KEY:
            analiz2_kuyrugu.sort(key=lambda x: x[0])
            print(f"İkinci profil analiz kuyruğu: {len(analiz2_kuyrugu)} ilan")
            for _, key, baslik, metin in analiz2_kuyrugu:
                if GROQ_DEAD or zaman_doldu() or p2_denenen >= P2_MAX_ANALIZ:
                    break
                p2_denenen += 1
                sonuc = analiz2(baslik, metin)
                if not GROQ_DEAD:
                    time.sleep(GROQ_SLEEP_SEC)
                if not sonuc:
                    continue
                ads[key]["p2"]["analiz"] = sonuc
                ads[key].pop("p2metin", None)
                if p2_denenen % 10 == 0:
                    save_state(state)
            save_state(state)

        browser.close()

    # Rapor verileri
    bolum, tum, dolmus, yeni_sayisi, okunanlar = [], [], [], 0, []
    ilgili_say = {}
    bugun_tarih = TR_NOW.date()
    for key in bugun:
        k = ads.get(key)
        if not k:
            continue
        yeni = k.get("first_seen") == TODAY and TODAY != state.get("first_run")
        an, neden = dogrula(k.get("analiz"), k.get("baslik", ""))
        if k.get("kaynak") == SBB_ADI:      # SBB'de detay okunmuyor; eski başlık-tabanlı analizler güvenilmez
            an, neden = None, None
        sonuc = sonuc_metni(k)
        if neden:
            sonuc = f"ILGISIZ ({neden})"
        ilgili = bool(an and an.get("kategori") in ("BOLUM", "TUM_LISANS"))
        bitis = tarih_ayrisir(an.get("basvuruBitis")) if ilgili else None
        sure_doldu = bool(bitis and bitis < bugun_tarih)
        if ilgili and sure_doldu:
            sonuc = f"SÜRESİ DOLMUŞ ({bitis.strftime('%d.%m.%Y')}) — {an['kategori']}"
        okunanlar.append({"kaynak": k.get("kaynak"), "baslik": k.get("baslik"), "link": key,
                          "len": k.get("metin_len", "?"), "duzey": k.get("detail_level", "?"),
                          "sonuc": sonuc, "yeni": yeni})
        if ilgili:
            a = dict(an)
            a.update({"link": key, "_kaynak": k.get("kaynak"), "_baslik": k.get("baslik"),
                      "_yeni": yeni, "_bitis": bitis, "_ilk": k.get("first_seen")})
            if sure_doldu:
                dolmus.append(a)
            else:
                ilgili_say[k.get("kaynak")] = ilgili_say.get(k.get("kaynak"), 0) + 1
                (bolum if a["kategori"] == "BOLUM" else tum).append(a)
    bolum, tum = birlestir(bolum), birlestir(tum)
    for c in bolum + tum:      # "yeni": kartın en eski kaynağı bugün görülmüşse; sayı da kartlar üzerinden
        c["_yeni"] = c.get("_ilk") == TODAY and TODAY != state.get("first_run")
        c["_tecrube"] = tecrube_yili(c)
        c["_uygunsuz"] = uygunluk(c)
    yetmeyen = [c for c in bolum + tum if c["_uygunsuz"]]
    bolum = [c for c in bolum if not c["_uygunsuz"]]
    tum = [c for c in tum if not c["_uygunsuz"]]
    yeni_sayisi = sum(1 for c in bolum + tum if c["_yeni"])
    elle = sbb_yalniz(ads, bugun) + kisa_metinliler(ads, bugun)
    for r in log_rows:                      # tablodaki "İlgili": bugün listede olan, süresi dolmamış ilgili ilanlar
        r[4] = ilgili_say.get(r[1], 0)
    okunanlar.sort(key=lambda o: (o["kaynak"] or "", o["sonuc"]))
    bekleyen = sum(1 for o in okunanlar if o["sonuc"].startswith("BEKLEMEDE"))
    if bekleyen and not GROQ_API_KEY:
        groq_notu = f"— ⏳ {bekleyen} ilan analiz kuyruğunda; GROQ_API_KEY tanımlı değil."
    elif bekleyen:
        groq_notu = f"— ⏳ {bekleyen} ilan Groq kotası/hatası nedeniyle analiz kuyruğunda."
    else:
        groq_notu = ""
    if GROQ_DEAD_NEDEN:
        groq_notu += " — ⚠ Groq: " + "; ".join(f"{m}: {n}" for m, n in GROQ_DEAD_NEDEN.items())

    yeni_dosya = not os.path.isfile(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if yeni_dosya:
            w.writerow(["tarih", "saat", "kaynak", "ham_kayit", "detay_okunan", "ilgili", "durum", "not"])
        for r in log_rows:
            w.writerow([TODAY] + r)

    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(build_report(bolum, tum, log_rows, yeni_sayisi, okunanlar, groq_notu, dolmus, elle, yetmeyen))
    if DIAGNOSE:
        with open("docs/debug.html", "w", encoding="utf-8") as f:
            f.write(build_debug())
    p2_bolumler, p2_dolmus, p2_elle, p2_bekleyen = p2_rapor(ads, bugun, state)
    with open("docs/onlisans.html", "w", encoding="utf-8") as f:
        f.write(build_report2(p2_bolumler, p2_dolmus, p2_elle, p2_bekleyen, groq_notu))
    save_state(state)
    print(f"İkinci rapor: engelli {len(p2_bolumler['ENGELLI_KADRO'])}, sağlık {len(p2_bolumler['ANESTEZI_SAGLIK'])}, "
          f"önlisans {len(p2_bolumler['ONLISANS'])}, beklemede {p2_bekleyen}")
    print(f"Bitti. Bölüm: {len(bolum)}, Tüm lisans: {len(tum)}, Süresi dolmuş: {len(dolmus)}, Yeni: {yeni_sayisi}, Elle kontrol: {len(elle)}, Beklemede: {bekleyen}")


if __name__ == "__main__":
    main()

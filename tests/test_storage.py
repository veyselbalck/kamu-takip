import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import Mock, patch

import scraper
from pypdf import PdfWriter


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_state_path = scraper.STATE_PATH
        self.old_database_path = scraper.DATABASE_PATH
        scraper.STATE_PATH = os.path.join(self.temp_dir.name, "state.json")
        scraper.DATABASE_PATH = os.path.join(self.temp_dir.name, "ads.sqlite3")

    def tearDown(self):
        scraper.STATE_PATH = self.old_state_path
        scraper.DATABASE_PATH = self.old_database_path
        self.temp_dir.cleanup()

    def test_listing_text_is_restored_from_database(self):
        state = {
            "version": scraper.STATE_VERSION,
            "first_run": "2026-11-01",
            "ads": {
                "https://example.test/ilan/1": {
                    "kaynak": "Test",
                    "baslik": "Uzman yardımcısı",
                    "first_seen": "2026-11-01",
                    "last_seen": "2026-11-01",
                    "metin": "İlanın saklanan tam metni",
                    "p2metin": "İkinci profil metni",
                    "metin_hash": "abc123",
                    "detail_checked": True,
                    "analiz": {"kategori": "BOLUM"},
                }
            },
        }

        scraper.save_state(state)

        with open(scraper.STATE_PATH, encoding="utf-8") as f:
            json_state = json.load(f)
        self.assertNotIn("metin", json_state["ads"]["https://example.test/ilan/1"])
        restored = scraper.load_state()
        self.assertEqual(restored["ads"]["https://example.test/ilan/1"]["metin"],
                         "İlanın saklanan tam metni")
        self.assertEqual(restored["ads"]["https://example.test/ilan/1"]["p2metin"],
                         "İkinci profil metni")

    def test_scrape_run_and_source_results_are_persisted(self):
        batch = {
            "name": "Test kaynağı",
            "ham": 4,
            "entries": [{"link": "https://example.test/ilan/1"}],
            "details_read": 1,
            "details_reused": 2,
            "note": "Test notu",
        }
        row = ["10:00:00", "Test kaynağı", 4, 3, 1, "OK", "Test notu"]
        metrics = {
            "total_ads": 1,
            "new_ads": 1,
            "details_read": 1,
            "details_reused": 2,
            "detail_errors": 0,
            "analysis_errors": 0,
        }

        scraper.save_run_history([batch], [row], metrics)

        with closing(sqlite3.connect(scraper.DATABASE_PATH)) as conn:
            run = conn.execute(
                "SELECT total_ads, new_ads, details_read, details_reused FROM scrape_runs"
            ).fetchone()
            source = conn.execute(
                "SELECT raw_count, listed_count, status FROM source_runs"
            ).fetchone()
        self.assertEqual(run, (1, 1, 1, 2))
        self.assertEqual(source, (4, 1, "OK"))


class ProfileFilterTests(unittest.TestCase):
    def test_completed_results_are_reused_but_read_failures_are_retried(self):
        self.assertTrue(scraper.kayitli_analiz_var({
            "bitti": True,
            "sonuc": "ADAY DEĞİL (anahtar kelime yok)",
        }))
        self.assertFalse(scraper.kayitli_analiz_var({
            "bitti": True,
            "sonuc": "DETAY OKUNAMADI",
        }))

    def test_primary_profile_filter_accepts_an_explicit_profile(self):
        listing = {"kpssTuru": "KPSS-P3 en az 75"}
        higher_score = dict(scraper.PROFIL, kpss=80)
        self.assertIsNone(scraper.uygunluk(listing, higher_score))
        self.assertIn("yetmez", scraper.uygunluk(listing, scraper.PROFIL))

    def test_secondary_profile_filter_accepts_an_explicit_profile(self):
        listing = {
            "kategori": "ENGELLI_KADRO",
            "kpssTuru": "EKPSS en az 70 puan",
            "degerlendirmeSekli": "",
            "engelliBilgi": "",
        }
        higher_score = dict(scraper.PROFIL2, ekpss=75)
        self.assertIsNone(scraper.profil2_kontrol(listing, higher_score)[0])
        self.assertIn("yetmez", scraper.profil2_kontrol(listing, scraper.PROFIL2)[0])

    def test_secondary_report_shows_ilgisiz_ads_as_profile_mismatches(self):
        key = "https://example.test/ilan/2"
        ads = {
            key: {
                "kaynak": "Test kaynağı",
                "baslik": "Muhasebe personeli",
                "p2": {
                    "aday": True,
                    "analiz": {
                        "kategori": "ILGISIZ",
                        "pozisyon": "Muhasebe personeli",
                        "kurum": "Test kurumu",
                        "bolumUygun": "Hayır (yalnız muhasebe mezunları)",
                    },
                },
            },
        }

        groups, expired, manual, pending = scraper.p2_rapor(
            ads, {key}, {"first_run": scraper.TODAY}
        )
        report = scraper.build_report2(groups, expired, manual, pending, "")

        self.assertEqual(len(groups["_uymayan"]), 1)
        self.assertIn("önlisans programı uymuyor", groups["_uymayan"][0]["_uygunsuz"])
        self.assertIn("Muhasebe personeli", report)
        self.assertIn("önlisans programı uymuyor", report)


class DedupTests(unittest.TestCase):
    def _ad(self, kaynak, kurum, pozisyon, **extra):
        ad = {"_kaynak": kaynak, "kurum": kurum, "pozisyon": pozisyon, "link": f"https://{kaynak}.test/{pozisyon}",
              "_ilk": "2026-11-10", "kategori": "BOLUM"}
        ad.update(extra)
        return ad

    def test_conflicting_quota_numbers_do_not_split_the_same_ad(self):
        # Gerçek veri: aynı Sayıştay ilanı bir kaynakta 12, diğerinde 3 kadro olarak çıkmıştı.
        a = self._ad("İlan.gov.tr", "Sayıştay Başkanlığı", "Sözleşmeli Çözümleyici ve Sözleşmeli Programcı", kadroSayisi=12)
        b = self._ad("Memurlar.net İlan", "Sayıştay", "Sözleşmeli Çözümleyici", kadroSayisi=3)
        self.assertTrue(scraper.ayni_ilan(a, b))
        self.assertEqual(len(scraper.birlestir([a, b])), 1)

    def test_same_institution_but_different_deadline_stays_separate(self):
        import datetime
        a = self._ad("SBB Kamu İlan", "Sayıştay Başkanlığı", "Sözleşmeli Bilişim Personeli",
                     _bitis=datetime.date(2026, 10, 30))
        b = self._ad("Memurlar.net İlan", "Sayıştay", "Sözleşmeli Bilişim Personeli",
                     _bitis=datetime.date(2026, 11, 25))
        self.assertFalse(scraper.ayni_ilan(a, b))

    def test_near_identical_text_merges_even_when_titles_differ(self):
        metin = " ".join(f"anlamlıkelime{i}" for i in range(120))
        a = self._ad("İlan.gov.tr", "AFAD Başkanlığı", "Denetçi Yardımcısı", _imza=scraper.imza(metin))
        b = self._ad("Memurlar.net İlan", "AFAD Başkanlığı", "Uzman Yardımcısı", _imza=scraper.imza(metin))
        self.assertTrue(scraper.ayni_ilan(a, b))

    def test_short_text_is_not_used_for_comparison(self):
        self.assertEqual(scraper.imza("çok kısa bir metin"), frozenset())

    def test_merge_prefers_official_source_and_fills_gaps(self):
        official = self._ad("Kariyer Kapısı", "TÜİK Başkanlığı", "Mühendis", kisaOzet="")
        mirror = self._ad("Memurlar.net İlan", "TÜİK", "Mühendis", kisaOzet="Özet burada", extra="x")
        merged = scraper.birlestir([mirror, official])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["_kaynak"], "Kariyer Kapısı")
        self.assertEqual(merged[0]["kisaOzet"], "Özet burada")
        self.assertEqual([k for k, _ in merged[0]["_digerleri"]], ["Memurlar.net İlan"])

    def test_categories_are_resolved_when_copies_disagree(self):
        a = self._ad("İlan.gov.tr", "Adli Tıp Kurumu", "Mühendis (Bilişim)", kategori="TUM_LISANS")
        b = self._ad("Kariyer Kapısı", "Adli Tıp Kurumu", "Mühendis (Bilişim)", kategori="BOLUM")
        merged = scraper.birlestir([a, b], ("BOLUM", "TUM_LISANS"))
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["kategori"], "BOLUM")

    def test_first_seen_uses_the_earliest_copy(self):
        a = self._ad("İlan.gov.tr", "Adli Tıp Kurumu", "Mühendis", _ilk="2026-11-20")
        b = self._ad("Memurlar.net İlan", "Adli Tıp Kurumu", "Mühendis", _ilk="2026-11-18")
        self.assertEqual(scraper.birlestir([a, b])[0]["_ilk"], "2026-11-18")

    def test_unknown_deadline_sorts_last_and_newest_first(self):
        import datetime
        yakin = self._ad("A", "K1", "P1", _bitis=datetime.date(2026, 11, 22))
        yeni_belirsiz = self._ad("A", "K2", "P2", _bitis=None, _ilk="2026-11-20")
        eski_belirsiz = self._ad("A", "K3", "P3", _bitis=None, _ilk="2026-10-01")
        sirali = sorted([eski_belirsiz, yeni_belirsiz, yakin], key=scraper.sira_anahtari)
        self.assertEqual([x["kurum"] for x in sirali], ["K1", "K2", "K3"])


class FitRankingTests(unittest.TestCase):
    def _ad(self, **extra):
        ad = {"pozisyon": "Sözleşmeli Bilişim Personeli", "kategori": "BOLUM", "kpssTuru": "KPSS-P3",
              "kanit": "Bilgisayar mühendisliği lisans mezunu", "ikametSarti": "Yok", "_baslik": "x"}
        ad.update(extra)
        return ad

    def test_department_match_with_matching_kpss_is_a_strong_fit(self):
        self.assertGreaterEqual(scraper.uyum_puani(self._ad()), scraper.UYUM_GUCLU)

    def test_experience_requirement_and_weak_evidence_lower_the_fit(self):
        zayif = self._ad(_tecrube=4, kanit="ilan metni")
        self.assertLess(scraper.uyum_puani(zayif), scraper.UYUM_GUCLU)

    def test_generic_posting_is_not_a_strong_fit(self):
        genel = self._ad(kategori="TUM_LISANS", pozisyon="Memur", kpssTuru="KPSS-P10")
        self.assertLess(scraper.uyum_puani(genel), scraper.UYUM_GUCLU)

    def test_strong_fit_is_listed_before_an_earlier_deadline(self):
        import datetime
        yakin_zayif = {"kurum": "A", "_bitis": datetime.date(2026, 11, 22), "_uyum": 1}
        uzak_guclu = {"kurum": "B", "_bitis": datetime.date(2026, 12, 30), "_uyum": 5}
        sirali = sorted([yakin_zayif, uzak_guclu], key=scraper.sira_anahtari)
        self.assertEqual([x["kurum"] for x in sirali], ["B", "A"])

    def test_second_profile_prefers_disabled_quota_postings(self):
        engelli = {"_p2": True, "kategori": "ENGELLI_KADRO", "_puan_notu": "puan şartını karşılıyor"}
        onlisans = {"_p2": True, "kategori": "ONLISANS"}
        self.assertGreaterEqual(scraper.uyum_puani(engelli), scraper.UYUM_GUCLU)
        self.assertLess(scraper.uyum_puani(onlisans), scraper.UYUM_GUCLU)


class ReadRetryTests(unittest.TestCase):
    def test_repeated_content_failures_move_to_weekly_retries(self):
        import datetime
        today = datetime.date(2026, 11, 21)
        ad = {}
        for _ in range(3):
            self.assertFalse(scraper.okuma_ertele(ad, today))
            scraper.okuma_basarisiz_say(ad, "İŞKUR PDF okunamadı: PDF metni çıkarılamadı")
        ad["okuma_son"] = "2026-11-21"
        self.assertTrue(scraper.okuma_ertele(ad, today))
        self.assertTrue(scraper.okuma_ertele(ad, today + datetime.timedelta(days=6)))
        self.assertFalse(scraper.okuma_ertele(ad, today + datetime.timedelta(days=7)))

    def test_transient_network_errors_never_count(self):
        ad = {}
        for _ in range(5):
            scraper.okuma_basarisiz_say(ad, "zaman aşımı (site yavaş ya da bu ağdan erişilemiyor)")
        self.assertFalse(scraper.okuma_ertele(ad))
        self.assertNotIn("okuma_deneme", ad)


class ReportUiTests(unittest.TestCase):
    def _ad(self, days, **extra):
        import datetime
        end = scraper.TR_NOW.date() + datetime.timedelta(days=days)
        ad = {"pozisyon": "Bilişim Mühendisi", "kurum": "Test Kurumu", "link": "https://example.test/a",
              "_kaynak": "Test", "_baslik": "b", "_bitis": end, "basvuruBitis": end.strftime("%d.%m.%Y"),
              "kanit": "lisans mezunu bilgisayar mühendisliği", "ekSartlar": "Yok", "ikametSarti": "Yok"}
        ad.update(extra)
        return ad

    def test_card_marks_urgency_by_remaining_days(self):
        self.assertIn('data-urg="today"', scraper.card_html(self._ad(0)))
        self.assertIn('data-urg="soon"', scraper.card_html(self._ad(2)))
        self.assertIn('data-urg="week"', scraper.card_html(self._ad(6)))
        self.assertIn('data-urg="later"', scraper.card_html(self._ad(30)))

    def test_card_escapes_html_and_flags_new_and_warnings(self):
        card = scraper.card_html(self._ad(10, kurum="<script>x</script>", _yeni=True, ekSartlar="Askerlik"))
        self.assertNotIn("<script>x", card)
        self.assertIn('data-new="1"', card)
        self.assertIn('data-warn="1"', card)

    def test_report_page_is_self_contained_with_theme_and_filter(self):
        groups = {"ENGELLI_KADRO": [], "ANESTEZI_SAGLIK": [], "ONLISANS": [], "_uymayan": [], "_fiziksel": []}
        report = scraper.build_report2(groups, [], [], 0, "")
        self.assertIn('id="q"', report)
        self.assertIn("prefers-color-scheme", report)
        self.assertNotIn("http://fonts", report)


class IskurMapSourceTests(unittest.TestCase):
    def test_map_crawl_visits_only_active_cities(self):
        class FakeLocator:
            def __init__(self, page, selector):
                self.page = page
                self.selector = selector

            def evaluate_all(self, _script):
                return self.page.cities if "data-count" in self.selector else self.page.rows

        class FakePage:
            def __init__(self):
                self.cities = [
                    {"id": f"il-{number}", "name": f"İl {number}", "count": 0}
                    for number in range(80)
                ]
                self.cities.append({"id": "ankara", "name": "Ankara", "count": 1})
                self.rows = [{
                    "cells": ["05.11.2026", "Bilişim Personeli"],
                    "links": ["https://www.iskur.gov.tr/medya/abc/ilan.pdf"],
                }]
                self.visited = []

            def goto(self, url, **_kwargs):
                self.visited.append(url)

            def locator(self, selector):
                return FakeLocator(self, selector)

        page = FakePage()

        entries, total, note = scraper.fetch_iskur_harita(page, scraper.ISKUR_MEMUR_ADI)

        self.assertEqual(len(page.visited), 2)
        self.assertIn("?idId=ankara&il=Ankara", page.visited[1])
        self.assertEqual(total, 1)
        self.assertEqual(len(entries), 1)
        self.assertIn("haritada ilan bulunan il: 1", note)

    def test_map_rows_become_unique_pdf_entries(self):
        rows = [
            {
                "cells": ["05.11.2026 18:00", "Bilişim Personeli Alım İlanı"],
                "links": [
                    "https://www.iskur.gov.tr/medya/abc/ilan.pdf",
                    "https://www.iskur.gov.tr/ilanlar/",
                ],
            },
            {
                "cells": ["05.11.2026 18:00", "Bilişim Personeli Alım İlanı"],
                "links": ["https://www.iskur.gov.tr/medya/abc/ilan.pdf"],
            },
            {
                "cells": ["05.11.2026 18:00", "Başka ilan"],
                "links": ["https://example.test/medya/ilan.pdf"],
            },
        ]

        entries = scraper._iskur_entries_from_rows(rows)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Bilişim Personeli Alım İlanı")
        self.assertEqual(entries[0]["detail_level"], "İŞKUR ilan PDF'si")

    def test_iskur_pdf_reader_downloads_and_extracts_the_pdf(self):
        url = "https://www.iskur.gov.tr/medya/abc/ilan.pdf"
        response = Mock(ok=True, headers={"content-type": "application/pdf"})
        response.body.return_value = b"%PDF test"
        page = Mock()
        page.context.request.get.return_value = response
        expected_text = "İlan metni " * 20

        with patch.object(scraper, "ek_metni", return_value=(expected_text, "pdf")) as extract:
            text = scraper.read_iskur_pdf(page, url)

        self.assertEqual(text, expected_text)
        extract.assert_called_once_with(b"%PDF test", "application/pdf", url)

    def test_iskur_pdf_reader_uses_http_fallback_when_browser_returns_html(self):
        url = "https://www.iskur.gov.tr/medya/abc/ilan.pdf"
        browser_response = Mock(
            ok=True, status=200, headers={"content-type": "text/html"}
        )
        browser_response.body.return_value = b"<html>temporary error</html>"
        page = Mock()
        page.context.request.get.return_value = browser_response
        http_response = Mock(
            status_code=200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF from HTTP fallback",
        )
        expected_text = "İlan metni " * 20
        scraper.DETAY_HATA.pop(url, None)

        with (
            patch.object(scraper, "request_with_retry", return_value=http_response),
            patch.object(scraper, "ek_metni", return_value=(expected_text, "pdf")) as extract,
        ):
            text = scraper.read_iskur_pdf(page, url)

        self.assertEqual(text, expected_text)
        extract.assert_called_once_with(b"%PDF from HTTP fallback", "application/pdf", url)

    def test_pdf_text_extraction_falls_back_to_groq_when_gemini_is_unavailable(self):
        page = Mock()
        page.extract_text.return_value = ""
        expected_text = "Kamu personeli alım koşulları. " * 10

        with (
            patch.object(scraper, "PdfReader", return_value=Mock(pages=[page])),
            patch.object(scraper, "yerel_ocr_pdf", return_value=(None, "model yok")),
            patch.object(scraper, "gemini_ocr_dosya", return_value=None),
            patch.object(scraper, "groq_ocr_pdf", return_value=(expected_text, "")) as groq_ocr,
        ):
            text, kind = scraper.ek_metni(b"%PDF-1.7 image-only", "application/pdf", "ilan.pdf")

        self.assertEqual(text, expected_text)
        self.assertEqual(kind, "pdf-ocr-groq")
        groq_ocr.assert_called_once()

    def test_pdf_text_extraction_prefers_local_ocr_before_cloud(self):
        page = Mock()
        page.extract_text.return_value = ""
        expected_text = "Türkçe yerel OCR ilan metni " * 10

        with (
            patch.object(scraper, "PdfReader", return_value=Mock(pages=[page])),
            patch.object(scraper, "yerel_ocr_pdf", return_value=(expected_text, "")) as local_ocr,
            patch.object(scraper, "gemini_ocr_dosya") as gemini,
            patch.object(scraper, "groq_ocr_pdf") as groq_ocr,
        ):
            text, kind = scraper.ek_metni(b"%PDF-1.7 image-only", "application/pdf", "ilan.pdf")

        self.assertEqual(text, expected_text)
        self.assertEqual(kind, "pdf-ocr-yerel")
        local_ocr.assert_called_once()
        gemini.assert_not_called()
        groq_ocr.assert_not_called()

    def test_local_ocr_reads_pdf_pages_and_joins_recognized_lines(self):
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        pdf = io.BytesIO()
        writer.write(pdf)
        ocr = Mock()
        ocr.image_to_string.return_value = "Kamu personeli\nAlım ilanı"

        with patch.object(scraper, "yerel_ocr_hazirla", return_value=((ocr, "tessdata"), "")):
            text, error = scraper.yerel_ocr_pdf(pdf.getvalue())

        self.assertEqual(text, "Kamu personeli\nAlım ilanı")
        self.assertEqual(error, "")
        ocr.image_to_string.assert_called_once()


class GroqRotationTests(unittest.TestCase):
    def test_load_groq_keys_supports_numbered_secrets_and_gaps(self):
        keys = scraper.load_groq_keys({
            "GROQ_API_KEY": " first ",
            "GROQ_API_KEY_2": "",
            "GROQ_API_KEY_3": "third",
            "GROQ_API_KEY_5": "fifth",
            "GROQ_API_KEY_EXTRA": "ignored",
        })

        self.assertEqual(keys, ["first", "third", "fifth"])

    def test_groq_chain_reports_403_as_network_access_denial(self):
        with (
            patch.object(scraper, "GROQ_KEYS", ["test-key"]),
            patch.object(scraper, "GROQ_MODELS", ["test-model"]),
            patch.object(scraper, "GROQ_DEAD_MODELS", set()),
            patch.object(scraper, "GROQ_DEAD_NEDEN", {}),
            patch.object(scraper, "GROQ_JSON_HATA", {}),
            patch.object(scraper, "GEMINI_API_KEY", ""),
            patch.object(scraper, "groq_modelleri_dogrula"),
            patch.object(scraper, "groq_call", return_value="ACCESS_HATA"),
        ):
            result = scraper.groq_zincir("prompt")

            self.assertIsNone(result)
            self.assertIn("HTTP 403", scraper.GROQ_DEAD_NEDEN["test-model"])
            self.assertIn("runner çıkış IP'sini", scraper.GROQ_DEAD_NEDEN["test-model"])

    def test_vision_ocr_reports_403_as_network_access_denial(self):
        denied = Mock(status_code=403, text="Access denied. Please check your network settings.")

        with (
            patch.object(scraper, "GROQ_KEYS", ["test-key"]),
            patch.object(scraper, "GROQ_DEAD_MODELS", set()),
            patch.object(scraper, "GROQ_DEAD_NEDEN", {}),
            patch.object(scraper.requests, "post", return_value=denied),
        ):
            text, error = scraper.groq_vision_call(["ZmFrZQ=="], [1])

            self.assertIsNone(text)
            self.assertIn("HTTP 403", error)
            self.assertTrue(any("ağ izni" in reason for reason in scraper.GROQ_DEAD_NEDEN.values()))

    def test_rate_limited_key_falls_through_to_the_next_key(self):
        limited = Mock(status_code=429, headers={"retry-after": "0"}, text="rate limit")
        success = Mock(status_code=200)
        success.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}}],
        }

        with (
            patch.object(scraper, "GROQ_KEYS", ["first-key", "second-key"]),
            patch.object(scraper, "GROQ_MODELS", ["test-model"]),
            patch.object(scraper, "GROQ_DEAD_MODELS", set()),
            patch.object(scraper, "GROQ_DEAD_NEDEN", {}),
            patch.object(scraper, "GROQ_JSON_HATA", {}),
            patch.object(scraper, "groq_modelleri_dogrula"),
            patch.object(scraper.time, "sleep"),
            patch.object(scraper.requests, "post", side_effect=[limited, limited, limited, success]) as post
        ):
            result = scraper.groq_zincir("prompt")

        self.assertTrue(result["ok"])
        self.assertEqual(post.call_count, 4)
        self.assertIn("first-key", post.call_args_list[0].kwargs["headers"]["Authorization"])
        self.assertIn("second-key", post.call_args_list[3].kwargs["headers"]["Authorization"])

    def test_vision_ocr_rotates_to_the_next_key_after_rate_limit(self):
        limited = Mock(status_code=429)
        success = Mock(status_code=200)
        success.json.return_value = {
            "choices": [{"message": {"content": "Okunan ilan metni"}}],
        }

        with (
            patch.object(scraper, "GROQ_KEYS", ["first-key", "second-key"]),
            patch.object(scraper, "GROQ_DEAD_MODELS", set()),
            patch.object(scraper, "GROQ_DEAD_NEDEN", {}),
            patch.object(scraper.requests, "post", side_effect=[limited, success]) as post,
        ):
            text, error = scraper.groq_vision_call(["ZmFrZQ=="], [1])

        self.assertEqual(text, "Okunan ilan metni")
        self.assertEqual(error, "")
        self.assertEqual(post.call_count, 2)
        self.assertIn("first-key", post.call_args_list[0].kwargs["headers"]["Authorization"])
        self.assertIn("second-key", post.call_args_list[1].kwargs["headers"]["Authorization"])

    def test_groq_pdf_ocr_batches_all_pages_in_groups_of_three(self):
        writer = PdfWriter()
        for _ in range(4):
            writer.add_blank_page(width=200, height=200)
        pdf = io.BytesIO()
        writer.write(pdf)

        with (
            patch.object(scraper, "GROQ_KEYS", ["test-key"]),
            patch.object(
                scraper, "groq_vision_call",
                side_effect=[("İlk üç sayfa", ""), ("Dördüncü sayfa", "")],
            ) as vision,
        ):
            text, error = scraper.groq_ocr_pdf(pdf.getvalue())

        self.assertEqual(text, "İlk üç sayfa\n\nDördüncü sayfa")
        self.assertEqual(error, "")
        self.assertEqual(vision.call_args_list[0].args[1], [1, 2, 3])
        self.assertEqual(vision.call_args_list[1].args[1], [4])


if __name__ == "__main__":
    unittest.main()

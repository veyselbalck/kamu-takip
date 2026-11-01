import json
import os
import sqlite3
import tempfile
import unittest

import scraper


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

        with sqlite3.connect(scraper.DATABASE_PATH) as conn:
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


if __name__ == "__main__":
    unittest.main()

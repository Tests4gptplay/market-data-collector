import base64
import json
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'collectors' / 'npm_downloads'))
import collect_npm as c

class CollectorTests(unittest.TestCase):
    def test_real_name_from_source(self):
        response = {"encoding": "base64", "content": base64.b64encode(json.dumps({"name": "@tests4gptplay/dsh-video-vision"}).encode()).decode()}
        with patch.object(c, "fetch_json", return_value=response):
            self.assertEqual(c.declared_package_name("dsh-video-vision"), "@tests4gptplay/dsh-video-vision")

    def test_scoped_registration(self):
        response = {"name": "@tests4gptplay/dsh-video-vision", "versions": {"0.10.1": {}}, "dist-tags": {"latest": "0.10.1"}}
        with patch.object(c, "fetch_json", return_value=response):
            self.assertEqual(c.verified_registry_package("dsh-video-vision", "@tests4gptplay/dsh-video-vision")["latest_published_version"], "0.10.1")

    def test_unscoped_requires_source_match(self):
        response = {"name": "deepseek-servant", "versions": {"0.4.1": {"repository": {"url": "https://github.com/stranger/different"}}}, "dist-tags": {"latest": "0.4.1"}}
        with patch.object(c, "fetch_json", return_value=response):
            with self.assertRaises(c.FetchError):
                c.verified_registry_package("deepseek-servant", "deepseek-servant")
        response["versions"]["0.4.1"]["repository"]["url"] = "git+https://github.com/Tests4gptplay/deepseek-servant.git"
        with patch.object(c, "fetch_json", return_value=response):
            self.assertEqual(c.verified_registry_package("deepseek-servant", "deepseek-servant")["name"], "deepseek-servant")

    def test_point_and_range_validation(self):
        p = c.validate_point({"downloads": 8, "start": "2026-11-01", "end": "2026-11-07", "package": "@tests4gptplay/dsh-dots"}, "@tests4gptplay/dsh-dots")
        self.assertEqual(p["downloads"], 8)
        d = c.validate_range({"downloads": [{"day": "2026-11-01", "downloads": 0}], "start": "2026-11-01", "end": "2026-11-01", "package": "@tests4gptplay/dsh-dots"}, "@tests4gptplay/dsh-dots")
        self.assertEqual(d["days"][0]["downloads"], 0)
        with self.assertRaises(c.FetchError):
            c.validate_point({"downloads": 0, "start": "2026-11-01", "end": "2026-11-07", "package": "other"}, "@tests4gptplay/dsh-dots")

    def test_unpublished_is_not_zero(self):
        with patch.object(c, "declared_package_name", return_value="@tests4gptplay/dsh-video-vision"), patch.object(c, "verified_registry_package", side_effect=c.FetchError("HTTP 404")):
            v = c.collect_one("dsh-video-vision")
        self.assertEqual(v["status"], "not_verified_published")
        self.assertNotIn("last_week", v)

    def test_partial_success_keeps_date_and_failure(self):
        name = "@tests4gptplay/dsh-dots"
        def fake(url, github=False):
            if "/downloads/range/" in url:
                raise c.FetchError("HTTP 503")
            return {"package": name, "start": "2026-11-01", "end": "2026-11-07", "downloads": 3}
        with patch.object(c, "declared_package_name", return_value=name), patch.object(c, "verified_registry_package", return_value={"name": name, "latest_published_version": "1.0.11"}), patch.object(c, "fetch_json", side_effect=fake):
            v = c.collect_one("dsh-dots")
        self.assertEqual(v["status"], "partial")
        self.assertEqual(v["last_week"]["downloads"], 3)

    def test_dots_scoped_npm_mapping_keeps_source_identity(self):
        published = "@tests4gptplay/dsh-dots"
        seen = []

        def fake(url, github=False):
            seen.append(url)
            if url.startswith(c.REGISTRY + "/"):
                return {"name": published, "versions": {"1.0.12": {}}, "dist-tags": {"latest": "1.0.12"}}
            if "/downloads/range/" in url:
                return {"package": published, "start": "2026-11-02", "end": "2026-11-08", "downloads": [{"day": "2026-11-08", "downloads": 35}]}
            if "/downloads/point/" in url:
                return {"package": published, "start": "2026-11-02", "end": "2026-11-08", "downloads": 35}
            raise AssertionError(f"Unexpected request: {url}")

        with patch.object(c, "declared_package_name", return_value="dsh-dots"), patch.object(c, "fetch_json", side_effect=fake):
            v = c.collect_one("dsh-dots")
        self.assertEqual(v["status"], "ok")
        self.assertEqual(v["declared_npm_name"], "dsh-dots")
        self.assertEqual(v["name"], published)
        self.assertEqual(v["latest_published_version"], "1.0.12")
        self.assertEqual(v["last_week"]["downloads"], 35)
        self.assertEqual(v["daily_30"]["days"][0]["downloads"], 35)
        self.assertIn(f"{c.REGISTRY}/%40tests4gptplay%2Fdsh-dots", seen)
        self.assertTrue(all(url.endswith("/@tests4gptplay/dsh-dots") for url in v["sources"].values()))

    def test_latest_day_stale_but_valid(self):
        data = {
            "sampled_at_utc": "2026-11-11T10:00:00+00:00",
            "packages": {"dots": {
                "status": "ok",
                "last_day": {"status": "ok", "end_utc": "2026-11-09", "downloads": 222},
                "last_week": {"status": "ok", "end_utc": "2026-11-09", "downloads": 469},
                "daily_30": {"status": "ok", "days": [{"day": "2026-11-09", "downloads": 222}]},
            }},
        }
        c.annotate_latest_day(data)
        p = data["packages"]["dots"]
        self.assertEqual(p["status"], "ok")
        self.assertEqual(p["last_day"]["downloads"], 222)
        self.assertEqual(p["timeliness"], {
            "expected_complete_utc_day": "2026-11-10",
            "returned_utc_day": "2026-11-09",
            "lag_complete_days": 1,
            "last_day_matches_daily_30": True,
            "last_week_end_matches": True,
        })

    def test_latest_day_detects_inconsistent_data(self):
        data = {
            "sampled_at_utc": "2026-11-11T10:00:00+00:00",
            "packages": {"test": {
                "status": "ok",
                "last_day": {"status": "ok", "end_utc": "2026-11-10", "downloads": 15},
                "last_week": {"status": "ok", "end_utc": "2026-11-09", "downloads": 30},
                "daily_30": {"status": "ok", "days": [{"day": "2026-11-10", "downloads": 12}]},
            }},
        }
        c.annotate_latest_day(data)
        t = data["packages"]["test"]["timeliness"]
        self.assertEqual(t["lag_complete_days"], 0)
        self.assertFalse(t["last_day_matches_daily_30"])
        self.assertFalse(t["last_week_end_matches"])

    def test_latest_day_unavailable_not_zero(self):
        data = {
            "sampled_at_utc": "2026-11-11T10:00:00+00:00",
            "packages": {
                "unpublished": {"status": "not_verified_published", "reason": "HTTP 404"},
                "partial": {"status": "partial", "last_day": {"status": "not_retrieved"}},
            },
        }
        c.annotate_latest_day(data)
        self.assertNotIn("timeliness", data["packages"]["unpublished"])
        self.assertNotIn("timeliness", data["packages"]["partial"])

if __name__ == "__main__":
    unittest.main()

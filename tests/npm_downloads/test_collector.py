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

if __name__ == "__main__":
    unittest.main()

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

    def test_dots_classic_manifest_matches_its_release(self):
        seen = []
        manifest = {"encoding": "base64", "content": base64.b64encode(json.dumps({"name": "dsh-dots"}).encode()).decode()}
        def fake(url, github=False):
            seen.append((url, github))
            return manifest
        with patch.object(c, "fetch_json", side_effect=fake):
            name = c.declared_package_name("dsh-dots", "package.json", ref=c.SOURCE_REFS["dsh-dots"])
        self.assertEqual(name, "dsh-dots")
        self.assertEqual(seen, [(f"{c.GITHUB_API}/repos/{c.OWNER}/dsh-dots/contents/package.json?ref=v1.0.12", True)])

    def test_dot_mcp_separately_reads_its_own_manifest_and_npm_name(self):
        published = "dsh-dot-mcp"
        seen = []
        manifest = {"encoding": "base64", "content": base64.b64encode(json.dumps({"name": published}).encode()).decode()}
        def fake(url, github=False):
            seen.append((url, github))
            if "/contents/" in url:
                return manifest
            if url.startswith(c.REGISTRY + "/"):
                return {
                    "name": published,
                    "versions": {"1.0.1": {"repository": {"url": "git+https://github.com/Tests4gptplay/dsh-dots.git"}}},
                    "dist-tags": {"latest": "1.0.1"},
                }
            if "/downloads/range/" in url:
                return {"package": published, "start": "2026-11-08", "end": "2026-11-14",
                        "downloads": [{"day": "2026-11-14", "downloads": 7}]}
            if "/downloads/point/" in url:
                return {"package": published, "start": "2026-11-08", "end": "2026-11-14", "downloads": 7}
            raise AssertionError(f"Unexpected URL: {url}")
        with patch.object(c, "fetch_json", side_effect=fake):
            item = c.collect_one("dsh-dots", "packages/dsh-dot-mcp/package.json", independent=True)
        self.assertEqual(item["status"], "ok")
        self.assertEqual(item["repository"], "Tests4gptplay/dsh-dots")
        self.assertEqual(item["manifest_path"], "packages/dsh-dot-mcp/package.json")
        self.assertEqual(item["declared_npm_name"], published)
        self.assertEqual(item["name"], published)
        self.assertEqual(item["last_day"]["downloads"], 7)
        self.assertEqual(item["last_week"]["downloads"], 7)
        self.assertIn((f"{c.GITHUB_API}/repos/{c.OWNER}/dsh-dots/contents/packages/dsh-dot-mcp/package.json", True), seen)
        self.assertIn((f"{c.REGISTRY}/dsh-dot-mcp", False), seen)
        self.assertTrue(all(url.endswith("/dsh-dot-mcp") for url in item["sources"].values()))

    def test_six_repositories_generate_nine_independent_package_records(self):
        def fake(repo, manifest_path="package.json", independent=False, npm_name_override=None):
            if npm_name_override:
                return {"name": npm_name_override}
            if independent and repo == "dsh-dots":
                return {"name": "dsh-dot-mcp"}
            if independent and repo == "dsh-kujira-persona":
                return {"name": "dsh-kujira-persona"}
            return {"name": repo}
        with patch.object(c, "collect_one", side_effect=fake) as collect:
            packages = c.collect_packages()
        self.assertEqual(len(packages), 9)
        self.assertEqual(set(packages), set(c.REPOSITORIES) | {"dsh-dot-mcp", "dsh-kujira-persona-unscoped", "dsh-codex-native-unscoped"})
        self.assertEqual(packages["dsh-dots"]["name"], "dsh-dots")
        self.assertEqual(packages["dsh-dot-mcp"]["name"], "dsh-dot-mcp")
        self.assertEqual(packages["dsh-kujira-persona-unscoped"]["name"], "dsh-kujira-persona")
        self.assertEqual(packages["dsh-codex-native-unscoped"]["name"], "dsh-codex-native")
        collect.assert_any_call("dsh-dots")
        collect.assert_any_call("dsh-dots", "packages/dsh-dot-mcp/package.json", independent=True)
        collect.assert_any_call("dsh-kujira-persona")
        collect.assert_any_call("dsh-kujira-persona", "package.json", independent=True)
        collect.assert_any_call("dsh-codex-native", "package.json", independent=True, npm_name_override="dsh-codex-native")

    def test_codex_unscoped_tracks_independent_package_with_repository_check(self):
        scoped = "@tests4gptplay/dsh-codex-native"
        unscoped = "dsh-codex-native"
        manifest = {"encoding": "base64", "content": base64.b64encode(
            json.dumps({"name": scoped}).encode()).decode()}
        seen = []

        def fake(url, github=False):
            seen.append(url)
            if "/contents/" in url:
                return manifest
            if url.startswith(c.REGISTRY + "/"):
                return {"name": unscoped,
                        "versions": {"0.2.5": {"repository": {"url":
                            "git+https://github.com/Tests4gptplay/dsh-codex-native.git"}}},
                        "dist-tags": {"latest": "0.2.5"}}
            if "/downloads/range/" in url:
                return {"package": unscoped, "start": "2026-11-09",
                        "end": "2026-11-15",
                        "downloads": [{"day": "2026-11-15", "downloads": 7}]}
            if "/downloads/point/" in url:
                return {"package": unscoped, "start": "2026-11-09",
                        "end": "2026-11-15", "downloads": 7}
            raise AssertionError(url)

        with patch.object(c, "fetch_json", side_effect=fake):
            item = c.collect_one("dsh-codex-native", "package.json",
                                 independent=True, npm_name_override=unscoped)
        self.assertEqual(item["status"], "ok")
        self.assertEqual(item["declared_npm_name"], scoped)
        self.assertEqual(item["name"], unscoped)
        self.assertEqual(item["last_day"]["downloads"], 7)
        self.assertIn(f"{c.REGISTRY}/dsh-codex-native", seen)
        self.assertTrue(all(url.endswith("/dsh-codex-native")
                            for url in item["sources"].values()))

    def test_kujira_scoped_manifest_source_is_preserved(self):
        scoped = "@tests4gptplay/dsh-kujira-persona"
        manifest = {"encoding": "base64", "content": base64.b64encode(json.dumps({"name": scoped}).encode()).decode()}
        calls = []
        def fake(url, github=False):
            calls.append(url)
            if "/contents/" in url:
                return manifest
            if url.startswith(c.REGISTRY + "/"):
                return {"name": scoped, "versions": {"2.4.3": {}}, "dist-tags": {"latest": "2.4.3"}}
            if "/downloads/range/" in url:
                return {"package": scoped, "start": "2026-11-09", "end": "2026-11-15",
                        "downloads": [{"day": "2026-11-15", "downloads": 11}]}
            if "/downloads/point/" in url:
                return {"package": scoped, "start": "2026-11-09", "end": "2026-11-15", "downloads": 11}
            raise AssertionError(url)
        with patch.object(c, "fetch_json", side_effect=fake):
            item = c.collect_one("dsh-kujira-persona")
        self.assertEqual(item["status"], "ok")
        self.assertEqual(item["name"], scoped)
        self.assertEqual(item["declared_npm_name"], scoped)
        self.assertEqual(item["last_week"]["downloads"], 11)
        self.assertIn(f"{c.GITHUB_API}/repos/{c.OWNER}/dsh-kujira-persona/contents/package.json?ref={c.SOURCE_REFS['dsh-kujira-persona']}", calls)
        self.assertIn(f"{c.REGISTRY}/%40tests4gptplay%2Fdsh-kujira-persona", calls)

    def test_kujira_unscoped_package_uses_current_manifest_and_separate_stats(self):
        published = "dsh-kujira-persona"
        manifest = {"encoding": "base64", "content": base64.b64encode(json.dumps({"name": published}).encode()).decode()}
        calls = []
        def fake(url, github=False):
            calls.append(url)
            if "/contents/" in url:
                return manifest
            if url.startswith(c.REGISTRY + "/"):
                return {"name": published,
                        "versions": {"2.4.3": {"repository": {"url": "git+https://github.com/Tests4gptplay/dsh-kujira-persona.git"}}},
                        "dist-tags": {"latest": "2.4.3"}}
            if "/downloads/range/" in url:
                return {"package": published, "start": "2026-11-09", "end": "2026-11-15",
                        "downloads": [{"day": "2026-11-15", "downloads": 9}]}
            if "/downloads/point/" in url:
                return {"package": published, "start": "2026-11-09", "end": "2026-11-15", "downloads": 9}
            raise AssertionError(url)
        with patch.object(c, "fetch_json", side_effect=fake):
            item = c.collect_one("dsh-kujira-persona", "package.json", independent=True)
        self.assertEqual(item["status"], "ok")
        self.assertEqual(item["repository"], "Tests4gptplay/dsh-kujira-persona")
        self.assertEqual(item["manifest_path"], "package.json")
        self.assertEqual(item["declared_npm_name"], published)
        self.assertEqual(item["name"], published)
        self.assertEqual(item["last_week"]["downloads"], 9)
        self.assertIn(f"{c.GITHUB_API}/repos/{c.OWNER}/dsh-kujira-persona/contents/package.json", calls)
        self.assertNotIn(f"{c.GITHUB_API}/repos/{c.OWNER}/dsh-kujira-persona/contents/package.json?ref={c.SOURCE_REFS['dsh-kujira-persona']}", calls)
        self.assertIn(f"{c.REGISTRY}/dsh-kujira-persona", calls)
        self.assertTrue(all(url.endswith("/dsh-kujira-persona") for url in item["sources"].values()))

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

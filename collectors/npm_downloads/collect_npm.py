#!/usr/bin/env python3
"""Weekly, read-only npm statistics collection for six specified public projects."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

OWNER = "Tests4gptplay"
REPOSITORIES = (
    "dsh-compaction-selector", "deepseek-servant", "dsh-codex-native",
    "dsh-kujira-persona", "dsh-video-vision", "dsh-dots",
)
GITHUB_API = "https://api.github.com"
REGISTRY = "https://registry.npmjs.org"
DOWNLOADS = "https://api.npmjs.org"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

class FetchError(Exception):
    pass

def fetch_json(url: str, github: bool = False) -> dict:
    headers = {"User-Agent": "Tests4gptplay-npm-stats-readonly/1.0", "Accept": "application/json"}
    if github:
        headers["Accept"] = "application/vnd.github+json"
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(url, headers=headers), timeout=25) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise FetchError("JSON response is not an object")
        return result
    except HTTPError as exc:
        raise FetchError(f"HTTP {exc.code}") from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise FetchError(f"network or JSON error: {type(exc).__name__}") from exc

def declared_package_name(repo: str) -> str:
    data = fetch_json(f"{GITHUB_API}/repos/{OWNER}/{repo}/contents/package.json", github=True)
    if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        raise FetchError("GitHub file encoding is not base64")
    try:
        manifest = json.loads(base64.b64decode(data["content"]))
    except (ValueError, TypeError) as exc:
        raise FetchError("invalid package.json") from exc
    name = manifest.get("name")
    if not isinstance(name, str) or not name or ("/" in name and not name.startswith("@")):
        raise FetchError("invalid package name in package.json")
    return name

def verified_registry_package(repo: str, declared_name: str) -> dict:
    meta = fetch_json(f"{REGISTRY}/{quote(declared_name, safe='')}")
    if meta.get("name", "").lower() != declared_name.lower():
        raise FetchError("npm package name mismatch")
    versions = meta.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise FetchError("npm Registry has no published versions")
    if not declared_name.lower().startswith("@tests4gptplay/"):
        # An unscoped package must explicitly refer back to its source repository.
        records = [meta, *list(versions.values())[-3:]]
        urls = []
        for record in records:
            if not isinstance(record, dict):
                continue
            source = record.get("repository", {})
            if isinstance(source, dict):
                urls.append(str(source.get("url", "")))
            elif isinstance(source, str):
                urls.append(source)
        needle = f"github.com/{OWNER}/{repo}".lower()
        normalized = [
            u.lower().replace(".git", "").replace("git@github.com:", "https://github.com/")
            for u in urls
        ]
        if not any(needle in u for u in normalized):
            raise FetchError("unscoped package ownership not verified by repository URL")
    tags = meta.get("dist-tags", {})
    return {"name": meta["name"], "latest_published_version": tags.get("latest") if isinstance(tags, dict) else None}

def validate_point(data: dict, package: str) -> dict:
    if data.get("package", "").lower() != package.lower():
        raise FetchError("package mismatch in npm download response")
    count = data.get("downloads")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise FetchError("invalid npm download count")
    if not all(isinstance(data.get(k), str) and DATE.fullmatch(data[k]) for k in ("start", "end")):
        raise FetchError("invalid npm point date interval")
    return {"start_utc": data["start"], "end_utc": data["end"], "downloads": count}

def validate_range(data: dict, package: str) -> dict:
    if data.get("package", "").lower() != package.lower():
        raise FetchError("package mismatch in npm daily response")
    rows = data.get("downloads")
    if not isinstance(rows, list):
        raise FetchError("invalid npm daily series")
    if not all(
        isinstance(x, dict) and isinstance(x.get("day"), str)
        and DATE.fullmatch(x["day"]) and isinstance(x.get("downloads"), int)
        and not isinstance(x["downloads"], bool) and x["downloads"] >= 0
        for x in rows
    ):
        raise FetchError("invalid npm daily data item")
    if not all(isinstance(data.get(k), str) and DATE.fullmatch(data[k]) for k in ("start", "end")):
        raise FetchError("invalid npm daily date interval")
    return {"start_utc": data["start"], "end_utc": data["end"], "days": rows}

def collect_one(repo: str) -> dict:
    item = {"repository": f"{OWNER}/{repo}", "status": "not_retrieved"}
    try:
        name = declared_package_name(repo)
        item["declared_npm_name"] = name
    except FetchError as exc:
        item.update(status="source_unavailable", reason=str(exc))
        return item
    try:
        item.update(verified_registry_package(repo, name))
    except FetchError as exc:
        item.update(status="not_verified_published", reason=str(exc))
        return item

    package_path = quote(name, safe="@/")
    endpoints = {
        "last_day": f"{DOWNLOADS}/downloads/point/last-day/{package_path}",
        "last_week": f"{DOWNLOADS}/downloads/point/last-week/{package_path}",
        "last_month": f"{DOWNLOADS}/downloads/point/last-month/{package_path}",
        "daily_30": f"{DOWNLOADS}/downloads/range/last-month/{package_path}",
    }
    item["sources"] = endpoints
    success = 0
    for key, url in endpoints.items():
        try:
            raw = fetch_json(url)
            parsed = validate_range(raw, name) if key == "daily_30" else validate_point(raw, name)
            item[key] = {"status": "ok", **parsed}
            success += 1
        except FetchError as exc:
            item[key] = {"status": "not_retrieved", "reason": str(exc)}
    item["status"] = "ok" if success == len(endpoints) else ("partial" if success else "stats_unavailable")
    return item

def main() -> None:
    snapshot = {
        "schema_version": 1,
        "sampled_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "npm official registry and downloads API; GitHub public package.json",
        "repos_scope": [f"{OWNER}/{r}" for r in REPOSITORIES],
        "packages": {r: collect_one(r) for r in REPOSITORIES},
    }
    base = Path(__file__).resolve().parents[2] / "data/npm_downloads"
    history = base / "history"
    history.mkdir(parents=True, exist_ok=True)
    day_jst = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (base / "latest.json").write_text(payload, encoding="utf-8")
    (history / f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')}.json").write_text(payload, encoding="utf-8")
    valid = 0
    for name, package in snapshot["packages"].items():
        week = package.get("last_week", {})
        if week.get("status") == "ok":
            valid += 1
            print(f"{name}: {package['declared_npm_name']} {week['start_utc']}..{week['end_utc']} {week['downloads']} weekly downloads")
        else:
            print(f"{name}: {package['status']} {package.get('reason', week.get('reason', 'not retrieved'))}")
    print(f"Official npm weekly responses: {valid}/{len(REPOSITORIES)}; snapshot={base/'latest.json'}")

if __name__ == "__main__":
    main()

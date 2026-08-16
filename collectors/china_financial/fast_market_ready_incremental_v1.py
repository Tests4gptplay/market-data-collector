#!/usr/bin/env python3
"""Fast-market READY incremental adapter V1.

Purpose
-------
The fast China Financial market block is a hard measurement gate, but a valid
market observation is session-dated rather than wall-clock-dated. Re-fetching
an already validated Friday session on Saturday/Sunday only re-exposes the
same observation to transient source failures and does not create fresher
market information.

This adapter therefore separates *session freshness* from *source availability*:

1. A persisted Market Family V4 run is reusable only when it proves:
   status=PASS, coverage_count=expected_count, and missing_series=[];
2. On a non-trading weekend, the latest validated session <= as_of is carried
   forward without re-fetching the same session;
3. An explicit market_date that has no validated persisted PASS is collected
   live with Market Family V4;
4. On a weekday newer than the latest persisted validated session, the adapter
   attempts a live collection for that date. It does not fabricate a holiday
   calendar; an operator may supply --market-date for an exact eligible date;
5. Core fast-market collection remains fail-closed when a genuinely new session
   is being collected. Reuse never turns a failed or incomplete historical run
   into PASS.

Historical Market Family V4 files are not modified.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

COLLECTOR_VERSION = "V1.9-READY-FAST-MARKET-INCREMENTAL-V1"
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "china_financial"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _valid_market_run(run: dict[str, Any]) -> bool:
    return (
        run.get("status") == "PASS"
        and isinstance(run.get("coverage_count"), int)
        and run.get("coverage_count") == run.get("expected_count")
        and run.get("missing_series") == []
        and bool(run.get("target_date"))
    )


def persisted_passes(as_of: date) -> list[tuple[date, Path, dict[str, Any]]]:
    rows: list[tuple[date, Path, dict[str, Any]]] = []
    base = DATA / "runs"
    if not base.exists():
        return rows
    for path in base.rglob("*-market-family-v4.json"):
        try:
            run = _load(path)
            if not _valid_market_run(run):
                continue
            d = date.fromisoformat(run["target_date"])
            if d <= as_of:
                rows.append((d, path, run))
        except Exception:
            continue
    return sorted(rows, key=lambda x: x[0])


def _copy_persisted_payload(target_date: date, out: Path) -> dict[str, str]:
    copied: dict[str, str] = {}
    yyyy = str(target_date.year)
    name = f"{target_date.isoformat()}-market-family-v4.json"
    for layer in ("observations", "derived", "gaps", "runs"):
        src = DATA / layer / yyyy / name
        if src.exists():
            dst = out / f"persisted_{layer}.json"
            shutil.copyfile(src, dst)
            copied[layer] = str(src.relative_to(ROOT))
    return copied


def _write(out: Path, payload: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collect(as_of: date, out: Path, explicit_market_date: date | None = None, force_live: bool = False) -> int:
    passes = persisted_passes(as_of)
    latest = passes[-1] if passes else None

    if explicit_market_date is not None:
        requested = explicit_market_date
    elif latest is not None and as_of.weekday() >= 5:
        requested = latest[0]
    else:
        requested = as_of

    matching = next((row for row in reversed(passes) if row[0] == requested), None)
    reuse_allowed = matching is not None and not force_live

    # On weekends, carrying the latest validated market session is the correct
    # session state. On weekdays, an exact matching persisted PASS can also be
    # reused unless the operator explicitly requests a live refresh.
    if reuse_allowed:
        d, path, upstream = matching
        copied = _copy_persisted_payload(d, out)
        result = {
            "module": "china_financial_fast_market_ready_incremental",
            "collector_version": COLLECTOR_VERSION,
            "status": "PASS",
            "mode": "PERSISTED_VALIDATED_SESSION_REUSE",
            "as_of": as_of.isoformat(),
            "market_date": d.isoformat(),
            "state_source": "PERSISTED_MARKET_FAMILY_V4_PASS",
            "upstream_run_path": str(path.relative_to(ROOT)),
            "upstream_collector_version": upstream.get("collector_version"),
            "coverage_count": upstream.get("coverage_count"),
            "expected_count": upstream.get("expected_count"),
            "missing_series": upstream.get("missing_series"),
            "copied_payloads": copied,
            "rules": {
                "session_dated_state": True,
                "already_validated_session_not_refetched_by_default": True,
                "weekend_carry_forward_is_not_stale": as_of.weekday() >= 5,
                "core_fail_closed_on_new_live_session": True,
                "unknown_is_not_zero": True,
            },
        }
        _write(out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # If no explicit date is supplied and we are on a weekend with no persisted
    # PASS, fail rather than inventing a session date.
    if explicit_market_date is None and as_of.weekday() >= 5 and latest is None:
        result = {
            "module": "china_financial_fast_market_ready_incremental",
            "collector_version": COLLECTOR_VERSION,
            "status": "INCOMPLETE",
            "mode": "NO_VALIDATED_SESSION_AVAILABLE",
            "as_of": as_of.isoformat(),
            "market_date": None,
            "reason": "Weekend/non-trading carry-forward requires an existing validated market session.",
        }
        _write(out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    requested_dir = out / "live"
    cmd = [
        sys.executable,
        str(ROOT / "collectors" / "china_financial" / "market_family_v4.py"),
        "--date",
        requested.isoformat(),
        "--out",
        str(requested_dir),
    ]
    proc = subprocess.run(cmd, cwd=ROOT)
    live_run_path = requested_dir / requested.isoformat() / "run.json"
    live_run = _load(live_run_path) if live_run_path.exists() else {}
    passed = proc.returncode == 0 and _valid_market_run(live_run)
    result = {
        "module": "china_financial_fast_market_ready_incremental",
        "collector_version": COLLECTOR_VERSION,
        "status": "PASS" if passed else "INCOMPLETE",
        "mode": "LIVE_NEW_SESSION_COLLECTION",
        "as_of": as_of.isoformat(),
        "market_date": requested.isoformat(),
        "state_source": "LIVE_MARKET_FAMILY_V4",
        "live_return_code": proc.returncode,
        "coverage_count": live_run.get("coverage_count"),
        "expected_count": live_run.get("expected_count"),
        "missing_series": live_run.get("missing_series"),
        "rules": {
            "new_session_collection_is_fail_closed": True,
            "persisted_incomplete_run_never_reused": True,
            "unknown_is_not_zero": True,
        },
    }
    _write(out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--market-date")
    ap.add_argument("--force-live", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    return collect(
        date.fromisoformat(args.as_of),
        Path(args.out),
        date.fromisoformat(args.market_date) if args.market_date else None,
        args.force_live,
    )


if __name__ == "__main__":
    raise SystemExit(main())

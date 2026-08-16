#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

COLLECTOR_VERSION = "V1.9-CANDIDATE-MARKET-FAMILY-V4"
BOOTSTRAP_VERSION = "FAST_MARKET_HISTORY_BOOTSTRAP_V1"
DATA_BASENAME = "market-family-v4.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Bounded producer-side bootstrap for China Financial fast-market history. "
            "Persists only validated market sessions and never mutates manifests/latest.json."
        )
    )
    p.add_argument("--end-date", required=True, help="Latest candidate date YYYY-MM-DD")
    p.add_argument("--sessions", type=int, default=60, help="Validated market sessions required")
    p.add_argument("--max-calendar-days", type=int, default=140, help="Hard backward calendar-day scan bound")
    p.add_argument("--work-root", default="out/china_financial_fast_market_history_bootstrap_v1")
    p.add_argument("--data-root", default="data/china_financial")
    p.add_argument("--sleep-seconds", type=float, default=0.35)
    return p.parse_args()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def is_existing_valid_session(data_root: Path, d: date) -> bool:
    run_path = data_root / "runs" / f"{d:%Y}" / f"{d.isoformat()}-{DATA_BASENAME}"
    if not run_path.exists():
        return False
    try:
        run = load_json(run_path)
    except Exception:
        return False
    return (
        run.get("status") == "PASS"
        and int(run.get("coverage_count", -1)) == int(run.get("expected_count", -2))
        and int(run.get("expected_count", 0)) > 0
    )


def collector_command(target_date: str, work_root: Path) -> list[str]:
    return [
        sys.executable,
        "collectors/china_financial/market_family_v4.py",
        "--date",
        target_date,
        "--out",
        str(work_root),
    ]


def persist_valid_session(data_root: Path, work_root: Path, d: date) -> dict:
    source_dir = work_root / d.isoformat()
    run = load_json(source_dir / "run.json")
    if run.get("status") != "PASS":
        raise RuntimeError(f"{d}: collector run status is not PASS")
    if int(run.get("coverage_count", -1)) != int(run.get("expected_count", -2)):
        raise RuntimeError(f"{d}: coverage_count != expected_count")

    year = f"{d:%Y}"
    targets = {
        "observations.json": data_root / "observations" / year / f"{d.isoformat()}-{DATA_BASENAME}",
        "derived.json": data_root / "derived" / year / f"{d.isoformat()}-{DATA_BASENAME}",
        "gaps.json": data_root / "gaps" / year / f"{d.isoformat()}-{DATA_BASENAME}",
        "run.json": data_root / "runs" / year / f"{d.isoformat()}-{DATA_BASENAME}",
    }

    existing = [str(dst) for dst in targets.values() if dst.exists()]
    if existing:
        raise RuntimeError(f"{d}: immutable target collision; refusing overwrite: {existing}")

    for src_name, dst in targets.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_dir / src_name, dst)

    return {
        "date": d.isoformat(),
        "coverage_count": run.get("coverage_count"),
        "expected_count": run.get("expected_count"),
        "collector_version": run.get("collector_version"),
        "paths": {k: str(v) for k, v in targets.items()},
    }


def main() -> int:
    args = parse_args()
    if args.sessions <= 0:
        raise SystemExit("--sessions must be positive")
    if args.max_calendar_days <= 0:
        raise SystemExit("--max-calendar-days must be positive")

    end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    work_root = Path(args.work_root)
    data_root = Path(args.data_root)
    work_root.mkdir(parents=True, exist_ok=True)

    summary = {
        "bootstrap_version": BOOTSTRAP_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "end_date": end.isoformat(),
        "requested_valid_sessions": args.sessions,
        "max_calendar_days": args.max_calendar_days,
        "manifest_latest_mutation_prohibited": True,
        "history_is_immutable_by_default": True,
        "validated_sessions": [],
        "existing_valid_sessions": [],
        "newly_persisted_sessions": [],
        "non_session_candidates": [],
        "hard_failures": [],
    }

    current = end
    scanned = 0
    valid = 0

    while scanned < args.max_calendar_days and valid < args.sessions:
        scanned += 1
        d = current
        current -= timedelta(days=1)

        if d.weekday() >= 5:
            continue

        if is_existing_valid_session(data_root, d):
            summary["existing_valid_sessions"].append(d.isoformat())
            summary["validated_sessions"].append(d.isoformat())
            valid += 1
            continue

        target = d.isoformat()
        proc = subprocess.run(collector_command(target, work_root), text=True, capture_output=True)
        run_path = work_root / target / "run.json"

        run = None
        if run_path.exists():
            try:
                run = load_json(run_path)
            except Exception as exc:
                summary["hard_failures"].append({"date": target, "stage": "RUN_JSON_PARSE", "error": repr(exc)})

        is_pass = bool(
            proc.returncode == 0
            and isinstance(run, dict)
            and run.get("status") == "PASS"
            and int(run.get("coverage_count", -1)) == int(run.get("expected_count", -2))
        )

        if is_pass:
            try:
                persisted = persist_valid_session(data_root, work_root, d)
            except Exception as exc:
                summary["hard_failures"].append({"date": target, "stage": "PERSIST", "error": repr(exc)})
                break
            summary["newly_persisted_sessions"].append(persisted)
            summary["validated_sessions"].append(target)
            valid += 1
        else:
            summary["non_session_candidates"].append(
                {
                    "date": target,
                    "returncode": proc.returncode,
                    "run_status": run.get("status") if isinstance(run, dict) else None,
                    "coverage_count": run.get("coverage_count") if isinstance(run, dict) else None,
                    "expected_count": run.get("expected_count") if isinstance(run, dict) else None,
                    "missing_series": run.get("missing_series") if isinstance(run, dict) else None,
                    "stderr_tail": proc.stderr[-1200:],
                    "stdout_tail": proc.stdout[-1200:],
                }
            )

        shutil.rmtree(work_root / target, ignore_errors=True)
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    summary["scanned_calendar_days"] = scanned
    summary["validated_session_count"] = valid
    summary["newly_persisted_session_count"] = len(summary["newly_persisted_sessions"])
    summary["existing_valid_session_count"] = len(summary["existing_valid_sessions"])
    summary["completed"] = valid >= args.sessions and not summary["hard_failures"]

    summary_path = data_root / "runs" / f"{end:%Y}" / f"{end.isoformat()}-fast-market-history-bootstrap-v1.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if summary_path.exists():
        raise RuntimeError(f"immutable bootstrap summary already exists: {summary_path}")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

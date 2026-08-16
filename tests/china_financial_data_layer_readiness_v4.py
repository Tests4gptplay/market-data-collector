#!/usr/bin/env python3
"""China Financial Draft Data-Layer Readiness V4.

V3 is preserved. V4 reuses the complete V3 economic/data-root audit and adds
explicit checks for the corrected Fast Market Incremental V2 active path and
the versioned Daily READY V4 workflow.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        base_path = Path(td) / "v3.json"
        proc = subprocess.run([
            sys.executable,
            str(ROOT / "tests" / "china_financial_data_layer_readiness_v3.py"),
            "--out",
            str(base_path),
        ], cwd=ROOT, stdout=subprocess.DEVNULL)
        base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else {}

    required = [
        "collectors/china_financial/fast_market_ready_incremental_v2.py",
        ".github/workflows/china-financial-fast-market-incremental-v2-ready-test.yml",
        ".github/workflows/china-financial-daily-ready-v4.yml",
    ]
    missing = [p for p in required if not (ROOT / p).exists()]
    active_path_ok = proc.returncode == 0 and not missing

    report = dict(base)
    report["audit"] = "CHINA_FINANCIAL_DRAFT_DATA_LAYER_READINESS_V4"
    report["status"] = "DATA_LAYER_READY" if active_path_ok and base.get("status") == "DATA_LAYER_READY" else "DATA_LAYER_NOT_READY"
    report["active_fast_market_path"] = {
        "collector": "collectors/china_financial/fast_market_ready_incremental_v2.py",
        "underlying_live_collector": "collectors/china_financial/market_family_v4.py",
        "session_reuse_test": ".github/workflows/china-financial-fast-market-incremental-v2-ready-test.yml",
        "status": "PASS" if not missing[:2] else "FAIL",
    }
    report["active_unified_ready_gate"] = {
        "workflow": ".github/workflows/china-financial-daily-ready-v4.yml",
        "status": "PASS" if (ROOT / ".github/workflows/china-financial-daily-ready-v4.yml").exists() else "FAIL",
    }
    report["v4_versioning_note"] = "V1/V2/V3 readiness audits and Fast V1 remain preserved for rollback; V4 only advances the active READY path."
    report["active_path_missing"] = missing

    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        p = Path(args.out); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["status"] == "DATA_LAYER_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())

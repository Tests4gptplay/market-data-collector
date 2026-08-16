#!/usr/bin/env python3
"""Fast-market READY incremental adapter V2.

V1 is preserved for rollback. V2 fixes the persisted-session reuse output-path
initialization bug discovered by the first READY regression. Session/freshness
semantics are unchanged.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import fast_market_ready_incremental_v1 as v1

COLLECTOR_VERSION = "V1.9-READY-FAST-MARKET-INCREMENTAL-V2"


def collect(as_of: date, out: Path, explicit_market_date: date | None = None, force_live: bool = False) -> int:
    out.mkdir(parents=True, exist_ok=True)
    v1.COLLECTOR_VERSION = COLLECTOR_VERSION
    return v1.collect(as_of, out, explicit_market_date, force_live)


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

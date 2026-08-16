#!/usr/bin/env python3
"""V1.9 candidate Fast collector revision 2.

Keeps the r1 collector intact and replaces only the FDR007 adapter with the
previously validated ChinaMoney FrrHis POST route. This preserves revision
history while the public collector is still in candidate testing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from collectors.china_financial import fast_market as base

FDR_HISTORY = base.CHINAMONEY_ROOT + "/ags/ms/cm-u-bk-currency/FrrHis"
FDR_REFERER = base.CHINAMONEY_ROOT + "/chinese/bkfrr/"


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def collect_fdr007(date: str, raw_dir: Path) -> dict[str, Any]:
    params = {"lang": "CN", "startDate": date, "endDate": date}
    url = FDR_HISTORY + "?" + urlencode(params)
    result = base.http(
        url,
        method="POST",
        data=b"",
        referer=FDR_REFERER,
        accept="application/json, text/javascript, */*; q=0.01",
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": base.CHINAMONEY_ROOT,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    digest = base.save_raw(raw_dir, "chinamoney_frr_history.json", result)
    if not result.ok:
        raise RuntimeError(result.error or "FrrHis request failed")

    payload = json.loads(base.decode(result.raw, result.content_type))
    candidates: list[tuple[str, float, dict[str, Any]]] = []

    for record in _walk_dicts(payload):
        # The current API has changed field naming across implementations.
        # Require the requested date to be present in the same record and only
        # accept a field whose normalized key explicitly identifies FDR007.
        record_text = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if date not in record_text:
            continue
        for key, raw_value in record.items():
            normalized_key = "".join(ch for ch in str(key).upper() if ch.isalnum())
            if "FDR007" not in normalized_key:
                continue
            value = _numeric(raw_value)
            if value is not None:
                candidates.append((str(key), value, record))

    unique = {(key, value) for key, value, _ in candidates}
    if len(unique) != 1:
        raise RuntimeError(
            "expected exactly one date-scoped FDR007 numeric field from FrrHis; "
            f"candidates={[(k, v) for k, v, _ in candidates]!r}"
        )

    key, value = next(iter(unique))
    return base.observation(
        series_id="FUND_FDR007",
        reference_date=date,
        value=value,
        unit="percent",
        provider="ChinaMoney",
        semantic="FDR007_FIXING_QC_ONLY",
        source_url=result.url,
        evidence_sha256=digest,
        collected_at=base.now_iso(),
        role="QC_DIAGNOSTIC",
        extra={
            "source_endpoint": "FrrHis",
            "selected_field": key,
            "substitution_prohibited_for": "FUND_DR007",
        },
    )


def main() -> int:
    base.collect_fdr007 = collect_fdr007
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=base.sys.stderr)
        raise

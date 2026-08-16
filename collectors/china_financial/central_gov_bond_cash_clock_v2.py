#!/usr/bin/env python3
"""Central-government bond cash-clock V2 semantic hardening.

V1 is preserved for rollback. V2 keeps the same candidate interface while
hardening a few MOF-page presentation/semantic edges found by live regression:

1. MOF pages sometimes contain layout whitespace inside Chinese phrases and
   numeric strings. V2 parses normalized text whitespace-insensitively.
2. Post-auction distribution-end dates are NOT silently promoted into explicit
   issue-payment facts. Explicit payment-clock facts continue to come from the
   pre-issue notice's 发行款缴纳 language.
3. MOF result notices use both "偿还本金" and "按面值偿还" maturity wording. V2
   accepts only dates explicitly adjacent to those redemption phrases.
4. Coupon month/day extraction is anchored to the explicit 利息支付日 sentence so
   unrelated publication, accrual, distribution or maturity dates are not
   accidentally interpreted as coupon schedule dates.

No historical V1 file is deleted or overwritten.
"""
from __future__ import annotations

from datetime import date
import re

import central_gov_bond_cash_clock_v1 as v1

COLLECTOR_VERSION = "V1.9-CANDIDATE-CENTRAL-GOV-BOND-CASH-CLOCK-V2"
_original_text_from_html = v1.text_from_html
_original_parse_document = v1.parse_document


def whitespace_tolerant_text_from_html(html: str) -> str:
    text = _original_text_from_html(html)
    # Chinese official pages do not use whitespace to delimit lexical tokens.
    # Removing presentation whitespace gives stable phrase/date recognition and
    # repairs digit fragments such as '202 6' or '2 5'.
    return re.sub(r"\s+", "", text)


def explicit_maturity_date(text: str) -> date | None:
    """Return only a date explicitly tied to principal redemption semantics."""
    patterns = (
        r"(20\d{2}年\d{1,2}月\d{1,2}日)(?:（[^）]*）)?按面值偿还",
        r"(20\d{2}年\d{1,2}月\d{1,2}日)(?:（[^）]*）)?偿还本金",
        r"于(20\d{2}年\d{1,2}月\d{1,2}日)(?:（[^）]*）)?按面值偿还",
        r"于(20\d{2}年\d{1,2}月\d{1,2}日)(?:（[^）]*）)?偿还本金",
    )
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return v1.parse_cn_date(m.group(1))
    return None


def explicit_coupon_month_days(text: str) -> list[tuple[int, int]]:
    """Extract recurring coupon calendar only from explicit payment wording."""
    if "到期一次还本付息" in text:
        return []

    # Typical MOF result language:
    # 利息按半年支付，利息支付日为每年的2月25日...、8月25日，2036年2月25日偿还本金...
    anchors = (
        r"利息支付日为每年的(.+?)(?=20\d{2}年\d{1,2}月\d{1,2}日(?:（[^）]*）)?偿还本金)",
        r"付息日为每年的(.+?)(?=20\d{2}年\d{1,2}月\d{1,2}日(?:（[^）]*）)?偿还本金)",
    )
    segment = None
    for pat in anchors:
        m = re.search(pat, text)
        if m:
            segment = m.group(1)
            break
    if segment is None:
        # Conservative bounded fallback: capture only a short clause after the
        # explicit payment-date label, never scan the whole page.
        m = re.search(r"(?:利息支付日|付息日)为(.{0,120})", text)
        segment = m.group(1) if m else ""

    out: list[tuple[int, int]] = []
    for month, day in re.findall(r"(?<!年)(\d{1,2})月(\d{1,2})日", segment):
        pair = (int(month), int(day))
        if 1 <= pair[0] <= 12 and 1 <= pair[1] <= 31 and pair not in out:
            out.append(pair)
    return out


def hardened_parse_document(url, publication_date):
    obs, gaps, run = _original_parse_document(url, publication_date)
    kept = []
    removed = 0
    for row in obs:
        dims = row.get("dimensions") or {}
        inferred_result_payment = (
            row.get("series_id") == "FISC_GOV_BOND_PAYMENT_EVENT"
            and dims.get("event_stage") == "ACTUAL_AMOUNT_SCHEDULED_DATE"
            and dims.get("payment_date_source") == "DISTRIBUTION_END_DATE"
        )
        if inferred_result_payment:
            removed += 1
            continue
        row["collector_version"] = COLLECTOR_VERSION
        kept.append(row)
    run = dict(run)
    run["collector_version"] = COLLECTOR_VERSION
    run["v2_semantic_hardening"] = {
        "whitespace_insensitive_mof_parsing": True,
        "distribution_end_is_not_payment_fact": True,
        "explicit_redemption_phrase_required_for_maturity": True,
        "coupon_schedule_anchored_to_interest_payment_clause": True,
        "inferred_result_payment_rows_removed": removed,
    }
    run["observation_count"] = len(kept)
    return kept, gaps, run


# Patch V1's module-global implementation hooks. V1 collect()/main() resolve
# these names at runtime, so all collection/list/detail paths use the hardened
# behavior without duplicating the mature V1 implementation.
v1.text_from_html = whitespace_tolerant_text_from_html
v1.maturity_date = explicit_maturity_date
v1.coupon_month_days = explicit_coupon_month_days
v1.parse_document = hardened_parse_document
v1.COLLECTOR_VERSION = COLLECTOR_VERSION


def main() -> int:
    return v1.main()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Central-government bond cash-clock V2 semantic hardening.

V1 is preserved for rollback. V2 intentionally changes only two implementation
behaviors while keeping the same candidate interface:

1. MOF pages sometimes contain layout whitespace inside Chinese phrases and
   numeric strings. V2 performs whitespace-insensitive parsing after V1's HTML
   normalization so a valid official announcement is not misclassified as
   OUT_OF_SCOPE because of presentation markup.
2. A post-auction result page's distribution-end date is NOT an explicit issue-
   payment receipt/deadline. V2 therefore removes any V1 PAYMENT_EVENT whose
   only date source is DISTRIBUTION_END_DATE. Explicit payment-clock facts still
   come from the pre-issue notice's 发行款缴纳 language.

No historical V1 file is deleted or overwritten.
"""
from __future__ import annotations

import re

import central_gov_bond_cash_clock_v1 as v1

COLLECTOR_VERSION = "V1.9-CANDIDATE-CENTRAL-GOV-BOND-CASH-CLOCK-V2"
_original_text_from_html = v1.text_from_html
_original_parse_document = v1.parse_document


def whitespace_tolerant_text_from_html(html: str) -> str:
    text = _original_text_from_html(html)
    # Chinese official pages do not use whitespace to delimit lexical tokens.
    # Removing presentation whitespace gives stable phrase/date recognition and
    # also repairs digit fragments such as '202 6'.
    return re.sub(r"\s+", "", text)


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
        "inferred_result_payment_rows_removed": removed,
    }
    run["observation_count"] = len(kept)
    return kept, gaps, run


# Patch V1's module-global implementation hooks. V1 collect()/main() resolve
# these names at runtime, so all collection/list/detail paths use the hardened
# behavior without copying the mature V1 code.
v1.text_from_html = whitespace_tolerant_text_from_html
v1.parse_document = hardened_parse_document
v1.COLLECTOR_VERSION = COLLECTOR_VERSION


def main() -> int:
    return v1.main()


if __name__ == "__main__":
    raise SystemExit(main())

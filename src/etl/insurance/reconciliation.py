"""Recover only independently reconciled fund categories from count-only defects.

Control codes:
https://www.cmfchile.cl/institucional/seil/certificacion_cir1835_tinver.php
F11: domestic-asset investment funds; F31: domestic-asset mutual funds.
Foreign-asset fund holdings/control H61–H65 are diagnosed separately by pilot.py;
their count defects are not eligible for the recovery implemented here.
An exact value match is evidence for inclusion with a warning, not proof that
each quantity/series is correct or that all categories in either file are complete.
"""
from __future__ import annotations

from decimal import Decimal

from .parser import ParsedFile

HOLDING_STATUSES = {"validated", "accepted_with_warning"}


def review_counts(parsed: dict[tuple[str, str], ParsedFile]) -> dict[tuple[str, str], dict]:
    reviews = {}
    for key, file in parsed.items():
        reviews[key] = {
            "status": "quarantined" if file.count_mismatch or file.missing_envelope else "validated",
            "accepted_lines": [] if file.count_mismatch or file.missing_envelope else [r["source_line"] for r in file.rows],
            "checks": [], "verified_control_codes": [],
        }
        if file.count_mismatch:
            reviews[key]["reason"] = "Record count mismatch; no independently reconciled category"
    for (rut, family), file in parsed.items():
        if family not in {"A", "F"} or file.missing_envelope:
            continue
        control = parsed.get((rut, "C"))
        if control is None or control.missing_envelope or not (file.count_mismatch or control.count_mismatch):
            continue
        # Never let callers combine filings from different periods or groups.
        if (file.period, file.group, file.insurer_rut) != (control.period, control.group, control.insurer_rut):
            continue
        code = "F11" if family == "A" else "F31"
        control_rows = [r for r in control.rows if r["investment_type"] == code]
        supported_types = {"CFI", "CFIP", "CFIPOIND"} if family == "A" else {"CFM"}
        relevant = [r for r in file.rows if (r.get("fund_kind") is not None if family == "A" else True)]
        # No unknown instrument variants, duplicate rows, missing values, negative
        # values, absent controls or empty 0=0 comparisons may recover a file.
        eligible = (
            bool(relevant) and len(control_rows) == 1
            and all(r["instrument_type"] in supported_types for r in relevant)
            and len({r["source_sha256"] for r in file.rows}) == len(file.rows)
            and all(r["final_value_clp"] >= 0 for r in relevant)
        )
        observed = sum((r["final_value_clp"] for r in relevant), Decimal(0))
        expected = control_rows[0]["final_value_clp"] if len(control_rows) == 1 else None
        matched = eligible and observed > 0 and observed == expected
        check = {
            "code": code, "status": "matched" if matched else "not_reconciled",
            "detail_file": file.filename, "detail_sha256": file.sha256,
            "control_file": control.filename, "control_sha256": control.sha256,
            "detail_rows": len(relevant), "detail_clp": observed, "control_clp": expected,
            "difference_clp": observed - expected if expected is not None else None,
            "detail_count_warning": file.count_mismatch, "control_count_warning": control.count_mismatch,
        }
        reviews[rut, family]["checks"].append(check)
        reviews[rut, "C"]["checks"].append(check)
        if not matched:
            continue
        if file.count_mismatch:
            reviews[rut, family].update(
                status="accepted_with_warning",
                accepted_lines=[r["source_line"] for r in relevant],
                reason=f"Count mismatch; only {code} fund holdings recovered by exact independent control match",
            )
        if control.count_mismatch:
            review = reviews[rut, "C"]
            review["status"] = "partially_reconciled"
            review["verified_control_codes"].append(code)
            review["accepted_lines"].append(control_rows[0]["source_line"])
            review["reason"] = "Count mismatch; only independently matched control categories are usable"
    return reviews

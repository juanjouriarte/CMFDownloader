"""Reproducible local pilot: download, validate, reconcile, and link two months.

PYTHONPATH=. .venv/bin/python -m src.etl.insurance.pilot --download --local-reference

Without --download, reuse staged ZIPs. No database writes or scheduler jobs.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .download import InsuranceArchiveClient, validate_archive, MAX_EXPANDED_BYTES
from .parser import parse_file, RECORD_LAYOUTS
from .reconciliation import HOLDING_STATUSES, review_counts
from .envelope_recovery import recover_envelopes

SPEC_URL = "https://www.cmfchile.cl/sitio/seil/software-manual/sgsci/Anexos_Tecnicos_Circular_N1835.pdf"
CODES_URL = "https://www.cmfchile.cl/institucional/seil/certificacion_cir1835_tinver.php"


def read_local_reference(administrator_ruts: set[str]) -> tuple[dict, dict]:
    from sqlalchemy import text
    from src.db.engine import engine

    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Pilot reference lookups require a local database")
    funds, administrators = {}, {}
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '15s'"))
        for row in connection.execute(text(
            "SELECT run_fondo, nombre_fondo, rut_administradora, razon_social_administradora FROM fondo_mutuo"
        )):
            funds["fm", row[0]] = row[1]
            administrators[row[2]] = row[3]
        for row in connection.execute(text("SELECT run_fondo, razon_social FROM fondos_inversion")):
            funds["fi", row[0]] = row[1]
        # Exact RUT labels only. Do not assign historic holdings to a current
        # fund administrator using names or fuzzy matching.
        for row in connection.execute(
            text("SELECT rut, razon_social FROM emisores WHERE rut = ANY(:ruts)"),
            {"ruts": sorted(administrator_ruts)},
        ):
            administrators.setdefault(row[0], row[1])
    return funds, administrators


def reference_ruts(paths: list[Path]) -> set[str]:
    """Collect candidate RUTs for bounded read-only labels; not accepted holdings."""
    ruts = set()
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED_BYTES:
                raise ValueError("Expanded archive exceeds limit")
            for info in archive.infolist():
                family = Path(info.filename).name[:1].upper()
                if family == "X":
                    try:
                        filing = parse_file(info.filename, archive.read(info), expected_period=path.name.split("-")[1],
                                            expected_group=path.name.split("-")[0], inspect_count_mismatch=True)
                        ruts.update(r["administrator_rut"] for r in filing.rows if r.get("administrator_rut"))
                    except (ValueError, UnicodeError):
                        pass  # Candidate label lookup only; the review reports the defect.
                if family not in {"A", "F"}:
                    continue
                for line in archive.read(info).split(b"\n"):
                    if line[:1] == b"2" and line[1:10].isdigit():
                        ruts.add(str(int(line[1:10])))
    return ruts


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def reconcile_fm(files: list[dict], fm_values: dict, controls: dict) -> list[dict]:
    """F31 is domestic-asset mutual funds. H64 belongs to the foreign file.

    This is a focused check, not certification of the insurer's entire portfolio.
    No percentage tolerance is silently applied: report the exact residual.
    """
    validated = {(f["insurer_rut"], f["family"]) for f in files if f["status"] in HOLDING_STATUSES}
    validated.update((f["insurer_rut"], "C") for f in files
                     if f["status"] == "partially_reconciled" and "F31" in f.get("verified_control_codes", []))
    insurers = sorted({rut for rut, _ in validated})
    checks = []
    for rut in insurers:
        if (rut, "F") not in validated or (rut, "C") not in validated:
            checks.append({"insurer_rut": rut, "check": "FM vs F31", "status": "not_checked",
                           "reason": "Missing or quarantined F/C file"})
            continue
        observed = fm_values.get(rut, Decimal(0))
        expected = controls.get((rut, "F31"), Decimal(0))
        difference = observed - expected
        checks.append({"insurer_rut": rut, "check": "FM vs F31", "status": "matched" if not difference else "difference",
                       "detail_clp": observed, "control_clp": expected, "difference_clp": difference})
    return checks


FOREIGN_FUND_CONTROLS = {
    "CFIE": "H61", "CFIEOIED": "H61",
    "CFIIE": "H62", "CFIIEP": "H62", "CFIIEPOIED": "H62",
    "CFME": "H63", "CFMIE": "H64", "ETFA": "H65", "ETFB": "H65",
}


def reconcile_foreign_funds(files: list[dict], values: dict, controls: dict) -> list[dict]:
    """Diagnostic exact checks only; never recover additional quarantined files."""
    checks = []
    for file in files:
        if file["family"] != "X":
            continue
        rut = file["insurer_rut"]
        for code in sorted(set(FOREIGN_FUND_CONTROLS.values())):
            result = {"insurer_rut": rut, "code": code, "check": "X fund holdings vs control"}
            if file["status"] not in HOLDING_STATUSES or (rut, code) not in controls:
                checks.append(result | {"status": "not_checked", "reason": "Missing or quarantined detail/control category"})
                continue
            observed, expected = values.get((rut, code), Decimal(0)), controls[rut, code]
            checks.append(result | {
                "status": "matched" if observed == expected else "difference",
                "detail_clp": observed, "control_clp": expected, "difference_clp": observed - expected,
            })
    return checks


def inspect_archive(path: Path, funds: dict, administrators: dict, *, record_sink=None) -> tuple[dict, list[dict]]:
    name = re.fullmatch(r"(CSVID|CSGEN)-(\d{6})-([0-9a-f]{12})\.zip", path.name)
    if not name:
        raise ValueError(f"Unexpected staged archive name: {path.name}")
    group, period, digest_prefix = name.groups()
    archive_bytes = path.read_bytes()
    digest = hashlib.sha256(archive_bytes).hexdigest()
    if not digest.startswith(digest_prefix):
        raise ValueError(f"Staged archive hash mismatch: {path}")
    members = validate_archive(archive_bytes)
    files, holdings, skipped = [], [], []
    fm_values, foreign_fund_values, controls = defaultdict(Decimal), defaultdict(Decimal), {}
    counts, values, instrument_types = Counter(), defaultdict(Decimal), Counter()
    record_types, roles, overlaps = Counter(), Counter(), Counter()
    insurers = {}
    seen = set()
    with zipfile.ZipFile(path) as archive:
        # Inspect A/F/C together so a count-only defect can be independently
        # checked. Other families are parsed one file at a time to bound memory.
        candidates, failures = {}, {}
        for member in members:
            if Path(member).name[:1].upper() not in {"A", "F", "C"}:
                continue
            try:
                candidate = parse_file(member, archive.read(member), expected_period=period,
                                       expected_group=group, inspect_count_mismatch=True)
                key = candidate.insurer_rut, candidate.family
                if key in candidates:
                    raise ValueError(f"Duplicate insurer/family filing: {key}")
                candidates[key] = candidate
            except (ValueError, UnicodeError) as exc:
                failures[member] = str(exc)
        reviews = review_counts(candidates)
        recovered, recovery_reviews = recover_envelopes(archive, members, period, group)
        candidates.update(recovered)
        reviews.update(recovery_reviews)
        for file in recovered.values():
            for member in list(failures):
                if Path(member).name == file.filename:
                    failures.pop(member)
        for member in members:
            family = Path(member).name[:1].upper()
            if family not in RECORD_LAYOUTS:
                skipped.append(member)
                continue
            if member.upper() in seen:
                raise ValueError(f"Duplicate archive member: {member}")
            seen.add(member.upper())
            raw = archive.read(member)
            try:
                if member in failures:
                    raise ValueError(failures[member])
                key = str(int(member.rsplit(".", 1)[-1])), family
                parsed = candidates[key] if key in candidates else parse_file(
                    member, raw, expected_period=period, expected_group=group, inspect_count_mismatch=True,
                )
            except (ValueError, UnicodeError) as exc:
                files.append({
                    "filename": member, "family": family, "status": "quarantined",
                    "reason": str(exc), "sha256": hashlib.sha256(raw).hexdigest(),
                    "insurer_rut": str(int(member.rsplit(".", 1)[-1])),
                })
                continue
            review = reviews.get(key, {
                "status": "quarantined" if parsed.count_mismatch else "validated",
                "accepted_lines": [] if parsed.count_mismatch else [r["source_line"] for r in parsed.rows],
                "reason": "Record count mismatch; no independently reconciled category" if parsed.count_mismatch else None,
            })
            accepted_lines = set(review["accepted_lines"])
            accepted_rows = [row for row in parsed.rows if row["source_line"] in accepted_lines]
            if review["status"] in HOLDING_STATUSES or review["status"] == "partially_reconciled":
                insurers[parsed.insurer_rut] = parsed.insurer_name
            files.append({
                "filename": member, "family": family, "status": review["status"], "rows": len(accepted_rows),
                "insurer_rut": parsed.insurer_rut, "insurer_name": parsed.insurer_name,
                "sha256": parsed.sha256,
                "declared_records": parsed.declared_records, "actual_records": parsed.actual_records,
                "count_warning": parsed.count_mismatch, "missing_envelope": parsed.missing_envelope, "source_detail_rows": len(parsed.rows),
                "excluded_detail_rows": len(parsed.rows) - len(accepted_rows),
                "reason": review.get("reason"), "reconciliation": review.get("checks", []),
                "verified_control_codes": review.get("verified_control_codes", []),
            })
            counts[family] += len(accepted_rows)
            for row in accepted_rows:
                record_types[family + ":" + row["record"]] += 1
                roles[row["record_role"]] += 1
                if row["overlap_role"]:
                    overlaps[row["overlap_role"]] += 1
                if record_sink:
                    record_sink({
                        "period": period, "group": group, "insurer_rut": parsed.insurer_rut,
                        "insurer_name": parsed.insurer_name, "family": family,
                        "source_file": member, "file_sha256": parsed.sha256, "archive_sha256": digest,
                        "data_quality": "envelope_warning_reconciled" if parsed.missing_envelope else "count_warning_reconciled" if parsed.count_mismatch else "validated",
                        "fields": row,
                    })
                if "final_value_clp" in row:
                    values[family] += row["final_value_clp"]
                if family == "C":
                    key = parsed.insurer_rut, row["investment_type"]
                    if key in controls:
                        raise ValueError(f"Duplicate control code in {member}: {key}")
                    controls[key] = row["final_value_clp"]
                    continue
                if "instrument_type" in row:
                    instrument_types[family + ":" + row["instrument_type"]] += 1
                if family == "F":
                    fm_values[parsed.insurer_rut] += row["final_value_clp"]
                if family == "X" and row["record"] == "3":
                    code = FOREIGN_FUND_CONTROLS.get(row["instrument_type"])
                    if code:
                        foreign_fund_values[parsed.insurer_rut, code] += row["final_value_clp"]
                if family not in {"A", "F", "X"} or row.get("fund_kind") not in {"fm", "fi", "fip"}:
                    continue
                admin = row["administrator_rut"]
                kind, run = row["fund_kind"], row["fund_run"]
                fund_name = funds.get((kind, run))
                private_rut = row.get("private_fund_rut")
                holdings.append({
                    "period": period, "group": group, "insurer_rut": parsed.insurer_rut,
                    "insurer_name": parsed.insurer_name, "administrator_rut": admin,
                    "administrator_name": administrators.get(admin),
                    "fund_kind": kind, "fund_run": run, "fund_name": fund_name,
                    "fund_match": "matched_run" if fund_name else ("private_fund" if kind == "fip" else "unmatched_run"),
                    "private_fund_rut": str(int(private_rut)) if private_rut else None,
                    "private_fund_name": row.get("private_fund_name"),
                    "instrument_type": row["instrument_type"], "identifier": row["identifier"],
                    "series": row["series"], "currency": row["currency"], "units": row["units"],
                    "final_value_clp": row["final_value_clp"], "reserve_backing": row["reserve_backing"],
                    "insurance_plan": row["insurance_plan"], "portfolio_name": row["portfolio_name"],
                    "source_file": member, "source_line": row["source_line"],
                    "source_family": family,
                    "source_sha256": row["source_sha256"],
                    "data_quality": "envelope_warning_reconciled" if parsed.missing_envelope else "count_warning_reconciled" if parsed.count_mismatch else "validated",
                    "reconciliation_code": ("F11" if family == "A" else "F31") if parsed.count_mismatch else None,
                })
    report = {
        "period": period, "group": group, "archive": str(path), "sha256": digest,
        "members": len(members), "insurers_in_validated_files": len(insurers),
        "accepted_rows": dict(counts), "family_value_clp": dict(values),
        "row_count_definition": "Accepted rows, including explicitly reconciled count warnings; see file statuses.",
        "family_value_definition": "Non-additive reported final values; controls and overlapping holdings must not be summed. Derivative notionals, collateral and cash-flow schedules are separate.",
        "record_types": dict(record_types), "record_roles": dict(roles), "overlap_roles": dict(overlaps),
        "absent_public_families": sorted(set(RECORD_LAYOUTS) - {f["family"] for f in files}),
        "manual_families_not_in_public_pilot": ["R", "V"],
        "files": files, "out_of_pilot_scope": skipped, "instrument_types": dict(instrument_types),
        "fm_control_checks": reconcile_fm(files, fm_values, controls),
        "foreign_fund_control_checks": reconcile_foreign_funds(files, foreign_fund_values, controls),
        "fund_links": dict(Counter(h["fund_match"] for h in holdings)),
        "administrator_labels": dict(Counter("resolved" if h["administrator_name"] else "unresolved" for h in holdings)),
    }
    return report, holdings


def administrator_summary(holdings: list[dict]) -> list[dict]:
    groups = {}
    for holding in holdings:
        key = (holding["period"], holding["administrator_rut"], holding["insurer_rut"])
        if key not in groups:
            groups[key] = {
                k: holding[k] for k in ["period", "administrator_rut", "administrator_name", "insurer_rut", "insurer_name"]
            } | {"observed_value_clp": Decimal(0), "funds": set(), "holdings": 0, "count_warning_holdings": 0}
        item = groups[key]
        item["observed_value_clp"] += holding["final_value_clp"]
        # Private fund RUT is not a public fund RUN.
        item["funds"].add((holding["fund_kind"], holding["fund_run"] or holding["private_fund_rut"]))
        item["holdings"] += 1
        item["count_warning_holdings"] += holding.get("data_quality") == "count_warning_reconciled"
    return sorted(
        [dict(item, funds=len(item["funds"])) for item in groups.values()],
        key=lambda item: (item["period"], -item["observed_value_clp"]),
    )


def compare_holdings(holdings: list[dict], reports: list[dict]) -> list[dict]:
    periods = sorted(r["period"] for r in reports)
    if len(periods) != 2:
        return []
    previous, current = periods
    valid_files = {
        (report["period"], f["insurer_rut"], f["family"])
        for report in reports for f in report["files"] if f["status"] in HOLDING_STATUSES
    }
    aggregates = {}
    key_fields = ["insurer_rut", "administrator_rut", "fund_kind", "fund_run",
                  "private_fund_rut", "series", "currency"]
    for holding in holdings:
        key = tuple(holding[field] for field in key_fields)
        if key not in aggregates:
            aggregates[key] = {
                field: holding[field] for field in key_fields + ["insurer_name", "administrator_name", "fund_name"]
            } | {"values": defaultdict(Decimal), "units": defaultdict(Decimal), "missing_units": set(), "families": set(),
                 "count_warning": False}
        item = aggregates[key]
        item["values"][holding["period"]] += holding["final_value_clp"]
        item["families"].add(holding.get("source_family", "F" if holding["fund_kind"] == "fm" else "A"))
        item["count_warning"] |= holding.get("data_quality") == "count_warning_reconciled"
        if holding["units"] is None:
            item["missing_units"].add(holding["period"])
        else:
            item["units"][holding["period"]] += holding["units"]
    rows = []
    for item in aggregates.values():
        families = item["families"] | {"F" if item["fund_kind"] == "fm" else "A"}
        # Fund positions can move between domestic/foreign-asset files. If X is
        # supplied, both months must have usable X before inferring an exit.
        if any(f["family"] == "X" and f["insurer_rut"] == item["insurer_rut"]
               for report in reports for f in report["files"]):
            families.add("X")
        comparable = all((period, item["insurer_rut"], family) in valid_files
                         for period in periods for family in families)
        row = {key: value for key, value in item.items() if key not in {"values", "units", "missing_units", "families"}}
        row |= {"previous_period": previous, "period": current, "status": "comparable" if comparable else "incomplete_source",
                "previous_value_clp": item["values"].get(previous),
                "value_clp": item["values"].get(current), "delta_value_clp": None, "delta_units": None}
        if comparable:
            row["previous_value_clp"] = item["values"][previous]
            row["value_clp"] = item["values"][current]
            row["delta_value_clp"] = row["value_clp"] - row["previous_value_clp"]
            if not item["missing_units"]:
                row["delta_units"] = item["units"][current] - item["units"][previous]
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--group", choices=["CSVID", "CSGEN"], default="CSVID")
    parser.add_argument("--local-reference", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("output/insurance-pilot"))
    args = parser.parse_args()
    raw_dir = args.output / "raw"
    if args.download:
        client = InsuranceArchiveClient(args.group)
        try:
            latest = client.latest_period()
            year, month = int(latest[:4]), int(latest[4:])
            previous = f"{year if month > 1 else year - 1:04d}{month - 1 if month > 1 else 12:02d}"
            for period in [previous, latest]:
                print(f"Downloading {args.group} {period}", flush=True)
                print(client.download(period, raw_dir), flush=True)
        finally:
            client.close()
    reports, holdings = [], []
    paths = sorted(raw_dir.glob(f"{args.group}-*.zip"))
    if not paths:
        parser.error("No staged archives; use --download first")
    references = read_local_reference(reference_ruts(paths)) if args.local_reference else ({}, {})
    periods = set()
    for path in paths:
        period = path.name.split("-")[1]
        if period in periods:
            parser.error(f"Multiple revisions for {period}: stage one explicit revision per period")
        periods.add(period)
        print(f"Validating {path.name}", flush=True)
        export = args.output / f"records-{args.group}-{period}.jsonl.gz"
        temporary = export.with_suffix(export.suffix + ".part")
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=3) as stream:
            def record_sink(record):
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            report, rows = inspect_archive(path, *references, record_sink=record_sink)
        temporary.replace(export)
        report["parsed_records"] = str(export)
        reports.append(report)
        holdings.extend(rows)
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "specification": SPEC_URL,
        "control_codes": CODES_URL,
        "scope": "All 12 public file families. Not total insurer AUM. Only accepted records are exported; R/V are absent from public downloads.",
        "count_warning_policy": "Count-only defects may recover F11/F31 rows after exact independent reconciliation. Other categories remain excluded. Identity/date conflicts are never overridden.",
        "valuation": "Reported final value converted from thousands of CLP to CLP; not necessarily market value.",
        "administrator_basis": "Reported administrator RUT; current local database supplies labels only.",
        "periods": reports,
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n")
    coverage = []
    for period_report in reports:
        for family in sorted(RECORD_LAYOUTS):
            files = [f for f in period_report["files"] if f["family"] == family]
            coverage.append({
                "period": period_report["period"], "group": period_report["group"], "family": family,
                "source_files": len(files),
                "validated_files": sum(f["status"] == "validated" for f in files),
                "warning_files": sum(f["status"] == "accepted_with_warning" for f in files),
                "partial_control_files": sum(f["status"] == "partially_reconciled" for f in files),
                "quarantined_files": sum(f["status"] == "quarantined" for f in files),
                "accepted_rows": period_report["accepted_rows"].get(family, 0),
                "accepted_record_types": ", ".join(k.split(":")[1] for k in period_report["record_types"] if k.startswith(family + ":")),
            })
    write_csv(args.output / "parser-coverage.csv", coverage, list(coverage[0]))
    fields = list(holdings[0]) if holdings else ["period", "insurer_rut", "fund_run", "final_value_clp"]
    write_csv(args.output / "fund-holdings.csv", holdings, fields)
    summaries = administrator_summary(holdings)
    write_csv(args.output / "administrator-insurers.csv", summaries, [
        "period", "administrator_rut", "administrator_name", "insurer_rut", "insurer_name",
        "observed_value_clp", "funds", "holdings", "count_warning_holdings",
    ])
    changes = compare_holdings(holdings, reports)
    if changes:
        write_csv(args.output / "fund-holding-changes.csv", changes, list(changes[0]))
    count_reviews = []
    for period in reports:
        for file in period["files"]:
            if not file.get("count_warning"):
                continue
            count_reviews.append({
                "period": period["period"], "filename": file["filename"],
                "insurer_name": file["insurer_name"], "declared_records": file["declared_records"],
                "actual_records": file["actual_records"], "status": file["status"],
                "accepted_detail_rows": file["rows"], "excluded_detail_rows": file["excluded_detail_rows"],
                "matched_control_codes": ", ".join(sorted({c["code"] for c in file["reconciliation"] if c["status"] == "matched"})),
                "reason": file["reason"],
            })
    if count_reviews:
        write_csv(args.output / "count-mismatch-review.csv", count_reviews, list(count_reviews[0]))
    for period in reports:
        print(period["period"], "accepted rows", period["accepted_rows"],
              "quarantined files", sum(f["status"] == "quarantined" for f in period["files"]),
              "fund matches", period["fund_links"],
              "FM reconciliation", dict(Counter(c["status"] for c in period["fm_control_checks"])), flush=True)
    print(f"Report and CSVs: {args.output}", flush=True)


if __name__ == "__main__":
    main()

"""Strict, deterministic parser for all 12 public insurance file families."""
from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from .download import MAX_EXPANDED_BYTES
from .layouts import IDENTITY, LAYOUTS, Field
from .extended_layouts import EXTENDED_LAYOUTS, TRAILERS, WIDTHS

RECORD_LAYOUTS = {family: {"2": fields} for family, fields in LAYOUTS.items()} | EXTENDED_LAYOUTS
FILE_WIDTHS = {family: sum(f.width for f in fields) for family, fields in LAYOUTS.items()} | WIDTHS


class FormatError(ValueError):
    pass


@dataclass
class ParsedFile:
    filename: str
    family: str
    group: str
    insurer_rut: str
    insurer_dv: str
    insurer_name: str
    period: str
    sha256: str
    rows: list[dict[str, Any]]
    declared_records: int | None
    actual_records: int
    missing_envelope: bool = False

    @property
    def count_mismatch(self) -> bool:
        return self.declared_records is not None and self.declared_records != self.actual_records


def parse_fields(line: str, fields: tuple[Field, ...]) -> dict[str, Any]:
    offset = 0
    result = {}
    for field in fields:
        raw = line[offset:offset + field.width]
        offset += field.width
        if len(raw) != field.width:
            raise FormatError(f"Truncated field {field.name}")
        if field.scale is None:
            result[field.name] = raw.strip()
            continue
        value = raw.strip()
        if not value:
            result[field.name] = None
        elif not re.fullmatch(r"[+-]?[0-9]+", value):
            raise FormatError(f"Invalid numeric field {field.name}: {value!r}")
        else:
            result[field.name] = Decimal(value).scaleb(-field.scale)
    return result


def parse_file(
    filename: str, data: bytes, *, expected_period: str, expected_group: str,
    inspect_count_mismatch: bool = False,
    envelope_anchor: ParsedFile | None = None,
) -> ParsedFile:
    """Strict by default. Inspection permits only a numeric trailer-count mismatch.

    An explicit envelope anchor permits diagnostic detail-only parsing, never
    approval. envelope_recovery.py must independently reconcile those results.
    Inspection results are not approved holdings; reconciliation.py decides which
    categories, if any, can be recovered. Source records are never rewritten.
    """
    name = PurePosixPath(filename.replace("\\", "/")).name
    match = re.fullmatch(r"([A-Z])(\d{6})([VG])\.(\d{1,9})", name, re.IGNORECASE)
    if not match:
        raise FormatError(f"Unsupported filename: {name}")
    family, file_period, group, rut = match.groups()
    family, group = family.upper(), group.upper()
    if family not in RECORD_LAYOUTS:
        raise FormatError(f"Unsupported file family: {family}")
    expected_letter = {"CSVID": "V", "CSGEN": "G"}.get(expected_group)
    if group != expected_letter:
        raise FormatError(f"Group mismatch: {name}")
    try:
        date(int(file_period[:4]), int(file_period[4:]), 1)
    except ValueError as exc:
        raise FormatError(f"Invalid filename date: {name}") from exc
    if file_period != expected_period:
        raise FormatError(f"Filename period mismatch: {name}")
    if file_period < "202412":
        raise FormatError(f"{name}: historical layout requires a versioned parser")
    # CMF's public exports are UTF-8. Widths are characters: accented insurer/
    # instrument names use more than one byte. Never replace decoding errors.
    lines = data.decode("utf-8").replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    width = FILE_WIDTHS[family]
    if len(lines) < 2 or any(len(line) != width for line in lines):
        sizes = sorted({len(line) for line in lines})
        raise FormatError(f"{name}: expected {width}-character records, found {sizes}")
    missing_envelope = envelope_anchor is not None
    if missing_envelope:
        anchor = envelope_anchor
        if (anchor.insurer_rut, anchor.period, anchor.group) != (str(int(rut)), expected_period, expected_group):
            raise FormatError(f"{name}: envelope anchor identity/period mismatch")
        if anchor.count_mismatch or anchor.missing_envelope or anchor.family not in {"I", "B"}:
            raise FormatError(f"{name}: invalid envelope anchor")
        if any(line[:1] not in RECORD_LAYOUTS[family] for line in lines):
            raise FormatError(f"{name}: recovery requires detail-only source")
        header = {"insurer_dv": anchor.insurer_dv, "insurer_name": anchor.insurer_name}
        insurer_rut = anchor.insurer_rut
        declared_records = None
        detail_lines, first_line = lines, 1
    else:
        if lines[0][:1] != "1" or lines[-1][:1] != TRAILERS.get(family, "3"):
            raise FormatError(f"{name}: missing identification/trailer")
        header = parse_fields(lines[0], IDENTITY)
        if header["insurer_rut"] is None or header["insurer_rut"] <= 0:
            raise FormatError(f"{name}: missing insurer RUT")
        insurer_rut = str(int(header["insurer_rut"]))
        if insurer_rut != str(int(rut)) or header["period"] != expected_period:
            raise FormatError(f"{name}: header disagrees with filename/request")
        if not header["insurer_name"] or not re.fullmatch(r"[0-9Kk]", header["insurer_dv"]):
            raise FormatError(f"{name}: invalid insurer identity")
        if lines[0][77:].strip() or lines[-1][7:].strip():
            raise FormatError(f"{name}: unexpected header/trailer data")
        if not lines[-1][1:7].isascii() or not lines[-1][1:7].isdigit():
            raise FormatError(f"{name}: invalid trailer record count")
        declared_records = int(lines[-1][1:7])
        if declared_records != len(lines) and not inspect_count_mismatch:
            raise FormatError(f"{name}: trailer record count mismatch")
        detail_lines, first_line = lines[1:-1], 2
    rows = []
    for number, line in enumerate(detail_lines, first_line):
        fields = RECORD_LAYOUTS[family].get(line[:1])
        if fields is None:
            raise FormatError(f"{name}:{number}: unexpected record type")
        try:
            row = parse_fields(line, fields)
        except (ValueError, UnicodeError) as exc:
            raise FormatError(f"{name}:{number}: {exc}") from exc
        row["source_line"] = number
        row["source_sha256"] = hashlib.sha256(line.encode("utf-8")).hexdigest()
        if row.get("filler"):
            raise FormatError(f"{name}:{number}: unexpected detail padding")
        row.pop("filler", None)
        if "final_value_mclp" in row:
            if row["final_value_mclp"] is None:
                raise FormatError(f"{name}:{number}: missing final value")
            row["final_value_clp"] = row["final_value_mclp"] * 1000
        if family in {"A", "F"}:
            run = row["fund_run"]
            row["fund_run"] = str(int(run)) if run is not None and run > 0 else None
            if family == "F" and row["fund_run"] is None:
                raise FormatError(f"{name}:{number}: FM without fund RUN")
            is_private = family == "A" and row["instrument_type"].startswith("CFIP")
            row["fund_kind"] = "fm" if family == "F" else ("fi" if row["fund_run"] else ("fip" if is_private else None))
            if family == "A":
                is_fund = row["fund_kind"] is not None
                row["administrator_rut"] = row["issuer_or_admin_rut"] if is_fund else None
                row["administrator_dv"] = row["issuer_or_admin_dv"] if is_fund else None
                row["issuer_rut"] = None if is_fund else row["issuer_or_admin_rut"]
            admin = row.get("administrator_rut")
            row["administrator_rut"] = str(int(admin)) if admin is not None and admin > 0 else None
        if family == "X" and row["record"] == "3":
            run = row["fund_run"]
            row["fund_run"] = str(int(run)) if run is not None and run > 0 else None
            admin = row["administrator_rut"]
            row["administrator_rut"] = str(int(admin)) if admin is not None and admin > 0 else None
            instrument = row["instrument_type"]
            # CFIIE(P)/CFMIE are Chilean funds investing abroad. Overseas funds
            # lack a Chilean RUN and must not be linked by their name/ISIN.
            row["fund_kind"] = (
                "fip" if instrument.startswith("CFIIEP") else
                "fi" if instrument.startswith("CFIIE") and row["fund_run"] else
                "fm" if instrument.startswith("CFMIE") and row["fund_run"] else
                "foreign_fund" if instrument.startswith(("CFE", "CFM", "CFI", "ETF")) else None
            )
        classify_row(family, row)
        rows.append(row)
    if family == "C" and len({row["investment_type"] for row in rows}) != len(rows):
        raise FormatError(f"{name}: duplicate control code")
    return ParsedFile(
        name, family, expected_group, insurer_rut, header["insurer_dv"],
        header["insurer_name"], expected_period, hashlib.sha256(data).hexdigest(), rows,
        declared_records, len(lines), missing_envelope,
    )


def classify_row(family: str, row: dict[str, Any]) -> None:
    """Describe records without inventing a grand total across overlapping files."""
    record = row["record"]
    instrument = row.get("instrument_type", "")
    if family in {"D", "T", "E", "G", "C"}:
        row["record_role"] = {
            "D": "descriptor", "T": "payment_schedule", "E": "eligible_cashflows",
            "G": "collateral", "C": "control",
        }[family]
        row["asset_class"] = None
    elif family == "P":
        row["record_role"] = "repo_contract" if record == "6" else "derivative_contract"
        row["asset_class"] = "repos" if record == "6" else "derivatives"
        row["contract_kind"] = {"2": "option", "3": "forward", "4": "future", "5": "swap", "6": "repo"}[record]
    else:
        row["record_role"] = "holding"
        row["asset_class"] = {
            "I": "fixed_income", "F": "funds", "B": "real_estate", "O": "other",
            "A": "funds" if row.get("fund_kind") else "equities",
            "X": {"2": "fixed_income", "3": "funds" if row.get("fund_kind") else "equities",
                  "4": "real_estate", "5": "subsidiaries"}.get(record),
        }[family]
    row["overlap_role"] = (
        "auxiliary" if family in {"C", "D", "T", "E", "G"} else
        "other_investments_summary" if family == "O" and instrument in {"OIND", "OIED"} else
        "other_investments_detail" if family != "O" and instrument.endswith(("OIND", "OIED")) else
        "leased_property_underlying" if family == "B" and instrument == "CLEAS" else
        "leasing_receivable" if family == "I" and instrument == "CLEAS" else
        "repo_underlying_requires_link" if family == "I" and row.get("repo_value_mclp") else
        "repo_contract_requires_link" if family == "P" and record == "6" else
        None
    )


def parse_archive(path: Path, period: str, group: str = "CSVID") -> tuple[list[ParsedFile], list[str]]:
    parsed, skipped = [], []
    seen = set()
    with zipfile.ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED_BYTES:
            raise FormatError("Expanded archive exceeds limit")
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename.replace("\\", "/")).name
            if name[:1].upper() not in RECORD_LAYOUTS:
                skipped.append(info.filename)
                continue
            key = name.upper()
            if key in seen:
                raise FormatError(f"Duplicate archive member: {name}")
            seen.add(key)
            parsed.append(parse_file(name, archive.read(info), expected_period=period, expected_group=group))
    if not parsed:
        raise FormatError("No supported portfolio files in archive")
    return parsed, skipped

"""Atomic monthly snapshots; archive hashes make repeat imports idempotent."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from src.base import DownloadResult
from src.db.engine import SessionLocal
from .download import PAGE
from .pilot import inspect_archive

PARSER_VERSION = "public-1835-v3"
COLUMNS = """import_id file_id insurer_rut family record_type record_role asset_class aggregation_role
instrument_type identifier display_name issuer_rut issuer_name administrator_rut administrator_name
fund_kind fund_run series currency currency_raw country rating maturity_date purchase_yield market_yield
units nominal final_value_clp source_line source_sha256 data_quality quality_flags fields""".split()


def rut(value):
    return str(int(Decimal(str(value)))) if value not in (None, "") and Decimal(str(value)) > 0 else None


def normalize_record(record, funds, names):
    """Keep original fields; field-level defects do not invent corrected values."""
    fields = record["fields"]
    family = record["family"]
    flags = []
    raw_currency = fields.get("currency") or None
    # CMF USD = Unidad Seguro Dólar. PROM = US dollar. Same convention as
    # industry currency_exposure; do not relabel the insurance unit as USD.
    currency = {"$$": "CLP", "$": "CLP", "CLF": "UF", "PROM": "USD", "US$": "USD",
                "USD": "USD_SEGURO", "DA": "AUD"}.get(raw_currency, raw_currency)
    maturity = None
    if fields.get("maturity_date") not in (None, "", "00000000"):
        try:
            maturity = datetime.strptime(fields["maturity_date"], "%Y%m%d").date().isoformat()
        except ValueError:
            flags.append("invalid_maturity_date")
    instrument = fields.get("instrument_type")
    if instrument in {"CS", "CSOIND"}:
        if not rut(fields.get("syndicated_debtor_rut")) or not fields.get("syndicated_debtor_name"):
            flags.append("missing_syndicated_debtor")
        participation = fields.get("syndicated_participation")
        if participation is None or not 0 < Decimal(participation) <= 100:
            flags.append("unusable_syndicated_participation")
        if not rut(fields.get("lead_rut")) or not fields.get("lead_name"):
            flags.append("missing_lead_bank")
        if fields.get("purchase_yield") is not None and Decimal(fields["purchase_yield"]) == 0:
            flags.append("zero_syndicated_yield")
    overlap = fields.get("overlap_role")
    role = fields["record_role"]
    aggregation = (
        "complementary" if role == "holding" and overlap in {
            "other_investments_detail", "leased_property_underlying", "repo_underlying_requires_link"
        } else "primary" if role == "holding" else
        "contract" if family == "P" else "auxiliary"
    )
    if overlap and overlap != "auxiliary":
        flags.append(overlap)
    issuer = rut(fields.get("issuer_rut"))
    admin = rut(fields.get("administrator_rut"))
    fund_kind, fund_run = fields.get("fund_kind"), fields.get("fund_run")
    fund_name = funds.get((fund_kind, fund_run))
    issuer_name = names.get(issuer) or fields.get("issuer_name") or None
    display = (fund_name or fields.get("private_fund_name") or fields.get("issuer_name")
               or fields.get("subsidiary_name") or fields.get("asset_description")
               or fields.get("asset_code") or fields.get("identifier") or fields.get("underlying_identifier")
               or fields.get("counterparty_name") or fields.get("property_roll")
               or fields.get("investment_type") or instrument or role)
    return dict(
        insurer_rut=record["insurer_rut"], family=family, record_type=fields["record"],
        record_role=role, asset_class=fields.get("asset_class"), aggregation_role=aggregation,
        instrument_type=instrument, identifier=fields.get("identifier") or fields.get("underlying_identifier"),
        display_name=display, issuer_rut=issuer, issuer_name=issuer_name,
        administrator_rut=admin, administrator_name=names.get(admin),
        fund_kind=fund_kind, fund_run=fund_run, series=fields.get("series") or None,
        currency=currency, currency_raw=raw_currency, country=fields.get("country") or None,
        rating=fields.get("rating") or None, maturity_date=maturity,
        purchase_yield=fields.get("purchase_yield"), market_yield=fields.get("market_yield"),
        units=fields.get("units"), nominal=fields.get("current_nominal") or fields.get("nominal"),
        final_value_clp=fields.get("final_value_clp"), source_line=fields["source_line"],
        source_sha256=fields["source_sha256"], data_quality=record["data_quality"],
        quality_flags=json.dumps(flags), fields=json.dumps(fields, ensure_ascii=False, default=str),
    )


def reference_labels(session, ruts):
    funds, names = {}, {}
    for row in session.execute(text("""SELECT run_fondo,nombre_fondo,rut_administradora,
        razon_social_administradora FROM fondo_mutuo""")):
        funds["fm", row[0]] = row[1]
        names[row[2]] = row[3]
    for row in session.execute(text("SELECT run_fondo,razon_social FROM fondos_inversion")):
        funds["fi", row[0]] = row[1]
    if ruts:
        for row in session.execute(text("SELECT rut,razon_social FROM emisores WHERE rut=ANY(:ruts)"),
                                   {"ruts": sorted(ruts)}):
            names.setdefault(row[0], row[1])
    return funds, names


def load_archive(path: Path, *, session=None, labels=None) -> DownloadResult:
    """Read original ZIP, validate, and activate a month in one transaction.

    Previous parsed rows are replaced only after the complete new snapshot has
    loaded. Original archives, file metadata and reports remain reproducible.
    Optional session/labels support isolated transactional integration tests.
    """
    match = re.fullmatch(r"(CSVID|CSGEN)-(\d{6})-([0-9a-f]{12})\.zip", path.name)
    if not match:
        raise ValueError("Expected a hashed monthly insurance archive filename")
    group, period, _ = match.groups()
    if group != "CSVID":
        raise ValueError("CSGEN imports require live source validation before enabling")
    month = date(int(period[:4]), int(period[4:]), 1)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if not digest.startswith(match[3]):
        raise ValueError("Archive content does not match filename hash")
    if session is None:
        with SessionLocal.begin() as db:
            return load_archive(path, session=db)
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"insurance:{group}:{period}"})
    old = session.execute(text("""SELECT id,archive_sha256,parser_version,accepted_records
        FROM insurance_imports WHERE entity_group=:group AND period=:month AND active"""),
        dict(group=group, month=month)).mappings().first()
    if old and old["archive_sha256"] == digest and old["parser_version"] == PARSER_VERSION:
        session.execute(text("UPDATE insurance_imports SET checked_at=now() WHERE id=:id"), {"id": old["id"]})
        return DownloadResult(skipped=1)
    ruts = set()
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as spool:
        def sink(record):
            spool.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
            fields = record["fields"]
            for key in ("issuer_rut", "administrator_rut"):
                value = rut(fields.get(key))
                if value:
                    ruts.add(value)
        report, _ = inspect_archive(path, {}, {}, record_sink=sink)
        count = sum(report["accepted_rows"].values())
        if not count or report["out_of_pilot_scope"] or report["absent_public_families"]:
            raise ValueError("Incomplete/unsupported monthly archive; previous snapshot preserved")
        filings = [(f["insurer_rut"], f["family"]) for f in report["files"]]
        source_insurers = {rut for rut, _ in filings}
        if len(filings) != len(set(filings)) or any(
            {family for rut, family in filings if rut == insurer} != set("IAFBXOPCDTEG")
            for insurer in source_insurers
        ):
            raise ValueError("Missing or duplicated insurer/family filing")
        if old and count < old["accepted_records"] * Decimal("0.8"):
            raise ValueError("Accepted coverage fell over 20%; previous snapshot preserved for review")
        funds, names = labels if labels is not None else reference_labels(session, ruts)
        # Pilot labels were not looked up during parsing; avoid persisting those
        # temporary unmatched-name counters as statements about DB linkage.
        report.pop("fund_links", None)
        report.pop("administrator_labels", None)
        report["persistence_policy"] = "Original archive retained; only accepted records loaded; field-level warnings preserved."
        source_url = PAGE + f"?tipoentidad={group}&fnAjax=descarga&peri={period}"
        fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        metadata = path.with_suffix(".json")
        if metadata.exists():
            provenance = json.loads(metadata.read_text())
            if provenance.get("sha256") != digest:
                raise ValueError("Archive provenance hash mismatch")
            fetched_at = datetime.fromisoformat(provenance["downloaded_at"])
        session.execute(text("""INSERT INTO insurance_archives(sha256,content,source_url,fetched_at,size_bytes)
            VALUES (:sha,:content,:url,:fetched,:size) ON CONFLICT(sha256) DO NOTHING"""),
            dict(sha=digest, content=raw, url=source_url, fetched=fetched_at, size=len(raw)))
        import_id = session.execute(text("""INSERT INTO insurance_imports
            (entity_group,period,archive_sha256,parser_version,accepted_records,report)
            VALUES (:group,:month,:sha,:version,:count,CAST(:report AS jsonb)) RETURNING id"""),
            dict(group=group, month=month, sha=digest, version=PARSER_VERSION, count=count,
                 report=json.dumps(report, default=str))).scalar_one()
        file_ids = {}
        for file in report["files"]:
            file_ids[file["filename"]] = session.execute(text("""INSERT INTO insurance_files
                (import_id,filename,family,insurer_rut,insurer_name,status,sha256,accepted_rows,
                 source_rows,declared_records,actual_records,reason,evidence)
                VALUES (:import_id,:filename,:family,:rut,:name,:status,:sha,:accepted,
                        :source,:declared,:actual,:reason,CAST(:evidence AS jsonb)) RETURNING id"""),
                dict(import_id=import_id, filename=file["filename"], family=file["family"],
                     rut=file["insurer_rut"], name=file.get("insurer_name"), status=file["status"],
                     sha=file["sha256"], accepted=file.get("rows", 0), source=file.get("source_detail_rows"),
                     declared=file.get("declared_records"), actual=file.get("actual_records"),
                     reason=file.get("reason"), evidence=json.dumps(file, default=str))).scalar_one()
        spool.seek(0)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        cursor = session.connection().connection.cursor()
        try:
            inserted = 0
            for line in spool:
                record = json.loads(line)
                row = normalize_record(record, funds, names)
                row.update(import_id=import_id, file_id=file_ids[record["source_file"]])
                writer.writerow([row.get(column) for column in COLUMNS])
                inserted += 1
                if inserted % 2000 == 0:
                    _copy(cursor, buffer)
            if buffer.tell():
                _copy(cursor, buffer)
        finally:
            cursor.close()
        if inserted != count:
            raise ValueError("Parsed/copied row count disagrees")
        if old:
            session.execute(text("UPDATE insurance_imports SET active=false WHERE id=:id"), {"id": old["id"]})
        session.execute(text("UPDATE insurance_imports SET active=true WHERE id=:id"), {"id": import_id})
        if old:
            # Reports/files/original ZIP preserve provenance; avoid duplicating
            # hundreds of MB of parsed rows on every revised monthly download.
            session.execute(text("DELETE FROM insurance_records WHERE import_id=:id"), {"id": old["id"]})
    return DownloadResult(downloaded=1, rows_upserted=count)


def _copy(cursor, buffer):
    buffer.seek(0)
    cursor.copy_expert("COPY insurance_records (" + ",".join(COLUMNS) + ") FROM STDIN WITH (FORMAT CSV)", buffer)
    buffer.seek(0)
    buffer.truncate(0)

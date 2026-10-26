"""Public source regressions: units, multi-record dispatch and safe aggregation."""
from decimal import Decimal
import gzip
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from src.etl.insurance.parser import FormatError, parse_file, RECORD_LAYOUTS
from src.etl.insurance.pilot import inspect_archive, compare_holdings, main, reconcile_foreign_funds

FIXTURES = Path(__file__).parent / "fixtures" / "insurance" / "public_families"


def fixture(kind):
    path = next((FIXTURES / kind).iterdir())
    return path, path.read_bytes()


def parsed(kind, data=None):
    path, original = fixture(kind)
    return parse_file(path.name, original if data is None else data,
                      expected_period="202608", expected_group="CSVID")


def packed(tmp_path, entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in entries:
            archive.writestr(name, raw)
    raw = stream.getvalue()
    path = tmp_path / f"CSVID-202608-{hashlib.sha256(raw).hexdigest()[:12]}.zip"
    path.write_bytes(raw)
    return path


@pytest.mark.parametrize("kind", ["B2", "D2", "E2", "G2", "O2", "P2", "P3", "P4", "P5", "P6", "T2", "X2", "X3"])
def test_real_public_record_provenance_and_dispatch(kind):
    result = parsed(kind)
    record = result.rows[0]
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    evidence = next(r for r in manifest if r["fixture"].startswith(kind + "/"))
    assert record["source_sha256"] == evidence["row_sha256"]
    assert result.family == kind[0]
    assert record["record"] == kind[1]
    assert result.actual_records == result.declared_records == 3


def test_foreign_debt_units_are_not_copied_from_domestic_debt():
    row = parsed("X2").rows[0]
    assert row["identifier"] == "USE0351QAB89"
    assert row["purchase_value_clp"] == 1673407286
    assert row["fair_value_mclp"] == 1254382
    assert row["final_value_clp"] == 1272288000
    assert row["purchase_yield"] == Decimal("6.4478")
    assert row["currency"] == "PROM"  # Preserve the reported denomination code.
    assert row["maturity_date"] == "20321129"
    assert row["asset_class"] == "fixed_income"


def test_property_rent_and_ownership_are_not_money_or_full_property_value():
    row = parsed("B2").rows[0]
    assert row["monthly_rent_uf"] == 635
    assert row["ownership_percent"] == Decimal("6.40")
    assert row["final_value_clp"] == 4533234000  # Already insurer's reported holding.
    assert row["asset_class"] == "real_estate"


def test_foreign_etf_is_a_fund_but_not_a_chilean_run():
    row = parsed("X3").rows[0]
    assert row["instrument_type"] == "ETFA"
    assert row["asset_class"] == "funds"
    assert row["fund_kind"] == "foreign_fund"
    assert row["fund_run"] is None
    assert row["administrator_rut"] is None


def test_auxiliary_values_do_not_become_aum():
    for kind in ["D2", "E2", "G2", "T2"]:
        row = parsed(kind).rows[0]
        assert "final_value_clp" not in row
        assert row["asset_class"] is None
        assert row["overlap_role"] == "auxiliary"
    assert parsed("T2").rows[0]["amortization_percent"] == Decimal("0.154500")
    assert parsed("E2").rows[0]["bucket_1_uf"] == Decimal("127523.030")
    assert parsed("G2").rows[0]["book_value_original"] == 5945000000
    assert parsed("G2").rows[0]["book_value_mclp"] == 5945000
    assert parsed("G2").rows[0]["company_position"] == "RG"


def test_derivatives_preserve_sides_signed_values_and_repo_overlap():
    option = parsed("P2").rows[0]
    assert option["contract_fair_value_mclp"] == -1221537
    assert option["liability_value_mclp"] == 1221537
    forward = parsed("P3").rows[0]
    assert forward["contract_fair_value_mclp"] == -47235
    swap = parsed("P5").rows[0]
    assert (swap["long_currency"], swap["short_currency"]) == ("UF", "PROM")
    assert swap["long_amount"] == Decimal("26196.486")
    assert swap["short_amount"] == 1000000
    assert swap["contract_fair_value_mclp"] == 137801
    for kind in ["P2", "P3", "P4", "P5", "P6"]:
        assert "final_value_clp" not in parsed(kind).rows[0]
    assert parsed("P6").rows[0]["overlap_role"] == "repo_contract_requires_link"


@pytest.mark.parametrize("kind", ["X2", "P3"])
def test_multi_record_trailer_is_not_an_ordinary_detail(kind):
    path, raw = fixture(kind)
    lines = raw.decode().splitlines()
    lines[-1] = "3" + lines[-1][1:]
    with pytest.raises(FormatError, match="missing identification/trailer"):
        parsed(kind, ("\n".join(lines) + "\n").encode())


def test_count_inspection_does_not_allow_padding_identity_or_bad_numeric_fields():
    path, raw = fixture("X2")
    lines = raw.decode().splitlines()
    lines[1] = lines[1][:-1] + "X"
    with pytest.raises(FormatError, match="unexpected detail padding"):
        parse_file(path.name, ("\n".join(lines) + "\n").encode(),
                   expected_period="202608", expected_group="CSVID", inspect_count_mismatch=True)
    bad = raw.replace(b"202608", b"202607", 1)
    with pytest.raises(FormatError, match="header disagrees"):
        parsed("X2", bad)


def test_history_is_not_silently_parsed_with_current_offsets():
    path, raw = fixture("X2")
    with pytest.raises(FormatError, match="historical layout"):
        parse_file(path.name.replace("202608", "202001"), raw.replace(b"202608", b"202001", 1),
                   expected_period="202001", expected_group="CSVID")


def encode_spec_record(family, record, values):
    """Synthetic fixtures for layouts absent from accepted real source files."""
    chunks = []
    for field in RECORD_LAYOUTS[family][record]:
        value = values.get(field.name, record if field.name == "record" else "")
        if field.scale is not None and value != "":
            value = str(int(Decimal(str(value)).scaleb(field.scale))).zfill(field.width)
        chunks.append(str(value).ljust(field.width))
    return "".join(chunks)


@pytest.mark.parametrize("record,values", [
    ("4", {"instrument_type": "BZE", "country": "US", "currency": "USD", "monthly_rent_uf": "20.75",
           "final_value_mclp": 1234, "impairment_mclp": -25}),
    ("5", {"instrument_type": "FEX", "country": "US", "subsidiary_name": "TEST SUBSIDIARY",
           "subsidiary_result_mclp": -432, "subsidiary_participation": "75.25", "final_value_mclp": 1234}),
])
def test_unobserved_foreign_property_and_subsidiary_layouts(record, values):
    path, raw = fixture("X2")
    lines = raw.decode().splitlines()
    lines[1] = encode_spec_record("X", record, values)
    result = parsed("X2", ("\n".join(lines) + "\n").encode()).rows[0]
    assert result["final_value_clp"] == 1234000
    for key, value in values.items():
        assert result[key] == (Decimal(str(value)) if isinstance(result[key], Decimal) else value)


def test_mixed_foreign_detail_records_and_domestic_fund_link(tmp_path):
    path, raw = fixture("X2")
    lines = raw.decode().splitlines()
    fund = encode_spec_record("X", "3", {
        "instrument_type": "CFMIE", "administrator_rut": 96634320, "fund_run": 8049,
        "currency": "USD", "series": "A", "units": "20.75", "final_value_mclp": 1234,
    })
    lines.insert(2, fund)
    lines[-1] = "6000004".ljust(611)
    data = ("\n".join(lines) + "\n").encode()
    rows = []
    report, holdings = inspect_archive(packed(tmp_path, [(path.name, data)]), {("fm", "8049"): "Matched"},
                                      {"96634320": "Administrator"}, record_sink=rows.append)
    assert report["record_types"] == {"X:2": 1, "X:3": 1}
    assert len(rows) == 2
    assert holdings[0]["fund_name"] == "Matched"
    assert holdings[0]["source_family"] == "X"
    assert holdings[0]["final_value_clp"] == 1234000


def test_other_investment_detail_overlap_is_explicit():
    path, raw = fixture("X3")
    lines = raw.decode().splitlines()
    lines[1] = encode_spec_record("X", "3", {"instrument_type": "CFIEOIED", "final_value_mclp": 1})
    row = parsed("X3", ("\n".join(lines) + "\n").encode()).rows[0]
    assert row["overlap_role"] == "other_investments_detail"


def test_new_count_defects_are_reported_but_not_recovered_without_evidence(tmp_path):
    path, raw = fixture("E2")
    blob = raw.replace(b"3000003", b"3000004")
    rows = []
    report, _ = inspect_archive(packed(tmp_path, [(path.name, blob)]), {}, {}, record_sink=rows.append)
    file = report["files"][0]
    assert file["status"] == "quarantined"
    assert file["count_warning"]
    assert file["source_detail_rows"] == 1
    assert file["excluded_detail_rows"] == 1
    assert file["rows"] == 0
    assert rows == []


def test_missing_foreign_file_prevents_inferred_fund_exit():
    holding = {
        "period": "202608", "insurer_rut": "1", "insurer_name": "Insurer",
        "administrator_rut": "2", "administrator_name": "AGF", "fund_kind": "fi", "fund_run": "3",
        "private_fund_rut": None, "fund_name": "Fund", "series": "A", "currency": "USD",
        "source_family": "X", "final_value_clp": 123000, "units": 10,
    }
    reports = [
        {"period": "202608", "files": [{"family": f, "insurer_rut": "1", "status": "validated"} for f in ["A", "X"]]},
        {"period": "202609", "files": [{"family": "A", "insurer_rut": "1", "status": "validated"},
                                     {"family": "X", "insurer_rut": "1", "status": "quarantined"}]},
    ]
    row = compare_holdings([holding], reports)[0]
    assert row["status"] == "incomplete_source"
    assert row["delta_value_clp"] is None
    assert row["value_clp"] is None


def test_cli_exports_every_accepted_field_with_units_and_provenance(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    entries = [(p.name, raw) for kind in ["X2", "P5", "G2", "E2", "T2"] for p, raw in [fixture(kind)]]
    packed(raw_dir, entries)
    monkeypatch.setattr("sys.argv", ["pilot", "--output", str(tmp_path)])
    main()
    with gzip.open(tmp_path / "records-CSVID-202608.jsonl.gz", "rt") as source:
        records = [json.loads(line) for line in source]
    assert {r["family"] for r in records} == {"X", "P", "G", "E", "T"}
    debt = next(r for r in records if r["family"] == "X")
    assert debt["fields"]["final_value_clp"] == "1272288000"
    assert len(debt["file_sha256"]) == len(debt["archive_sha256"]) == 64
    report = json.loads((tmp_path / "report.json").read_text())["periods"][0]
    assert report["out_of_pilot_scope"] == []
    assert report["family_value_clp"] == {"X": "1272288000"}


def test_general_insurance_identity_uses_same_layout_without_reassignment():
    path, raw = fixture("G2")
    result = parse_file(path.name.replace("v.", "g."), raw, expected_period="202608", expected_group="CSGEN")
    assert result.group == "CSGEN"
    with pytest.raises(FormatError, match="Group mismatch"):
        parse_file(path.name, raw, expected_period="202608", expected_group="CSGEN")


def test_foreign_controls_distinguish_absent_category_from_zero_and_mismatch():
    files = [{"insurer_rut": "1", "family": "X", "status": "validated"}]
    values = {("1", "H62"): Decimal(123), ("1", "H64"): Decimal(10)}
    controls = {("1", "H62"): Decimal(123), ("1", "H64"): Decimal(11)}
    checks = {c["code"]: c for c in reconcile_foreign_funds(files, values, controls)}
    assert checks["H62"]["status"] == "matched"
    assert checks["H64"]["status"] == "difference"
    assert checks["H64"]["difference_clp"] == -1
    assert checks["H61"]["status"] == "not_checked"
    files[0]["status"] = "quarantined"
    assert all(c["status"] == "not_checked" for c in reconcile_foreign_funds(files, values, controls))


def test_current_public_parser_families_are_explicit():
    assert set(RECORD_LAYOUTS) == set("AB CDEFGIOPTX".replace(" ", ""))

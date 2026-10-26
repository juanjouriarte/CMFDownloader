from decimal import Decimal
from pathlib import Path
import hashlib
import io
import zipfile
from copy import deepcopy

import pytest

from src.etl.insurance.download import InsuranceArchiveClient, validate_archive, _validate_period
from src.etl.insurance.parser import FormatError, parse_file
from src.etl.insurance.pilot import inspect_archive, reconcile_fm, administrator_summary, compare_holdings
from src.etl.insurance.reconciliation import review_counts

FIXTURES = Path(__file__).parent / "fixtures" / "insurance"


def source(family):
    name = f"{family.lower()}202608v.70015730"
    return name, (FIXTURES / name).read_bytes()


def parse(family, data=None, **kwargs):
    name, original = source(family)
    return parse_file(name, original if data is None else data,
                      expected_period=kwargs.get("period", "202608"),
                      expected_group=kwargs.get("group", "CSVID"))


def test_real_debt_units_rates_and_valuation():
    row = parse("I").rows[0]
    assert row["identifier"] == "BCHIAC1011"
    assert row["currency"] == "UF"
    assert row["nominal"] == Decimal("34000")
    assert row["maturity_date"] == "20261001"
    assert row["issue_rate"] == Decimal("3.5")
    assert row["market_yield"] == Decimal("2.06")
    assert row["final_value_clp"] == Decimal("1409608000")
    # Fair value is already CLP, final value is M$; do not multiply both.
    assert row["fair_value_clp"] == Decimal("1411372065")


def test_real_fund_identifiers_series_and_implied_decimals():
    result = parse("F")
    row = result.rows[0]
    assert result.insurer_rut == "70015730"
    assert row["administrator_rut"] == "96634320"
    assert row["fund_run"] == "8049"
    assert row["series"] == "SMALL"
    assert row["units"] == Decimal("190.1548")
    assert row["unit_value_clp"] == Decimal("4732.0117")
    assert row["final_value_clp"] == 900000
    assert row["fund_kind"] == "fm"


def test_share_issuer_is_not_an_administrator():
    row = parse("A").rows[0]
    assert row["identifier"] == "AGUAS-A"
    assert row["fund_kind"] is None
    assert row["administrator_rut"] is None
    assert row["issuer_rut"] == 61808000
    assert row["fair_value_mclp"] == 116572
    assert row["final_value_clp"] == 116572000


@pytest.mark.parametrize("kind,expected", [
    ("fi", ("9740", "76034728", None)),
    ("fip", (None, "76392725", Decimal("76392725"))),
])
def test_fund_links_keep_public_run_and_private_rut_separate(kind, expected):
    path = next((FIXTURES / "fund" / kind).iterdir())
    row = parse_file(path.name, path.read_bytes(), expected_period="202608", expected_group="CSVID").rows[0]
    assert row["fund_kind"] == kind
    assert (row["fund_run"], row["administrator_rut"], row["private_fund_rut"] or None) == expected
    assert row["issuer_rut"] is None


def test_control_signed_amount_and_unit():
    row = parse("C").rows[0]
    assert row["investment_type"] == "A00"
    assert row["final_value_clp"] == 5731349000


def test_utf8_accented_header_uses_character_not_byte_offsets():
    _, raw = source("F")
    text = raw.decode().replace("MUTUAL DE SEGUROS", "MUTUÁL DE SEGUROS")
    assert parse("F", text.encode()).insurer_name.startswith("MUTUÁL")


@pytest.mark.parametrize("change,expected", [
    (lambda s: s.replace("3000003", "3000004"), "record count"),
    (lambda s: s.replace("202608", "202606", 1), "header disagrees"),
    (lambda s: s.replace("1070015730K", "10992250003", 1), "header disagrees"),
    (lambda s: "\n".join(s.splitlines()[1:]) + "\n", "missing identification"),
    (lambda s: s[:-3] + "\n", "character records"),
    (lambda s: s.replace("00000000001901548", "0000000000190X548"), "numeric field"),
])
def test_quarantine_unreliable_files(change, expected):
    _, raw = source("F")
    with pytest.raises(FormatError, match=expected):
        parse("F", change(raw.decode()).encode())


def test_request_identity_enforced():
    with pytest.raises(FormatError, match="period mismatch"):
        parse("F", period="202609")
    with pytest.raises(FormatError, match="Group mismatch"):
        parse("F", group="CSGEN")


def archive_bytes(entries):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries:
            archive.writestr(name, value)
    return target.getvalue()


def test_archive_validation_html_corruption_and_empty():
    name, data = source("F")
    blob = archive_bytes([(name, data)])
    assert validate_archive(blob) == [name]
    for bad in [b"<html>captcha failed</html>", blob[:30], archive_bytes([])]:
        with pytest.raises(ValueError):
            validate_archive(bad)


def test_report_keeps_bad_file_visible_and_links_only_valid_holdings(tmp_path):
    name, data = source("F")
    cname, cdata = source("C")
    cdata = cdata.replace(b"202608", b"202606", 1)
    blob = archive_bytes([(name, data), (cname, cdata), ("r202608v.70015730", b"out of scope")])
    path = tmp_path / f"CSVID-202608-{hashlib.sha256(blob).hexdigest()[:12]}.zip"
    path.write_bytes(blob)
    report, holdings = inspect_archive(path, {("fm", "8049"): "Fund"}, {"96634320": "AGF"})
    assert [f["status"] for f in report["files"]] == ["validated", "quarantined"]
    assert report["fm_control_checks"][0]["status"] == "not_checked"
    assert holdings[0]["fund_match"] == "matched_run"
    assert holdings[0]["administrator_name"] == "AGF"
    assert len(report["out_of_pilot_scope"]) == 1


def test_reconciliation_has_no_hidden_tolerance():
    files = [{"insurer_rut": "1", "family": family, "status": "validated"} for family in ("F", "C")]
    assert reconcile_fm(files, {"1": Decimal(1000)}, {("1", "F31"): Decimal(1000)})[0]["status"] == "matched"
    check = reconcile_fm(files, {"1": Decimal(1001)}, {("1", "F31"): Decimal(1000)})[0]
    assert check["status"] == "difference" and check["difference_clp"] == 1


def holding(period="202608", units=1, value=1000):
    return dict(period=period, insurer_rut="1", insurer_name="Insurer", administrator_rut="2",
                administrator_name="AGF", fund_kind="fm", fund_run="3", fund_name="Fund",
                private_fund_rut=None, series="A", currency="$$", units=Decimal(units),
                final_value_clp=Decimal(value))


def test_admin_totals_sum_positions_but_count_distinct_funds():
    rows = [holding(), holding(units=2, value=2000)]
    result = administrator_summary(rows)[0]
    assert result["observed_value_clp"] == 3000 and result["funds"] == 1 and result["holdings"] == 2


def test_missing_file_is_not_inferred_as_exit():
    reports = [{"period": period, "files": [{"insurer_rut": "1", "family": "F", "status": status}]}
               for period, status in [("202608", "validated"), ("202609", "quarantined")]]
    result = compare_holdings([holding()], reports)[0]
    assert result["status"] == "incomplete_source" and result["delta_units"] is None
    reports[1]["files"][0]["status"] = "validated"
    result = compare_holdings([holding()], reports)[0]
    assert result["status"] == "comparable" and result["delta_units"] == -1


def test_quantity_change_is_separate_from_valuation_change():
    reports = [{"period": period, "files": [{"insurer_rut": "1", "family": "F", "status": "validated"}]}
               for period in ("202608", "202609")]
    result = compare_holdings([holding(), holding("202609", value=1100)], reports)[0]
    assert result["delta_units"] == 0 and result["delta_value_clp"] == 100


def test_invalid_download_parameters_rejected():
    with pytest.raises(ValueError):
        InsuranceArchiveClient("INVALID")
    for period in ["202613", "../202601", "2026-01", "20260101"]:
        with pytest.raises(ValueError):
            _validate_period(period)


def test_download_validates_captcha_in_same_session_before_fetching_zip(monkeypatch, tmp_path):
    from src.etl.insurance import download
    name, data = source("F")
    blob = archive_bytes([(name, data)])
    calls = []

    class Response:
        def __init__(self, *, text="", payload=None, content=b""):
            self.text, self.payload, self.content = text, payload, content

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

        def iter_content(self, size):
            yield self.content

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Session:
        def get(self, url, **kwargs):
            calls.append(("get", url, kwargs))
            action = kwargs.get("params", {}).get("fnAjax")
            if action == "archi":
                return Response(payload=1)
            if action == "descarga":
                return Response(content=blob)
            return Response(content=b"captcha-image")

        def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            assert kwargs["data"] == {"accion": "valida", "valor": "ABC123"}
            return Response(text="1")

    monkeypatch.setattr(download, "make_session", lambda **kwargs: Session())
    monkeypatch.setattr(download.captcha, "solve", lambda image: "ABC123")
    path = InsuranceArchiveClient().download("202608", tmp_path)
    assert path.read_bytes() == blob
    assert [call[0] for call in calls] == ["get", "get", "post", "get", "get"]
    assert calls[-1][2]["params"]["peri"] == "202608"
    assert calls[-1][2]["params"]["tipoentidad"] == "CSVID"
    assert path.with_suffix(".json").exists()


def count_candidates():
    result = {}
    for path in (FIXTURES / "count_mismatch").iterdir():
        parsed = parse_file(path.name, path.read_bytes(), expected_period="202608",
                            expected_group="CSVID", inspect_count_mismatch=True)
        result[parsed.insurer_rut, parsed.family] = parsed
    return result


def test_count_inspection_is_not_automatic_acceptance():
    path = FIXTURES / "count_mismatch" / "a202608v.76034737"
    raw = path.read_bytes()
    with pytest.raises(FormatError, match="record count"):
        parse_file(path.name, raw, expected_period="202608", expected_group="CSVID")
    candidates = count_candidates()
    a = candidates["76034737", "A"]
    assert (a.declared_records, a.actual_records) == (7, 6)
    assert a.sha256 == hashlib.sha256(raw).hexdigest()
    assert review_counts({("76034737", "A"): a})["76034737", "A"]["status"] == "quarantined"


def test_exact_cross_file_match_recovers_only_verified_categories():
    candidates = count_candidates()
    reviews = review_counts(candidates)
    a, c = reviews["76034737", "A"], reviews["76034737", "C"]
    assert a["status"] == "accepted_with_warning" and len(a["accepted_lines"]) == 4
    assert c["status"] == "partially_reconciled" and len(c["accepted_lines"]) == 1
    assert c["verified_control_codes"] == ["F11"]
    assert a["checks"][0]["detail_clp"] == Decimal("1647244000")
    assert a["checks"][0]["difference_clp"] == 0
    assert a["checks"][0]["detail_count_warning"] and a["checks"][0]["control_count_warning"]


@pytest.mark.parametrize("defect", ["amount", "missing_control", "duplicate", "unknown_type", "negative", "wrong_period"])
def test_no_recovery_without_independent_exact_evidence(defect):
    candidates = count_candidates()
    a, c = candidates["76034737", "A"], candidates["76034737", "C"]
    if defect == "amount":
        next(r for r in c.rows if r["investment_type"] == "F11")["final_value_clp"] += 1
    elif defect == "missing_control":
        c.rows = [r for r in c.rows if r["investment_type"] != "F11"]
    elif defect == "duplicate":
        a.rows.append(deepcopy(a.rows[0]))
    elif defect == "unknown_type":
        a.rows[0]["instrument_type"] = "CFIUNKNOWN"
    elif defect == "negative":
        a.rows[0]["final_value_clp"] *= -1
    else:
        c.period = "202609"
    assert review_counts(candidates)["76034737", "A"]["status"] == "quarantined"


def test_recovery_does_not_include_unverified_share_rows():
    candidates = count_candidates()
    share = parse("A").rows[0]
    share["source_line"] = 100
    candidates["76034737", "A"].rows.append(share)
    review = review_counts(candidates)["76034737", "A"]
    assert review["status"] == "accepted_with_warning"
    assert 100 not in review["accepted_lines"]


@pytest.mark.parametrize("change", [
    lambda s: s.replace("202608", "202606", 1),
    lambda s: s.replace("1076034737K", "10992250003", 1),
    lambda s: "\n".join(s.splitlines()[1:]) + "\n",
])
def test_count_inspection_cannot_bypass_identity_date_or_header_checks(change):
    path = FIXTURES / "count_mismatch" / "a202608v.76034737"
    with pytest.raises(FormatError):
        parse_file(path.name, change(path.read_text()).encode(), expected_period="202608",
                   expected_group="CSVID", inspect_count_mismatch=True)


def test_recovered_holdings_carry_warnings_into_exports_and_comparisons(tmp_path):
    entries = [(p.name, p.read_bytes()) for p in (FIXTURES / "count_mismatch").iterdir()]
    blob = archive_bytes(entries)
    path = tmp_path / f"CSVID-202608-{hashlib.sha256(blob).hexdigest()[:12]}.zip"
    path.write_bytes(blob)
    report, holdings = inspect_archive(path, {}, {})
    assert len(holdings) == 4
    assert all(h["data_quality"] == "count_warning_reconciled" for h in holdings)
    assert sum(r["count_warning_holdings"] for r in administrator_summary(holdings)) == 4
    next_report = deepcopy(report)
    next_report["period"] = "202609"
    next_holdings = [dict(h, period="202609") for h in holdings]
    changes = compare_holdings(holdings + next_holdings, [report, next_report])
    assert all(r["status"] == "comparable" and r["count_warning"] for r in changes)
    assert all(r["delta_units"] == 0 for r in changes)

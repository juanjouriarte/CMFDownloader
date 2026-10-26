"""Persistence and explorer regressions; DB fixtures never modify live holdings."""
from datetime import date
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api import insurance as api
from src.etl.insurance import jobs, loader

FIXTURES = Path(__file__).parent / "fixtures" / "insurance"


def record(**fields):
    return dict(insurer_rut="70015730", family="I", data_quality="validated",
                fields=dict(record="2", record_role="holding", source_line=2,
                            source_sha256="a" * 64, **fields))


def test_normalization_preserves_bad_fields_and_separates_overlaps():
    source = record(instrument_type="CS", syndicated_participation="239.70",
                    syndicated_debtor_rut="0", purchase_yield="0", currency="PROM",
                    final_value_clp="1000", maturity_date="20300230")
    row = loader.normalize_record(source, {}, {})
    assert row["currency"] == "USD" and row["final_value_clp"] == "1000"
    assert row["maturity_date"] is None
    assert set(json.loads(row["quality_flags"])) == {
        "missing_syndicated_debtor", "unusable_syndicated_participation",
        "missing_lead_bank", "zero_syndicated_yield", "invalid_maturity_date"}
    assert json.loads(row["fields"])["syndicated_participation"] == "239.70"
    assert loader.normalize_record(record(currency="USD"), {}, {})["currency"] == "USD_SEGURO"
    for overlap in ("other_investments_detail", "leased_property_underlying", "repo_underlying_requires_link"):
        row = loader.normalize_record(record(overlap_role=overlap), {}, {})
        assert row["aggregation_role"] == "complementary"
    source["family"] = "C"
    source["fields"]["record_role"] = "control"
    assert loader.normalize_record(source, {}, {})["aggregation_role"] == "auxiliary"


@pytest.mark.parametrize("latest,expected", [
    ("202607", []), ("202608", ["202608"]), ("202609", ["202608", "202609"]),
    ("202701", ["202612", "202701"]),
])
def test_job_rolling_window(latest, expected):
    assert jobs.refresh_periods(latest) == expected


def test_job_retains_failed_archive_and_closes_client(tmp_path, monkeypatch):
    path = tmp_path / "source.zip"
    path.write_bytes(b"source")
    class Client:
        closed = False
        def __init__(self, group):
            assert group == "CSVID"
        def latest_period(self):
            return "202608"
        def download(self, period, target):
            return path
        def close(self):
            Client.closed = True
    monkeypatch.setattr(jobs, "InsuranceArchiveClient", Client)
    monkeypatch.setattr(jobs, "DOWNLOADS_DIR", tmp_path)
    def fail(path):
        raise ValueError("rejected")
    monkeypatch.setattr(jobs, "load_archive", fail)
    with pytest.raises(ValueError, match="rejected"):
        jobs.InsurancePortfoliosDownloader().run()
    assert path.exists() and Client.closed
    monkeypatch.setattr(jobs, "load_archive", lambda p: jobs.DownloadResult(skipped=1))
    assert jobs.InsurancePortfoliosDownloader().run().skipped == 1
    assert not path.exists()


def client():
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


@pytest.mark.parametrize("query", [
    "period=2026-09-30", "period=bad", "period=2026-09-01&limit=201",
    "period=2026-09-01&offset=-1", "period=2026-09-01&sort=1;DROP",
    "period=2026-09-01&insurer=abc", "period=2026-09-01&section=all",
    "period=2026-09-01&asset_class=loans", "period=2026-09-01&family=R",
])
def test_api_rejects_invalid_input_before_database(query):
    assert client().get("/industry-workspace/insurance/records?" + query).status_code == 422


def test_daily_job_registered():
    from apscheduler.schedulers.background import BackgroundScheduler
    from src.scheduler import register_jobs
    scheduler = BackgroundScheduler(timezone="America/Santiago")
    register_jobs(scheduler)
    job = scheduler.get_job("insurance_portfolios")
    assert job.max_instances == 1
    assert "hour='8', minute='55'" in str(job.trigger)


@pytest.fixture
def db(monkeypatch):
    if os.getenv("RUN_WORKSPACE_DB_TESTS") != "1":
        pytest.skip("Enable local transactional fixtures")
    from src.db.engine import engine
    from sqlalchemy.orm import Session
    assert engine.url.host in ("localhost", "127.0.0.1", "::1")
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    for table in ("insurance_archives", "insurance_imports", "insurance_files", "insurance_records"):
        session.execute(text(f"CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL) ON COMMIT DROP"))
    monkeypatch.setattr(api, "read", lambda sql, params=None: [
        dict(r) for r in session.execute(text(sql), params or {}).mappings()])
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def archive(tmp_path, period="202608", *, missing=None, revision=""):
    """Combine tiny fixtures into a synthetic complete insurer, only in temp DB."""
    header = (FIXTURES / "i202608v.70015730").read_text().splitlines()[0][:71] + period
    sources = [FIXTURES / f"{f}202608v.70015730" for f in "iafc"]
    sources += [next((FIXTURES / "public_families" / kind).iterdir())
                for kind in ("B2", "D2", "E2", "G2", "O2", "P5", "T2", "X3")]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        target.comment = revision.encode()
        for path in sources:
            family = path.name[0]
            if family == missing:
                continue
            lines = path.read_text().splitlines()
            lines[0] = header + lines[0][77:]
            target.writestr(f"{family}{period}v.70015730", "\n".join(lines) + "\n")
    raw = buffer.getvalue()
    path = tmp_path / f"CSVID-{period}-{hashlib.sha256(raw).hexdigest()[:12]}.zip"
    path.write_bytes(raw)
    return path


def test_atomic_revision_idempotence_and_failed_copy(db, tmp_path, monkeypatch):
    first = archive(tmp_path)
    assert loader.load_archive(first, session=db, labels=({}, {})).rows_upserted == 12
    assert loader.load_archive(first, session=db, labels=({}, {})).skipped == 1
    assert db.scalar(text("SELECT count(*) FROM insurance_records")) == 12
    with pytest.raises(ValueError, match="Incomplete"):
        loader.load_archive(archive(tmp_path, missing="g"), session=db, labels=({}, {}))
    original_copy = loader._copy
    def fail(*args):
        raise RuntimeError("COPY interrupted")
    monkeypatch.setattr(loader, "_copy", fail)
    revised = archive(tmp_path, revision="revision")
    with pytest.raises(RuntimeError, match="COPY interrupted"):
        with db.begin_nested():
            loader.load_archive(revised, session=db, labels=({}, {}))
    assert db.scalar(text("SELECT count(*) FROM insurance_imports")) == 1
    assert db.scalar(text("SELECT count(*) FROM insurance_records")) == 12
    monkeypatch.setattr(loader, "_copy", original_copy)
    assert loader.load_archive(revised, session=db, labels=({}, {})).rows_upserted == 12
    assert db.scalar(text("SELECT count(*) FROM insurance_imports WHERE active")) == 1
    assert db.scalar(text("SELECT count(*) FROM insurance_archives")) == 2
    assert db.scalar(text("SELECT count(*) FROM insurance_files")) == 24
    assert db.scalar(text("SELECT count(*) FROM insurance_records")) == 12


def test_api_snapshot_filters_units_and_contract_currency(db, tmp_path):
    for period in ("202608", "202609"):
        loader.load_archive(archive(tmp_path, period), session=db, labels=({}, {}))
    http = client()
    root = "/industry-workspace/insurance"
    assert http.get(root + "/directory").json()["selected_period"] == "2026-09-01"
    query = "?period=2026-09-01"
    summary = http.get(root + "/summary" + query).json()
    expected = db.scalar(text("""SELECT sum(final_value_clp) FROM insurance_records r
        JOIN insurance_imports i ON i.id=r.import_id
        WHERE i.active AND i.period='2026-09-01' AND aggregation_role='primary'"""))
    assert summary["metrics"]["value_clp"] == expected
    assert len(summary["history"]) == 2
    with_details = http.get(root + "/summary" + query + "&include_complementary=true").json()
    assert with_details["metrics"]["value_clp"] == expected
    rows = http.get(root + "/records" + query + "&asset_class=fixed_income").json()
    assert rows["total"] == 1
    detail = http.get(root + f"/records/{rows['rows'][0]['id']}").json()
    assert detail["source_line"] == 2 and detail["fields"]
    assert detail["final_value_clp"] == float(detail["fields"]["final_value_mclp"]) * 1000
    assert http.get(root + "/records" + query + "&search=%25").json()["total"] == 0
    swaps = http.get(root + "/records" + query + "&section=contracts&currency=USD").json()
    assert swaps["total"] == 1 and swaps["rows"][0]["long_currency"] == "UF"
    assert http.get(root + "/records" + query + "&section=contracts&currency=USD_SEGURO").json()["total"] == 0
    assert http.get(root + "/coverage" + query).json()["summary"]["validated"] == 12


def test_panorama_currency_breakdowns_reconcile_clp_and_filters(db, tmp_path):
    for period in ("202608", "202609"):
        loader.load_archive(archive(tmp_path, period), session=db, labels=({}, {}))
    http = client()
    endpoint = "/industry-workspace/insurance/summary?period=2026-09-01&include_breakdowns=true"
    for extra in ("", "&currency=USD", "&asset_class=funds", "&insurer=70015730", "&include_complementary=true"):
        response = http.get(endpoint + extra)
        assert response.status_code == 200
        data = response.json()
        total = data["metrics"]["value_clp"]
        assert sum(r["value_clp"] or 0 for r in data["currencies"]) == total
        assert sum(r["value_clp"] or 0 for r in data["asset_currencies"]) == total
        for month in data["history"]:
            assert sum(r["value_clp"] or 0 for r in data["currency_history"]
                       if r["period"] == month["period"]) == month["value_clp"]
        if extra == "&currency=USD":
            assert {r["key"] for r in data["currencies"]} == {"USD"}
            assert {r["currency"] for r in data["asset_currencies"]} == {"USD"}
        if extra == "&asset_class=funds":
            assert {r["asset_class"] for r in data["asset_currencies"]} == {"funds"}
    contracts = http.get(endpoint + "&section=contracts").json()
    assert contracts["currency_history"] == contracts["asset_currencies"] == []


def test_position_only_history_does_not_load_fund_nav(db, tmp_path, monkeypatch):
    from src.api import insurance_history
    for period in ("202608", "202609"):
        loader.load_archive(archive(tmp_path, period), session=db, labels=({}, {}))
    def no_nav(*args):
        raise AssertionError("Position-only history must not fetch fund NAV")
    monkeypatch.setattr(insurance_history, "load_bases", no_nav)
    response = client().get("/industry-workspace/insurance/fund-history?"
                            "period=2026-09-01&insurer=70015730&include_fund_metrics=false")
    assert response.status_code == 200
    data = response.json()
    assert data["rows"] and len(data["dates"]) == 2
    assert all(not row["current"]["fund_aum"] for row in data["rows"])
    assert any(row["current"]["value_clp"] is not None for row in data["rows"])
    investors=client().get("/industry-workspace/insurance/fund-history?period=2026-09-01&insurer=70015730&include_fund_metrics=false&include_investors=true").json()
    assert investors["rows"]
    assert all([i["rut"] for i in row["investors"]]==["70015730"] for row in investors["rows"])
    assert all(row["current"]["value_clp"]==row["investors"][0]["current"]["value_clp"] for row in investors["rows"])


def test_portfolio_matrix_totals_pagination_and_history(db, tmp_path):
    for period in ("202608", "202609"):
        loader.load_archive(archive(tmp_path, period), session=db, labels=({}, {}))
    http = client()
    root = "/industry-workspace/insurance"
    query = "?period=2026-09-01"
    expected = http.get(root+"/summary"+query).json()["metrics"]["value_clp"]
    overview = http.get(root+"/portfolio-heatmap"+query).json()
    assert overview["value_clp"] == expected
    assert sum(c["value_clp"] or 0 for c in overview["columns"]) == expected
    classes = [c["asset_class"] for c in overview["columns"]]
    for asset in classes:
        url = root+"/portfolio-heatmap"+query+"&asset_class="+asset
        data = http.get(url).json()
        assert sum(c["value_clp"] or 0 for c in data["cells"]) == data["value_clp"]
        page = http.get(url+"&limit=1").json()
        assert len(page["columns"]) == 1
        assert page["value_clp"] == data["value_clp"]
        assert page["totals"] == data["totals"]
        history = http.get(root+"/portfolio-history"+query+
                           "&insurer=70015730&asset_class="+asset+"&from_month=2026-09-01").json()
        assert history["dates"] == ["2026-09-01"]
        assert sum(r["current"]["value_clp"] or 0 for r in history["rows"]) == data["value_clp"]
        assert all(r["current"]["delta_pct"] == 0 for r in history["rows"]
                   if r["identity_linked"] and r["current"]["value_clp"] > 0)
    usd = http.get(root+"/portfolio-heatmap"+query+"&currency=USD").json()
    assert usd["value_clp"] == http.get(root+"/summary"+query+"&currency=USD").json()["metrics"]["value_clp"]
    invalid = http.get(root+"/portfolio-history"+query)
    assert invalid.status_code == 422
    assert http.get(root+"/portfolio-history"+query+"&insurer=70015730&sort=2026-13-01").status_code == 422
    assert http.get(root+"/portfolio-heatmap"+query+"&limit=99999").status_code == 422


def test_portfolio_identity_deltas_and_sorting_do_not_merge_local_assets(db, tmp_path):
    for period in ("202608", "202609"):
        loader.load_archive(archive(tmp_path, period), session=db, labels=({}, {}))
    # Use actual file FK fixtures but only synthetic positions in temp tables.
    db.execute(text("DELETE FROM insurance_records"))
    for period, values in (("2026-08-01", [100, 200, 300, 400]), ("2026-09-01", [200, 100, 300, 0])):
        iid = db.scalar(text("SELECT id FROM insurance_imports WHERE period=:p"), dict(p=period))
        fid = db.scalar(text("SELECT id FROM insurance_files WHERE import_id=:i AND family='I'"), dict(i=iid))
        for index, value in enumerate(values):
            db.execute(text("""INSERT INTO insurance_records(import_id,file_id,insurer_rut,family,record_type,record_role,
                asset_class,aggregation_role,instrument_type,identifier,display_name,source_sha256,source_line,data_quality,
                quality_flags,fields,final_value_clp,currency)
                VALUES(:iid,:fid,'70015730','I','2','holding','fixed_income','primary','BE',:identifier,:name,
                  :sha,:line,'validated','[]','{}',:value,'UF')"""),
                dict(iid=iid,fid=fid,identifier=f"bond-{index}" if index<3 else None,name=f"Bond {index}",
                     sha="a"*64,line=index+2,value=value))
    http=client()
    endpoint="/industry-workspace/insurance/portfolio-history?period=2026-09-01&insurer=70015730&asset_class=fixed_income&from_month=2026-09-01"
    data=http.get(endpoint+"&sort=2026-09-01&mode=deltaPct&limit=1").json()
    assert data["total"] == 5  # Missing identifiers remain separate, not falsely linked across months.
    assert data["rows"][0]["run"] == "bond-0"
    assert data["rows"][0]["current"]["delta_pct"] == 100
    second=http.get(endpoint+"&sort=2026-09-01&mode=deltaPct&limit=1&offset=1").json()
    assert second["rows"][0]["run"] == "bond-2"
    unlinked=http.get(endpoint).json()["rows"]
    assert all(r["current"]["delta_clp"] is None for r in unlinked if not r["identity_linked"])
    db.execute(text("""UPDATE insurance_files SET status='quarantined'
        WHERE family='X' AND import_id=(SELECT id FROM insurance_imports WHERE period='2026-08-01')"""))
    assert all(r["current"]["delta_clp"] is None for r in http.get(endpoint).json()["rows"])


def test_portfolio_identifier_scope_and_property_collisions(db):
    from src.api.insurance_portfolio import IDENTITY
    fields = ("family", "insurer_rut", "asset_class", "instrument_type", "identifier",
              "series", "currency", "issuer_rut", "country")
    sql = "SELECT ("+IDENTITY+") FROM (SELECT "+", ".join(f"CAST(:{f} AS text) {f}" for f in fields)+", CAST(:fields AS jsonb) fields) r"
    def identity(**updates):
        values = dict(family="I",insurer_rut="1",asset_class="fixed_income",instrument_type="BE",
                      identifier="SAME",series="A",currency="UF",issuer_rut="99",country="CL",fields="{}")
        values.update(updates)
        return db.scalar(text(sql), values)
    assert identity() == identity(insurer_rut="2")  # Exact listed instrument, same issuer/series/currency.
    assert identity(instrument_type="CS") != identity(instrument_type="CS",insurer_rut="2")
    assert identity(currency="UF") != identity(currency="CLP")
    assert identity(identifier="NO APLICA") is None
    assert identity(identifier="000") is None
    a=json.dumps(dict(property_roll="100-2",municipality="1",city="City"))
    b=json.dumps(dict(property_roll="100-2",municipality="2",city="City"))
    assert identity(family="B",fields=a) != identity(family="B",fields=b)
    assert identity(family="B",fields=a) != identity(family="B",fields=a,insurer_rut="2")
    assert identity(family="B",identifier=None,fields='{"property_roll":"000-000"}') is None


def test_fund_heatmap_exact_identity_latest_category_and_no_double_count(db, tmp_path, monkeypatch):
    loader.load_archive(archive(tmp_path), session=db, labels=({}, {}))
    # Shadow effective views only inside this transaction. Simulate multiple
    # category periods; FI and FM deliberately share a RUN to test the join key.
    for kind, category in (("fm", "Accionario"), ("fi", "Deuda")):
        db.execute(text(f"""CREATE TEMP TABLE categoria_{kind}_effective (
            run_fondo text,tipo text,grupo text,nombre_cat text,periodo date) ON COMMIT DROP"""))
        db.execute(text(f"""INSERT INTO categoria_{kind}_effective VALUES
            ('1234','Anterior','Anterior','Anterior','2020-01-01'),
            ('1234',:category,'Nacional',:category,CURRENT_DATE),
            ('1234','Futuro','Futuro','Futuro','2100-01-01')"""), dict(category=category))
    import_id = db.scalar(text("SELECT id FROM insurance_imports WHERE active"))
    file_id = db.scalar(text("SELECT min(id) FROM insurance_files"))
    db.execute(text("DELETE FROM insurance_records"))
    for index, (kind, value, series, role, currency, admin) in enumerate([
        ("fm", 10, "A", "primary", "CLP", "111"),
        ("fm", 20, "B", "primary", "CLP", "111"),
        ("fi", 30, "O", "primary", "USD", "222"),
        ("foreign_fund", 40, "", "primary", "USD", None),
        ("fm", 50, "A", "complementary", "CLP", "111"),
    ]):
        db.execute(text("""INSERT INTO insurance_records(import_id,file_id,insurer_rut,family,
            record_type,record_role,asset_class,aggregation_role,display_name,source_sha256,
            data_quality,source_line,fields,quality_flags,fund_kind,fund_run,identifier,
            final_value_clp,series,currency,administrator_rut)
            VALUES (:import_id,:file_id,'70015730','F','2','holding','funds',:role,
            'Same name','test','validated',:line,'{}','[]',:kind,'1234',:identifier,:value,
            :series,:currency,:admin)"""),
            dict(import_id=import_id, file_id=file_id, role=role, line=index+2, kind=kind,
                 identifier="foreign-isin" if kind=="foreign_fund" else "1234",
                 value=value, series=series, currency=currency, admin=admin))
    http = client()
    endpoint = "/industry-workspace/insurance/fund-heatmap?period=2026-08-01"
    data = http.get(endpoint).json()
    assert data["classification_basis"] == "current_effective"
    assert len(data["funds"]) == 3
    assert sum(f["value_clp"] for f in data["funds"]) == 100
    by_kind = {f["fund_kind"]: f for f in data["funds"]}
    assert by_kind["fm"]["tipo"] == "Accionario"
    assert by_kind["fm"]["series"] == ["A", "B"]
    assert by_kind["fm"]["value_clp"] == 30 and by_kind["fm"]["positions"] == 2
    assert by_kind["fi"]["tipo"] == "Deuda"
    assert by_kind["foreign_fund"]["category"] is None  # Never matches FM/FI by RUN or name.
    assert http.get(endpoint+"&administrator=111").json()["funds"] == [by_kind["fm"]]
    assert len(http.get(endpoint+"&currency=USD").json()["funds"]) == 2
    assert http.get(endpoint+"&insurer=999").json()["funds"] == []
    assert http.get(endpoint+"&search=%25").json()["funds"] == []
    assert http.get(endpoint.replace("2026-08-01", "2026-08-31")).status_code == 422
    # A new manual/effective category is visible without reimporting holdings.
    db.execute(text("UPDATE categoria_fm_effective SET tipo='Balanceado' WHERE periodo=CURRENT_DATE"))
    assert next(f for f in http.get(endpoint).json()["funds"] if f["fund_kind"]=="fm")["tipo"] == "Balanceado"
    from src.api import insurance_history
    monkeypatch.setattr(insurance_history, "load_bases", lambda *args: ({}, {}))
    history = "/industry-workspace/insurance/fund-history?period=2026-08-01"
    data = http.get(history+"&months=3&tipo=Balanceado").json()
    assert data["dates"] == ["2026-08-01"]
    assert data["loaded_months"] == 1 and data["requested_months"] == 3
    assert len(data["rows"]) == 1 and data["rows"][0]["current"]["value_clp"] == 30
    assert data["rows"][0]["current"]["delta_clp"] is None
    for query in ("months=61", "months=0", "from_month=2026-09-01", "from_month=2026-08-31"):
        assert http.get(history+"&"+query).status_code == 422


def test_issuer_rut_grouping_drilldown_keeps_category_denominator(db,tmp_path):
    loader.load_archive(archive(tmp_path,'202609'),session=db,labels=({},{}))
    db.execute(text('DELETE FROM insurance_records'))
    iid=db.scalar(text('SELECT id FROM insurance_imports WHERE active'))
    fid=db.scalar(text("SELECT id FROM insurance_files WHERE import_id=:i AND family='I'"),dict(i=iid))
    for idx,(issuer,identifier,value) in enumerate([('99','BOND-A',100),('99','BOND-B',200),('88','BOND-C',300),(None,'LOAN-1',400),(None,'LOAN-2',500)]):
        db.execute(text("""INSERT INTO insurance_records(import_id,file_id,insurer_rut,family,record_type,record_role,
          asset_class,aggregation_role,instrument_type,identifier,display_name,issuer_rut,issuer_name,source_sha256,
          source_line,data_quality,quality_flags,fields,final_value_clp,currency)
          VALUES(:iid,:fid,'70015730','I','2','holding','fixed_income','primary','BE',:identifier,'Same name',
            :issuer,'Same name',:sha,:line,'validated','[]','{}',:value,'UF')"""),
          dict(iid=iid,fid=fid,identifier=identifier,issuer=issuer,sha='a'*64,line=idx+2,value=value))
    http=client();url='/industry-workspace/insurance/portfolio-heatmap?period=2026-09-01&asset_class=fixed_income'
    grouped=http.get(url+'&group_by=issuer').json()
    assert grouped['total_columns']==4  # 2 RUT groups, 2 distinct missing-RUT positions.
    assert next(c for c in grouped['columns'] if c['issuer_rut']=='99')['value_clp']==300
    drill=http.get(url+'&issuer=99').json()
    assert drill['total_columns']==2 and sum(c['value_clp'] for c in drill['columns'])==300
    assert drill['totals'][0]['value_clp']==1500 and drill['value_clp']==1500
    assert http.get(url+'&group_by=name').status_code==422
    assert http.get(url+'&issuer=abc').status_code==422


def test_directory_administrators_only_include_accepted_fund_holdings(db,tmp_path):
    loader.load_archive(archive(tmp_path,'202609'),session=db,labels=({},{}))
    db.execute(text("UPDATE insurance_records SET administrator_rut='222',administrator_name='Non-fund administrator'"))
    db.execute(text("""UPDATE insurance_records SET administrator_rut='111',administrator_name='Fund AGF'
      WHERE asset_class='funds' AND aggregation_role='primary'"""))
    admins=client().get('/industry-workspace/insurance/directory?period=2026-09-01').json()['administrators']
    assert [a['rut'] for a in admins]==['111']

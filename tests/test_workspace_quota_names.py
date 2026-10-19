"""Read-only SQL fixtures for quota naming; no persistent data is changed."""
import os
from importlib import import_module

import pytest

api = import_module('src.api.industry_workspace.router')


def test_quota_lookup_is_unambiguous_and_preserves_positions():
    if os.getenv('RUN_WORKSPACE_DB_TESTS') != '1':
        pytest.skip('Enable RUN_WORKSPACE_DB_TESTS=1 for read-only PostgreSQL fixtures')
    fixtures = """WITH
    fondo_mutuo(run_fondo,nombre_fondo,razon_social_administradora) AS (
        VALUES ('owner','Owning Fund','AGF'),('fm1','Mutual Fund','Other AGF')),
    fondos_inversion(run_fondo,razon_social,administrador) AS (
        VALUES ('fi1','Investment Fund','AGF'),('fi2','Another Fund','AGF')),
    nemotecnicos(nemotecnico,run_fondo,nombre_fondo) AS (
        VALUES ('CFM-A','fm1','Old name'),('CFICLASH','fm1','Mutual Fund')),
    nemotecnicos_fi(nemotecnico,run_fondo,nombre_fondo) AS (
        VALUES ('CFI-A','fi1','Old FI name'),('CFIA','fi1','Old FI name'),
               ('CFI-B','fi1','Investment Fund'),('CFIB','fi2','Another Fund'),
               ('CFICLASH','fi1','Investment Fund'),('CFIORPHAN','fi3','Registry Only Fund')),
    emisores(rut,razon_social) AS (VALUES ('rut','Reported Issuer')),
    ref_codes(domain,code,name) AS (
        VALUES ('instrument','CFI','Cuotas FI'),('instrument','CFM','Cuotas FM')),
    p(kind,run,identifier,instrument_type,issuer_rut,issuer_name,country_code,weight) AS (
        VALUES ('fm','owner',' cfi - a ','CFI','rut',NULL::text,'CL',5),
               ('fm','owner','CFM-A','CFM','rut',NULL,'CL',6),
               ('fm','owner','CFI-B','CFI','rut',NULL,'CL',7),
               ('fm','owner','CFICLASH','CFI','rut',NULL,'CL',8),
               ('fm','owner','UNKNOWN','CFIE','rut',NULL,'CL',9),
               ('fm','owner','CFI-A','B','rut',NULL,'CL',10),
               ('fm','owner',NULL,'CFI','rut',NULL,'CL',11),
               ('fm','owner','CFIORPHAN','CFI','rut',NULL,'CL',12))
    """
    result = api.rows(fixtures + api.ENRICH + ' ORDER BY weight')
    assert len(result) == 8
    assert [r['weight'] for r in result] == list(range(5, 13))
    assert [r['underlying_fund_name'] for r in result] == [
        'Investment Fund', 'Mutual Fund', None, None, None, None, None, 'Registry Only Fund']
    assert [r['underlying_fund_kind'] for r in result[:2]] == ['fi', 'fm']
    assert result[0]['underlying_fund_run'] == 'fi1'
    assert result[0]['identifier'] == ' cfi - a '
    assert result[0]['instrument_name'] == 'Cuotas FI'
    assert all(r['fund_name'] == 'Owning Fund' and r['issuer'] == 'Reported Issuer' for r in result)

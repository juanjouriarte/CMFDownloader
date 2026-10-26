"""Real SURA exports and adversarial changes: never silently bypass quarantine."""
import hashlib
import io
from pathlib import Path
from zipfile import ZipFile

import pytest

from src.etl.insurance.envelope_recovery import recover_envelopes, RUT
from src.etl.insurance.parser import FormatError, parse_file
from src.etl.insurance.pilot import inspect_archive

FIXTURES = Path(__file__).parent / 'fixtures' / 'insurance'


def archive(period='202608', change=None):
    buffer = io.BytesIO()
    with ZipFile(FIXTURES / f'sura-{period}.zip') as source, ZipFile(buffer, 'w') as dest:
        for name in source.namelist():
            raw = source.read(name)
            if change:
                raw = change(Path(name).name[0].upper(), raw)
            dest.writestr(name, raw)
    buffer.seek(0)
    return ZipFile(buffer)


@pytest.mark.parametrize('period,count', [('202608', 31), ('202609', 30)])
def test_source_recovery_preserves_provenance_and_only_verified_controls(period, count):
    with archive(period) as z:
        files, reviews = recover_envelopes(z, z.namelist(), period, 'CSVID')
        assert set(files) == {(RUT, f) for f in 'ACFOX'}
        file = files[RUT, 'F']
        assert len(file.rows) == count
        assert file.declared_records is None
        assert file.actual_records == count
        assert file.missing_envelope and not file.count_mismatch
        from src.etl.insurance.reconciliation import review_counts
        assert review_counts(files)[RUT, 'F']['status'] == 'quarantined'
        name = next(n for n in z.namelist() if Path(n).name[0].upper() == 'F')
        raw = z.read(name)
        assert file.sha256 == hashlib.sha256(raw).hexdigest()
        assert file.rows[0]['source_line'] == 1
        assert file.rows[0]['source_sha256'] == hashlib.sha256(raw.decode().splitlines()[0].encode()).hexdigest()
        assert reviews[RUT, 'F']['status'] == 'accepted_with_warning'
        assert len(reviews[RUT, 'F']['checks']) == 6  # five anchors + fund control
        assert 'J00' not in reviews[RUT, 'C']['verified_control_codes']
        with pytest.raises(FormatError, match='identification/trailer'):
            parse_file(name, raw, expected_period=period, expected_group='CSVID')


@pytest.mark.parametrize('family', ['I', 'B', 'C'])
def test_identity_anchor_or_control_mutation_blocks_recovery(family):
    def change(f, raw):
        if f != family:
            return raw
        if f == 'C':
            return raw.replace(b'2A00000018893798', b'2A00000018893799', 1)
        return raw.replace(b'202608', b'202607', 1)
    with archive(change=change) as z:
        assert recover_envelopes(z, z.namelist(), '202608', 'CSVID') == ({}, {})


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'numeric', 'total', 'header'])
def test_bad_fund_file_is_not_recovered(mutation):
    def change(f, raw):
        if f != 'F':
            return raw
        lines = raw.decode().splitlines()
        if mutation == 'duplicate': lines.append(lines[0])
        elif mutation == 'missing': lines.pop()
        elif mutation == 'numeric': lines[0] = lines[0][:1] + 'X' + lines[0][2:]
        elif mutation == 'header': lines[0] = '1' + lines[0][1:]
        else:
            # Exact fixed-width final_value field; one thousand pesos off.
            from src.etl.insurance.layouts import LAYOUTS
            offset = 0
            for field in LAYOUTS['F']:
                if field.name == 'final_value_mclp':
                    value = int(lines[0][offset:offset+field.width]) + 1
                    lines[0] = lines[0][:offset] + str(value).zfill(field.width) + lines[0][offset+field.width:]
                    break
                offset += field.width
        return ('\n'.join(lines)+'\n').encode()
    with archive(change=change) as z:
        files, reviews = recover_envelopes(z, z.namelist(), '202608', 'CSVID')
        assert (RUT, 'F') not in files
        assert 'F31' not in reviews[RUT, 'C']['verified_control_codes']
        assert (RUT, 'A') in files


def test_unaudited_period_and_group_rejected():
    with archive() as z:
        assert recover_envelopes(z, z.namelist(), '202610', 'CSVID') == ({}, {})
        assert recover_envelopes(z, z.namelist(), '202608', 'CSGEN') == ({}, {})


def test_pilot_retains_warning_and_original_lines(tmp_path):
    raw = (FIXTURES / 'sura-202608.zip').read_bytes()
    path = tmp_path / f'CSVID-202608-{hashlib.sha256(raw).hexdigest()[:12]}.zip'
    path.write_bytes(raw)
    records = []
    report, holdings = inspect_archive(path, {}, {}, record_sink=records.append)
    status = {f['family']: f for f in report['files']}
    assert status['I']['status'] == status['B']['status'] == 'validated'
    assert status['F']['status'] == status['X']['status'] == 'accepted_with_warning'
    assert status['F']['declared_records'] is None
    assert status['F']['missing_envelope']
    assert status['C']['rows'] == 18
    assert len(holdings) == 47
    assert {h['data_quality'] for h in holdings} == {'envelope_warning_reconciled'}
    assert next(r for r in records if r['family'] == 'F')['fields']['source_line'] == 1

"""Audited SURA detail-only exports, August/September 2026.

Missing headers are never treated as valid declarations. Identity is corroborated
by two strict same-archive I/B headers and five exact control totals; every
recovered holding must additionally match its own control bucket. Original lines,
hashes and absent declared counts are preserved. Other issuers/months fail closed.
Mappings: CMF Circular 1835 control investment types (tinver.php).
"""
from decimal import Decimal
from pathlib import PurePosixPath

from .parser import FormatError, parse_file

# Explicitly reviewed source scope, not a general missing-header exception.
RUT = "96549050"
PERIODS = {"202608", "202609"}
ANCHORS = {
    "I": {"BCU": "A00", "BTU": "A00", "PDBC": "A00", "BB": "B20", "BU": "B20",
          "MHA": "D20", "BE": "E10", "BS": "E10"},
    "B": {"BZ": "X10"},
}
BUCKETS = {
    "A": {"ACCR": "Z20", "CFI": "F11"},
    "F": {"CFM": "F31"},
    "O": {"CTE": "H91", "ATP": "LL0", "CT": "Z10", "CJ": "Z30",
          "IMEQ": "Z50", "IMMM": "Z50", "IMOT": "Z50"},
    "X": {"BEEC": "H40", "CFMIE": "H64", "CFME": "H63", "ACE": "H50", "ETFA": "H65", "ETFB": "H65"},
}


def match_buckets(file, control, mapping):
    """Require complete known-type coverage and exact positive totals, no tolerance."""
    if not file.rows or len({r['source_sha256'] for r in file.rows}) != len(file.rows):
        raise FormatError("Empty or duplicate recovery details")
    totals = {}
    for row in file.rows:
        code = mapping.get(row.get("instrument_type"))
        value = row.get("final_value_clp")
        if code is None or value is None or value < 0:
            raise FormatError("Unsupported recovery instrument/value")
        totals[code] = totals.get(code, Decimal(0)) + value
    controls = {r["investment_type"]: r["final_value_clp"] for r in control.rows}
    # All audited buckets must be present, including identity anchors.
    if set(totals) != set(mapping.values()):
        raise FormatError("Missing recovery bucket")
    checks = []
    for code, value in totals.items():
        if value <= 0 or controls.get(code) != value:
            raise FormatError(f"Unreconciled recovery bucket {code}")
        checks.append(dict(code=code, status="matched", detail_file=file.filename,
                           detail_sha256=file.sha256, control_file=control.filename,
                           control_sha256=control.sha256, detail_clp=value,
                           control_clp=value, difference_clp=Decimal(0)))
    return checks


def recover_envelopes(archive, members, period, group):
    if period not in PERIODS or group != "CSVID":
        return {}, {}
    names = {}
    for member in members:
        name = PurePosixPath(member.replace('\\', '/')).name
        if name.lower().endswith(f"{period}v.{RUT}"):
            family = name[0].upper()
            if family in names:
                return {}, {}
            names[family] = member
    try:
        anchors = {f: parse_file(names[f], archive.read(names[f]), expected_period=period,
                                 expected_group=group) for f in ANCHORS}
        if len({(f.insurer_rut, f.insurer_dv, f.insurer_name) for f in anchors.values()}) != 1:
            return {}, {}
        anchor = anchors["I"]
        control = parse_file(names["C"], archive.read(names["C"]), expected_period=period,
                             expected_group=group, envelope_anchor=anchor)
        evidence = [check for f, mapping in ANCHORS.items()
                    for check in match_buckets(anchors[f], control, mapping)]
    except (KeyError, ValueError, UnicodeError):
        return {}, {}
    recovered, reviews, verified = {}, {}, set()
    for family, mapping in BUCKETS.items():
        try:
            file = parse_file(names[family], archive.read(names[family]), expected_period=period,
                              expected_group=group, envelope_anchor=anchor)
            checks = match_buckets(file, control, mapping)
        except (KeyError, ValueError, UnicodeError):
            continue
        key = RUT, family
        recovered[key] = file
        verified.update(c['code'] for c in checks)
        reviews[key] = dict(status="accepted_with_warning",
            accepted_lines=[r['source_line'] for r in file.rows], checks=evidence + checks,
            verified_control_codes=[c['code'] for c in checks],
            reason="Missing identification/trailer; identity corroborated by strict I/B headers and five exact control totals; all holdings reconciled")
    if recovered:
        verified.update(c['code'] for c in evidence)
        recovered[RUT, "C"] = control
        reviews[RUT, "C"] = dict(status="partially_reconciled",
            accepted_lines=[r['source_line'] for r in control.rows if r['investment_type'] in verified],
            checks=evidence + [c for k, v in reviews.items() for c in v['checks'][len(evidence):]],
            verified_control_codes=sorted(verified),
            reason="Missing identification/trailer; only independently matched control categories are usable")
    return recovered, reviews

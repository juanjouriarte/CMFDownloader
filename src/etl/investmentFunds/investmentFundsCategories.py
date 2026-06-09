"""
Clasificación de Fondos de Inversión (FI) basada en composición de cartera IFRS trimestral.

Ponderador: pct_activo_fondo (% del patrimonio neto del fondo que representa cada posición).

Categorías (alineadas con ACAFI):
  Alternativo — Capital Privado
    FI_PE                — Private Equity / Buyout
    FI_DEUDA_PRIVADA     — Deuda Privada / Private Credit / Mezzanine
    FI_VC                — Venture Capital / Growth
    FI_SECONDARY         — Fondos Secundarios
  Alternativo — Inmobiliario
    FI_INMOB_HIPOTECARIO — Hipotecario (mutuos hipotecarios, MBS)
    FI_INMOB_DESARROLLO  — Desarrollo Inmobiliario
    FI_INMOB_RENTA       — Renta Inmobiliaria (activos estabilizados)
  Alternativo — Infraestructura
    FI_INFRA             — Infraestructura (transporte, concesiones)
    FI_ENERGIA           — Energía (renovable, eléctrica)
    FI_FORESTAL          — Forestal y Agrícola
  Accionario
    FI_ACC_NAC           — Renta Variable Nacional
    FI_ACC_NAC_LC        — Renta Variable Nacional Large Cap
    FI_ACC_NAC_SC        — Renta Variable Nacional Small/Mid Cap
    FI_ACC_INT           — Renta Variable Internacional
    FI_ACC_INT_LC        — Renta Variable Internacional Large Cap
    FI_ACC_INT_SC        — Renta Variable Internacional Small/Mid Cap
  Deuda
    FI_DEUDA_NAC         — Deuda Nacional
    FI_DEUDA_INT         — Deuda Internacional
  Fondo de Fondos
    FI_FOF               — Fondo de Fondos
  Balanceado
    FI_MULTIACTIVO       — Multiactivo
  Otro
    FI_OTRO              — Sin datos suficientes para clasificar
"""
from __future__ import annotations

import logging
import re
from datetime import date

import pandas as pd
from sqlalchemy import text

from src.db.engine import SessionLocal

logger = logging.getLogger(__name__)

# Run fondo of the IPSA-tracking ETF used as dynamic IPSA constituent proxy
IPSA_PROXY_RUN_FONDO = "10748"  # ETF SINGULAR IPSA

# IPSA size thresholds (ratio of equity portfolio in IPSA stocks)
IPSA_LC_THRESHOLD = 0.65   # >= 65% of equity is IPSA → Large Cap
IPSA_SC_THRESHOLD = 0.25   # <= 25% of equity is IPSA → Small Cap

# ---------------------------------------------------------------------------
# Instrument type → asset group
# ---------------------------------------------------------------------------

INMOB_NAC  = {"MH", "OIIF"}
PE_NAC     = {"PE"}
PE_EXT     = {"PEE"}
EQUITY_NAC = {"ACC", "ACN", "ACIN", "ACCR"}
EQUITY_EXT = {"ACE", "ETFA", "ADR", "ACNE"}
DEBT_NAC   = {"BB", "BE", "BTP", "BTU", "BS", "DPC", "PDBC", "BNEE", "BU",
              "BH", "BC", "BCP", "DPSA", "DPL", "LH", "BVL", "OTDN", "OTROD"}
DEBT_EXT   = {"ETFB", "BEBCE", "BEE", "BBFE", "OTDNE", "OTE", "DPBFE", "DPSAE"}
FOF_ALL    = {"CFM", "CFI", "CFIP", "CFME", "CFIE"}

# ---------------------------------------------------------------------------
# Name-based heuristics
# ---------------------------------------------------------------------------

# Infraestructura subcategories
_FORESTAL_RE = re.compile(
    r"FORESTAL|AGRICOL|AGRO\b|AGRI.?FOOD|MADERA|SILV|TIMBER|FOREST|CAMPO|GANADERO|VITIVIN",
    re.IGNORECASE,
)
_ENERGIA_RE = re.compile(
    r"ENERG[IÍ]A|SOLAR|E[ÓO]LIC|RENOVABLE|ELECTRICIDAD|TRANSMISI[ÓO]N|HIDRO|GAS\b|TERMOEL",
    re.IGNORECASE,
)
# Default infra: INFRAESTRUCTURA, CONCESION, AUTOPISTA, TRANSPORTE (caught by _INFRA_RE below)

# Inmobiliario subcategories
_INMOB_HIPOTECARIO_RE = re.compile(
    r"HIPOTECAR|MBS|SUBSIDIO|HABITACIONAL|VIVIENDA|RESIDENCIAL",
    re.IGNORECASE,
)
_INMOB_RENTA_RE = re.compile(
    r"RENTA INDUSTRIAL|RENTA INMOBILIARIA|RENTA COMERCIAL|RENTA RESIDENCIAL|"
    r"STRIP CENTER|MULTIFAMILY|NEORENTAS|PARQUE INDUSTRIAL|PARQUE CHICUREO|"
    r"PARQUE LOG|OFICINAS|BODEGA|LOGIST|REALTY",
    re.IGNORECASE,
)

# Capital Privado subcategories
_SECONDARY_RE = re.compile(r"SECONDAR|SECUNDAR", re.IGNORECASE)
_VC_RE = re.compile(
    r"\bVC\b|VENTURE|FINTECH|TELCO|\bTECH\b|TECNOLOG|INNOVATION|INNOVACI[ÓO]N|"
    r"IMPACTO|START.?UP|EMPRENDIMIENTO",
    re.IGNORECASE,
)
_DEUDA_PRIVADA_RE = re.compile(
    r"DEUDA PRIVADA|DEUDA DIRECTA|PRIVATE DEBT|PRIVATE CREDIT|"
    r"MEZZANINE|MEZANINE|SINDICADO|FINANCIAMIENTO|STRUCTURED|ESTRUCTURADO|"
    r"\bBDC\b|DIRECT LEND|HIGH YIELD|CORTO PLAZO",
    re.IGNORECASE,
)

# Equity size subcategories
_LARGE_CAP_RE = re.compile(
    r"LARGE.?CAP|LARGE CAP|\bIPSA\b|S&P.?500|SP500|NASDAQ|BLUE.?CHIP|MEGA.?CAP|"
    r"LEADERS|CONVICTION|CORE US|TOTAL WORLD|GLOBAL LEADERS",
    re.IGNORECASE,
)
_SMALL_CAP_RE = re.compile(
    r"SMALL.?CAP|SMALL AND MID|MID.?&.?SMALL|SMALL.?&.?MID|MID.?CAP|"
    r"RUSSELL|MICRO.?CAP",
    re.IGNORECASE,
)

# Top-level category detection (for OTROC-dominated funds)
# NOTE: _PE_ANY_RE must be checked BEFORE _EQUITY_RE to avoid "PRIVATE EQUITY" → Accionario
_PE_ANY_RE = re.compile(
    r"PRIVATE EQUITY|CAPITAL PRIVADO|\bPE\b|SECONDAR|SECUNDAR|BUYOUT|"
    r"MID MARKET|\bVC\b|VENTURE|DEUDA PRIVADA|DEUDA DIRECTA|PRIVATE DEBT|"
    r"PRIVATE CREDIT|FINANCIAMIENTO|SINDICADO|MEZZANINE|MEZANINE|IMPACTO|"
    r"ALTERNATIVES|ALTERNATIVOS|ESTRUCTURADO|STRUCTURED|\bBDC\b|"
    r"AGRI.?FOOD|AGRICOL|FINTECH|TELCO|\bTECH\b",
    re.IGNORECASE,
)
_INFRA_RE = re.compile(
    r"INFRAESTRUCTURA|INFRASTRUCTURE|CONCESI[ÓO]N|AUTOPISTA|TRANSPORTE",
    re.IGNORECASE,
)
_INMOB_ANY_RE = re.compile(
    r"INMOBILI|INMUEBLE|HIPOTECAR|RESIDENCIAL|VIVIENDA|HABITACIONAL|"
    r"REAL ESTATE|REALTY|MBS|RENTA INDUSTRIAL|DESARROLLO INDUSTRIAL|"
    r"RENTA INMOBILIARIA|PARQUE INDUSTRIAL|MULTIFAMILY|STRIP CENTER|"
    r"OFICINAS|BODEGA|LOGIST|RENTA COMERCIAL|DESARROLLO COMERCIAL|"
    r"CEMENTERIO|PARQUE CHICUREO|NEORENTAS",
    re.IGNORECASE,
)
_EQUITY_RE = re.compile(
    r"ACCIONES|ACCIONARIO|RENTA VARIABLE|BOLSA|ETF|"
    r"(?<!PRIVATE )(?<!CAPITAL )EQUITY",   # avoid matching PRIVATE EQUITY / CAPITAL PRIVADO
    re.IGNORECASE,
)
_DEBT_RE = re.compile(
    r"\bDEUDA\b|RENTA FIJA|BONOS|FIXED INCOME|MONEY MARKET|LIQUIDEZ|HIGH YIELD",
    re.IGNORECASE,
)
_FOF_RE = re.compile(r"FONDO DE FONDOS|MULTI.?FONDO|FOF\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Category catalogue
# ---------------------------------------------------------------------------

CATEGORIAS: dict[str, dict] = {
    # Capital Privado
    "FI_PE":               {"nombre": "PE / Buyout",              "tipo": "Alternativo", "grupo": "Capital Privado"},
    "FI_DEUDA_PRIVADA":    {"nombre": "Deuda Privada",             "tipo": "Alternativo", "grupo": "Capital Privado"},
    "FI_VC":               {"nombre": "Venture Capital",           "tipo": "Alternativo", "grupo": "Capital Privado"},
    "FI_SECONDARY":        {"nombre": "Secundarios",               "tipo": "Alternativo", "grupo": "Capital Privado"},
    # Inmobiliario
    "FI_INMOB_HIPOTECARIO":{"nombre": "Hipotecario",               "tipo": "Alternativo", "grupo": "Inmobiliario"},
    "FI_INMOB_DESARROLLO": {"nombre": "Desarrollo Inmobiliario",   "tipo": "Alternativo", "grupo": "Inmobiliario"},
    "FI_INMOB_RENTA":      {"nombre": "Renta Inmobiliaria",        "tipo": "Alternativo", "grupo": "Inmobiliario"},
    # Infraestructura
    "FI_INFRA":            {"nombre": "Infraestructura",           "tipo": "Alternativo", "grupo": "Infraestructura"},
    "FI_ENERGIA":          {"nombre": "Energía",                   "tipo": "Alternativo", "grupo": "Infraestructura"},
    "FI_FORESTAL":         {"nombre": "Forestal y Agrícola",       "tipo": "Alternativo", "grupo": "Infraestructura"},
    # Accionario
    "FI_ACC_NAC":          {"nombre": "RV Nacional",               "tipo": "Accionario",  "grupo": "Accionario Nacional"},
    "FI_ACC_NAC_LC":       {"nombre": "RV Nacional Large Cap",     "tipo": "Accionario",  "grupo": "Accionario Nacional"},
    "FI_ACC_NAC_SC":       {"nombre": "RV Nacional Small/Mid Cap", "tipo": "Accionario",  "grupo": "Accionario Nacional"},
    "FI_ACC_INT":          {"nombre": "RV Internacional",          "tipo": "Accionario",  "grupo": "Accionario Internacional"},
    "FI_ACC_INT_LC":       {"nombre": "RV Internacional Large Cap","tipo": "Accionario",  "grupo": "Accionario Internacional"},
    "FI_ACC_INT_SC":       {"nombre": "RV Internacional Small/Mid Cap","tipo": "Accionario","grupo": "Accionario Internacional"},
    # Deuda
    "FI_DEUDA_NAC":        {"nombre": "Deuda Nacional",            "tipo": "Deuda",       "grupo": "Deuda"},
    "FI_DEUDA_INT":        {"nombre": "Deuda Internacional",       "tipo": "Deuda",       "grupo": "Deuda"},
    # Other
    "FI_FOF":              {"nombre": "Fondo de Fondos",           "tipo": "Fondo de Fondos","grupo": "Fondo de Fondos"},
    "FI_MULTIACTIVO":      {"nombre": "Multiactivo",               "tipo": "Balanceado",  "grupo": "Balanceado"},
    "FI_OTRO":             {"nombre": "Sin clasificar",            "tipo": "Otro",        "grupo": "Otro"},
}

# ---------------------------------------------------------------------------
# Subcategory helpers
# ---------------------------------------------------------------------------

def _inmob_sub(nombre: str, pct_mh: float) -> str:
    if pct_mh >= 0.30 or _INMOB_HIPOTECARIO_RE.search(nombre):
        return "FI_INMOB_HIPOTECARIO"
    if _INMOB_RENTA_RE.search(nombre):
        return "FI_INMOB_RENTA"
    return "FI_INMOB_DESARROLLO"


def _infra_sub(nombre: str) -> str:
    if _FORESTAL_RE.search(nombre):
        return "FI_FORESTAL"
    if _ENERGIA_RE.search(nombre):
        return "FI_ENERGIA"
    return "FI_INFRA"


def _pe_sub(nombre: str) -> str:
    if _SECONDARY_RE.search(nombre):
        return "FI_SECONDARY"
    if _VC_RE.search(nombre):
        return "FI_VC"
    if _DEUDA_PRIVADA_RE.search(nombre):
        return "FI_DEUDA_PRIVADA"
    return "FI_PE"


def _equity_sub(is_nac: bool, nombre: str, ipsa_ratio: float | None = None) -> str:
    if is_nac:
        # Portfolio-based size (IPSA overlap) takes priority over name heuristics
        if ipsa_ratio is not None:
            if ipsa_ratio >= IPSA_LC_THRESHOLD:
                return "FI_ACC_NAC_LC"
            if ipsa_ratio <= IPSA_SC_THRESHOLD:
                return "FI_ACC_NAC_SC"
            return "FI_ACC_NAC"
        # No IPSA data available — fall back to name
        if _SMALL_CAP_RE.search(nombre):
            return "FI_ACC_NAC_SC"
        if _LARGE_CAP_RE.search(nombre):
            return "FI_ACC_NAC_LC"
        return "FI_ACC_NAC"
    else:
        # International funds — name heuristics only
        if _SMALL_CAP_RE.search(nombre):
            return "FI_ACC_INT_SC"
        if _LARGE_CAP_RE.search(nombre):
            return "FI_ACC_INT_LC"
        return "FI_ACC_INT"


def _debt_sub(pct_nac: float, pct_ext: float) -> str:
    return "FI_DEUDA_INT" if pct_ext > pct_nac else "FI_DEUDA_NAC"


# ---------------------------------------------------------------------------
# Core classifier
# ---------------------------------------------------------------------------

def _classify(
    pct_mh: float,
    pct_inmob: float,
    pct_pe: float,
    pct_eq_nac: float,
    pct_eq_ext: float,
    pct_debt_nac: float,
    pct_debt_ext: float,
    pct_fof: float,
    pct_other: float,
    has_met_part: bool,
    nombre: str,
    rescatable: bool | None,
    ipsa_ratio: float | None = None,
) -> tuple[str, str]:
    """Return (categoria_code, confianza)."""

    pct_alt  = pct_inmob + pct_pe
    pct_eq   = pct_eq_nac + pct_eq_ext
    pct_debt = pct_debt_nac + pct_debt_ext
    total    = pct_alt + pct_eq + pct_debt + pct_fof + pct_other

    def conf(dominant: float) -> str:
        if dominant >= 0.80: return "Alta"
        if dominant >= 0.50: return "Media"
        return "Baja"

    is_nac_eq = pct_eq_nac >= 0.60 * pct_eq if pct_eq > 0 else True

    # 1. Fondo de Fondos
    if pct_fof >= 0.60:
        return "FI_FOF", conf(pct_fof)

    # 2. Alternative assets (portfolio signal)
    if pct_alt >= 0.40 or has_met_part:
        if pct_inmob >= 0.30:
            return _inmob_sub(nombre, pct_mh), conf(pct_inmob)
        if _INFRA_RE.search(nombre) or _ENERGIA_RE.search(nombre) or _FORESTAL_RE.search(nombre):
            return _infra_sub(nombre), conf(pct_alt)
        if _INMOB_ANY_RE.search(nombre):
            return _inmob_sub(nombre, pct_mh), conf(pct_alt)
        if not (has_met_part and pct_alt < 0.20):
            return _pe_sub(nombre), conf(pct_alt)

    # 3. Equity
    if pct_eq >= 0.50:
        return _equity_sub(is_nac_eq, nombre, ipsa_ratio if is_nac_eq else None), conf(pct_eq)

    # 4. Debt
    if pct_debt >= 0.40:
        return _debt_sub(pct_debt_nac, pct_debt_ext), conf(pct_debt)

    # 5. Fondo de Fondos (minority)
    if pct_fof >= 0.30:
        return "FI_FOF", conf(pct_fof)

    # 6. OTROC/unclassified dominated — name heuristics
    # NOTE: _PE_ANY_RE checked before _EQUITY_RE to avoid "PRIVATE EQUITY" → Accionario
    if pct_other >= 0.50:
        if _INFRA_RE.search(nombre) or _ENERGIA_RE.search(nombre) or _FORESTAL_RE.search(nombre):
            return _infra_sub(nombre), "Media"
        if _INMOB_ANY_RE.search(nombre):
            return _inmob_sub(nombre, pct_mh), "Media"
        if _FOF_RE.search(nombre):
            return "FI_FOF", "Media"
        if _PE_ANY_RE.search(nombre):                     # ← before _EQUITY_RE
            return _pe_sub(nombre), "Media"
        if _EQUITY_RE.search(nombre):
            return _equity_sub(is_nac_eq, nombre), "Media"
        if _DEBT_RE.search(nombre):
            return _debt_sub(pct_debt_nac, pct_debt_ext), "Media"
        if not rescatable:
            return _pe_sub(nombre), "Baja"
        return "FI_OTRO", "Baja"

    # 7. Mixed
    if total < 0.10:
        return "FI_OTRO", "Baja"

    return "FI_MULTIACTIVO", "Baja"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_cartera(session, periodo: date) -> pd.DataFrame:
    nac = pd.DataFrame(session.execute(text("""
        SELECT run_fondo, tipo_instrumento, pct_activo_fondo
        FROM cartera_fi_nac WHERE periodo = :p AND pct_activo_fondo IS NOT NULL
    """), {"p": periodo}).fetchall(), columns=["run_fondo", "tipo", "pct"])
    nac["origin"] = "nac"

    ext = pd.DataFrame(session.execute(text("""
        SELECT run_fondo, tipo_instrumento, pct_activo_fondo
        FROM cartera_fi_ext WHERE periodo = :p AND pct_activo_fondo IS NOT NULL
    """), {"p": periodo}).fetchall(), columns=["run_fondo", "tipo", "pct"])
    ext["origin"] = "ext"

    met = pd.DataFrame(session.execute(text("""
        SELECT run_fondo, tipo_instrumento, pct_activo_fondo
        FROM cartera_fi_met_part WHERE periodo = :p AND pct_activo_fondo IS NOT NULL
    """), {"p": periodo}).fetchall(), columns=["run_fondo", "tipo", "pct"])
    met["origin"] = "met"

    return pd.concat([nac, ext, met], ignore_index=True)


def _load_ipsa_ruts(session, periodo: date) -> set[str]:
    """Return the set of rut_emisor values from the IPSA-tracking ETF for a given period."""
    rows = session.execute(text("""
        SELECT DISTINCT rut_emisor FROM cartera_fi_nac
        WHERE run_fondo = :rf AND periodo = :p
          AND tipo_instrumento IN ('ACC','ACN','ACIN','ACCR')
          AND rut_emisor IS NOT NULL
    """), {"rf": IPSA_PROXY_RUN_FONDO, "p": periodo}).fetchall()
    return {r[0] for r in rows}


def _load_nac_equity(session, periodo: date) -> pd.DataFrame:
    """Return national equity positions with rut_emisor for IPSA overlap computation."""
    return pd.DataFrame(session.execute(text("""
        SELECT run_fondo, rut_emisor, pct_activo_fondo
        FROM cartera_fi_nac
        WHERE periodo = :p
          AND tipo_instrumento IN ('ACC','ACN','ACIN','ACCR')
          AND rut_emisor IS NOT NULL
          AND pct_activo_fondo IS NOT NULL
    """), {"p": periodo}).fetchall(), columns=["run_fondo", "rut_emisor", "pct"])


def _load_funds(session) -> pd.DataFrame:
    return pd.DataFrame(session.execute(text(
        "SELECT run_fondo, razon_social, rescatable, vigente FROM fondos_inversion"
    )).fetchall(), columns=["run_fondo", "razon_social", "rescatable", "vigente"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def latest_periodo() -> date:
    with SessionLocal() as s:
        return s.execute(text("SELECT MAX(periodo) FROM cartera_fi_nac")).scalar()


def save(df: pd.DataFrame) -> int:
    """Upsert a classification DataFrame (from run()) into categoria_fi."""
    from datetime import datetime, timezone
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from src.db.models.categoria_fi import CategoriaFI

    now = datetime.now(timezone.utc)
    records = [
        {
            "run_fondo":     r["run_fondo"],
            "periodo":       r["periodo"],
            "categoria":     r["categoria"],
            "grupo":         r["grupo"],
            "tipo":          r["tipo"],
            "nombre_cat":    r["nombre_cat"],
            "confianza":     r["confianza"],
            "ipsa_ratio":    r["ipsa_ratio"] if r["ipsa_ratio"] != 0 else None,
            "pct_pe":        r["pct_pe"],
            "pct_inmob":     r["pct_inmob"],
            "pct_mh":        r["pct_mh"],
            "pct_eq_nac":    r["pct_eq_nac"],
            "pct_eq_ext":    r["pct_eq_ext"],
            "pct_deuda_nac": r["pct_deuda_nac"],
            "pct_deuda_int": r["pct_deuda_int"],
            "pct_fof":       r["pct_fof"],
            "pct_other":     r["pct_other"],
            "met_part":      bool(r["met_part"]),
            "updated_at":    now,
        }
        for _, r in df.iterrows()
    ]

    with SessionLocal() as s:
        stmt = pg_insert(CategoriaFI).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["run_fondo", "periodo"],
            set_={c: stmt.excluded[c] for c in records[0] if c not in ("run_fondo", "periodo")},
        )
        s.execute(stmt)
        s.commit()

    logger.info("categoria_fi: %d rows upserted for periodo %s", len(records), df["periodo"].iloc[0])
    return len(records)


def run_and_save(periodo: date | None = None) -> int:
    """Classify all FI funds and persist results to categoria_fi. Returns rows saved."""
    df = run(periodo)
    return save(df)


def run(periodo: date | None = None) -> pd.DataFrame:
    """
    Classify all FI funds for the given period (default: latest available).
    Returns a DataFrame sorted by tipo → grupo → categoria → razon_social.
    """
    with SessionLocal() as s:
        if periodo is None:
            periodo = s.execute(text("SELECT MAX(periodo) FROM cartera_fi_nac")).scalar()
            if periodo is None:
                raise RuntimeError("No FI cartera data found in DB")
        cartera    = _load_cartera(s, periodo)
        funds      = _load_funds(s)
        ipsa_ruts  = _load_ipsa_ruts(s, periodo)
        nac_equity = _load_nac_equity(s, periodo)

    if cartera.empty:
        raise RuntimeError(f"No FI cartera data for periodo={periodo}")

    # Pre-compute IPSA overlap ratio per fund
    # ratio = (pct in IPSA stocks) / (total pct in national equity stocks)
    ipsa_ratios: dict[str, float] = {}
    if ipsa_ruts:
        for rf, grp in nac_equity.groupby("run_fondo"):
            total_eq = grp["pct"].sum()
            if total_eq > 0:
                ipsa_pct = grp[grp["rut_emisor"].isin(ipsa_ruts)]["pct"].sum()
                ipsa_ratios[rf] = float(ipsa_pct / total_eq)

    if ipsa_ruts:
        logger.info("IPSA proxy: %d constituents loaded for periodo %s", len(ipsa_ruts), periodo)
    else:
        logger.warning("IPSA proxy fund %s not found — equity size will use name heuristics", IPSA_PROXY_RUN_FONDO)

    met_part_funds = set(cartera[cartera["origin"] == "met"]["run_fondo"])

    records = []
    for run_fondo, grp in cartera.groupby("run_fondo"):
        fund_row   = funds[funds["run_fondo"] == run_fondo]
        nombre     = fund_row["razon_social"].iloc[0] if not fund_row.empty else ""
        rescatable = bool(fund_row["rescatable"].iloc[0]) if not fund_row.empty else None

        def wpct(tipos: set) -> float:
            return float(grp[grp["tipo"].isin(tipos)]["pct"].sum()) / 100

        pct_mh       = wpct({"MH"})
        pct_inmob    = wpct(INMOB_NAC)
        pct_pe       = wpct(PE_NAC | PE_EXT)
        pct_eq_nac   = wpct(EQUITY_NAC)
        pct_eq_ext   = wpct(EQUITY_EXT)
        pct_debt_nac = wpct(DEBT_NAC)
        pct_debt_ext = wpct(DEBT_EXT)
        pct_fof      = wpct(FOF_ALL)
        known        = pct_inmob + pct_pe + pct_eq_nac + pct_eq_ext + pct_debt_nac + pct_debt_ext + pct_fof
        pct_other    = max(0.0, 1.0 - known)
        has_met      = run_fondo in met_part_funds

        codigo, confianza = _classify(
            pct_mh, pct_inmob, pct_pe, pct_eq_nac, pct_eq_ext,
            pct_debt_nac, pct_debt_ext, pct_fof, pct_other,
            has_met, nombre, rescatable,
            ipsa_ratio=ipsa_ratios.get(run_fondo),
        )

        cat = CATEGORIAS[codigo]
        records.append({
            "run_fondo":     run_fondo,
            "razon_social":  nombre,
            "rescatable":    rescatable,
            "periodo":       periodo,
            "categoria":     codigo,
            "grupo":         cat["grupo"],
            "tipo":          cat["tipo"],
            "nombre_cat":    cat["nombre"],
            "pct_pe":        round(pct_pe        * 100, 1),
            "pct_inmob":     round(pct_inmob      * 100, 1),
            "pct_mh":        round(pct_mh         * 100, 1),
            "pct_eq_nac":    round(pct_eq_nac     * 100, 1),
            "pct_eq_ext":    round(pct_eq_ext     * 100, 1),
            "pct_deuda_nac": round(pct_debt_nac   * 100, 1),
            "pct_deuda_int": round(pct_debt_ext   * 100, 1),
            "pct_fof":       round(pct_fof        * 100, 1),
            "pct_other":     round(pct_other      * 100, 1),
            "met_part":      has_met,
            "ipsa_ratio":    round(ipsa_ratios.get(run_fondo, 0) * 100, 1),
            "confianza":     confianza,
        })

    df = (
        pd.DataFrame(records)
        .sort_values(["tipo", "grupo", "categoria", "razon_social"])
        .reset_index(drop=True)
    )
    logger.info("FI clasificados: %d fondos para periodo %s", len(df), periodo)
    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    df = run()
    periodo = df["periodo"].iloc[0]

    print(f"\nFondos de Inversión clasificados: {len(df)}")
    print(f"Período: {periodo.strftime('%B %Y')}\n")

    for tipo in ["Alternativo", "Accionario", "Deuda", "Fondo de Fondos", "Balanceado", "Otro"]:
        sub = df[df["tipo"] == tipo]
        if sub.empty:
            continue
        print(f"\n{'='*95}")
        print(f"  {tipo.upper()} ({len(sub)} fondos)")
        print(f"{'='*95}")
        for grupo in sub["grupo"].unique():
            gsub = sub[sub["grupo"] == grupo]
            print(f"\n  ── {grupo} ──")
            for cat in gsub["categoria"].unique():
                rows = gsub[gsub["categoria"] == cat]
                print(f"\n    [{cat}] {CATEGORIAS[cat]['nombre']}  ({len(rows)} fondos)")
                print(f"    {'RUN':<8} {'Nombre':<52} {'PE%':>5} {'IMB%':>5} {'EQ%':>5} {'DBT%':>5} Conf")
                print(f"    {'-'*90}")
                for _, r in rows.iterrows():
                    resc = "R" if r["rescatable"] else "NR"
                    print(
                        f"    {r['run_fondo']:<8} {r['razon_social'][:51]:<52} "
                        f"{r['pct_pe']:>5.1f} {r['pct_inmob']:>5.1f} "
                        f"{r['pct_eq_nac']+r['pct_eq_ext']:>5.1f} "
                        f"{r['pct_deuda_nac']+r['pct_deuda_int']:>5.1f}  "
                        f"{r['confianza']:<6} {resc}"
                    )

    print(f"\n\n{'RESUMEN':=^65}")
    summary = (
        df.groupby(["tipo", "grupo", "nombre_cat"])
        .agg(fondos=("run_fondo", "count"),
             alta=("confianza", lambda x: (x == "Alta").sum()),
             media=("confianza", lambda x: (x == "Media").sum()),
             baja=("confianza", lambda x: (x == "Baja").sum()))
        .reset_index()
    )
    for _, r in summary.iterrows():
        print(f"  {r['nombre_cat']:<38} {r['fondos']:>4} fondos  "
              f"Alta:{r['alta']:>3}  Media:{r['media']:>3}  Baja:{r['baja']:>3}")

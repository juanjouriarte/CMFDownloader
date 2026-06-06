"""
Clasificación de Fondos Mutuos según Circular No. 7 (AFM, versión 2025).
Usa la cartera de un período dado para clasificar cada fondo.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text

from src.categories import (
    CATEGORIAS,
    clasificar_pais,
)
from src.db.engine import SessionLocal

# Region label per category code (None = computed dynamically for balanced)
REGION_POR_CATEGORIA: dict[str, str | None] = {
    "RF<90NAC":        "Nacional",
    "RF<90INTUSD":     "Internacional",
    "RF<365NCLP":      "Nacional",
    "RF<365NUF":       "Nacional",
    "RF<365INT":       "Internacional",
    "RF>365NCLP":      "Nacional",
    "RF>365NUF<3":     "Nacional",
    "RF>365NUF>3<5":   "Nacional",
    "RF>365NUF>5":     "Nacional",
    "RF>365INTMEMER":  "Internacional - Emergente",
    "RF>365INTMINT":   "Internacional - Desarrollado",
    "RF>365OF":        "Flexible",
    "FDOBALCON":       None,
    "FDOBALMOD":       None,
    "FDOBALAGR":       None,
    "FDOACCNACLC":     "Nacional",
    "FDOACCEEUU":      "EE.UU.",
    "FDOACCEUR":       "Europa Desarrollada",
    "FDOACCDES":       "Desarrollado",
    "FDOACCALAT":      "América Latina",
    "FDOACCAEME":      "Asia Emergente",
    "FDOACCEME":       "Emergente",
    "FDOESTNOACC":     None,
    "FDOICALDEU":      None,
}

logger = logging.getLogger(__name__)

PERIODO = date(2026, 5, 1)

# ---------------------------------------------------------------------------
# Instrument type → asset class
# ---------------------------------------------------------------------------

EQUITY_NACI  = {"ACC", "PE"}
EQUITY_EXTR  = {"ETFA", "ACE", "ADR"}
DEBT_NACI    = {"BB", "DPC", "BE", "PDBC", "BTP", "BTU", "BU", "DPL", "BS",
                "BNEE", "LH", "BBNEE", "BVL", "DPSA", "BH", "BC", "BCP"}
DEBT_EXTR    = {"ETFB", "BEBCE", "BEE", "BBFE", "ADR_BOND"}
FOF_NACI     = {"CFM", "CFI"}   # fund-of-funds: classify based on underlying
FOF_EXTR     = {"CFME", "CFIE"}

# ISO-2 country code → Spanish name (for categories.clasificar_pais)
ISO2_PAIS: dict[str, str | None] = {
    "US": "USA",   "CA": "Canadá",  "MX": "México",  "BR": "Brasil",
    "CL": "Chile", "CO": "Colombia","PE": "Perú",     "AR": "Argentina",
    "GB": "Reino Unido", "DE": "Alemania", "FR": "Francia", "IT": "Italia",
    "ES": "España", "NL": "Países Bajos", "CH": "Suiza",  "SE": "Suecia",
    "NO": "Noruega","DK": "Dinamarca","FI": "Finlandia","BE": "Bélgica",
    "AT": "Austria","IE": "Irlanda", "PT": "Portugal", "GR": "Grecia",
    "PL": "Polonia","HU": "Hungría", "CZ": "República Checa",
    "JP": "Japón",  "HK": "Hong Kong","SG": "Singapur","AU": "Australia",
    "NZ": "Nueva Zelanda","TW": "Taiwán","KR": "Corea del Sur",
    "CN": "China",  "IN": "India",   "ID": "Indonesia","MY": "Malasia",
    "TH": "Tailandia","PH": "Filipinas",
    "ZA": "Sudáfrica","EG": "Egipto","TR": "Turquía",
    "SA": "Arabia Saudita","AE": "Emiratos Árabes","KW": "Kuwait","QA": "Catar",
    "IL": "Israel", "RU": "Rusia",
    # Fund domiciles — not real market indicators
    "LU": None,    "KY": None,    "IG": None,   "PROM": None,
}

LATAM = {"Brasil", "Chile", "Colombia", "México", "Perú"}
EUR_DES = {"Reino Unido","Alemania","Francia","Italia","España","Países Bajos",
           "Suiza","Suecia","Noruega","Dinamarca","Finlandia","Bélgica",
           "Austria","Portugal","Irlanda","Grecia","Israel"}


def _classify_equity_geography(extr_equity: pd.DataFrame) -> str:
    """Given EXTR equity rows, return the accionario category code."""
    if extr_equity.empty:
        return "FDOACCDES"

    by_country = (
        extr_equity.groupby("pais_nombre")["valor"]
        .sum()
        .sort_values(ascending=False)
    )
    total = by_country.sum()
    if total == 0:
        return "FDOACCDES"

    top_pais, top_val = by_country.index[0], by_country.iloc[0]
    top_pct = top_val / total

    if top_pct >= 0.75:
        if top_pais == "USA":
            return "FDOACCEEUU"
        if top_pais in LATAM:
            return "FDOACCALAT"
        info = clasificar_pais(top_pais)
        if info:
            market, region = info
            if market == "Emergente":
                if region == "América Latina":
                    return "FDOACCALAT"
                if region == "Asia Emergente":
                    return "FDOACCAEME"
                return "FDOACCEME"
            if region == "Europa Desarrollada":
                return "FDOACCEUR"
            if region == "Norte América":
                return "FDOACCEEUU"

    # Check region concentration
    latam_pct  = by_country[by_country.index.isin(LATAM)].sum() / total
    eurdes_pct = by_country[by_country.index.isin(EUR_DES)].sum() / total

    if latam_pct >= 0.75:
        return "FDOACCALAT"
    if eurdes_pct >= 0.75:
        return "FDOACCEUR"

    # Developed vs Emergent
    dev_paises = {"USA","Canadá","Japón","Hong Kong","Singapur","Australia",
                  "Nueva Zelanda"} | EUR_DES
    dev_pct = by_country[by_country.index.isin(dev_paises)].sum() / total
    if dev_pct >= 0.75:
        return "FDOACCDES"

    return "FDOACCEME"


def _classify_debt(pct_naci: float, pct_extr: float, pct_uf: float,
                   pct_clp: float, wam_dias: float | None) -> str:
    """Classify a debt fund."""
    if pct_naci >= 1.0:
        # 100% national
        dur = wam_dias or 999
        if dur <= 90:
            if pct_uf >= 0.60:
                return "RF<90NAC"
            return "RF<90NAC"
        if dur <= 365:
            if pct_clp >= 0.60:
                return "RF<365NCLP"
            if pct_uf >= 0.60:
                return "RF<365NUF"
            return "RF<365NCLP"
        # > 365
        if pct_clp >= 0.60:
            return "RF>365NCLP"
        if pct_uf >= 0.60:
            if dur <= 365 * 3:
                return "RF>365NUF<3"
            if dur <= 365 * 5:
                return "RF>365NUF>3<5"
            return "RF>365NUF>5"
        return "RF>365OF"

    if pct_extr >= 0.60:
        dur = wam_dias or 999
        if dur <= 90:
            return "RF<90INTUSD"
        if dur <= 365:
            return "RF<365INT"
        return "RF>365INTMINT"

    return "RF>365OF"


def run(periodo: date = PERIODO) -> pd.DataFrame:
    with SessionLocal() as s:
        # ------------------------------------------------------------------ NACI
        naci = pd.DataFrame(s.execute(text("""
            SELECT
                run_fondo,
                nombre_fondo,
                ffm_6010400  AS tipo,
                ffm_6011000  AS moneda,
                ffm_6010500  AS fecha_vcto,
                ffm_6011200  AS valor_raw
            FROM cartera_naci
            WHERE periodo = :p
        """), {"p": periodo}).fetchall(),
            columns=["run_fondo","nombre_fondo","tipo","moneda","fecha_vcto","valor_raw"])

        # ------------------------------------------------------------------ EXTR
        extr = pd.DataFrame(s.execute(text("""
            SELECT
                run_fondo,
                ffm_6020400  AS tipo,
                ffm_6020300  AS codigo_pais,
                ffm_6021200  AS valor_raw
            FROM cartera_extr
            WHERE periodo = :p
        """), {"p": periodo}).fetchall(),
            columns=["run_fondo","tipo","codigo_pais","valor_raw"])

        # ------------------------------------------------------------------ fund names
        names = pd.DataFrame(s.execute(text(
            "SELECT run_fondo, nombre_fondo FROM fondo_mutuo"
        )).fetchall(), columns=["run_fondo","nombre_fondo"])

    # Numeric values
    naci["valor"] = pd.to_numeric(naci["valor_raw"], errors="coerce").fillna(0)
    extr["valor"] = pd.to_numeric(extr["valor_raw"], errors="coerce").fillna(0)

    # Map country codes → Spanish names
    extr["pais_nombre"] = extr["codigo_pais"].map(lambda c: ISO2_PAIS.get(str(c).strip(), str(c)))

    # Asset class flags
    naci["es_equity"] = naci["tipo"].isin(EQUITY_NACI)
    naci["es_deuda"]  = naci["tipo"].isin(DEBT_NACI)
    naci["es_fof"]    = naci["tipo"].isin(FOF_NACI)
    naci["es_uf"]     = naci["moneda"] == "UF"
    naci["es_clp"]    = naci["moneda"] == "$$"

    extr["es_equity"] = extr["tipo"].isin(EQUITY_EXTR)
    extr["es_deuda"]  = extr["tipo"].isin(DEBT_EXTR)

    # WAM: weighted average maturity in days from period end
    ref_date = date(periodo.year, periodo.month,
                    (date(periodo.year, periodo.month + 1, 1)
                     if periodo.month < 12 else date(periodo.year + 1, 1, 1))
                    .toordinal() - date(periodo.year, periodo.month, 1).toordinal()
                    + periodo.day - 1)
    ref_date = date(periodo.year, periodo.month,
                    pd.Period(str(periodo), "M").days_in_month)

    def parse_vcto(s: str) -> float | None:
        if not s or s in ("NaN", "99999999", "nan"):
            return None
        try:
            d = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
            if pd.isna(d):
                return None
            days = (d.date() - ref_date).days
            return max(days, 0)
        except Exception:
            return None

    naci["days_to_maturity"] = naci["fecha_vcto"].apply(
        lambda x: parse_vcto(str(x)) if x else None
    )

    # ------------------------------------------------------------------ Per-fund metrics
    all_funds = set(naci["run_fondo"]) | set(extr["run_fondo"])
    records: list[dict[str, Any]] = []

    for run in sorted(all_funds):
        fn = naci[naci["run_fondo"] == run]
        fe = extr[extr["run_fondo"] == run]

        val_naci   = fn["valor"].sum()
        val_extr   = fe["valor"].sum()
        val_total  = val_naci + val_extr
        if val_total == 0:
            continue

        eq_naci  = fn[fn["es_equity"]]["valor"].sum()
        eq_extr  = fe[fe["es_equity"]]["valor"].sum()
        eq_total = eq_naci + eq_extr

        fof_val  = fn[fn["es_fof"]]["valor"].sum()

        pct_equity = eq_total / val_total
        pct_naci   = val_naci  / val_total
        pct_extr   = val_extr  / val_total
        pct_fof    = fof_val   / val_total

        naci_uf  = fn[fn["es_uf"]]["valor"].sum()
        naci_clp = fn[fn["es_clp"]]["valor"].sum()
        pct_uf   = naci_uf  / val_total
        pct_clp  = naci_clp / val_total

        # WAM from NACI debt positions with known maturity
        debt_with_mat = fn[fn["es_deuda"] & fn["days_to_maturity"].notna()]
        if not debt_with_mat.empty and debt_with_mat["valor"].sum() > 0:
            wam = (debt_with_mat["days_to_maturity"] * debt_with_mat["valor"]).sum() / debt_with_mat["valor"].sum()
        else:
            wam = None

        # --- Classify ---
        nota = ""
        if pct_fof > 0.20:
            nota = f"FOF {pct_fof:.0%}"

        if pct_equity >= 0.90:
            if pct_naci >= 0.90:
                codigo = "FDOACCNACLC"
            else:
                fe_eq = fe[fe["es_equity"]].copy()
                codigo = _classify_equity_geography(fe_eq)
            confianza = "Alta" if pct_fof < 0.10 else "Media"

        elif pct_equity >= 0.60:
            codigo = "FDOBALAGR"
            confianza = "Alta" if pct_fof < 0.10 else "Media"

        elif pct_equity >= 0.30:
            codigo = "FDOBALMOD"
            confianza = "Alta" if pct_fof < 0.10 else "Media"

        elif pct_equity >= 0.01:
            codigo = "FDOBALCON"
            confianza = "Alta" if pct_fof < 0.10 else "Media"

        else:
            # Deuda
            codigo = _classify_debt(pct_naci, pct_extr, pct_uf, pct_clp, wam)
            confianza = "Media" if pct_fof > 0.10 or wam is None else "Alta"

        nombre = (
            names[names["run_fondo"] == run]["nombre_fondo"].iloc[0]
            if run in names["run_fondo"].values else
            fn["nombre_fondo"].iloc[0] if not fn.empty else "—"
        )

        # Region
        region = REGION_POR_CATEGORIA.get(codigo)
        if region is None:
            # Balanced or unknown: derive from national/international mix
            if pct_naci >= 0.70:
                region = "Nacional"
            elif pct_extr >= 0.70:
                region = "Internacional"
            else:
                region = "Mixto"

        records.append({
            "run_fondo":    run,
            "nombre_fondo": nombre,
            "categoria":    codigo,
            "tipo":         CATEGORIAS.get(codigo, {}).get("tipo", "—"),
            "nombre_cat":   CATEGORIAS.get(codigo, {}).get("nombre", codigo),
            "region":       region,
            "pct_equity":   round(pct_equity * 100, 1),
            "pct_naci":     round(pct_naci   * 100, 1),
            "pct_uf":       round(pct_uf     * 100, 1),
            "pct_clp":      round(pct_clp    * 100, 1),
            "wam_dias":     round(wam) if wam else None,
            "val_total_mm": round(val_total / 1e6, 1),
            "confianza":    confianza,
            "nota":         nota,
        })

    df = pd.DataFrame(records).sort_values(["tipo", "categoria", "nombre_fondo"])
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = run()
    print(f"\nFondos clasificados: {len(df)}")
    print(f"Período: {PERIODO.strftime('%B %Y')}\n")
    for tipo in ["Deuda", "Balanceado", "Accionario", "Estructurado", "Inversionistas Calificados"]:
        sub = df[df["tipo"] == tipo]
        if sub.empty:
            continue
        print(f"\n{'='*80}")
        print(f"  {tipo.upper()} ({len(sub)} fondos)")
        print(f"{'='*80}")
        for cat in sub["categoria"].unique():
            rows = sub[sub["categoria"] == cat]
            print(f"\n  [{cat}] {CATEGORIAS.get(cat,{}).get('nombre', cat)}")
            for _, r in rows.iterrows():
                nota = f" ⚠ {r['nota']}" if r["nota"] else ""
                print(f"    {r['run_fondo']:<6} {r['nombre_fondo'][:40]:<40} "
                      f"EQ:{r['pct_equity']:5.1f}%  NAC:{r['pct_naci']:5.1f}%  "
                      f"Region:{r['region']:<25}  AUM:{r['val_total_mm']:>8.1f}MM  [{r['confianza']}]{nota}")
    df.to_csv("downloads/clasificacion_mayo_2026.csv", index=False, encoding="utf-8-sig")
    print(f"\nGuardado en downloads/clasificacion_mayo_2026.csv")

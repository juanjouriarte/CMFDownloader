"""
Categorías de Fondos Mutuos — Circular No. 7 (versión 2025)
Asociación de Fondos Mutuos de Chile A.G.
"""
from __future__ import annotations

from enum import StrEnum


class TipoFondo(StrEnum):
    DEUDA       = "Deuda"
    ACCIONARIO  = "Accionario"
    BALANCEADO  = "Balanceado"
    ESTRUCTURADO = "Estructurado"
    INV_CALIFICADOS = "Inversionistas Calificados"


# ---------------------------------------------------------------------------
# Códigos oficiales → nombre completo + tipo
# ---------------------------------------------------------------------------

CATEGORIAS: dict[str, dict] = {
    # --- Deuda con duración <= 90 días ---
    "RF<90NAC": {
        "nombre": "Deuda Nacional con Duración ≤ 90 días",
        "tipo": TipoFondo.DEUDA,
        "origen": "Nacional",
        "duracion_max_dias": 90,
        "moneda": None,
        "descripcion": "100% en instrumentos denominados en CLP, UF o IVP.",
    },
    "RF<90INTUSD": {
        "nombre": "Deuda Internacional en USD con Duración ≤ 90 días",
        "tipo": TipoFondo.DEUDA,
        "origen": "Internacional",
        "duracion_max_dias": 90,
        "moneda": "USD",
        "descripcion": "Al menos 80% en instrumentos denominados en USD.",
    },

    # --- Deuda con duración <= 365 días ---
    "RF<365NCLP": {
        "nombre": "Deuda Nacional en Pesos con Duración ≤ 365 días",
        "tipo": TipoFondo.DEUDA,
        "origen": "Nacional",
        "duracion_max_dias": 365,
        "moneda": "CLP",
        "descripcion": "100% nacional; >60% en pesos.",
    },
    "RF<365NUF": {
        "nombre": "Deuda Nacional en UF con Duración ≤ 365 días",
        "tipo": TipoFondo.DEUDA,
        "origen": "Nacional",
        "duracion_max_dias": 365,
        "moneda": "UF",
        "descripcion": "100% nacional; >60% en UF.",
    },
    "RF<365INT": {
        "nombre": "Deuda Internacional con Duración ≤ 365 días",
        "tipo": TipoFondo.DEUDA,
        "origen": "Internacional",
        "duracion_max_dias": 365,
        "moneda": None,
        "descripcion": "Al menos 60% en moneda o unidades de reajuste extranjeras.",
    },

    # --- Deuda con duración > 365 días ---
    "RF>365NCLP": {
        "nombre": "Deuda Nacional en Pesos con Duración > 365 días",
        "tipo": TipoFondo.DEUDA,
        "origen": "Nacional",
        "duracion_min_dias": 365,
        "moneda": "CLP",
        "descripcion": "100% nacional; >60% en pesos.",
    },
    "RF>365NUF<3": {
        "nombre": "Deuda Nacional en UF con Duración > 365 días y ≤ 3 años",
        "tipo": TipoFondo.DEUDA,
        "origen": "Nacional",
        "duracion_min_dias": 365,
        "duracion_max_anios": 3,
        "moneda": "UF",
        "descripcion": "100% nacional; >60% en UF; duración promedio ≤ 3 años.",
    },
    "RF>365NUF>3<5": {
        "nombre": "Deuda Nacional en UF con Duración > 3 años y ≤ 5 años",
        "tipo": TipoFondo.DEUDA,
        "origen": "Nacional",
        "duracion_min_anios": 3,
        "duracion_max_anios": 5,
        "moneda": "UF",
        "descripcion": "100% nacional; >60% en UF; duración promedio >3 y ≤5 años.",
    },
    "RF>365NUF>5": {
        "nombre": "Deuda Nacional en UF con Duración > 5 años",
        "tipo": TipoFondo.DEUDA,
        "origen": "Nacional",
        "duracion_min_anios": 5,
        "moneda": "UF",
        "descripcion": "100% nacional; >60% en UF; duración promedio >5 años.",
    },
    "RF>365INTMEMER": {
        "nombre": "Deuda Internacional Mercados Emergentes con Duración > 365 días",
        "tipo": TipoFondo.DEUDA,
        "origen": "Internacional",
        "duracion_min_dias": 365,
        "mercado": "Emergente",
        "descripcion": "Al menos 80% en mercados emergentes.",
    },
    "RF>365INTMINT": {
        "nombre": "Deuda Internacional Mercados Internacionales con Duración > 365 días",
        "tipo": TipoFondo.DEUDA,
        "origen": "Internacional",
        "duracion_min_dias": 365,
        "mercado": "Internacional",
        "descripcion": "Al menos 80% en mercados internacionales desarrollados.",
    },
    "RF>365OF": {
        "nombre": "Deuda Origen Flexible con Duración > 365 días",
        "tipo": TipoFondo.DEUDA,
        "origen": "Flexible",
        "duracion_min_dias": 365,
        "descripcion": "No clasifica como Nacional ni Internacional.",
    },

    # --- Balanceados ---
    "FDOBALCON": {
        "nombre": "Balanceado Conservador",
        "tipo": TipoFondo.BALANCEADO,
        "rango_capitalizacion": (0.01, 0.30),
        "descripcion": "Capitalización entre 1% y 30% (excl.); deuda entre 70% y 99%.",
    },
    "FDOBALMOD": {
        "nombre": "Balanceado Moderado",
        "tipo": TipoFondo.BALANCEADO,
        "rango_capitalizacion": (0.30, 0.60),
        "descripcion": "Capitalización entre 30% (incl.) y 60% (excl.); deuda entre 40% y 70%.",
    },
    "FDOBALAGR": {
        "nombre": "Balanceado Agresivo",
        "tipo": TipoFondo.BALANCEADO,
        "rango_capitalizacion": (0.60, 0.90),
        "descripcion": "Capitalización entre 60% (incl.) y 90% (incl.); deuda entre 10% y 40%.",
    },

    # --- Accionarios ---
    "FDOACCNACLC": {
        "nombre": "Accionario Nacional Large Cap",
        "tipo": TipoFondo.ACCIONARIO,
        "origen": "Nacional",
        "mercado": "Large Cap",
        "descripcion": "≥90% capitalización nacional; ≥80% en acciones del índice S&P/CLX IPSA.",
    },
    "FDOACCEEUU": {
        "nombre": "Accionario EE.UU.",
        "tipo": TipoFondo.ACCIONARIO,
        "origen": "Internacional",
        "mercado": "Desarrollado",
        "region": "Norte América",
        "descripcion": ">75% concentrado en EE.UU.",
    },
    "FDOACCEUR": {
        "nombre": "Accionario Europa Desarrollada",
        "tipo": TipoFondo.ACCIONARIO,
        "origen": "Internacional",
        "mercado": "Desarrollado",
        "region": "Europa Desarrollada",
        "descripcion": ">75% concentrado en Europa desarrollada.",
    },
    "FDOACCDES": {
        "nombre": "Accionario Desarrollado",
        "tipo": TipoFondo.ACCIONARIO,
        "origen": "Internacional",
        "mercado": "Desarrollado",
        "descripcion": "≥75% en mercados desarrollados sin concentración regional específica.",
    },
    "FDOACCALAT": {
        "nombre": "Accionario América Latina",
        "tipo": TipoFondo.ACCIONARIO,
        "origen": "Internacional",
        "mercado": "Emergente",
        "region": "América Latina",
        "descripcion": ">75% concentrado en América Latina.",
    },
    "FDOACCAEME": {
        "nombre": "Accionario Asia Emergente",
        "tipo": TipoFondo.ACCIONARIO,
        "origen": "Internacional",
        "mercado": "Emergente",
        "region": "Asia Emergente",
        "descripcion": ">75% concentrado en Asia emergente.",
    },
    "FDOACCEME": {
        "nombre": "Accionario Emergente",
        "tipo": TipoFondo.ACCIONARIO,
        "origen": "Internacional",
        "mercado": "Emergente",
        "descripcion": "≥75% en mercados emergentes sin concentración regional específica.",
    },

    # --- Estructurados ---
    "FDOESTNOACC": {
        "nombre": "Estructurado No Accionario",
        "tipo": TipoFondo.ESTRUCTURADO,
        "descripcion": (
            "Fondo balanceado/deuda con rentabilidad mínima predeterminada "
            "(Circular CMF N°1578). No indexado a acciones."
        ),
    },

    # --- Inversionistas Calificados ---
    "FDOICALDEU": {
        "nombre": "Inversionistas Calificados Títulos de Deuda",
        "tipo": TipoFondo.INV_CALIFICADOS,
        "descripcion": (
            "Dirigido a inversionistas calificados (NCG N°119). "
            ">60% en instrumentos de deuda; objetivo: preservación del capital."
        ),
    },
}


# ---------------------------------------------------------------------------
# Anexo 2 — Países por región para fondos accionarios internacionales
# ---------------------------------------------------------------------------

PAISES_MERCADOS_EMERGENTES: dict[str, list[str]] = {
    "Asia Emergente": [
        "China", "India", "Indonesia", "Corea del Sur", "Malasia",
        "Filipinas", "Taiwán", "Tailandia",
    ],
    "América Latina": ["Brasil", "Chile", "Colombia", "México", "Perú"],
    "Europa Emergente": [
        "República Checa", "Egipto", "Grecia", "Hungría", "Kuwait",
        "Polonia", "Catar", "Arabia Saudita", "Sudáfrica", "Turquía",
        "Emiratos Árabes",
    ],
}

PAISES_MERCADOS_DESARROLLADOS: dict[str, list[str]] = {
    "Asia Pacífico": ["Japón", "Hong Kong", "Singapur", "Australia", "Nueva Zelanda"],
    "Europa Desarrollada": [
        "Austria", "Bélgica", "Dinamarca", "Finlandia", "Francia", "Alemania",
        "Irlanda", "Israel", "Italia", "Países Bajos", "Noruega", "Portugal",
        "España", "Suecia", "Suiza", "Reino Unido",
    ],
    "Norte América": ["Canadá", "USA"],
}

# Flat lookup: country → (market_type, region)
_PAIS_REGION: dict[str, tuple[str, str]] = {}
for _region, _paises in PAISES_MERCADOS_EMERGENTES.items():
    for _p in _paises:
        _PAIS_REGION[_p] = ("Emergente", _region)
for _region, _paises in PAISES_MERCADOS_DESARROLLADOS.items():
    for _p in _paises:
        _PAIS_REGION[_p] = ("Desarrollado", _region)


def clasificar_pais(pais: str) -> tuple[str, str] | None:
    """Returns (market_type, region) for a given country, or None if not found."""
    return _PAIS_REGION.get(pais)


def categorias_por_tipo(tipo: TipoFondo) -> dict[str, dict]:
    """Returns all categories of a given fund type."""
    return {k: v for k, v in CATEGORIAS.items() if v["tipo"] == tipo}

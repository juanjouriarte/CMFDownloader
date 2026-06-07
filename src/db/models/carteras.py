from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class _CarteraBase:
    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    periodo: Mapped[date] = mapped_column(Date, nullable=False)
    run_fondo: Mapped[str] = mapped_column(String(20), nullable=False)


class CarteraNaci(_CarteraBase, Base):
    __tablename__ = "cartera_naci"
    __table_args__ = (
        Index("ix_cartera_naci_periodo", "periodo"),
        Index("ix_cartera_naci_run_fondo_periodo", "run_fondo", "periodo"),
    )

    nemotecnico: Mapped[str | None] = mapped_column(Text)
    rut_emisor: Mapped[str | None] = mapped_column(Text)
    dv_emisor: Mapped[str | None] = mapped_column(Text)
    codigo_pais_emisor: Mapped[str | None] = mapped_column(Text)
    tipo_instrumento: Mapped[str | None] = mapped_column(Text)
    fecha_vencimiento: Mapped[str | None] = mapped_column(Text)
    situacion_instrumento: Mapped[str | None] = mapped_column(Text)
    clasificacion_riesgo: Mapped[str | None] = mapped_column(Text)
    codigo_grupo_empresarial: Mapped[str | None] = mapped_column(Text)
    cantidad_unidades: Mapped[str | None] = mapped_column(Text)
    tipo_unidades: Mapped[str | None] = mapped_column(Text)
    tir: Mapped[str | None] = mapped_column(Text)
    porcentaje_valor_par: Mapped[str | None] = mapped_column(Text)
    valor_relevante: Mapped[str | None] = mapped_column(Text)
    codigo_valorizacion: Mapped[str | None] = mapped_column(Text)
    base_tasa: Mapped[str | None] = mapped_column(Text)
    tipo_interes: Mapped[str | None] = mapped_column(Text)
    valorizacion_cierre: Mapped[str | None] = mapped_column(Text)
    moneda_liquidacion: Mapped[str | None] = mapped_column(Text)
    pais_transaccion: Mapped[str | None] = mapped_column(Text)
    porcentaje_capital_emisor: Mapped[str | None] = mapped_column(Text)
    porcentaje_activos_emisor: Mapped[str | None] = mapped_column(Text)
    porcentaje_activos_fondo: Mapped[str | None] = mapped_column(Text)


class CarteraExtr(_CarteraBase, Base):
    __tablename__ = "cartera_extr"
    __table_args__ = (
        Index("ix_cartera_extr_periodo", "periodo"),
        Index("ix_cartera_extr_run_fondo_periodo", "run_fondo", "periodo"),
    )

    nemotecnico: Mapped[str | None] = mapped_column(Text)
    nombre_emisor: Mapped[str | None] = mapped_column(Text)
    codigo_pais_emisor: Mapped[str | None] = mapped_column(Text)
    tipo_instrumento: Mapped[str | None] = mapped_column(Text)
    fecha_vencimiento: Mapped[str | None] = mapped_column(Text)
    situacion_instrumento: Mapped[str | None] = mapped_column(Text)
    clasificacion_riesgo: Mapped[str | None] = mapped_column(Text)
    nombre_grupo_empresarial: Mapped[str | None] = mapped_column(Text)
    cantidad_unidades: Mapped[str | None] = mapped_column(Text)
    tipo_unidades: Mapped[str | None] = mapped_column(Text)
    tir: Mapped[str | None] = mapped_column(Text)
    porcentaje_valor_par: Mapped[str | None] = mapped_column(Text)
    valor_relevante: Mapped[str | None] = mapped_column(Text)
    codigo_valorizacion: Mapped[str | None] = mapped_column(Text)
    base_tasa: Mapped[str | None] = mapped_column(Text)
    tipo_interes: Mapped[str | None] = mapped_column(Text)
    valorizacion_cierre: Mapped[str | None] = mapped_column(Text)
    moneda_liquidacion: Mapped[str | None] = mapped_column(Text)
    pais_transaccion: Mapped[str | None] = mapped_column(Text)
    porcentaje_capital_emisor: Mapped[str | None] = mapped_column(Text)
    porcentaje_activos_emisor: Mapped[str | None] = mapped_column(Text)
    porcentaje_activos_fondo: Mapped[str | None] = mapped_column(Text)


class CarteraOpci(_CarteraBase, Base):
    __tablename__ = "cartera_opci"
    __table_args__ = (
        Index("ix_cartera_opci_periodo", "periodo"),
        Index("ix_cartera_opci_run_fondo_periodo", "run_fondo", "periodo"),
    )

    activo_objeto: Mapped[str | None] = mapped_column(Text)
    nemotecnico: Mapped[str | None] = mapped_column(Text)
    forma_ejercicio: Mapped[str | None] = mapped_column(Text)
    fecha_expiracion: Mapped[str | None] = mapped_column(Text)
    moneda_liquidacion: Mapped[str | None] = mapped_column(Text)
    codigo_pais: Mapped[str | None] = mapped_column(Text)
    tipo_opcion: Mapped[str | None] = mapped_column(Text)
    valor_mercado_prima_unitario: Mapped[str | None] = mapped_column(Text)
    numero_contratos: Mapped[str | None] = mapped_column(Text)
    precio_ejercicio: Mapped[str | None] = mapped_column(Text)
    valor_mercado_activo_objeto: Mapped[str | None] = mapped_column(Text)
    unidades_activo_objeto: Mapped[str | None] = mapped_column(Text)
    inversion_primas: Mapped[str | None] = mapped_column(Text)
    valorizacion_precio_ejercicio: Mapped[str | None] = mapped_column(Text)
    valorizacion_mercado: Mapped[str | None] = mapped_column(Text)
    porcentaje_primas_activo_total: Mapped[str | None] = mapped_column(Text)


class CarteraFutu(_CarteraBase, Base):
    __tablename__ = "cartera_futu"
    __table_args__ = (
        Index("ix_cartera_futu_periodo", "periodo"),
        Index("ix_cartera_futu_run_fondo_periodo", "run_fondo", "periodo"),
    )

    activo_objeto: Mapped[str | None] = mapped_column(Text)
    nemotecnico: Mapped[str | None] = mapped_column(Text)
    unidad_cotizacion: Mapped[str | None] = mapped_column(Text)
    fecha_vencimiento: Mapped[str | None] = mapped_column(Text)
    moneda_liquidacion: Mapped[str | None] = mapped_column(Text)
    codigo_pais: Mapped[str | None] = mapped_column(Text)
    posicion: Mapped[str | None] = mapped_column(Text)
    unidades_nominales_totales: Mapped[str | None] = mapped_column(Text)
    precio_futuro: Mapped[str | None] = mapped_column(Text)
    monto_comprometido: Mapped[str | None] = mapped_column(Text)
    valorizacion_mercado: Mapped[str | None] = mapped_column(Text)


class CarteraOpla(_CarteraBase, Base):
    __tablename__ = "cartera_opla"
    __table_args__ = (
        Index("ix_cartera_opla_periodo", "periodo"),
        Index("ix_cartera_opla_run_fondo_periodo", "run_fondo", "periodo"),
    )

    activo_objeto: Mapped[str | None] = mapped_column(Text)
    nemotecnico: Mapped[str | None] = mapped_column(Text)
    forma_ejercicio: Mapped[str | None] = mapped_column(Text)
    fecha_expiracion: Mapped[str | None] = mapped_column(Text)
    moneda_liquidacion: Mapped[str | None] = mapped_column(Text)
    codigo_pais: Mapped[str | None] = mapped_column(Text)
    tipo_opcion: Mapped[str | None] = mapped_column(Text)
    valor_mercado_prima_unitario: Mapped[str | None] = mapped_column(Text)
    numero_contratos: Mapped[str | None] = mapped_column(Text)
    precio_ejercicio: Mapped[str | None] = mapped_column(Text)
    valor_mercado_activo_objeto: Mapped[str | None] = mapped_column(Text)
    unidades_activo_objeto: Mapped[str | None] = mapped_column(Text)
    inversion_primas: Mapped[str | None] = mapped_column(Text)
    valorizacion_precio_ejercicio: Mapped[str | None] = mapped_column(Text)
    valorizacion_mercado: Mapped[str | None] = mapped_column(Text)
    porcentaje_primas_activo_total: Mapped[str | None] = mapped_column(Text)

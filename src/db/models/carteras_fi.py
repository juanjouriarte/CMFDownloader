from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.engine import Base


class _CarteraFIBase:
    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    run_fondo: Mapped[str] = mapped_column(String(20), nullable=False)
    periodo: Mapped[date] = mapped_column(Date, nullable=False)


class CarteraFINac(_CarteraFIBase, Base):
    __tablename__ = "cartera_fi_nac"
    __table_args__ = (
        Index("ix_cartera_fi_nac_periodo", "periodo"),
        Index("ix_cartera_fi_nac_run_fondo_periodo", "run_fondo", "periodo"),
    )
    clasif_esf: Mapped[str | None] = mapped_column(String(10), nullable=True)
    nemotecnico: Mapped[str | None] = mapped_column(Text, nullable=True)
    rut_emisor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cod_pais: Mapped[str | None] = mapped_column(String(5), nullable=True)
    tipo_instrumento: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fecha_vencimiento: Mapped[str | None] = mapped_column(String(20), nullable=True)
    situacion_instrumento: Mapped[str | None] = mapped_column(String(5), nullable=True)
    clasif_riesgo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    grupo_empresarial: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cant_unidades: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)
    tipo_unidades: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tir_val_par_precio: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    cod_valorizacion: Mapped[str | None] = mapped_column(String(10), nullable=True)
    base_tasa: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tipo_interes: Mapped[str | None] = mapped_column(String(10), nullable=True)
    valorizacion_cierre: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)
    cod_moneda_liquidacion: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cod_pais_transaccion: Mapped[str | None] = mapped_column(String(5), nullable=True)
    pct_capital_emisor: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    pct_activo_emisor: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    pct_activo_fondo: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)


class CarteraFIExt(_CarteraFIBase, Base):
    __tablename__ = "cartera_fi_ext"
    __table_args__ = (
        Index("ix_cartera_fi_ext_periodo", "periodo"),
        Index("ix_cartera_fi_ext_run_fondo_periodo", "run_fondo", "periodo"),
    )
    clasif_esf: Mapped[str | None] = mapped_column(String(10), nullable=True)
    nemo_isin: Mapped[str | None] = mapped_column(Text, nullable=True)
    nombre_emisor_corto: Mapped[str | None] = mapped_column(Text, nullable=True)
    nombre_emisor: Mapped[str | None] = mapped_column(Text, nullable=True)
    cod_pais: Mapped[str | None] = mapped_column(String(5), nullable=True)
    tipo_instrumento: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fecha_vencimiento: Mapped[str | None] = mapped_column(String(20), nullable=True)
    situacion_instrumento: Mapped[str | None] = mapped_column(String(5), nullable=True)
    clasif_riesgo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cant_unidades: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)
    tipo_unidades: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cod_valorizacion: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tir_val_par_precio: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    base_tasa: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tipo_interes: Mapped[str | None] = mapped_column(String(10), nullable=True)
    valorizacion_cierre: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)
    cod_moneda_liquidacion: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cod_pais_transaccion: Mapped[str | None] = mapped_column(String(5), nullable=True)
    pct_capital_emisor: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    pct_activo_emisor: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    pct_activo_fondo: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)


class CarteraFIMetPart(_CarteraFIBase, Base):
    __tablename__ = "cartera_fi_met_part"
    __table_args__ = (
        Index("ix_cartera_fi_met_part_periodo", "periodo"),
        Index("ix_cartera_fi_met_part_run_fondo_periodo", "run_fondo", "periodo"),
    )
    clasif_esf: Mapped[str | None] = mapped_column(String(10), nullable=True)
    nemotecnico: Mapped[str | None] = mapped_column(Text, nullable=True)
    rut_emisor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cod_pais: Mapped[str | None] = mapped_column(String(5), nullable=True)
    tipo_instrumento: Mapped[str | None] = mapped_column(String(20), nullable=True)
    situacion_instrumento: Mapped[str | None] = mapped_column(String(5), nullable=True)
    acciones_suscritas_pag: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)
    pct_participacion: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    patrimonio_contable: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)
    valor_contable: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)
    ajuste_valor_pat: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)
    correc_monetaria: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)
    cod_moneda: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cod_pais_transaccion: Mapped[str | None] = mapped_column(String(5), nullable=True)
    pct_activo_fondo: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)


class CarteraFIFutFw(_CarteraFIBase, Base):
    __tablename__ = "cartera_fi_fut_fw"
    __table_args__ = (
        Index("ix_cartera_fi_fut_fw_periodo", "periodo"),
        Index("ix_cartera_fi_fut_fw_run_fondo_periodo", "run_fondo", "periodo"),
    )
    activo_subyacente: Mapped[str | None] = mapped_column(Text, nullable=True)
    tipo_contrato: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cod_moneda_precio: Mapped[str | None] = mapped_column(String(10), nullable=True)
    fecha_inicio: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fecha_vencimiento: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contraparte: Mapped[str | None] = mapped_column(Text, nullable=True)
    cod_moneda_subyacente: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cod_pais: Mapped[str | None] = mapped_column(String(5), nullable=True)
    posicion: Mapped[str | None] = mapped_column(String(5), nullable=True)
    monto_nominal: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)
    precio_pactado: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    valor_razonable_activo: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)
    valor_razonable_pasivo: Mapped[float | None] = mapped_column(Numeric(25, 4), nullable=True)

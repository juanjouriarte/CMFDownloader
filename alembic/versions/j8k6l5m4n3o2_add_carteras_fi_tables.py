"""add cartera_fi_nac, cartera_fi_ext, cartera_fi_met_part, cartera_fi_fut_fw tables

Revision ID: j8k6l5m4n3o2
Revises: i7j5k4l3m2n1
Create Date: 2026-06-07 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'j8k6l5m4n3o2'
down_revision: Union[str, None] = 'i7j5k4l3m2n1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

N = sa.Numeric(25, 4)
N10 = sa.Numeric(10, 4)
N20 = sa.Numeric(20, 4)
S5  = sa.String(5)
S10 = sa.String(10)
S20 = sa.String(20)


def _base_cols():
    return [
        sa.Column('id',        sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('run_fondo', S20, nullable=False),
        sa.Column('periodo',   sa.Date(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table('cartera_fi_nac',
        *_base_cols(),
        sa.Column('clasif_esf',            S10,   nullable=True),
        sa.Column('nemotecnico',            sa.Text(), nullable=True),
        sa.Column('rut_emisor',             S20,   nullable=True),
        sa.Column('cod_pais',               S5,    nullable=True),
        sa.Column('tipo_instrumento',       S20,   nullable=True),
        sa.Column('fecha_vencimiento',      S20,   nullable=True),
        sa.Column('situacion_instrumento',  S5,    nullable=True),
        sa.Column('clasif_riesgo',          S20,   nullable=True),
        sa.Column('grupo_empresarial',      S20,   nullable=True),
        sa.Column('cant_unidades',          N,     nullable=True),
        sa.Column('tipo_unidades',          S10,   nullable=True),
        sa.Column('tir_val_par_precio',     N20,   nullable=True),
        sa.Column('cod_valorizacion',       S10,   nullable=True),
        sa.Column('base_tasa',              S10,   nullable=True),
        sa.Column('tipo_interes',           S10,   nullable=True),
        sa.Column('valorizacion_cierre',    N,     nullable=True),
        sa.Column('cod_moneda_liquidacion', S10,   nullable=True),
        sa.Column('cod_pais_transaccion',   S5,    nullable=True),
        sa.Column('pct_capital_emisor',     N10,   nullable=True),
        sa.Column('pct_activo_emisor',      N10,   nullable=True),
        sa.Column('pct_activo_fondo',       N10,   nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_fi_nac_periodo',   'cartera_fi_nac', ['periodo'])
    op.create_index('ix_cartera_fi_nac_run_fondo', 'cartera_fi_nac', ['run_fondo'])

    op.create_table('cartera_fi_ext',
        *_base_cols(),
        sa.Column('clasif_esf',            S10,       nullable=True),
        sa.Column('nemo_isin',             sa.Text(), nullable=True),
        sa.Column('nombre_emisor_corto',   sa.Text(), nullable=True),
        sa.Column('nombre_emisor',         sa.Text(), nullable=True),
        sa.Column('cod_pais',              S5,        nullable=True),
        sa.Column('tipo_instrumento',      S20,       nullable=True),
        sa.Column('fecha_vencimiento',     S20,       nullable=True),
        sa.Column('situacion_instrumento', S5,        nullable=True),
        sa.Column('clasif_riesgo',         S20,       nullable=True),
        sa.Column('cant_unidades',         N,         nullable=True),
        sa.Column('tipo_unidades',         S10,       nullable=True),
        sa.Column('cod_valorizacion',      S10,       nullable=True),
        sa.Column('tir_val_par_precio',    N20,       nullable=True),
        sa.Column('base_tasa',             S10,       nullable=True),
        sa.Column('tipo_interes',          S10,       nullable=True),
        sa.Column('valorizacion_cierre',   N,         nullable=True),
        sa.Column('cod_moneda_liquidacion',S10,       nullable=True),
        sa.Column('cod_pais_transaccion',  S5,        nullable=True),
        sa.Column('pct_capital_emisor',    N10,       nullable=True),
        sa.Column('pct_activo_emisor',     N10,       nullable=True),
        sa.Column('pct_activo_fondo',      N10,       nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_fi_ext_periodo',   'cartera_fi_ext', ['periodo'])
    op.create_index('ix_cartera_fi_ext_run_fondo', 'cartera_fi_ext', ['run_fondo'])

    op.create_table('cartera_fi_met_part',
        *_base_cols(),
        sa.Column('clasif_esf',              S10,       nullable=True),
        sa.Column('nemotecnico',             sa.Text(), nullable=True),
        sa.Column('rut_emisor',              S20,       nullable=True),
        sa.Column('cod_pais',                S5,        nullable=True),
        sa.Column('tipo_instrumento',        S20,       nullable=True),
        sa.Column('situacion_instrumento',   S5,        nullable=True),
        sa.Column('acciones_suscritas_pag',  N,         nullable=True),
        sa.Column('pct_participacion',       N10,       nullable=True),
        sa.Column('patrimonio_contable',     N,         nullable=True),
        sa.Column('valor_contable',          N,         nullable=True),
        sa.Column('ajuste_valor_pat',        N,         nullable=True),
        sa.Column('correc_monetaria',        N,         nullable=True),
        sa.Column('cod_moneda',              S10,       nullable=True),
        sa.Column('cod_pais_transaccion',    S5,        nullable=True),
        sa.Column('pct_activo_fondo',        N10,       nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_fi_met_part_periodo',   'cartera_fi_met_part', ['periodo'])
    op.create_index('ix_cartera_fi_met_part_run_fondo', 'cartera_fi_met_part', ['run_fondo'])

    op.create_table('cartera_fi_fut_fw',
        *_base_cols(),
        sa.Column('activo_subyacente',       sa.Text(), nullable=True),
        sa.Column('tipo_contrato',           S20,       nullable=True),
        sa.Column('cod_moneda_precio',       S10,       nullable=True),
        sa.Column('fecha_inicio',            S20,       nullable=True),
        sa.Column('fecha_vencimiento',       S20,       nullable=True),
        sa.Column('contraparte',             sa.Text(), nullable=True),
        sa.Column('cod_moneda_subyacente',   S10,       nullable=True),
        sa.Column('cod_pais',                S5,        nullable=True),
        sa.Column('posicion',                S5,        nullable=True),
        sa.Column('monto_nominal',           N,         nullable=True),
        sa.Column('precio_pactado',          N20,       nullable=True),
        sa.Column('valor_razonable_activo',  N,         nullable=True),
        sa.Column('valor_razonable_pasivo',  N,         nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_fi_fut_fw_periodo',   'cartera_fi_fut_fw', ['periodo'])
    op.create_index('ix_cartera_fi_fut_fw_run_fondo', 'cartera_fi_fut_fw', ['run_fondo'])


def downgrade() -> None:
    for tbl in ['cartera_fi_fut_fw', 'cartera_fi_met_part', 'cartera_fi_ext', 'cartera_fi_nac']:
        op.drop_index(f'ix_{tbl}_run_fondo', table_name=tbl)
        op.drop_index(f'ix_{tbl}_periodo',   table_name=tbl)
        op.drop_table(tbl)

"""rebuild cartera tables with semantic column names, drop nombre_fondo

Revision ID: a3f9c2d1e5b8
Revises: cd6b80f447dc
Create Date: 2026-06-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a3f9c2d1e5b8'
down_revision: Union[str, None] = 'cd6b80f447dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_cartera_naci_periodo', table_name='cartera_naci')
    op.drop_table('cartera_naci')
    op.drop_index('ix_cartera_extr_periodo', table_name='cartera_extr')
    op.drop_table('cartera_extr')
    op.drop_index('ix_cartera_opci_periodo', table_name='cartera_opci')
    op.drop_table('cartera_opci')
    op.drop_index('ix_cartera_futu_periodo', table_name='cartera_futu')
    op.drop_table('cartera_futu')
    op.drop_index('ix_cartera_opla_periodo', table_name='cartera_opla')
    op.drop_table('cartera_opla')

    op.create_table(
        'cartera_naci',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('nemotecnico', sa.Text(), nullable=True),
        sa.Column('rut_emisor', sa.Text(), nullable=True),
        sa.Column('dv_emisor', sa.Text(), nullable=True),
        sa.Column('codigo_pais_emisor', sa.Text(), nullable=True),
        sa.Column('tipo_instrumento', sa.Text(), nullable=True),
        sa.Column('fecha_vencimiento', sa.Text(), nullable=True),
        sa.Column('situacion_instrumento', sa.Text(), nullable=True),
        sa.Column('clasificacion_riesgo', sa.Text(), nullable=True),
        sa.Column('codigo_grupo_empresarial', sa.Text(), nullable=True),
        sa.Column('cantidad_unidades', sa.Text(), nullable=True),
        sa.Column('tipo_unidades', sa.Text(), nullable=True),
        sa.Column('tir', sa.Text(), nullable=True),
        sa.Column('porcentaje_valor_par', sa.Text(), nullable=True),
        sa.Column('valor_relevante', sa.Text(), nullable=True),
        sa.Column('codigo_valorizacion', sa.Text(), nullable=True),
        sa.Column('base_tasa', sa.Text(), nullable=True),
        sa.Column('tipo_interes', sa.Text(), nullable=True),
        sa.Column('valorizacion_cierre', sa.Text(), nullable=True),
        sa.Column('moneda_liquidacion', sa.Text(), nullable=True),
        sa.Column('pais_transaccion', sa.Text(), nullable=True),
        sa.Column('porcentaje_capital_emisor', sa.Text(), nullable=True),
        sa.Column('porcentaje_activos_emisor', sa.Text(), nullable=True),
        sa.Column('porcentaje_activos_fondo', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_naci_periodo', 'cartera_naci', ['periodo'])

    op.create_table(
        'cartera_extr',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('nemotecnico', sa.Text(), nullable=True),
        sa.Column('nombre_emisor', sa.Text(), nullable=True),
        sa.Column('codigo_pais_emisor', sa.Text(), nullable=True),
        sa.Column('tipo_instrumento', sa.Text(), nullable=True),
        sa.Column('fecha_vencimiento', sa.Text(), nullable=True),
        sa.Column('situacion_instrumento', sa.Text(), nullable=True),
        sa.Column('clasificacion_riesgo', sa.Text(), nullable=True),
        sa.Column('nombre_grupo_empresarial', sa.Text(), nullable=True),
        sa.Column('cantidad_unidades', sa.Text(), nullable=True),
        sa.Column('tipo_unidades', sa.Text(), nullable=True),
        sa.Column('tir', sa.Text(), nullable=True),
        sa.Column('porcentaje_valor_par', sa.Text(), nullable=True),
        sa.Column('valor_relevante', sa.Text(), nullable=True),
        sa.Column('codigo_valorizacion', sa.Text(), nullable=True),
        sa.Column('base_tasa', sa.Text(), nullable=True),
        sa.Column('tipo_interes', sa.Text(), nullable=True),
        sa.Column('valorizacion_cierre', sa.Text(), nullable=True),
        sa.Column('moneda_liquidacion', sa.Text(), nullable=True),
        sa.Column('pais_transaccion', sa.Text(), nullable=True),
        sa.Column('porcentaje_capital_emisor', sa.Text(), nullable=True),
        sa.Column('porcentaje_activos_emisor', sa.Text(), nullable=True),
        sa.Column('porcentaje_activos_fondo', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_extr_periodo', 'cartera_extr', ['periodo'])

    op.create_table(
        'cartera_opci',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('activo_objeto', sa.Text(), nullable=True),
        sa.Column('nemotecnico', sa.Text(), nullable=True),
        sa.Column('forma_ejercicio', sa.Text(), nullable=True),
        sa.Column('fecha_expiracion', sa.Text(), nullable=True),
        sa.Column('moneda_liquidacion', sa.Text(), nullable=True),
        sa.Column('codigo_pais', sa.Text(), nullable=True),
        sa.Column('tipo_opcion', sa.Text(), nullable=True),
        sa.Column('valor_mercado_prima_unitario', sa.Text(), nullable=True),
        sa.Column('numero_contratos', sa.Text(), nullable=True),
        sa.Column('precio_ejercicio', sa.Text(), nullable=True),
        sa.Column('valor_mercado_activo_objeto', sa.Text(), nullable=True),
        sa.Column('unidades_activo_objeto', sa.Text(), nullable=True),
        sa.Column('inversion_primas', sa.Text(), nullable=True),
        sa.Column('valorizacion_precio_ejercicio', sa.Text(), nullable=True),
        sa.Column('valorizacion_mercado', sa.Text(), nullable=True),
        sa.Column('porcentaje_primas_activo_total', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_opci_periodo', 'cartera_opci', ['periodo'])

    op.create_table(
        'cartera_futu',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('activo_objeto', sa.Text(), nullable=True),
        sa.Column('nemotecnico', sa.Text(), nullable=True),
        sa.Column('unidad_cotizacion', sa.Text(), nullable=True),
        sa.Column('fecha_vencimiento', sa.Text(), nullable=True),
        sa.Column('moneda_liquidacion', sa.Text(), nullable=True),
        sa.Column('codigo_pais', sa.Text(), nullable=True),
        sa.Column('posicion', sa.Text(), nullable=True),
        sa.Column('unidades_nominales_totales', sa.Text(), nullable=True),
        sa.Column('precio_futuro', sa.Text(), nullable=True),
        sa.Column('monto_comprometido', sa.Text(), nullable=True),
        sa.Column('valorizacion_mercado', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_futu_periodo', 'cartera_futu', ['periodo'])

    op.create_table(
        'cartera_opla',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('activo_objeto', sa.Text(), nullable=True),
        sa.Column('nemotecnico', sa.Text(), nullable=True),
        sa.Column('forma_ejercicio', sa.Text(), nullable=True),
        sa.Column('fecha_expiracion', sa.Text(), nullable=True),
        sa.Column('moneda_liquidacion', sa.Text(), nullable=True),
        sa.Column('codigo_pais', sa.Text(), nullable=True),
        sa.Column('tipo_opcion', sa.Text(), nullable=True),
        sa.Column('valor_mercado_prima_unitario', sa.Text(), nullable=True),
        sa.Column('numero_contratos', sa.Text(), nullable=True),
        sa.Column('precio_ejercicio', sa.Text(), nullable=True),
        sa.Column('valor_mercado_activo_objeto', sa.Text(), nullable=True),
        sa.Column('unidades_activo_objeto', sa.Text(), nullable=True),
        sa.Column('inversion_primas', sa.Text(), nullable=True),
        sa.Column('valorizacion_precio_ejercicio', sa.Text(), nullable=True),
        sa.Column('valorizacion_mercado', sa.Text(), nullable=True),
        sa.Column('porcentaje_primas_activo_total', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_opla_periodo', 'cartera_opla', ['periodo'])


def downgrade() -> None:
    op.drop_index('ix_cartera_naci_periodo', table_name='cartera_naci')
    op.drop_table('cartera_naci')
    op.drop_index('ix_cartera_extr_periodo', table_name='cartera_extr')
    op.drop_table('cartera_extr')
    op.drop_index('ix_cartera_opci_periodo', table_name='cartera_opci')
    op.drop_table('cartera_opci')
    op.drop_index('ix_cartera_futu_periodo', table_name='cartera_futu')
    op.drop_table('cartera_futu')
    op.drop_index('ix_cartera_opla_periodo', table_name='cartera_opla')
    op.drop_table('cartera_opla')

    op.create_table(
        'cartera_naci',
        sa.Column('ffm_6010100', sa.Text(), nullable=True),
        sa.Column('ffm_6010211', sa.Text(), nullable=True),
        sa.Column('ffm_6010212', sa.Text(), nullable=True),
        sa.Column('ffm_6010300', sa.Text(), nullable=True),
        sa.Column('ffm_6010400', sa.Text(), nullable=True),
        sa.Column('ffm_6010500', sa.Text(), nullable=True),
        sa.Column('ffm_6010600', sa.Text(), nullable=True),
        sa.Column('ffm_6010700', sa.Text(), nullable=True),
        sa.Column('ffm_6010800', sa.Text(), nullable=True),
        sa.Column('ffm_6010900', sa.Text(), nullable=True),
        sa.Column('ffm_6011000', sa.Text(), nullable=True),
        sa.Column('ffm_tir_6011111', sa.Text(), nullable=True),
        sa.Column('ffm_par_6011111', sa.Text(), nullable=True),
        sa.Column('ffm_rel_6011111', sa.Text(), nullable=True),
        sa.Column('ffm_6011112', sa.Text(), nullable=True),
        sa.Column('ffm_6011113', sa.Text(), nullable=True),
        sa.Column('ffm_6011114', sa.Text(), nullable=True),
        sa.Column('ffm_6011200', sa.Text(), nullable=True),
        sa.Column('ffm_6011300', sa.Text(), nullable=True),
        sa.Column('ffm_6011400', sa.Text(), nullable=True),
        sa.Column('ffm_6011511', sa.Text(), nullable=True),
        sa.Column('ffm_6011512', sa.Text(), nullable=True),
        sa.Column('ffm_6011513', sa.Text(), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('nombre_fondo', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_naci_periodo', 'cartera_naci', ['periodo'])

    op.create_table(
        'cartera_extr',
        sa.Column('ffm_6020100', sa.Text(), nullable=True),
        sa.Column('ffm_6020200', sa.Text(), nullable=True),
        sa.Column('ffm_6020300', sa.Text(), nullable=True),
        sa.Column('ffm_6020400', sa.Text(), nullable=True),
        sa.Column('ffm_6020500', sa.Text(), nullable=True),
        sa.Column('ffm_6020600', sa.Text(), nullable=True),
        sa.Column('ffm_6020700', sa.Text(), nullable=True),
        sa.Column('ffm_6020800', sa.Text(), nullable=True),
        sa.Column('ffm_6020900', sa.Text(), nullable=True),
        sa.Column('ffm_6021000', sa.Text(), nullable=True),
        sa.Column('ffm_tir_6021111', sa.Text(), nullable=True),
        sa.Column('ffm_par_6021111', sa.Text(), nullable=True),
        sa.Column('ffm_rel_6021111', sa.Text(), nullable=True),
        sa.Column('ffm_6021112', sa.Text(), nullable=True),
        sa.Column('ffm_6021113', sa.Text(), nullable=True),
        sa.Column('ffm_6021114', sa.Text(), nullable=True),
        sa.Column('ffm_6021200', sa.Text(), nullable=True),
        sa.Column('ffm_6021300', sa.Text(), nullable=True),
        sa.Column('ffm_6021400', sa.Text(), nullable=True),
        sa.Column('ffm_6021511', sa.Text(), nullable=True),
        sa.Column('ffm_6021512', sa.Text(), nullable=True),
        sa.Column('ffm_6021513', sa.Text(), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('nombre_fondo', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_extr_periodo', 'cartera_extr', ['periodo'])

    op.create_table(
        'cartera_opci',
        sa.Column('ffm_6030111', sa.Text(), nullable=True),
        sa.Column('ffm_6030112', sa.Text(), nullable=True),
        sa.Column('ffm_6030113', sa.Text(), nullable=True),
        sa.Column('ffm_6030114', sa.Text(), nullable=True),
        sa.Column('ffm_6030115', sa.Text(), nullable=True),
        sa.Column('ffm_6030116', sa.Text(), nullable=True),
        sa.Column('ffm_6030200', sa.Text(), nullable=True),
        sa.Column('ffm_6030300', sa.Text(), nullable=True),
        sa.Column('ffm_6030400', sa.Text(), nullable=True),
        sa.Column('ffm_6030500', sa.Text(), nullable=True),
        sa.Column('ffm_6030600', sa.Text(), nullable=True),
        sa.Column('ffm_6030700', sa.Text(), nullable=True),
        sa.Column('ffm_6030800', sa.Text(), nullable=True),
        sa.Column('ffm_6030900', sa.Text(), nullable=True),
        sa.Column('ffm_6031000', sa.Text(), nullable=True),
        sa.Column('ffm_6031100', sa.Text(), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('nombre_fondo', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_opci_periodo', 'cartera_opci', ['periodo'])

    op.create_table(
        'cartera_futu',
        sa.Column('ffm_6040111', sa.Text(), nullable=True),
        sa.Column('ffm_6040112', sa.Text(), nullable=True),
        sa.Column('ffm_6040113', sa.Text(), nullable=True),
        sa.Column('ffm_6040114', sa.Text(), nullable=True),
        sa.Column('ffm_6040115', sa.Text(), nullable=True),
        sa.Column('ffm_6040116', sa.Text(), nullable=True),
        sa.Column('ffm_6040200', sa.Text(), nullable=True),
        sa.Column('ffm_6040300', sa.Text(), nullable=True),
        sa.Column('ffm_6040400', sa.Text(), nullable=True),
        sa.Column('ffm_6040500', sa.Text(), nullable=True),
        sa.Column('ffm_6040600', sa.Text(), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('nombre_fondo', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_futu_periodo', 'cartera_futu', ['periodo'])

    op.create_table(
        'cartera_opla',
        sa.Column('ffm_6050111', sa.Text(), nullable=True),
        sa.Column('ffm_6050112', sa.Text(), nullable=True),
        sa.Column('ffm_6050113', sa.Text(), nullable=True),
        sa.Column('ffm_6050114', sa.Text(), nullable=True),
        sa.Column('ffm_6050115', sa.Text(), nullable=True),
        sa.Column('ffm_6050116', sa.Text(), nullable=True),
        sa.Column('ffm_6050200', sa.Text(), nullable=True),
        sa.Column('ffm_6050300', sa.Text(), nullable=True),
        sa.Column('ffm_6050400', sa.Text(), nullable=True),
        sa.Column('ffm_6050500', sa.Text(), nullable=True),
        sa.Column('ffm_6050600', sa.Text(), nullable=True),
        sa.Column('ffm_6050700', sa.Text(), nullable=True),
        sa.Column('ffm_6050800', sa.Text(), nullable=True),
        sa.Column('ffm_6050900', sa.Text(), nullable=True),
        sa.Column('ffm_6051000', sa.Text(), nullable=True),
        sa.Column('ffm_6051100', sa.Text(), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('periodo', sa.Date(), nullable=False),
        sa.Column('run_fondo', sa.String(20), nullable=False),
        sa.Column('nombre_fondo', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cartera_opla_periodo', 'cartera_opla', ['periodo'])

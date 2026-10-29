"""Fixed allowlist for the downloader UI; never accept callable names from clients."""
JOBS = [
    ('ref_codes_refresh','Códigos CMF','Referencias','Diario','Monedas, países e instrumentos.'),
    ('bonds_tickers','Nemotécnicos de bonos','Referencias','Diario','Identificadores de instrumentos de deuda.'),
    ('fm_identity','Registro de fondos mutuos','Fondos mutuos','Diario','Fondos, nombres y administradoras.'),
    ('mf_tickers','Nemotécnicos FM','Fondos mutuos','Diario','Identificadores de fondos y series.'),
    ('fi_tickers','Nemotécnicos FI','Fondos de inversión','Diario','Identificadores de fondos y series.'),
    ('fi_identity','Registro de fondos de inversión','Fondos de inversión','Diario','Fondos vigentes y no vigentes.'),
    ('mf_daily_nav','Valores cuota FM','Fondos mutuos','Diario','Revisa al menos los últimos 14 días; actualiza flujos, patrimonio y rentabilidades.'),
    ('mf_rentabilidad','Rentabilidades FM','Cálculos','Diario','Recalcula retornos con distribuciones.'),
    ('fi_daily_nav','Valores cuota FI','Fondos de inversión','Diario','Revisa los últimos tres meses; actualiza flujos, patrimonio y rentabilidades.'),
    ('fund_deposits','Nuevos fondos','Registros','Diario','Depósitos y reglamentos de fondos.'),
    ('insiders_transactions','Insiders','Registros','Diario','Transacciones reportadas por entidades vigentes y no vigentes.'),
    ('insurance_portfolios','Carteras de seguros','Seguros','Diario','Revisa los últimos dos meses publicados de seguros de vida.'),
    ('mf_portfolios','Carteras FM','Fondos mutuos','Mensual','Actualiza carteras y clasificaciones.'),
    ('mf_costs','Costos TAC','Fondos mutuos','Mensual','Revisa el último mes cargado y los siguientes disponibles.'),
    ('dividends','Dividendos','Registros','Diario','Distribuciones publicadas por la Bolsa; recalcula rentabilidades FI.'),
    ('fi_shareholders','Aportantes y cuotas FI','Fondos de inversión','Mensual','Revisa los dos últimos trimestres cerrados.'),
    ('fi_portfolios','Carteras FI','Fondos de inversión','Mensual','Busca el último trimestre cerrado y actualiza clasificaciones.'),
    ('mf_categories','Categorías FM','Cálculos','Mensual','Clasifica carteras; respeta categorías manuales.'),
    ('fi_categories','Categorías FI','Cálculos','Mensual','Clasifica carteras; respeta categorías manuales.'),
    ('fm_flow_adjustments','Ajustes de flujos FM','Cálculos','Diario','Detecta migraciones internas entre fondos.'),
    ('fi_rentabilidad','Rentabilidades FI','Cálculos','Diario','Recalcula retornos y distribuciones.'),
    ('administradores','Administradoras','Cálculos','Diario','Actualiza el directorio consolidado.'),
    ('fm_nnm_daily','Net New Money FM','Cálculos','Tras NAV','Actualiza flujos calculados por día.'),
    ('fm_industry_monthly','Patrimonio mensual FM','Cálculos','Tras NAV','Actualiza la evolución mensual.'),
    ('fi_nnm_daily','Net New Money FI','Cálculos','Tras NAV','Actualiza flujos calculados por día.'),
    ('fi_industry_monthly','Patrimonio mensual FI','Cálculos','Tras NAV','Actualiza la evolución mensual.'),
]
CATALOG = {j[0]: dict(id=j[0],name=j[1],group=j[2],frequency=j[3],description=j[4]) for j in JOBS}
AUTO = {'mf_daily_nav':['fm_nnm_daily','fm_industry_monthly'],
        'fi_daily_nav':['fi_nnm_daily','fi_industry_monthly']}
AFTER = {'mf_daily_nav':['mf_rentabilidad'], 'fi_daily_nav':['fi_rentabilidad'],
         'mf_portfolios':['mf_categories'], 'fi_portfolios':['fi_categories'],
         'fm_identity':['administradores'], 'fi_identity':['administradores'],
         'dividends':['fi_rentabilidad']}
ALL_PLAN = [j[0] for j in JOBS[:22]]


def plan_for(job_id):
    if job_id == 'all':
        return ALL_PLAN.copy()
    if job_id not in CATALOG:
        raise ValueError('Trabajo no disponible.')
    return [job_id, *AFTER.get(job_id, [])]


def affected_jobs(plan):
    return set(plan).union(*(AUTO.get(j, []) for j in plan))

"""Audited FI series continuity, linked returns and NAV-implied flows."""
from alembic import op
revision = 'e1f2a3b4c5d6'
down_revision = 'd0e1f2a3b4c5'
branch_labels = None
depends_on = None

SQL = r"""
CREATE TABLE IF NOT EXISTS fi_series_link_sets (
 run_fondo varchar(20) PRIMARY KEY, version integer NOT NULL DEFAULT 0,
 updated_by varchar(120) NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS fi_series_links (
 run_fondo varchar(20) NOT NULL REFERENCES fi_series_link_sets(run_fondo),
 source_serie varchar(50) NOT NULL, target_serie varchar(50) NOT NULL,
 effective_date date NOT NULL, event_type varchar(20) NOT NULL,
 ratio numeric NOT NULL, source_url text NOT NULL, reason text NOT NULL,
 PRIMARY KEY(run_fondo,source_serie), UNIQUE(run_fondo,target_serie),
 CHECK(source_serie<>target_serie), CHECK(event_type IN ('rename','conversion')),
 CHECK(ratio>0 AND ratio::text NOT IN ('NaN','Infinity','-Infinity')),
 CHECK(event_type<>'rename' OR ratio=1)
);
CREATE TABLE IF NOT EXISTS fi_series_link_audit (
 id bigserial PRIMARY KEY, run_fondo varchar(20) NOT NULL, version integer NOT NULL,
 actor varchar(120) NOT NULL, reason text NOT NULL, before_state jsonb NOT NULL,
 after_state jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(run_fondo,version)
);
CREATE INDEX IF NOT EXISTS ix_fi_series_audit_run ON fi_series_link_audit(run_fondo,id);

CREATE OR REPLACE FUNCTION fi_normalized_currency(value text) RETURNS text
 LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $function$
 SELECT CASE upper(trim(value)) WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
 WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD' WHEN '0' THEN NULL WHEN '' THEN NULL
 ELSE upper(trim(value)) END
$function$;

-- Ratio = successor units per predecessor unit. Historical prices/dividends
-- are divided by the cumulative ratio; money amounts are never rescaled.
CREATE OR REPLACE FUNCTION fi_series_segments(p_run text,p_serie text,p_end date,p_linked boolean DEFAULT true)
RETURNS TABLE(source_serie text,start_date date,end_date date,factor numeric)
LANGUAGE sql STABLE AS $function$
 WITH RECURSIVE chain(serie,until_date,factor,path) AS (
  SELECT p_serie,p_end+1,1::numeric,ARRAY[p_serie]::text[]
  UNION ALL
  SELECT l.source_serie::text,LEAST(c.until_date,l.effective_date),c.factor*l.ratio,c.path||l.source_serie::text
  FROM chain c JOIN fi_series_links l ON l.run_fondo=p_run AND l.target_serie=c.serie
  WHERE p_linked AND NOT l.source_serie=ANY(c.path) AND cardinality(c.path)<32
 )
 SELECT c.serie,l.effective_date,c.until_date,c.factor FROM chain c
 LEFT JOIN fi_series_links l ON p_linked AND l.run_fondo=p_run AND l.target_serie=c.serie
$function$;

CREATE OR REPLACE FUNCTION fi_nav_history(p_run text,p_serie text,p_end date,p_linked boolean DEFAULT true)
RETURNS TABLE(fecha date,source_serie text,valor_libro numeric,patrimonio_neto bigint,currency text)
LANGUAGE sql STABLE AS $function$
 SELECT v.fecha,s.source_serie,v.valor_libro/s.factor,v.patrimonio_neto,fi_normalized_currency(v.moneda)
 FROM fi_series_segments(p_run,p_serie,p_end,p_linked) s JOIN valores_cuota_fi v
 ON v.run_fondo=p_run AND v.serie IS NOT DISTINCT FROM s.source_serie
 AND (s.start_date IS NULL OR v.fecha>=s.start_date) AND v.fecha<s.end_date
$function$;

CREATE OR REPLACE FUNCTION fi_series_distributions(p_run text,p_serie text,p_end date,p_linked boolean DEFAULT true)
RETURNS TABLE(fecha date,amount numeric,currency text)
LANGUAGE sql STABLE AS $function$
 SELECT d.fec_lim::date,d.val_acc/s.factor,fi_normalized_currency(d.moneda)
 FROM fi_series_segments(p_run,p_serie,p_end,p_linked) s
 JOIN (SELECT DISTINCT run_fondo,serie,replace(nemotecnico,'-','') nemo FROM nemotecnicos_fi) n
 ON n.run_fondo=p_run AND n.serie IS NOT DISTINCT FROM s.source_serie
 JOIN dividendos d ON replace(d.nemo,'-','')=n.nemo
 WHERE d.fec_lim IS NOT NULL AND d.val_acc>0 AND d.val_acc::text NOT IN ('NaN','Infinity','-Infinity')
 AND (s.start_date IS NULL OR d.fec_lim::date>=s.start_date) AND d.fec_lim::date<s.end_date
$function$;

CREATE OR REPLACE FUNCTION fi_linked_returns(p_run text,p_serie text,p_end date)
RETURNS TABLE(r_1d numeric,r_1w numeric,r_1m numeric,r_1y numeric,r_5y numeric,r_ytd numeric)
LANGUAGE sql STABLE AS $function$
 WITH nav AS MATERIALIZED (SELECT * FROM fi_nav_history(p_run,p_serie,p_end)),
 current_nav AS (SELECT * FROM nav WHERE fecha=p_end AND valor_libro>0
   AND valor_libro::text NOT IN ('NaN','Infinity','-Infinity') AND patrimonio_neto>=0),
 periods(label,base) AS (VALUES ('1d',p_end-1),('1w',p_end-7),('1m',p_end-30),
 ('1y',p_end-365),('5y',p_end-1825),('ytd',date_trunc('year',p_end)::date-1)),
 divs AS MATERIALIZED (SELECT * FROM fi_series_distributions(p_run,p_serie,p_end)),
 returns AS (
 SELECT p.label,CASE WHEN b.valor_libro>0 AND b.valor_libro::text NOT IN ('NaN','Infinity','-Infinity')
 AND b.currency=c.currency AND NOT EXISTS(SELECT 1 FROM nav n WHERE n.fecha BETWEEN b.fecha AND p_end AND n.currency IS DISTINCT FROM c.currency)
 THEN round((c.valor_libro-b.valor_libro+COALESCE((SELECT sum(d.amount) FROM divs d
 WHERE d.fecha>p.base AND d.fecha<=p_end AND d.currency=c.currency),0))/b.valor_libro*100,4) END value
 FROM periods p CROSS JOIN current_nav c LEFT JOIN nav b ON b.fecha=p.base
 )
 SELECT max(value) FILTER(WHERE label='1d'),max(value) FILTER(WHERE label='1w'),
 max(value) FILTER(WHERE label='1m'),max(value) FILTER(WHERE label='1y'),
 max(value) FILTER(WHERE label='5y'),max(value) FILTER(WHERE label='ytd') FROM returns
$function$;

-- Only linked series need an overlay. Ordinary rankings keep the daily MV path.
CREATE OR REPLACE VIEW v_rentabilidad_fi_continuous AS
 SELECT r.* FROM mv_rentabilidad_fi r WHERE NOT EXISTS (
 SELECT 1 FROM fi_series_links l WHERE l.run_fondo=r.run_fondo AND l.target_serie=r.serie AND l.effective_date<=r.fecha_calculo)
 UNION ALL
 SELECT r.run_fondo,r.serie,r.fecha_calculo,r.valor_actual,r.razon_social,r.administrador,
 v.r_1d,v.r_1w,v.r_1m,v.r_1y,v.r_5y,v.r_ytd
 FROM mv_rentabilidad_fi r JOIN fi_series_links l ON l.run_fondo=r.run_fondo
 AND l.target_serie=r.serie AND l.effective_date<=r.fecha_calculo
 CROSS JOIN LATERAL fi_linked_returns(r.run_fondo,r.serie,r.fecha_calculo) v;

-- One canonical identity per chain for flows. Never sum predecessor and successor
-- histories as separate funds or rescale patrimonio.
CREATE OR REPLACE VIEW v_fi_linked_nav AS
 WITH linked_runs AS (SELECT DISTINCT run_fondo FROM fi_series_links),
 series AS (
 SELECT DISTINCT v.run_fondo,v.serie FROM valores_cuota_fi v JOIN linked_runs USING(run_fondo)
 UNION SELECT run_fondo,target_serie FROM fi_series_links
 ), leaves AS (
 SELECT s.* FROM series s WHERE NOT EXISTS(SELECT 1 FROM fi_series_links l
 WHERE l.run_fondo=s.run_fondo AND l.source_serie=s.serie)
 )
 SELECT l.run_fondo,l.serie,n.fecha,n.currency moneda,n.valor_libro,n.patrimonio_neto
 FROM leaves l CROSS JOIN LATERAL fi_nav_history(l.run_fondo,l.serie,CURRENT_DATE) n;

CREATE OR REPLACE VIEW v_fi_nav_canonical AS
 SELECT v.run_fondo,v.serie,v.fecha,v.moneda::text,v.valor_libro,v.patrimonio_neto
 FROM valores_cuota_fi v WHERE NOT EXISTS(SELECT 1 FROM fi_series_links l WHERE l.run_fondo=v.run_fondo)
 UNION ALL SELECT * FROM v_fi_linked_nav;

CREATE OR REPLACE VIEW v_nnm_daily_fi_continuous AS
 SELECT d.* FROM mv_nnm_daily_fi d WHERE NOT EXISTS(SELECT 1 FROM fi_series_links l WHERE l.run_fondo=d.run_fondo)
 UNION ALL SELECT * FROM (WITH
      source AS MATERIALIZED (
        SELECT v.run_fondo, v.serie, v.fecha, CASE UPPER(TRIM(v.moneda))
        WHEN '$$' THEN 'CLP' WHEN '$' THEN 'CLP'
        WHEN 'PROM' THEN 'USD' WHEN 'US$' THEN 'USD'
        WHEN '0' THEN NULL WHEN '' THEN NULL
        ELSE UPPER(TRIM(v.moneda)) END currency,
          v.valor_libro price,
          COALESCE(v.valor_libro=0 AND v.patrimonio_neto=0, FALSE) empty_series,
          CASE WHEN v.valor_libro>0 AND v.valor_libro::text NOT IN ('NaN','Infinity','-Infinity')
            AND v.patrimonio_neto>=0 THEN v.patrimonio_neto::numeric/v.valor_libro END units,
          v.patrimonio_neto IS NOT NULL AND v.patrimonio_neto>=0 has_nav
        FROM v_fi_linked_nav v
        WHERE v.fecha >= DATE '2019-12-25'
      ), paired AS (
        SELECT *, LAG(fecha) OVER w previous_date, LAG(currency) OVER w previous_currency,
          LAG(units) OVER w previous_units, LAG(empty_series) OVER w previous_empty_series
        FROM source WINDOW w AS (PARTITION BY run_fondo,serie ORDER BY fecha)
      ), comparable AS (
        SELECT *, COALESCE(empty_series AND previous_empty_series, FALSE) unchanged_empty
        FROM paired
      ), daily AS (
        SELECT run_fondo,fecha,currency,
          ARRAY_AGG(serie ORDER BY serie) series,
          BOOL_OR(has_nav) has_nav,
          BOOL_AND((unchanged_empty OR (units IS NOT NULL AND previous_units IS NOT NULL))
            AND currency IS NOT DISTINCT FROM previous_currency) valid_values,
          MIN(previous_date) earliest_base, MAX(previous_date) latest_base,
          SUM(CASE WHEN unchanged_empty THEN 0 ELSE (units-previous_units)*price END) estimate
        FROM comparable GROUP BY run_fondo,fecha,currency
      ), snapshots AS (
        SELECT *, LAG(fecha) OVER w previous_snapshot, LAG(series) OVER w previous_series
        FROM daily WINDOW w AS (PARTITION BY run_fondo,currency ORDER BY fecha)
      ), checked AS (
        SELECT *, CASE
          WHEN previous_snapshot IS NULL THEN 'missing_base'
          WHEN fecha-previous_snapshot>7 THEN 'gap'
          WHEN series IS DISTINCT FROM previous_series THEN 'series_change'
          WHEN NOT valid_values OR earliest_base IS DISTINCT FROM previous_snapshot
            OR latest_base IS DISTINCT FROM previous_snapshot THEN 'incomparable_nav'
          END exclusion
        FROM snapshots
      )
SELECT run_fondo,fecha,currency,has_nav,previous_snapshot,estimate,exclusion FROM checked) linked;

INSERT INTO fi_series_link_sets(run_fondo,version,updated_by)
VALUES ('7018',1,'migration:official-btg-notice') ON CONFLICT DO NOTHING;
INSERT INTO fi_series_links(run_fondo,source_serie,target_serie,effective_date,event_type,ratio,source_url,reason)
VALUES ('7018','A','O','2026-09-07','rename',1,
'https://static.btgpactual.cl/assets/modificaciones/Modificacion_RI_BTG_Pactual_Small_Cap.pdf',
'Cambio de denominación A a O, vigente desde el 7 de septiembre de 2026 según aviso oficial BTG.')
ON CONFLICT DO NOTHING;
INSERT INTO fi_series_link_audit(run_fondo,version,actor,reason,before_state,after_state)
SELECT '7018',1,'migration:official-btg-notice','Aviso oficial BTG: cambio de denominación A → O.',
'[]'::jsonb,jsonb_agg(to_jsonb(l)) FROM fi_series_links l WHERE l.run_fondo='7018'
ON CONFLICT DO NOTHING;
"""

def upgrade():
    op.execute(SQL)

def downgrade():
    for name in ('v_nnm_daily_fi_continuous','v_fi_nav_canonical','v_fi_linked_nav','v_rentabilidad_fi_continuous'):
        op.execute(f'DROP VIEW IF EXISTS {name}')
    op.execute('DROP FUNCTION IF EXISTS fi_linked_returns(text,text,date)')
    op.execute('DROP FUNCTION IF EXISTS fi_series_distributions(text,text,date,boolean)')
    op.execute('DROP FUNCTION IF EXISTS fi_nav_history(text,text,date,boolean)')
    op.execute('DROP FUNCTION IF EXISTS fi_series_segments(text,text,date,boolean)')
    op.execute('DROP FUNCTION IF EXISTS fi_normalized_currency(text)')
    for name in ('fi_series_link_audit','fi_series_links','fi_series_link_sets'):
        op.execute(f'DROP TABLE IF EXISTS {name}')

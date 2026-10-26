"""Per-series FI detail returns; rankings retain their common reference date."""
from sqlalchemy import text

LATEST_SERIES_RETURNS = text("""
    WITH latest AS (
        SELECT DISTINCT ON (v.serie) v.run_fondo, v.serie,
               v.fecha AS fecha_calculo, v.valor_libro AS valor_actual
        FROM valores_cuota_fi v
        WHERE v.run_fondo=:run AND v.fecha<=CURRENT_DATE
          AND v.valor_libro>0
          AND v.valor_libro::text NOT IN ('NaN','Infinity','-Infinity')
          AND v.patrimonio_neto>=0
          AND fi_normalized_currency(v.moneda) ~ '^(?:[A-Z]{3}|UF)$'
          AND NOT EXISTS (
              SELECT 1 FROM fi_series_links l WHERE l.run_fondo=v.run_fondo
              AND l.source_serie=v.serie AND l.effective_date<=CURRENT_DATE
          )
        ORDER BY v.serie, v.fecha DESC
    )
    SELECT n.*, f.razon_social, f.administrador, r.*
    FROM latest n JOIN fondos_inversion f USING(run_fondo)
    CROSS JOIN LATERAL fi_linked_returns(n.run_fondo,n.serie,n.fecha_calculo) r
    ORDER BY n.serie
""")


def latest_series_returns(session, run):
    # Reuse confirmed series conversions, additive distributions and exact
    # baselines. Unknown/changed currencies still prevent a period calculation;
    # a fund-level currency must not overwrite an ambiguous historical series.
    return session.execute(LATEST_SERIES_RETURNS, dict(run=run)).mappings().all()

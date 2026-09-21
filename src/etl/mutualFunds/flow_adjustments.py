from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from src.base import DownloadResult
from src.db.engine import SessionLocal

logger = logging.getLogger(__name__)

FULL_SCAN_INTERVAL = timedelta(days=7)
INCREMENTAL_LOOKBACK = timedelta(days=7)

_DETECTION_CONTEXT_SQL = text("""
SELECT
    (SELECT MAX(fecha) FROM cartola_diaria) AS data_date,
    state.last_data_date,
    state.last_full_scan_at
FROM (SELECT 1) singleton
LEFT JOIN fm_flow_adjustment_state state ON state.id = 1
""")

_DETECT_FM_MIGRATIONS_SQL = text("""
WITH terminated AS (
    SELECT
        fm.run_fondo AS source_run_fondo,
        fm.razon_social_administradora AS administrador,
        fm.fecha_termino_operaciones AS termination_date,
        CASE cd.moneda
            WHEN '$$' THEN 'CLP'
            WHEN 'PROM' THEN 'USD'
            ELSE cd.moneda
        END AS currency,
        SUM(cd.patrimonio_neto)::numeric AS source_final_aum
    FROM fondo_mutuo fm
    JOIN cartola_diaria cd
      ON cd.run_fondo = fm.run_fondo
     AND cd.fecha = fm.fecha_termino_operaciones
    WHERE fm.fecha_termino_operaciones BETWEEN :scan_from AND :data_date
      AND cd.patrimonio_neto IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM cartola_diaria later
          WHERE later.run_fondo = fm.run_fondo
            AND later.fecha > fm.fecha_termino_operaciones
      )
      AND NOT EXISTS (
          SELECT 1
          FROM fm_flow_adjustments existing
          WHERE existing.source_run_fondo = fm.run_fondo
            AND existing.currency = CASE cd.moneda
                WHEN '$$' THEN 'CLP'
                WHEN 'PROM' THEN 'USD'
                ELSE cd.moneda
            END
      )
    GROUP BY
        fm.run_fondo,
        fm.razon_social_administradora,
        fm.fecha_termino_operaciones,
        CASE cd.moneda
            WHEN '$$' THEN 'CLP'
            WHEN 'PROM' THEN 'USD'
            ELSE cd.moneda
        END
),
target_flows AS (
    SELECT
        terminated.source_run_fondo,
        terminated.termination_date,
        terminated.currency,
        terminated.source_final_aum,
        cd.run_fondo AS target_run_fondo,
        cd.fecha AS event_date,
        SUM(cd.monto_aportado - cd.monto_rescatado)::numeric AS target_net
    FROM terminated
    JOIN fondo_mutuo fm
      ON fm.razon_social_administradora = terminated.administrador
     AND fm.run_fondo <> terminated.source_run_fondo
    JOIN cartola_diaria cd
      ON cd.run_fondo = fm.run_fondo
     AND cd.fecha > terminated.termination_date
     AND cd.fecha <= terminated.termination_date + 7
     AND CASE cd.moneda
             WHEN '$$' THEN 'CLP'
             WHEN 'PROM' THEN 'USD'
             ELSE cd.moneda
         END = terminated.currency
    WHERE cd.monto_aportado IS NOT NULL
    GROUP BY
        terminated.source_run_fondo,
        terminated.termination_date,
        terminated.currency,
        terminated.source_final_aum,
        cd.run_fondo,
        cd.fecha
    HAVING SUM(cd.monto_aportado - cd.monto_rescatado) > 0
),
candidates AS (
    SELECT
        flow.source_run_fondo,
        flow.target_run_fondo,
        flow.event_date,
        flow.currency,
        flow.target_net AS amount,
        flow.source_final_aum,
        ABS(flow.target_net - flow.source_final_aum)
            / NULLIF(ABS(flow.source_final_aum), 0) AS relative_difference,
        source_cat.categoria AS source_category,
        target_cat.categoria AS target_category
    FROM target_flows flow
    JOIN LATERAL (
        SELECT categoria
        FROM categoria_fm
        WHERE run_fondo = flow.source_run_fondo
          AND periodo <= flow.termination_date
        ORDER BY periodo DESC
        LIMIT 1
    ) source_cat ON TRUE
    JOIN LATERAL (
        SELECT categoria
        FROM categoria_fm
        WHERE run_fondo = flow.target_run_fondo
          AND periodo <= flow.event_date
        ORDER BY periodo DESC
        LIMIT 1
    ) target_cat ON target_cat.categoria = source_cat.categoria
    WHERE flow.source_final_aum > 0
      AND ABS(flow.target_net - flow.source_final_aum)
          / ABS(flow.source_final_aum) <= 0.01
),
ranked AS (
    SELECT
        candidates.*,
        ROW_NUMBER() OVER (
            PARTITION BY source_run_fondo, currency
            ORDER BY relative_difference, event_date, target_run_fondo
        ) AS source_rank,
        ROW_NUMBER() OVER (
            PARTITION BY target_run_fondo, event_date, currency
            ORDER BY relative_difference, source_run_fondo
        ) AS target_rank
    FROM candidates
)
INSERT INTO fm_flow_adjustments (
    source_run_fondo,
    target_run_fondo,
    event_date,
    currency,
    amount,
    source_final_aum,
    relative_difference,
    source_category,
    target_category,
    status,
    reason
)
SELECT
    source_run_fondo,
    target_run_fondo,
    event_date,
    currency,
    amount,
    source_final_aum,
    relative_difference,
    source_category,
    target_category,
    'auto_confirmed',
    'Official termination; same AGF, category, and currency; successor flow matched final AUM within 1%'
FROM ranked
WHERE source_rank = 1 AND target_rank = 1
ON CONFLICT ON CONSTRAINT uq_fm_flow_adjustment_event DO UPDATE SET
    amount = EXCLUDED.amount,
    source_final_aum = EXCLUDED.source_final_aum,
    relative_difference = EXCLUDED.relative_difference,
    source_category = EXCLUDED.source_category,
    target_category = EXCLUDED.target_category,
    reason = EXCLUDED.reason,
    updated_at = NOW()
""")

_UPDATE_STATE_SQL = text("""
INSERT INTO fm_flow_adjustment_state (
    id, last_data_date, last_full_scan_at, updated_at
)
VALUES (1, :data_date, :full_scan_at, NOW())
ON CONFLICT (id) DO UPDATE SET
    last_data_date = EXCLUDED.last_data_date,
    last_full_scan_at = COALESCE(
        EXCLUDED.last_full_scan_at,
        fm_flow_adjustment_state.last_full_scan_at
    ),
    updated_at = NOW()
""")


def run() -> DownloadResult:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        context = session.execute(_DETECTION_CONTEXT_SQL).mappings().one()
        data_date: date | None = context["data_date"]
        if data_date is None:
            return DownloadResult(skipped=1)

        last_data_date: date | None = context["last_data_date"]
        last_full_scan_at: datetime | None = context["last_full_scan_at"]
        full_scan = (
            last_data_date is None
            or last_full_scan_at is None
            or now - last_full_scan_at >= FULL_SCAN_INTERVAL
        )
        if full_scan:
            scan_from = date(data_date.year, 1, 1)
        else:
            scan_from = max(
                date(data_date.year, 1, 1),
                last_data_date - INCREMENTAL_LOOKBACK,
            )

        result = session.execute(
            _DETECT_FM_MIGRATIONS_SQL,
            {"scan_from": scan_from, "data_date": data_date},
        )
        session.execute(
            _UPDATE_STATE_SQL,
            {
                "data_date": data_date,
                "full_scan_at": now if full_scan else None,
            },
        )
        session.commit()

    rows = max(result.rowcount or 0, 0)
    logger.info(
        "FM migration scan complete data_date=%s scan_from=%s full=%s rows=%d",
        data_date,
        scan_from,
        full_scan,
        rows,
    )
    return DownloadResult(downloaded=1, rows_upserted=rows)

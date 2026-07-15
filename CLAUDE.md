# CLAUDE.md

This is the canonical project instructions file for coding agents working in this repository.

## Project Purpose

Daily ETL pipeline that downloads public datasets from the Chilean CMF (Comisión para el Mercado Financiero) and Bolsa de Santiago, loads them into PostgreSQL, and exposes a public read API.

## Stack

- **Language**: Python 3.11
- **Database**: PostgreSQL 16
- **HTTP**: `requests` (sync downloads) + `urllib3.util.retry.Retry` transport adapter
- **Data parsing**: `pandas`, `beautifulsoup4`, `xlrd`
- **ORM / migrations**: `SQLAlchemy` (sync engine) + `Alembic`
- **Scheduling**: `APScheduler` — `web` (FastAPI) and `worker` (BlockingScheduler) run as separate processes via `docker-compose`
- **API**: FastAPI
- **Deployment**: Oracle Cloud Always Free (2x AMD VMs) via `docker-compose`

## Infrastructure

Two Oracle Cloud Always Free VMs (VM.Standard.E2.1.Micro — 1 OCPU, 1 GB RAM each):

| VM | Hostname | Public IP | Private IP | Runs |
|---|---|---|---|---|
| `cmf-btg-db` | — | `146.181.47.236` | `10.0.0.42` | PostgreSQL 16 (port 5433) |
| `cmf-btg-app` | — | `146.181.34.54` | `10.0.0.10` | web + worker (Docker, port 8080) |

**API base URL**: `https://financial-cmf.ddns.net` (nginx + Let's Encrypt SSL, DuckDNS-style domain via No-IP)
**MCP endpoint**: `https://financial-cmf.ddns.net/mcp/sse` (add in Claude.ai → Settings → Integrations)

### SSH access
```bash
ssh -i ~/.ssh/oracle_cmf.key ubuntu@146.181.34.54   # app VM
ssh -i ~/.ssh/oracle_cmf.key ubuntu@146.181.47.236  # db VM
```

### Deploy (automatic via GitHub Actions)
Push to `main` triggers `.github/workflows/deploy.yml` — SSHes into the app VM, pulls, migrates, and rebuilds automatically.

Manual deploy if needed:
```bash
ssh -i ~/.ssh/oracle_cmf.key ubuntu@146.181.34.54
cd ~/CMFDownloader
git pull origin main
sudo docker compose run --rm web alembic upgrade head
sudo docker compose up -d --build
```

### DB connection (from app VM)
```
postgresql://cmf:cmf2026secure@10.0.0.42:5433/cmf
```

### DB connection (from db VM directly)
```bash
psql -h localhost -p 5433 -U cmf -d cmf
```

## Dev DB Workflow

**Rule**: all schema changes and data experiments must be validated locally before deploying to production.

Local PostgreSQL runs at `127.0.0.1:5432` (connection string in `.env`). It holds a full copy of production data (same row counts, same schema).

### Dev workflow for every change
```bash
# 1. Write the migration / code change on your feature branch
# 2. Apply and test locally
alembic upgrade head          # apply new migrations
uvicorn main:app --reload     # verify API endpoints

# 3. Only when it works locally, open the PR and merge to main
# 4. Automatic deploy picks it up via GitHub Actions
```

### New Alembic migrations
- Always use `IF NOT EXISTS` / `IF EXISTS` in raw SQL so migrations are idempotent (safe to re-run if partially applied)
- Never use `CREATE INDEX CONCURRENTLY` inside a migration — it cannot run inside Alembic's transaction block. Use `CREATE INDEX IF NOT EXISTS` instead (lock is acceptable during a deploy window)
- Test with `alembic upgrade head` locally before pushing

### Querying / debugging production
Read-only queries against production are fine via `docker exec`:
```bash
ssh -i ~/.ssh/oracle_cmf.key ubuntu@146.181.34.54
sudo docker exec cmfdownloader-web-1 python -c "
from src.db.engine import SessionLocal
from sqlalchemy import text
with SessionLocal() as s:
    print(s.execute(text('SELECT COUNT(*) FROM cartola_diaria')).scalar())
"
```
Never modify production schema or data directly — apply via Alembic migrations.

## Architecture

```
src/
├── api/                      # Public read API — CORS-open, cached (max-age=3600)
│   ├── deps.py               # Shared: Pagination (limit/offset) + CacheHook (Cache-Control header)
│   ├── mutual_funds.py       # /mutual-funds — FM list, detail, NAV history, TAC, flows, portfolio (SII-enriched)
│   ├── investment_funds.py   # /investment-funds — FI list, detail, NAV history, portfolio (SII-enriched)
│   ├── rentability.py        # /rentability/fm + /rentability/fi — rankings from MVs
│   ├── categories.py         # /categories/fm + /fi + /catalog
│   ├── industry.py           # Unified overview, screener, and AUM evolution
│   ├── shareholders.py       # /shareholders — fund/entity/admin/compare + intelligence endpoints
│   ├── admins.py             # /admins — administradora list + detail (from mv_administradores)
│   ├── emisores.py           # /emisores — company market exposure, positions, history, concentration
│   ├── ref_codes.py          # /ref-codes — ref_codes table (countries, currencies, instruments)
│   └── router.py             # Assembles all sub-routers
├── mcp_server.py             # FastMCP server — 15 tools for fund-market intelligence (see MCP section)
├── etl/                      # All ETL domain packages (extract + load per data source)
│   ├── cmf/                  # CMF reference data (non-fund)
│   │   └── ref_codes.py      # Scrapes CMF country/currency/instrument tables → ref_codes table (daily)
│   ├── sii/                  # SII (tax authority) company registry
│   │   └── load_emisores.py  # Loads ~994k Chilean companies → emisores table
│   ├── financialStatements/  # IFRS statements for all CMF-supervised companies
│   │   ├── downloaders/
│   │   │   └── financialStatementsDownloader.py
│   │   ├── loaders/
│   │   │   └── financial_statements.py
│   │   └── api.py            # FastAPI router — on-demand download trigger (auth-protected)
│   ├── mutualFunds/
│   │   ├── downloaders/      # One class per CMF mutual-fund dataset, all extend BaseDownloader
│   │   │   ├── cartolaDownloader.py
│   │   │   ├── carterasDownloader.py
│   │   │   ├── identificationDownloader.py
│   │   │   ├── nemotecnicosDownloader.py
│   │   │   ├── bondsNemotecnicos.py
│   │   │   └── tacDownloader.py
│   │   ├── loaders/
│   │   │   ├── cartola.py
│   │   │   ├── carteras.py
│   │   │   ├── identidad.py
│   │   │   ├── nemotecnicos.py
│   │   │   ├── bonos.py
│   │   │   └── tac.py
│   │   └── mutualFundsCategories.py  # Fund classifier per Circular No. 7 (AFM 2025)
│   ├── investmentFunds/      # Fondos de Inversión (FI) — CMF
│   │   ├── downloaders/
│   │   │   ├── nemotecnicosDownloader.py   # FI series tickers
│   │   │   ├── identidadDownloader.py      # Fund registry (FIRES + FINRE, VI + NV)
│   │   │   ├── valoresCuotaDownloader.py   # Daily NAV per fund (pestania=7)
│   │   │   ├── aportantesDownloader.py     # Quarterly shareholders + cuotas (pestania=27)
│   │   │   └── carterasDownloader.py       # Quarterly IFRS portfolio positions (NACI/EXT/MET_PART/FUT_FW)
│   │   ├── loaders/
│   │   │   ├── nemotecnicos.py
│   │   │   ├── identidad.py
│   │   │   ├── valores_cuota.py
│   │   │   ├── aportantes.py
│   │   │   ├── carteras.py
│   │   │   ├── entidades.py      # refresh_entidades() — canonical names from aportantes_fi
│   │   │   └── utils.py          # mark_has_data() helper
│   │   └── investmentFundsCategories.py  # FI classifier — 20 subcategories, IPSA-based size detection
│   └── bolsaSantiago/        # Bolsa de Santiago data sources
│       ├── downloaders/
│       │   └── dividendosDownloader.py   # Dividends + capital changes 1973→today
│       └── loaders/
│           └── dividendos.py
├── db/
│   ├── engine.py             # SQLAlchemy engine + SessionLocal + Base
│   └── models/
│       ├── mutual_funds.py         # fondo_mutuo
│       ├── cartola.py              # cartola_diaria (+ monto_aportado/monto_rescatado generated columns)
│       ├── carteras.py             # cartera_naci/extr/opci/futu/opla
│       ├── nemotecnicos.py         # nemotecnicos (FM)
│       ├── bonos.py                # bonos_nemotecnicos
│       ├── tac.py                  # tac
│       ├── financial_statements.py # financial_statements
│       ├── fondos_inversion.py     # fondos_inversion + nemotecnicos_fi
│       ├── valores_cuota_fi.py     # valores_cuota_fi
│       ├── aportantes_fi.py        # aportantes_fi + cuotas_fi
│       ├── carteras_fi.py          # cartera_fi_nac/ext/met_part/fut_fw
│       ├── entidades.py            # entidades (canonical names)
│       ├── emisores.py             # SIIEmisor — SII company registry (rut → razon_social)
│       ├── dividendos.py           # dividendos
│       ├── job_runs.py             # job_runs (scheduler execution history)
│       ├── categoria_fi.py         # categoria_fi (FI fund classifications)
│       ├── categoria_fm.py         # categoria_fm (FM fund classifications)
│       └── ref_codes.py            # ref_codes — unified CMF reference table (domain + code + name)
├── base.py                   # BaseDownloader + DownloadResult
├── categories.py             # Circular No. 7 category definitions + country tables
├── config.py                 # CMFUrl enum + env vars
├── http.py                   # make_session() with Retry adapter + fetch() helper
└── scheduler.py              # register_jobs() + _run_tracked() job wrapper

alembic/                      # Migration scripts
tests/                        # pytest unit tests for loaders + classifiers
main.py                       # Web entrypoint: FastAPI only (no scheduler)
worker.py                     # Worker entrypoint: BlockingScheduler only
mcp_worker.py                 # MCP entrypoint: FastMCP SSE server on port 8081
docker-compose.yml            # Oracle deploy — web + worker + mcp process groups
Dockerfile
.dockerignore
```

> **Note on layout**: each dataset domain is a self-contained package with its own
> `downloaders/` and `loaders/`. The public read API lives in `src/api/` (cross-domain reads).
> Financial statements are triggered on demand via `src/financialStatements/api.py` (auth-protected, not public).

### Process architecture

The app runs as **three independent containers** on the `cmf-btg-app` VM (`docker-compose.yml`):
- `web` — `uvicorn main:app` (port 8080) — public read API, no scheduler
- `worker` — `python worker.py` — `BlockingScheduler` with all 16 jobs
- `mcp` — `python mcp_worker.py` (port 8081) — FastMCP SSE server for Claude

All three have `restart: always`. A crash in one does not affect the others. nginx on the
host terminates HTTPS (`financial-cmf.ddns.net`) and reverse-proxies `/` → 8080 and
`/mcp/` + `/messages/` → 8081.

### Downloader interface

Every downloader extends `BaseDownloader` and exposes:

```python
def run(self) -> DownloadResult: ...                    # incremental — picks up from last DB date
def backfill(self, from_date) -> DownloadResult: ...    # historical population
```

All downloaders use `make_session()` which attaches a `Retry` adapter (3 attempts, exponential backoff, retries on 429/500/502/503/504). `fetch()` in `http.py` provides application-level retry on top.

Downloaded files are deleted immediately after successful load (`path.unlink(missing_ok=True)`).
The DB is the sole source of truth — ephemeral disk is just a staging area.

### `has_data` flag (investmentFunds downloaders)

`fondos_inversion.has_data` controls which FI funds are scraped:
- `NULL` — unknown, always attempt
- `True` — fund has data, always fetch
- `False` — non-vigente fund confirmed empty, **skip forever**

Set automatically on first fetch result. Vigente funds are never marked `False`.
This eliminates wasted requests for the ~750 non-vigente funds with no CMF portal data.

### Job tracking

Every scheduler job writes a row to `job_runs` on start and updates it on finish via `_run_tracked()` in `scheduler.py`. Fields: `job_id`, `started_at`, `finished_at`, `status` (running/success/error), `rows_upserted`, `errors`, `error_detail`.

### FI Fund Classifier (`investmentFundsCategories.py`)

Classifies all FI funds based on IFRS quarterly cartera positions using `pct_activo_fondo` as portfolio weight. 20 subcategories across 6 types:

| Type | Subcategories |
|---|---|
| Alternativo — Capital Privado | PE/Buyout, Deuda Privada, Venture Capital, Secundarios |
| Alternativo — Inmobiliario | Hipotecario, Desarrollo, Renta |
| Alternativo — Infraestructura | Infraestructura, Energía, Forestal y Agrícola |
| Accionario | RV Nacional (General/LC/SC), RV Internacional (General/LC/SC) |
| Deuda | Deuda Nacional, Deuda Internacional |
| Fondo de Fondos | Fondo de Fondos |

Large/Small Cap detection uses `rut_emisor` overlap with IPSA ETF (run_fondo `10748`) — dynamic, no hardcoded tickers. FI results persist to `categoria_fi`; FM Circular No. 7 classifications persist to `categoria_fm`. Both refresh after their portfolio jobs.

### Rentability materialized views

Two materialized views compute returns for 1D/1W/1M/1Y/5Y/YTD periods, refreshed daily via the scheduler with `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

**`mv_rentabilidad_fm`** (Mutual Funds) — total return:
```
r = (VC_end × PRODUCT(factor_reparto in period)) / VC_start - 1
```
- `factor_reparto` is the daily reinvestment multiplier for distributions; cumulative product via `EXP(SUM(LN(factor)))` over valid factors only (NaN/NULL/0 → neutral 1.0).

**`mv_rentabilidad_fi`** (rescatable Investment Funds only) — NAV + dividends:
```
r = (VL_end - VL_start + SUM(dividends in period)) / VL_start × 100
```
- `VL` = `valores_cuota_fi.valor_libro`. Dividends matched via `nemotecnicos_fi` → `dividendos` using `fec_lim` (ex-date), currency-matched: `$$`/CLP → `$`, `PROM`/USD → `US$` (no FX needed). Hyphen formats normalized (`CFICOF4A-E` = `CFI-COF4AE`).

Both pick the **most recent date whose fund count is ≥ 90% of the maximum seen in the last 7 days** as reference. This tolerates a handful of late-publishing funds while still preferring recency (e.g. one fund missing on a newer date no longer anchors the MV to an older date). Note: raw CMF data occasionally has corrupt `valor_cuota` jumps for individual fund/series — the views reflect source data faithfully and do not mask these.

### `mv_administradores` materialized view

Unified administradora dimension derived entirely from existing tables (no new download). Joins `fondo_mutuo` (FM, has admin RUT) with `fondos_inversion` (FI, name only) via `LOWER(TRIM(nombre))` match — name matching works because both come from the same CMF source. ~50 rows, refreshed daily at 09:20. Columns: `rut`, `nombre`, `funds_fm`, `funds_fm_vigente`, `funds_fi`, `funds_fi_vigente`, `funds_total`.

### SII company registry (`emisores`)

The `emisores` table holds ~994k Chilean companies from the SII (tax authority) registry — `rut`, `dv`, `razon_social`. Loaded via `src/sii/load_emisores.py` from a tab-separated SII export (one-off, refreshed yearly). Used to resolve `rut_emisor` → company name in the portfolio endpoints.

**Coverage**: All SII RUTs are ≥ 50M (companies). Chilean individuals have lower RUTs, so loan/mortgage funds whose "issuers" are individual debtors won't match — by design. Match rates: **96%** on `cartera_naci` (FM), **73%** on `cartera_fi_nac` (FI; remainder are individual debtors).

### MCP server (`src/mcp_server.py`)

FastMCP server exposing 15 tools for AI-driven fund-market analysis. Runs as the `mcp` container (`mcp_worker.py`, SSE on port 8081), reverse-proxied by nginx at `/mcp/sse`. Connected to Claude.ai via Settings → Integrations. Tools:

| Tool | Purpose |
|---|---|
| `search_funds` | Find FM/FI funds by name or admin |
| `compare_funds` | Side-by-side returns for 2+ funds |
| `top_funds_by_return` | Rankings by 1D/1W/1M/1Y/5Y/YTD. Optional `as_of_date` (YYYY-MM-DD) computes returns dynamically from raw data for any historical date (FM: total return with factor_reparto; FI: NAV-only) |
| `net_new_money_ranking` | Net new money by AGF or fund. `fund_type`: `fm` (daily, explicit aportes+rescates) or `fi` (rescatable: daily implied via `flujo_neto`; non-rescatable: quarterly `cuotas_fi`). `rescatable` filter for FI. Optional `from_date`/`to_date` overrides `period` preset |
| `fi_equity_activity` | Equity events for non-rescatable FI funds: `raising` (new cuotas issued), `returning` (cuotas paid back), `pending_calls` (cuotas subscribed but not yet paid). Returns `capital_raised_bn_clp`, `capital_returned_bn_clp`, `net_equity_change_bn_clp`, `pending_calls_bn_clp`, `num_contratos_promesa`, `num_promitentes`. Group by fund or AGF |
| `get_fund_full_picture` | Identity, returns, flows, portfolio, shareholders |
| `get_administrator_full_picture` | AUM, market share, best/worst funds, flows, shareholders, `top_fm_positions` (top 15 holdings across all admin FM funds, enriched) |
| `compare_administrators` | M&A view: shared shareholders, AUM, merge scenario |
| `get_shareholder_positions` | Track an institutional investor across funds/time |
| `potential_clients` | Market shareholders NOT in an admin's funds |
| `market_overview` | Total market snapshot: AUM, top AGFs, flows, top performers |
| `get_fund_portfolio` | Full portfolio positions (FM or FI) with all fields + SII names + fund names from nemotecnicos + instrument-type summary |
| `top_emisores_in_market` | Rank companies by how many funds hold them + total weight (FM or FI, domestic) |
| `emisor_fund_exposure` | Given a company (RUT or name), list every fund holding it with weight and instrument type. Covers domestic (naci) + foreign (extr) portfolios — foreign matched by nombre_emisor when searching by name; results tagged with `source=naci/extr` |
| `portfolio_overlap` | Jaccard overlap score + shared positions between two funds |

AUM figures are CLP. FM net new money uses `cartola_diaria` generated columns (`monto_aportado`, `monto_rescatado`). FI rescatable NNM uses `valores_cuota_fi.flujo_neto` (daily implied flow, pre-computed at load time). FI non-rescatable uses quarterly `cuotas_fi`. The `mcp` container only needs `DATABASE_URL`.

### Scheduler jobs (America/Santiago)

| Job ID | Schedule | Description |
|---|---|---|
| `ref_codes_refresh` | Daily 07:45 | CMF country/currency/instrument reference codes → ref_codes table (152+118+79 rows) |
| `bonds_tickers` | Daily 08:00 | FM bond tickers with fiscal rate |
| `fm_identity` | Daily 08:10 | MF fund identity register |
| `mf_tickers` | Daily 08:15 | MF series nemotecnicos |
| `fi_tickers` | Daily 08:15 | FI cuota tickers |
| `fi_identity` | Daily 08:20 | FI fund registry (rescatable/vigente) |
| `mf_daily_nav` | Daily 08:30 | MF daily cartola (NAV/AUM/flows) |
| `mf_rentabilidad` | Daily 09:15 | Refresh `mv_rentabilidad_fm` (FM returns) |
| `administradores` | Daily 09:20 | Refresh `mv_administradores` (admin fund counts) |
| `mf_portfolios` | Day 5 of month 09:00 | MF monthly investment portfolios |
| `mf_categories` | Day 5 of month 09:15 | FM fund classification → categoria_fm |
| `mf_costs` | Day 5 of month 09:30 | MF monthly TAC costs |
| `dividends` | Daily 09:00 | Dividends + capital changes (Bolsa de Santiago) |
| `fi_daily_nav` | Daily 09:30 | FI daily NAV/AUM (vigente funds only) |
| `fi_rentabilidad` | Daily 10:00 | Refresh `mv_rentabilidad_fi` (FI returns) |
| `fi_shareholders` | Day 5 of month 10:00 | FI quarterly shareholders + cuotas (vigente only) |
| `fi_portfolios` | Day 5 of month 10:30 | FI quarterly IFRS portfolio positions (vigente only) |
| `fi_categories` | Day 5 of month 11:00 | FI fund classification → categoria_fi |

### API endpoints

All public endpoints return `Cache-Control: public, max-age=3600` and allow all CORS origins. Pagination via `?limit=50&offset=0` (max limit 1500).

#### Public read API (`src/api/`)

| Endpoint | Description |
|---|---|
| `GET /mutual-funds` | List FM funds. Filters: `admin`, `tipo_fondo`, `vigente`, `categoria`, `tipo`. Each item includes `categoria`, `tipo`, `nombre_cat` from `categoria_fm` |
| `GET /mutual-funds/{run}` | FM fund detail: identity + latest NAV per serie (field: `series[]`) + rentability + `category` (full object: `categoria`, `tipo`, `grupo`, `nombre_cat`, `confianza`, `periodo`) + `geo_breakdown` (`[{pais, pct_peso}]` from latest portfolio) + `latest_tac` (`tac_total`, `tac_rem_fija`, `tac_rem_var`, `tac_gastos_op`, `periodo`) |
| `GET /mutual-funds/{run}/nav` | FM NAV history for charts. Filters: `serie`, `from_date`, `to_date`. Field: `valor_cuota` |
| `GET /mutual-funds/{run}/tac` | FM TAC history — last 24 months descending (`periodo`, `serie`, `tac_total`, `tac_rem_fija`, `tac_rem_var`, `tac_gastos_op`). Secondary sort by `serie`. |
| `GET /mutual-funds/{run}/flows` | Monthly aportes/rescates/nnm aggregated across all series. Filters: `serie`, `from_date`, `to_date`. Fields: `aportes`, `rescates`, `nnm` |
| `GET /mutual-funds/{run}/portfolio` | FM portfolio (naci + extr), SII-enriched. Optional `?period=YYYY-MM-DD` (any date in the month resolves to first-of-month). Fields per position: `tir`, `fecha_vencimiento`, `cantidad_unidades`, `tipo_unidades`, `moneda_liquidacion`, `porcentaje_valor_par`, `tipo_interes`, `codigo_pais_emisor`, `situacion_instrumento`, `porcentaje_capital_emisor`, `porcentaje_activos_emisor`, `codigo_grupo_empresarial` |
| `GET /mutual-funds/{run}/portfolio/history` | All FM monthly portfolio positions across every period. Flat list with `periodo` field per row. Paginated. |
| `GET /mutual-funds/{run}/return-series` | FM cumulative total-return time series for charting. Params: `serie` (optional — defaults to serie with highest recent `patrimonio_neto`), `from_date` (optional — defaults to today − 1 year; pass further back for longer periods, e.g. today − 1825 for 5Y; data available from 2020), `to_date` (optional — for exact point-in-time alignment with rentability endpoint). Response: `[{ fecha, return_pct }]` where `return_pct` is cumulative % from `from_date` (first point always `0.0`). Accounts for distributions via `factor_reparto`. Returns `[]` if no data for the range. |
| `GET /investment-funds` | List FI funds. Filters: `admin`, `rescatable`, `vigente`, `categoria`, `tipo`. Each item includes `categoria`, `tipo`, `nombre_cat` from `categoria_fi` |
| `GET /investment-funds/{run}` | FI fund detail: identity + latest NAV per serie (field: `series[]`, `valor_cuota` aliased from `valor_libro`) + rentability + `category` (full object) + `geo_breakdown` (`[{pais, pct_peso}]` from latest quarterly portfolio). No TAC (FI not covered by CMF TAC) |
| `GET /investment-funds/{run}/nav` | FI NAV history. Fields: `valor_cuota` (aliased from `valor_libro`), `patrimonio_neto` |
| `GET /investment-funds/{run}/portfolio` | FI portfolio (nac + ext), SII-enriched, sorted by weight. Optional `?period=YYYY-MM-DD`. Fields per position: `tir_val_par_precio`, `fecha_vencimiento`, `cant_unidades`, `tipo_unidades`, `cod_moneda_liquidacion`, `tipo_interes`, `pct_capital_emisor`, `pct_activo_emisor`, `situacion_instrumento`, `clasif_esf`, `cod_pais` |
| `GET /investment-funds/{run}/portfolio/history` | All FI quarterly portfolio positions across every period. Flat list with `periodo` field per row. Paginated. |
| `GET /investment-funds/{run}/equity-activity` | Quarterly equity activity history (capital calls, new authorizations, pending promesas) for a fund — primarily meaningful for non-rescatable FI funds. `cuotas_emitidas`/`cuotas_pagadas` are cumulative stocks from `cuotas_fi`; deltas computed via `LAG()` per quarter. Fields: `delta_emitidas`, `delta_pagadas`, `capital_called_clp` (delta_pagadas × valor_libro), `new_auth_clp`, `pending_formal_clp` (cuotas_suscritas_no_pagadas × valor_libro), `pending_promise_clp` (num_cuotas_promesa × valor_libro), `num_contratos_promesa`, `num_promitentes`. Ordered chronologically ascending for charting. Same methodology as the `fi_equity_activity` MCP tool |
| `GET /investment-funds/{run}/return-series` | FI cumulative total-return time series for charting (rescatable funds only — non-rescatable funds have quarterly NAV at best). Same params and response shape as the FM endpoint (`serie`, `from_date`, `to_date`). Accounts for dividends via the `dividendos` table matched by currency — same methodology as `r_1m`/`r_1y` in the rentability endpoint. |
| `GET /rentability/fm` | FM return rankings. Sort: `r_1d/r_1w/r_1m/r_1y/r_5y/r_ytd`. Filters: `admin`, `categoria`, `tipo` |
| `GET /rentability/fi` | FI return rankings. Same sort options. Filters: `admin`, `categoria`, `tipo` |
| `GET /categories/fi` | FI fund classifications. Filters: `categoria`, `tipo`, `admin` |
| `GET /categories/fm` | FM fund classifications. Filters: `categoria`, `tipo`, `admin` |
| `GET /categories/catalog` | Hierarchical category catalog with fund counts. Optional `?fund_type=fm\|fi` returns just that side's tree; omitted returns `{fm: [...], fi: [...]}` combined |
| `GET /industry/overview` | Market snapshot: total AUM, active funds, admins, flows. Filters: `fund_type`, `categoria`, `tipo`. Returns `aportes_month_clp`, `rescates_month_clp`, `neto_month_clp` (FM gross flows) + `top_administrators` (with `nnm_ytd_clp`) + `category_aum_breakdown` (with `nnm_ytd_clp`) |
| `GET /industry/funds` | Unified FM/FI screener with classification, AUM, returns, and flows. Filters: `fund_type`, `type`, `group`, `category`, `admin`, `rescatable`, `vigente` |
| `GET /industry/evolution` | Monthly AUM history grouped by market/admin/category. Filters: `fund_type`, `categoria`, `tipo`, `from_date`, `to_date`. Returns `aportes_clp`, `rescates_clp`, `nnm_clp` per month point (FM only; FI flows are null) |
| `GET /ref-codes` | All CMF reference codes. Filter: `?domain=country\|currency\|instrument`. Returns `domain`, `code`, `name`, `updated_at` |
| `GET /admins` | List administradoras with FM+FI fund counts. Filter: `search` |
| `GET /admins/{rut}` | Single administradora by RUT |
| `GET /shareholders/fund/{run}` | Shareholder evolution for a fund across quarters |
| `GET /shareholders/entity/{rut}` | All fund positions held by a shareholder across time |
| `GET /shareholders/admin` | Top shareholders aggregated across an admin's funds. Required: `admin` |
| `GET /shareholders/compare` | Side-by-side admin comparison: shared holders, exclusives, merge summary. Required: `admin_a`, `admin_b` |
| `GET /shareholders/top` | Largest institutional investors in Chile by total FI AUM across all funds. Filters: `tipo_persona` (J/N), `period` |
| `GET /shareholders/entity/{rut}/profile` | Full investor dossier: total AUM, fund count, AGF count, AGF wallet breakdown (`agf_breakdown[]`), top 10 holdings (`top_holdings[]`). Optional `?period=` |
| `GET /shareholders/entity/{rut}/evolution` | Quarter-by-quarter AUM + fund count + AGF count timeline since 2020. Optional `?from_date=`. Powers "is this investor growing or leaving?" chart |
| `GET /shareholders/entity/{rut}/wallet-share` | How an investor distributes capital across AGFs — each with `aum_clp`, `pct_of_wallet`, `funds_count`. Competitive intelligence view |
| `GET /shareholders/fund/{run}/concentration` | Ownership concentration per quarter: top-1/3/5/10 (% and CLP) + Herfindahl index. Label: `captive` (HHI>5000 or top-1>50%), `concentrated` (HHI>2500 or top-1>30%), `moderate`, `diversified` |
| `GET /shareholders/admin/retention` | Quarter-over-quarter AUM delta per investor for an admin's funds. Status per investor: `new`, `growing` (+>5%), `stable` (±5%), `shrinking` (>-5%), `exiting`. Sorted by risk (exiting first). Required: `admin`. Optional: `current_period`, `prev_period` |
| `GET /shareholders/matrix` | AGF × aportante wallet-share matrix for the FI market. Filters: `period`, `min_aum_clp`, `admin`, `tipo_persona`, `limit`. Includes BTG wallet share per investor plus nested AGF rows |
| `GET /shareholders/opportunities` | BTG opportunity ranking: large investors where BTG has 0–5% wallet share. Filters: `period`, `tipo_persona`, `min_aum_clp`, `limit`. Includes main AGF, opportunity AUM, score, and reason |
| `GET /shareholders/flows` | Market-wide quarterly movement by aportante. Filters: `admin`, `period`, `status`, `min_abs_delta_clp`, pagination. Status: `new`, `growing`, `stable`, `shrinking`, `exiting` |
| `GET /shareholders/fund-concentration/ranking` | Funds ranked by shareholder concentration risk for the latest or selected quarter. Filters: `period`, `admin`, pagination. Includes top-1/3/5/10, HHI, label, and largest holder |
| `GET /shareholders/merge-simulation` | Advanced AGF merger simulator. Required: `admin_a`, `admin_b`. Optional: `period`. Returns overlap AUM, HHI before/after, top-10 concentration, cross-sell targets, high-risk overlap, and exclusive/shared segments |
| `GET /emisores` | Companies ranked by total FM+FI CLP exposure. Filters: `search` (name or RUT), `fund_type`, `tipo_instrumento`. Returns exposure split FM/FI, fund count, AGF count per company |
| `GET /emisores/{rut}` | Full company profile: identity + aggregate FM/FI exposure + `instrument_breakdown[]` (CLP + % per instrument type with human-readable names) + `top_funds[]` (top 10 funds holding it) |
| `GET /emisores/{rut}/funds` | Every fund currently holding this company (latest period), sorted by CLP. Filters: `fund_type`, `tipo_instrumento`. Paginated |
| `GET /emisores/{rut}/positions` | Every **raw individual position row** for this company across all funds — not aggregated. Full field set per instrument type: bonds get `tir`, `fecha_vencimiento`, `tipo_interes`, `porcentaje_valor_par`; equities get `cantidad_unidades`, `porcentaje_capital_emisor`. Filters: `fund_type`, `tipo_instrumento`, `period` |
| `GET /emisores/{rut}/history` | Monthly (FM) + quarterly (FI) time series of total market exposure. Filters: `fund_type`, `tipo_instrumento` (drill-down by instrument type), `from_date`. Powers exposure timeline chart |
| `GET /emisores/{rut}/funds/{run_fondo}/history` | Time series of how much a **specific fund** has held a **specific company** — one row per period × instrument type. Filters: `fund_type`, `from_date` |
| `GET /emisores/{rut}/concentration` | Herfindahl index of company exposure across AGFs. Label: `diversified` (<1500), `moderate` (1500-2500), `concentrated` (>2500). Returns `agfs[]` with `pct_of_total` |

**Shareholder AUM formula**: `pct_propiedad / 100 × fund_aum`. Fund AUM is estimated as the median of `valorizacion_cierre × 100 / pct_activo_fondo` across all `cartera_fi_nac` + `cartera_fi_ext` positions for that fund/quarter.

**Shareholder `tipo_persona` API alias**: CMF source data uses letter codes such as `A/B/C/E/F/G` for entity types. Public shareholder endpoints expose/filter the simplified frontend contract: `J` means any non-natural/entity code, `N` means natural person.

**Numeric TEXT casting**: `cartera_naci` stores all numeric fields as TEXT. The regex `'^-?[0-9]*\.?[0-9]+$'` guards all CAST operations — it handles values with no leading zero (e.g. `.143` stored as-is by CMF). Non-matching values cast to NULL rather than erroring.

#### Internal / authenticated

| Endpoint | Description |
|---|---|
| `GET /health` | Health check |
| `POST /financial-statements/download?inicio=YYYYMM&termino=YYYYMM` | Download + load IFRS statements. Add `&background=true` for async. Requires `API_TOKEN`. |

### DB tables

| Table | Rows (approx) | Notes |
|---|---|---|
| `fondo_mutuo` | 1,340 | MF identity (447 vigentes, 893 terminated), 36 AGFs |
| `cartola_diaria` | 7M+ | Daily NAV/AUM/flows 2020–2026, 1.4 GB. Composite index (run_fondo, fecha). Generated columns: monto_aportado, monto_rescatado |
| `cartera_naci` | 2.1M+ | National portfolio positions. Composite index (run_fondo, periodo) |
| `cartera_extr` | 315K+ | Foreign portfolio positions |
| `cartera_opci` | ~155 | Options positions |
| `cartera_futu` | 134K+ | Futures/forwards positions |
| `cartera_opla` | 0 | Always empty (CMF never publishes OPLA data) |
| `nemotecnicos` | 2,432 | FM series with tipo_serie classification |
| `bonos_nemotecnicos` | 1,254 | Bonds with fiscal interest rate |
| `tac` | 230K+ | Monthly TAC costs 2020–2026 |
| `financial_statements` | 2M+ | IFRS statements (quarterly) for all CMF companies 2009–2026 |
| `fondos_inversion` | 1,641 | FI registry: run_fondo, administrador, rescatable, vigente, has_data, moneda (dominant currency, synced from valores_cuota_fi on each load — avoids scanning 2.9M rows in overview queries) |
| `nemotecnicos_fi` | 2,370 | FI cuota tickers |
| `valores_cuota_fi` | 2.9M+ | FI daily NAV/AUM 2020–2026, 531 MB. `flujo_neto` = daily implied net flow in CLP: `(cuotas_t − cuotas_{t-1}) × valor_libro_t` where `cuotas = patrimonio_neto / valor_libro`. Covering index `(fecha, run_fondo, flujo_neto)` |
| `aportantes_fi` | 176K+ | FI quarterly top-12 shareholders with ownership % (periodo = quarter-end) |
| `cuotas_fi` | 35K+ | FI quarterly: cuotas emitidas/pagadas, valor libro (periodo = quarter-end) |
| `cartera_fi_nac` | 888K+ | FI quarterly domestic positions (IFRS). Composite index (run_fondo, periodo) |
| `cartera_fi_ext` | 74K+ | FI quarterly foreign positions (IFRS) |
| `cartera_fi_met_part` | 1,600+ | FI equity method investments |
| `cartera_fi_fut_fw` | 12K+ | FI futures + forwards positions |
| `entidades` | 4,521 | Canonical entity names by RUT (normalized from aportantes_fi) |
| `emisores` | 994K+ | SII company registry (rut → razon_social). Enriches portfolio endpoints |
| `dividendos` | 75,469 | Dividends + capital changes 1973–2026 (Bolsa de Santiago) |
| `job_runs` | growing | Scheduler job execution history (status, duration, rows, errors) |
| `categoria_fi` | 851 | FI fund classifications — refreshed quarterly |
| `categoria_fm` | growing | FM fund classifications — refreshed monthly |
| `ref_codes` | 432 | Unified CMF reference data: `(domain, code)` unique — domains: `country` (152), `currency` (118), `instrument` (162). Scraped daily from CMF across all 4 tables on the instrument page (fixed income + equities + fund units + derivatives). Used to resolve country codes, currency codes, and instrument type codes across portfolio endpoints |
| `mv_rentabilidad_fm` (MV) | ~2,900 | FM returns 1D/1W/1M/1Y/5Y/YTD (total return via factor_reparto). Refreshed daily |
| `mv_rentabilidad_fi` (MV) | ~380 | FI rescatable returns 1D/1W/1M/1Y/5Y/YTD (NAV + dividends). Refreshed daily |
| `mv_administradores` (MV) | ~50 | Admin dimension: FM+FI fund counts per administradora. Refreshed daily |

## Git Workflow

- Always develop on a feature branch, never directly on `main`.
- Branch naming: `feature/<short-description>`
- Use **conventional commits** for all commit messages and PR titles:
  - `feat:` new feature
  - `fix:` bug fix
  - `refactor:` restructure with no behavior change
  - `chore:` maintenance (deps, config, tooling)
  - `docs:` documentation only
  - `test:` adding or fixing tests
- Push the branch and open a PR into `main` when ready.

## Common Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Generate a new migration after model changes
alembic revision --autogenerate -m "description"

# Run the web process locally
uvicorn main:app --reload

# Run the worker (scheduler) locally
python worker.py

# Run a specific downloader manually
python -c "
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from src.etl.mutualFunds.downloaders.cartolaDownloader import CartolaDownloader
print(CartolaDownloader().run())
"

# Run FI classifier and save to DB
python -c "
from src.etl.investmentFunds.investmentFundsCategories import run_and_save
print(run_and_save(), 'rows saved')
"

# Backfill FI carteras (quarterly IFRS portfolios, 2020→today)
python -c "
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from src.etl.investmentFunds.downloaders.carterasDownloader import CarterasFIDownloader
print(CarterasFIDownloader().backfill())
"

# Backfill FI daily NAV (all funds, 2020→today)
python -c "
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from src.etl.investmentFunds.downloaders.valoresCuotaDownloader import ValoresCuotaFIDownloader
print(ValoresCuotaFIDownloader().backfill())
"

# Backfill dividends (1973→today)
python -c "
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from src.etl.bolsaSantiago.downloaders.dividendosDownloader import DividendosDownloader
print(DividendosDownloader().backfill())
"

# Load SII emisores (yearly refresh)
# 1. Copy sii_dbb.txt to DB VM, then in psql:
# CREATE TEMP TABLE sii_raw (ano text, rut text, dv text, razon_social text, col5 text, col6 text, col7 text, col8 text, col9 text, col10 text, col11 text, col12 text, col13 text, col14 text, col15 text, col16 text, col17 text, col18 text, col19 text, col20 text, col21 text, col22 text);
# \COPY sii_raw FROM '/tmp/sii_dbb.txt' WITH (FORMAT csv, DELIMITER E'\t', HEADER true, ENCODING 'utf8');
# INSERT INTO emisores(rut, dv, razon_social) SELECT TRIM(rut), TRIM(dv), TRIM(razon_social) FROM sii_raw ON CONFLICT (rut) DO UPDATE SET dv = EXCLUDED.dv, razon_social = EXCLUDED.razon_social;
# DROP TABLE sii_raw;

```

## Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | SQLAlchemy sync DSN e.g. `postgresql+psycopg2://user:pass@host/db` |
| `API_TOKEN` | Bearer token for `POST /financial-statements/download`. If unset, auth is skipped (dev mode) |
| `GEMINI_API_KEY` | Google Gemini key — used for CAPTCHA solving in cartola downloader |
| `DOWNLOADS_DIR` | Local path for downloaded raw files (default: `./downloads`) |
| `BOLSA_COOKIES` | Session cookies for Bolsa de Santiago API (expires periodically) |
| `BOLSA_CSRF` | CSRF token for Bolsa de Santiago API (expires with cookies) |

Set locally via `.env`. Production environment variables are configured for the
Oracle-hosted Docker Compose services.

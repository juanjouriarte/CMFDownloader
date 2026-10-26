# CLAUDE.md

This is the canonical project instructions file for coding agents working in this repository.

## Project Purpose

Daily ETL pipeline that downloads public datasets from the Chilean CMF (Comisión para el Mercado Financiero) and Bolsa de Santiago, loads them into PostgreSQL, and exposes a public read API.

## Dashboard UI work

For changes to the sibling `../BTGDashboard`, read its `CLAUDE.md`, including
“UI consistency rules for AI contributors”. Industria → Evolución de patrimonio
is the agreed visual reference for calendars, period presets, selectors and
charts. Reuse its shared controls (daily/monthly `EvolutionPeriodPicker`, return
presets and searchable selectors); keep financial calculations separate from
styling. Apply this to the requested view, not an unsolicited app-wide redesign.

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

**API base URL**: `https://api-cmf-146-181-34-54.sslip.io` (nginx + Let's Encrypt SSL)
**MCP endpoints**: Streamable HTTP at `https://btg-agf-ai-assistant-146-181-34-54.sslip.io/mcp/` (ChatGPT custom app) and legacy SSE at `https://btg-agf-ai-assistant-146-181-34-54.sslip.io/mcp/sse` (existing clients). The former `api-cmf-146-181-34-54.sslip.io` hostname remains an HTTPS alias during migration.

### SSH access
```bash
ssh -i ~/.ssh/oracle_cmf.key ubuntu@146.181.34.54   # app VM
ssh -i "$HOME/Downloads/ssh-key-2026-06-08 (2).key" ubuntu@146.181.47.236  # db VM
```

### Deploy (automatic via GitHub Actions)
Push to `main` triggers `.github/workflows/deploy.yml` — SSHes into the app VM, pulls and builds the new image, runs migrations from that image, then starts the updated services. Deployment stops on a failed build or migration.

SQLAlchemy stays on the tested 2.0 series with `psycopg2-binary`. Version 2.1
changes the default driver for `postgresql://` to psycopg 3, which also changes
parameter binding. Treat that upgrade as a separate migration with database
integration tests, rather than allowing a fresh Docker build to select it.

Manual deploy if needed:
```bash
ssh -i ~/.ssh/oracle_cmf.key ubuntu@146.181.34.54
cd ~/CMFDownloader
git pull origin main
sudo docker compose build
sudo docker compose run --rm web alembic upgrade head
sudo docker compose run --rm worker python -c "from src.etl.mutualFunds.flow_adjustments import run; print(run())"
sudo docker compose up -d
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
│   ├── bolsa.py              # /bolsa/quotes/{nemo} live + /bolsa/btg-fixed-income buffered quotes
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
│       ├── client.py         # Authenticated read-only client for live market quotes
│       ├── quote_buffer.py   # Rate-limited in-memory buffer for BTG fixed-income quotes
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
│       ├── fm_flow_adjustments.py  # Auditable internal FM migrations excluded from external NNM
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
- `mcp` — `python mcp_worker.py` (port 8081) — FastMCP Streamable HTTP + legacy SSE server

All three have `restart: always`. A crash in one does not affect the others. nginx on the
host terminates HTTPS (`btg-agf-ai-assistant-146-181-34-54.sslip.io`, with the former API hostname as an alias) and reverse-proxies `/` → 8080 and
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

### FI shareholder import completeness

The shareholder downloader skips a fund/quarter only when **both** `cuotas_fi`
and `aportantes_fi` contain records for that exact pair. Cuota data alone does not
prove shareholders were imported. Empty shareholder responses remain eligible
for retry, including legitimate empty filings; no completion marker is inferred.
This presence check does not certify that all source shareholder rows are present.
The incremental shareholder job revisits the two previous completed quarters to
catch late filings, and quarter iteration excludes future quarter-end dates.

### Job tracking

Every scheduler job writes a row to `job_runs` on start and updates it on finish via `_run_tracked()` in `scheduler.py`. Fields: `job_id`, `started_at`, `finished_at`, `status` (running/success/error), `rows_upserted`, `errors`, `error_detail`.

FI daily NAV requests the full last **three calendar months**, including the
start date, with month-end clamping. It does not shorten that range to a fund's
latest stored NAV: that date cannot establish completeness of prior dates or
series. Explicit NAV backfills also revisit the entire requested range without
requiring `force=True`. An empty recent-range response cannot mark an inactive
fund permanently empty; that requires a full-history request.

FM daily NAV rechecks at least 14 days, including previously stored dates, to
capture delayed reports. A recent maximum date must not skip incomplete earlier
days; older stored dates still trigger a full catch-up. Regression coverage:
`tests/test_fm_nav_downloader.py`.

The CAPTCHA reader supports `CMF_CAPTCHA_MODEL` (default `gemma-4-31b-it`) and
`CMF_CAPTCHA_FALLBACK_MODEL` (default `gemini-3.1-flash-lite`; empty disables it).
After bounded primary retries, one fallback attempt prevents a provider outage
from blocking the imports. Test via `tests/test_captcha.py`; never log keys.

For a local-only fund data reload, use
`PYTHONPATH=. .venv/bin/python scripts/reload_local_data.py --since YYYY-MM-DD`.
The command rejects non-local DB hosts, uses the normal tracked loaders, refreshes
reference/identity data, NAV, portfolios, TAC, shareholders/quotas, classifications,
dividends (annual source granularity) and derived dashboard views. FI NAV uses up
to four disjoint workers; FI quarterly imports use two. `--only` supports retrying
`fm`, `fm_nav`, `fi_nav`, `fi_reports`, or `derived`. FM NAV exports use monthly
chunks and split interrupted transfers into smaller ranges. JSON/log reports are written under
`output/local-refresh/`. It never starts the scheduler or sends report emails.
TAC reloads atomically replace the requested month, using the source's month-end
dates; a source with dates outside that month is rejected before deletion. This
prevents duplicate TAC rows on repeated imports.
Tests: `tests/test_fi_nav_downloader.py`, `tests/test_industry_refresh.py`,
`tests/test_local_reload.py`, `tests/test_tac_loader.py` (DB fixture opt-in).

### FI Fund Classifier (`investmentFundsCategories.py`)

Classifies all FI funds based on IFRS quarterly cartera positions using `pct_activo_fondo` as portfolio weight. 34 subcategories across 6 high-level types:

| Type | Subcategories |
|---|---|
| Alternativo — Capital Privado | PE/Buyout, Deuda Privada, Venture Capital, Secundarios |
| Alternativo — Inmobiliario | Hipotecario, Desarrollo, Renta |
| Alternativo — Infraestructura | Infraestructura, Energía, Forestal y Agrícola |
| Accionario | RV Nacional (General/LC/SC), RV Internacional (General/LC/SC) |
| Deuda | Nacional: ≤90d, CLP/UF/Mixta ≤365d, CLP >365d, UF 1–3y/3–5y/>5y, Mixta >365d; Internacional: ≤90d/≤365d/>365d; Origen Flexible: ≤365d/>365d; duración no disponible |
| Fondo de Fondos | Fondo de Fondos |
| Balanceado | Multiactivo |

Large/Small Cap detection uses `rut_emisor` overlap with IPSA ETF (run_fondo `10748`) — dynamic, no hardcoded tickers. National-equity funds with IPSA overlap ≤35% are Small/Mid Cap, ≥65% are Large Cap, and the remainder are general RV Nacional. FI results persist to `categoria_fi`; FM Circular No. 7 classifications persist to `categoria_fm`. Both refresh after their portfolio jobs.

FI public-debt categories mirror the FM reporting hierarchy without claiming Circular No. 7 status: debt origin is national, international, or flexible; weighted-average maturity uses `fecha_vencimiento` weighted by `pct_activo_fondo`; national debt is further split by CLP/UF dominance. At least 50% of position weight must have a valid maturity or the duration is left unavailable. Non-rescatable OTDN/OTE vehicles without maturity evidence fall back to alternative-strategy classification instead of being forced into public debt. The default FI classification period is the latest quarter whose fund coverage is at least 90% of the maximum observed, preventing a partially filed new quarter from replacing the complete classification.

The FM classifier treats CMF instrument code `OTROC` (otros instrumentos de capitalización) as national equity. CMF classifies it as renta variable; `situacion_instrumento` restrictions such as a commitment do not change the underlying asset class.

### Rentability materialized views

Two materialized views compute returns for 1D/1W/1M/1Y/5Y/YTD periods, refreshed daily via the scheduler with `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

**`mv_rentabilidad_fm`** (Mutual Funds) — total return:
```
r = (VC_end × PRODUCT(factor_reparto in period)) / VC_start - 1
```
- `factor_reparto` is the daily reinvestment multiplier for distributions; cumulative product via `EXP(SUM(LN(factor)))` over valid factors only (NaN/NULL/0 → neutral 1.0).

**`mv_rentabilidad_fi`** (active Investment Funds with valid NAV, rescatable and non-rescatable) — NAV + dividends:
```
r = (VL_end - VL_start + SUM(dividends in period)) / VL_start × 100
```
- `VL` = `valores_cuota_fi.valor_libro`. Dividends matched via `nemotecnicos_fi` → `dividendos` using `fec_lim` (ex-date), currency-matched: `$$`/CLP → `$`, `PROM`/USD → `US$` (no FX needed). Hyphen formats normalized (`CFICOF4A-E` = `CFI-COF4AE`).

Both pick the **most recent date whose fund count is ≥ 90% of the maximum seen in the last 7 days** as reference. This tolerates a handful of late-publishing funds while still preferring recency (e.g. one fund missing on a newer date no longer anchors the MV to an older date). Note: raw CMF data occasionally has corrupt `valor_cuota` jumps for individual fund/series — the views reflect source data faithfully and do not mask these.

### Industria monthly history

Industry snapshot flow scans use scalar reference-date subqueries for their
inclusive date bounds. Keep those bounds indexable: joining date ranges directly
to the reference CTE can make PostgreSQL scan all daily history on production,
even for a monthly overview. Validate query-plan changes against matching results
for FM/FI, native currencies and monthly/YTD windows.

`mv_industry_monthly_fm` and `mv_industry_monthly_fi` store monthly history per
fund and normalized native currency. Each row contains the latest available
fund AUM in that month and full-month raw FM flows. The API joins current fund
classifications and confirmed flow adjustments at read time, so changes to
those dimensions do not require rebuilding history.

The scheduled NAV jobs refresh their corresponding view **after** importing,
using `REFRESH MATERIALIZED VIEW CONCURRENTLY`; child job runs are tracked as
`fm_industry_monthly` / `fi_industry_monthly`. This also incorporates corrections
to already-loaded dates. After manual NAV imports/backfills, refresh the matching
view explicitly with `src.scheduler._refresh_view('mv_industry_monthly_fm')`
(or the FI counterpart).

`/industry/evolution` uses these summaries for month-start ranges ending today
or later. It compares the latest source date with the summary date before use;
if a newer source date is present, or a request needs partial historical months,
it falls back to the original exact daily query. Same-date source corrections
become visible after the post-import refresh. The response and currency contract
are identical on both paths. Migration `x4y5z6a7b8c9` builds the initial history
and unique indexes required for concurrent refresh.

### `mv_administradores` materialized view

Unified administradora dimension derived entirely from existing tables (no new download). Joins `fondo_mutuo` (FM, has admin RUT) with `fondos_inversion` (FI, name only) via `LOWER(TRIM(nombre))` match — name matching works because both come from the same CMF source. ~50 rows, refreshed daily at 09:20. Columns: `rut`, `nombre`, `funds_fm`, `funds_fm_vigente`, `funds_fi`, `funds_fi_vigente`, `funds_total`.

### SII company registry (`emisores`)

The `emisores` table holds ~994k Chilean companies from the SII (tax authority) registry — `rut`, `dv`, `razon_social`. Loaded via `src/sii/load_emisores.py` from a tab-separated SII export (one-off, refreshed yearly). Used to resolve `rut_emisor` → company name in the portfolio endpoints.

**Coverage**: All SII RUTs are ≥ 50M (companies). Chilean individuals have lower RUTs, so loan/mortgage funds whose "issuers" are individual debtors won't match — by design. Match rates: **96%** on `cartera_naci` (FM), **73%** on `cartera_fi_nac` (FI; remainder are individual debtors).

### MCP server (`src/mcp_server.py`)

FastMCP server exposing 16 tools for AI-driven fund-market analysis. Runs as the `mcp` container (`mcp_worker.py`, Streamable HTTP and legacy SSE on port 8081), reverse-proxied by nginx at `/mcp/`. Tools:

| Tool | Purpose |
|---|---|
| `search_funds` | Find FM/FI funds by name or admin |
| `compare_funds` | Side-by-side returns for 2+ funds |
| `top_funds_by_return` | Rankings by 1D/1W/1M/1Y/5Y/YTD. Optional `as_of_date` (YYYY-MM-DD) computes returns dynamically from raw data for any historical date (FM: total return with factor_reparto; FI: NAV-only) |
| `net_new_money_ranking` | Net new money by AGF, category, or fund. Returns a structured response with explicit data/classification dates and amounts in millions, always separated and ranked within normalized currency. `limit` applies per currency/methodology partition. Supports AGF, high-level type, category, currency, rescatable, and custom-date filters plus optional fund contributors. FM exposes reported NNM, confirmed internal migrations, and adjusted external NNM; FI uses daily implied `flujo_neto` for rescatables and quarterly `cuotas_fi` for non-rescatables. Preset periods anchor to each source's latest loaded date rather than the server clock |
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

AUM figures are CLP. MCP NNM amounts are returned in millions of the explicit currency on each row and currencies are never aggregated together. FM external net new money preserves reported aportes/rescates and subtracts confirmed internal migrations stored in `fm_flow_adjustments`; its MCP query reads the indexed raw date range and joins those adjustments to avoid full-history view scans. FI rescatable NNM uses `valores_cuota_fi.flujo_neto` (daily implied flow, pre-computed at load time). FI non-rescatable uses quarterly `cuotas_fi`. The `mcp` container only needs `DATABASE_URL`.

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
| `fm_flow_adjustments` | Daily 10:45 | Incrementally detect and persist high-confidence internal FM migrations; full current-year safety scan weekly |
| `mf_nnm_email_report` | Daily 11:15 | Three BTG-styled flow emails: mutual funds; FI Accionario/Balanceado/Deuda; and FI Alternativo/Fondo de Fondos/Otro. FM external NNM excludes high-confidence internal fund migrations detected from official terminations and stored in `fm_flow_adjustments`; raw CMF rows remain unchanged. FI categories aggregate rescatable and non-rescatable funds together. Each email includes its high-level overview, detailed categories, and AGF breakdown by subcategory, with daily/1W/MTD/YTD split by reported currency |
| `fi_daily_nav` | Daily 09:30 | FI daily NAV/AUM; rechecks three calendar months (vigente funds only) |
| `fi_rentabilidad` | Daily 10:00 | Refresh `mv_rentabilidad_fi` (FI returns) |
| `fi_shareholders` | Day 5 of month 10:00 | FI quarterly shareholders + cuotas (vigente only) |
| `fi_portfolios` | Day 5 of month 10:30 | FI quarterly IFRS portfolio positions (vigente only) |
| `fi_categories` | Day 5 of month 11:00 | FI fund classification → categoria_fi |

### API endpoints

Public database endpoints return `Cache-Control: public, max-age=3600` and all endpoints allow all CORS origins. The live Bolsa quote endpoint returns `Cache-Control: no-store`. Pagination via `?limit=50&offset=0` (max limit 1500).

#### Public read API (`src/api/`)

| Endpoint | Description |
|---|---|
| `GET /bolsa/quotes/{nemotecnico}` | Current best bid/ask from Bolsa de Santiago. Returns prices, quantities, midpoint, absolute/percentage spread, quote status, and retrieval timestamp. Not cached. Requires valid `BOLSA_COOKIES` + `BOLSA_CSRF` on the web service. |
| `GET /bolsa/btg-fixed-income` | One-minute in-memory cache of 14 BTG fixed-income fund-series quotes, grouped into 6 funds. A request within the TTL returns immediately; the first request after expiry refreshes every ticker synchronously and sequentially (default 3 seconds apart). Concurrent callers share the same refresh. Returns cache/freshness/error metadata. |
| `GET /mutual-funds` | List FM funds. Filters: `admin`, `tipo_fondo`, `vigente`, `categoria`, `tipo`. Each item includes `categoria`, `tipo`, `nombre_cat` from `categoria_fm` |
| `GET /mutual-funds/{run}` | FM fund detail: identity + latest NAV per serie (field: `series[]`) + rentability + `category` (full object: `categoria`, `tipo`, `grupo`, `nombre_cat`, `confianza`, `periodo`) + `geo_breakdown` (`[{pais, pct_peso}]` from latest portfolio) + `latest_tac` (`tac_total`, `tac_rem_fija`, `tac_rem_var`, `tac_gastos_op`, `periodo`) |
| `GET /mutual-funds/{run}/nav` | FM NAV history for charts. Filters: `serie`, `from_date`, `to_date`. Field: `valor_cuota` |
| `GET /mutual-funds/{run}/tac` | FM TAC history — all series in the last 24 reported periods, descending (`periodo`, `serie`, `tac_total`, `tac_rem_fija`, `tac_rem_var`, `tac_gastos_op`). Secondary sort by `serie`. |
| `GET /mutual-funds/{run}/flows` | Monthly aportes/rescates/NNM. Fund-level responses expose `nnm_reportado`, `migraciones_internas`, and adjusted external `nnm`; `serie` requests remain raw because migrations cannot be assigned reliably to one share class. Filters: `serie`, `from_date`, `to_date` |
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
| `GET /industry/currencies` | Normalized reporting currencies present in the latest FM/FI NAV data |
| `GET /industry/overview` | Single-currency snapshot (default CLP): AUM, counts, monthly flows, top ten admins, independent BTG aggregate, category breakdown, and source dates. Filters: `currency`, `fund_type`, `categoria`, `tipo`, `nombre_cat`, `admin`, `rescatable`. `include_ytd=false` restricts flow scans to the source's current month and returns null YTD fields; defaults to true for existing consumers. FM external flows subtract confirmed internal migrations; FI flows cover rescatable funds only. |
| `GET /industry/funds` | Unified FM/FI screener with classification, AUM, returns, and flows. Filters: `currency`, `fund_type`, `type`, `group`, `category`, `admin`, `rescatable`, `vigente`. Without currency filtering, results remain partitioned by currency. |
| `GET /industry/evolution` | Monthly AUM history within one currency (default CLP). Filters: `currency`, `fund_type`, `categoria`, `tipo`, `nombre_cat`, `admin`, `rescatable`, `from_date`, `to_date`; group by market/admin/category. Uses last available fund AUM per month and sums full-month FM flows, subtracting confirmed migrations. FI flows remain null. |
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

**Industria currency contract**: generic amount fields (`aum`, `total_aum`, `nnm`, etc.) are in native units of the explicit currency. No FX conversion is performed. Legacy `*_clp` aliases are populated only for CLP; they are null for other currencies. Source symbols `$`/`$$` normalize to CLP, `US$`/`PROM` to USD. The FI fund list includes `moneda` so the dashboard can filter its universe correctly.

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

### Working with BTGDashboard

The frontend lives in the sibling repository `../BTGDashboard`. Backend and
frontend work can be coordinated from this checkout. Read that repository's
`AGENTS.md` and `CLAUDE.md` before changing frontend files; its instructions apply
there. Keep changes and commits scoped to their respective repositories and
preserve existing local work in each.

Open `BTG.code-workspace` in an editor that supports multi-root workspaces to
browse both repositories. This file does not change sandbox write permissions;
access outside the active writable roots still requires tool approval.

Run the API here with `.venv/bin/uvicorn main:app --reload` and, in a second
terminal from this directory, run `npm --prefix ../BTGDashboard run dev`.
The dashboard's development `/api/cmf` proxy targets `http://127.0.0.1:8000`.
Validate frontend changes with `npm --prefix ../BTGDashboard run build` and
the relevant lint checks. Coordinate API contract changes across both repos.

### Branches and commits

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
| `BOLSA_COOKIES` | Session cookies for Bolsa de Santiago dividend and live-quote APIs (expires periodically) |
| `BOLSA_CSRF` | CSRF token for Bolsa de Santiago dividend and live-quote APIs (expires with cookies) |
| `BOLSA_QUOTE_PACE_SECONDS` | Delay between individual Bolsa requests in the BTG fixed-income buffer (default `3`) |
| `BOLSA_QUOTE_CACHE_SECONDS` | TTL for the on-demand BTG fixed-income quote snapshot (default `60`) |
| `NNM_EMAIL_ENABLED` | Enable the three scheduled fund net-new-money emails (`true`/`false`) |
| `NNM_EMAIL_FROM` | Resend sender identity (test default: `CMF Reports <onboarding@resend.dev>`) |
| `NNM_EMAIL_RECIPIENTS` | Comma-separated report recipients |
| `RESEND_API_KEY` | Resend API key used only by the worker; never commit it |

Set locally via `.env`. Production environment variables are configured for the
Oracle-hosted Docker Compose services.

## Connected industry workspace

`src/api/industry_workspace/` is mounted under `/industry-workspace` in the public
API. It promotes the reviewed design's read-only fund, portfolio, instrument,
shareholder, capital-activity, competitive-analysis and currency-exposure queries.
The production frontend uses `/api/cmf/industry-workspace`; no separate preview
service or localhost proxy is needed. Queries set a read-only transaction and a
20-second statement timeout. No schema or ETL changes are part of this promotion.

Administrator endpoints: `/administrators/analysis` provides the monthly ranking
and FM/FI strategy map; `/administrators/history` takes exact inclusive dates,
current active-fund/category identity and one native currency, returning daily
NAV points for spans up to 400 days. Longer spans use common reporting dates
from the monthly NAV views, with an exact daily query for the final boundary
month so a custom end date never includes later data. Share denominators use the same reporting day and currency. There
is no carried-forward NAV or synthetic zero for an absent administrator. History
samples can change; point-level fund counts remain visible.

`/administrators/flows` aggregates observed FM contributions minus redemptions
and confirmed/auto-confirmed receiving-fund migration adjustments. Adjustments
match currency, fund and observed flow day. Category/total rows sum their fund
children. Only observations with both valid flow amounts contribute; missing
flows stay null and coverage is returned. FM NNM is not inferred from NAV changes.
All these endpoints require NAV and use current fund identities/classifications.
Administrator responses use a bounded 60-second process cache.
`/administrators/currencies` returns NAV-backed native currencies for the selected
administrator/month/type/category, plus FM/FI currencies separately in the five-year NNM
history (`flow_currencies_by_kind`; the legacy FM list remains available). It uses monthly summaries and current active-fund identities; availability
does not imply that the AGF has NAV on the market's common reporting day or
reported flows in every window. Profile currency tabs must preserve empty states.
`/administrators/flows-periods` returns aligned 1D/1W/1M/3M/6M/1A/5A/YTD
columns for the same end date, with category/fund/AGF totals and per-cell coverage.
A single bound SQL query scans source observations once, then aggregates each
window and its matching migrations. The existing exact-range `/flows` uses the
same query path. Absent periods remain null; actual zero flows remain zero.
`/flows-periods?kind=fi` uses `investment_flows.py` to estimate net flow from
changes in implied units × current unit NAV, separately by native currency and
fund, for both rescatables and non-rescatables. It recalculates from source NAV
rather than relying on stored `flujo_neto`: series pairs require known matching
source currencies, finite valid NAV, identical series sets on consecutive fund
snapshots and at most seven calendar days between snapshots. First observations,
series changes, currency changes and longer gaps stay unavailable; fund-cell
exclusion counts disclose them. Aggregate rows sum only known estimates and
return comparable/observed fund-day counts. Missing days are not filled. This is
`methodology=nav_implied`, not reported cash flow, a capital-call confirmation or
migration-adjusted external NNM. Distributions and corporate adjustments may not
be captured. FI gross aportes/rescates remain null. FM remains the default.
Read-only PostgreSQL fixture checks: `RUN_WORKSPACE_DB_TESTS=1 PYTHONPATH=.
.venv/bin/pytest tests/test_workspace_fi_flows.py`.

Tests: `tests/test_workspace_*.py`; run with `PYTHONPATH=. .venv/bin/pytest`.
Local design/audit artifacts under `output/industry-concept/` are ignored; the
maintained frontend lives in the BTGDashboard repository. Deploy this backend
before the frontend that depends on `/industry-workspace`.

### Issuer portfolio history

Workspace portfolio snapshots, histories and instrument search expose optional
`underlying_fund_name`, `underlying_fund_run` and `underlying_fund_kind` for quota
instruments (`CF*`). The CMF FM/FI ticker registries resolve names, ignoring only
case, whitespace and hyphens; ambiguous normalized tickers remain unresolved.
The grouped lookup preserves row counts, weights and instrument/issuer identity.
`fund_name` remains the owning fund; `instrument_name` remains the CMF type label.
Resolved names are current registry labels, not historical legal names. No schema
changes or ETL writes are required.

`/industry-workspace/portfolio-history/{kind}/{run}` reads national and foreign
positions in one range query plus the available-period query. `months=3..60`
defaults to 12; `period` must be an available normalized month end. Monthly FM
and quarterly FI axes include an extra opening baseline and explicit reporting
gaps. Each source uses its latest filing in a month; original filing dates and
source/row/invalid-weight coverage remain visible. A bounded 60-second process
cache avoids repeated range scans. No schema changes or ETL writes are required.

Issuer keys use normalized RUT, otherwise exact reported name plus country;
unidentified issuers are not merged into a single entity. Instruments retain type,
reported settlement currency and source coverage. CMF currency mapping reuses
`currency_exposure.normalize` (PROM is USD; USD is insurance-unit USD). Duplicates
sum within a snapshot; invalid/negative/nonfinite weights propagate as missing.
Absent positions count as zero only if all their historical source tables have
positions in that period. Missing source reports remain null, including partial
multi-source holdings. Weights are not renormalized; this is national/foreign
asset exposure, excluding derivatives and equity-method participation tables.
Frontend issuer totals propagate unknown components. Weight changes are not trades.

History also returns `quantities`, `quantity_units` and per-instrument
`report_dates` arrays on the same axis. Quantities read FM `cantidad_unidades`
and FI `cant_unidades`, preserving raw `tipo_unidades` codes (these are not
necessarily settlement currencies or security counts). Duplicate quantities
sum only with a single known unit and valid nonnegative finite values; mixed
units, invalid values and unidentified multi-row groups remain null. Source
gaps retain null and observed absences retain zero, independently of weights.
Coverage includes `invalid_quantities` for rows with invalid amounts or missing
units. Consumers must reject quantity comparisons across unit changes and
unidentified securities, and must not sum quantities across issuer instruments.
The calendar uses available normalized closing dates; FM month-start source
dates are reporting-month labels, not daily holdings. Only positions actually
present in the selected period belong in its cartola. No daily interpolation
or transaction inference is performed.

`portfolio_debt.py` adds aligned `debt_terms` per instrument and whole-fund
`debt_profiles` per period using the same range query (no additional DB calls).
Maturity buckets use the selected month-end and final reported maturity; weighted
remaining years exclude expired, perpetual and missing maturities (not duration).
Reported yields require a recognized debt type and valuation code 1. Code 2 is
par and code 3 is price/relevant value, never yield. Rate averages require known
quantity denomination, interest type (NL/NC/RL/RC) and positive base days; each
convention is a separate group, with no annualization or FX conversion. Values
may be zero or negative. Missing source/period observations remain null; duplicate
instrument terms with incompatible rates or maturities stay explicitly mixed.
Weight coverage and unknown instrument types are exposed; profiles do not
look through fund units or include derivatives. Portfolio weights are not
renormalized. Tests: `tests/test_workspace_portfolio_debt.py`.

Validation: `tests/test_workspace_portfolio_history.py`; real local FM/FI response
weights reconcile with the selected source snapshot totals. Deploy the endpoint
before the frontend Cartera tree/heatmap.

### Fund portfolio denomination

Portfolio history also returns aligned `denomination_weights` maps for each holding,
using raw `tipo_unidades` independently of settlement currency and TIR convention.
CMF aliases reuse `currency_exposure.normalize`; fund and ETF types are grouped
as `FUND_UNITS` without look-through, unknown units as `UNKNOWN`. Duplicate rows
retain separate denomination weights and reconcile to the instrument total. Invalid
weights or partial instrument sources return null; an observed absence returns an
empty map. This is additive to the existing two-query response; no migration, extra
DB query or change to the legacy currency-exposure API. Frontend aggregation rejects
missing reports, historical source gaps, invalid positions and totals above 105% for
composition comparisons; reported weights are never renormalized. The fund
frontend always uses the denomination view (no legacy-view selector). Tests:
`tests/test_workspace_portfolio_denomination.py`.

### Instrument holder history

`/industry-workspace/instrument-history` takes exact `identifier`, nullable
`instrument_type`, required `issuer` (including an explicit blank string) and
`months=3..60` (default 12). It uses the same resolved issuer identity as the
instrument directory and returns separate FM monthly / FI quarterly axes, each
with an opening baseline and its latest completed reporting-month cutoff.
Every historical holder in the window is included, not just the latest holders.
Rows separate fund kind/RUN and normalized settlement currency. Quantity units
remain raw CMF codes. Arrays include weights, quantities, units, presence and
actual fund/source report dates; no daily/monthly interpolation is performed.

Three batched read-only queries load source cutoffs, matching instrument rows,
and source-filing presence for all historical holders. The instrument subset is
materialized before dimension joins to avoid an unstable broad join plan. Source
coverage scans only funds that held the instrument in that source. Latest filing
per fund/source/month wins. Missing source reports retain null; observed absence
can be zero only when all historical sources for that row report. Duplicate
quantities require a common known unit; invalid or mixed-unit quantities remain
null independently of weights. Consumers compare quantities only in matching
units and distinguish zero-base entries from percentage changes. A bounded
60-second cache avoids repeated scans; no new table or migration is required.

Validation: `tests/test_workspace_instrument_history.py`, existing portfolio
history/quantity helper tests and real FM/FI reconciliation against individual
fund histories. Deploy this endpoint before the instrument heatmap frontend.

### Ranking workspace

`/industry-workspace/rankings/returns?kind=fm|fi|all&currency=CLP` reuses existing
return views and joins the exact series NAV on the calculation date to filter
currency. It returns all available series (including null period values), current
effective classification and 1D/1W/1M/1A/5A/YTD columns. FM quality-flagged series
are excluded with a count; FI returns cover active rescatable and non-rescatable funds with valid NAV.
Returns are not averaged into administrator/category returns.
`kind=all` combines FM and FI series while retaining their kind and actual return
dates. `dates` contains each type's cutoff; the shared `date` is null when they
differ. The frontend can select exact kind/RUN/series identities without new queries.

`/rankings/nnm` takes kind, currency and 1D/1W/1M/3M/6M/1A/5A/YTD, or inclusive
`from_date`/`to_date`. Presets anchor to the latest monthly-summary source date
for that currency. It reuses administrator FM reported-external/FI NAV-implied
accounting and exposes fund metadata, observation coverage and FI exclusions.
Ranking calculations read `mv_nnm_daily_fm` / `mv_nnm_daily_fi` rather than
rescanning series history. Daily facts retain valid/observed counts, NAV eligibility,
FI exclusion reasons and prior snapshot dates. Current active status, names and
effective classifications are joined when querying; confirmed FM migration
adjustments remain live and require no fact refresh. FI request lookback boundaries
still distinguish missing bases from long gaps. Administrator source queries remain
available as the accounting reference.
`kind=all` uses the earlier available FM/FI cutoff for preset windows and applies
one identical range to both loaders. If only one kind has a cutoff, it uses that
date; an entirely empty currency has no invented window. Explicit date ranges
remain unchanged. Each combined row retains its kind and methodology; frontend
groups and totals stay separate for reported FM flows versus estimated FI flows.

Both result loaders use bounded 60-second caches with classification revisions.
`/rankings/nnm-periods` aggregates daily facts for multiple windows, retaining each
cell's adjustments, coverage and exclusions. FM/FI share
the earlier available cutoff; explicit dates override it. `window=recent` omits
5A and `window=long` returns only 5A, enabling progressive table loading at the
same cutoff. The default `all` returns all eight flow windows; custom ranges add
`custom`. Missing period cells are omitted, never substituted with zero. Long
ranking windows use the same daily-fact query path as shorter windows.

FI flow comparability permits unchanged empty series: both consecutive values
must explicitly report patrimonio=0 and valor_libro=0, with the same currency
and matching fund snapshot dates. Such pairs contribute zero while active series
retain the existing checks. Empty-to-active/active-to-empty transitions, nulls,
invalid NAV, series-set changes and long gaps remain excluded. This fixes funds
such as Pionero without dropping their empty series from identity/date checks.
Validated by read-only PostgreSQL fixtures in `tests/test_workspace_fi_flows.py`.

Migration `a7b8c9d0e1f2` builds and indexes the daily materialized views locally
before API rollout. NAV scheduler wrappers run tracked `fm_nnm_daily` /
`fi_nnm_daily` refreshes concurrently before publishing each monthly summary.
Full refreshes incorporate historical/same-date corrections and preserve the last
committed view for readers if refresh fails; failures mark the job as failed.
API caches may retain an older result for up to 60 seconds. Manual NAV backfills
must refresh these views as well (after the import):
`REFRESH MATERIALIZED VIEW CONCURRENTLY mv_nnm_daily_fm;` and/or
`REFRESH MATERIALIZED VIEW CONCURRENTLY mv_nnm_daily_fi;`.
The initial migration/refresh is heavier than a ranking read; run it before starting
the updated API. No production migration is performed by local development.

The calculation endpoints remain read-only. Saved definitions now live in the
separate authenticated shared-ranking library described below.
Validation: `tests/test_workspace_rankings.py`, `tests/test_industry_refresh.py`,
plus read-only PostgreSQL fixtures in `tests/test_workspace_fi_flows.py` (both
source and daily paths) and `tests/test_nnm_daily_fm.py`.

### Shared ranking library

`/shared-rankings` stores team definitions in PostgreSQL, not market-data snapshots.
Migration `z6a7b8c9d0e1` adds `shared_rankings` plus revision history. Apply it before
deploying the frontend library. GET list/detail/search are public and return
no-store responses, independently of editor credentials or key configuration.
`/session`, POST create, PUT update and DELETE require an editor bearer key.
Only authenticated editors may change saved definitions. `RANKINGS_KEYS` maps
editor names to SHA-256 hashes; unset/empty falls back to existing classification
editor keys. A malformed/nonempty ranking key configuration fails closed for editing. Keys
remain server-side; browsers keep entered credentials only in React memory.

All authorized ranking editors share one library and can edit its rankings.
Saved fund rankings also accept `sort=aum`, with either direction. AUM sorting
uses the frontend's latest native-currency fund snapshot; reject it for grouped
administrator/category rankings. No schema migration is required.
Updates/deletes require the loaded version and return 409 on stale versions.
Actor identity comes from authentication. Deletes hide the ranking and preserve
its revisions. Client UUIDs make a retried create idempotent for the same editor
and definition. Server validation preserves exact FM/FI/fund/series identities,
currency, date ranges and limits. List pagination is 100 maximum per request.
Saved definitions also accept an optional `description` (maximum 500 characters,
trimmed; defaults to empty for older definitions). It lives in the existing config
JSON and revision history, requiring no schema migration. Public listing
accepts `search` (maximum 120 characters): literal, case-insensitive matching over
name and description, applied before counts/pagination. Deploy this API update
before the selector UI, since older API validators reject the new config field.

Definitions accept up to 2,000 concrete fund keys to support complete AGF/category
selections, retaining the 500-explicit-series limit. Groups are saved as the funds
selected at that time; new members are not automatically added later.

`.venv/bin/python scripts/create_ranking_editor.py --name NAME` provisions a
ranking-only key in ignored `.local/` and its hash in `.env`, retaining existing
editor access when first creating the separate map. It does not grant category
administration. Restart the API after provisioning. Never commit key files.
Docker forwards `RANKINGS_KEYS` to the web service. Existing admin access works
without additional local provisioning. No production migration/deployment is
performed by local setup.

Validation: `RUN_WORKSPACE_DB_TESTS=1 PYTHONPATH=. .venv/bin/pytest -q
tests/test_shared_rankings.py` exercises schema validation, auth, two-editor CRUD,
revision history and conflicts in rollback-isolated transactions.


## Private classification administration

The workspace `/funds` response includes `category_type` and `category_group`
alongside `category`, from the same effective classification row. The dashboard
uses these fields for its FM/FI → type → group → category filter hierarchy.

`/classification-admin` provides authenticated session validation, FM/FI catalogs,
fund search/detail/history and version-checked PUT lock/unlock operations. The
frontend exposes `/admin/clasificaciones` through the module selector. The public
read API stays available without authentication; all admin reads and writes require
an individual bearer key and use `Cache-Control: no-store`. Missing or malformed
`CLASSIFICATION_ADMIN_KEYS` disables admin access (503); it never bypasses auth.
Keys are random secrets, mapped to editor names by SHA-256 hashes in that JSON env
variable. Actor names come from authentication, never the edit payload. Use HTTPS
in production; never embed keys or this variable in frontend builds.

Provision local private access with `.venv/bin/python
scripts/create_classification_admin.py --name Juan`. It saves only the hash in
`.env`, writes the secret to a mode-0600 file in gitignored `.local/`, and never
prints the secret. Restart the API to activate keys. Repeat with another editor
name for distinct attribution. Remove a hash to revoke access, then restart API
workers; the script refuses accidental replacement of an existing editor.

Migration `y5z6a7b8c9d0` adds `classification_overrides`, append-only application
audit history and `categoria_fm_effective` / `categoria_fi_effective` read views.
An active per-kind/fund override replaces the category hierarchy in those views
across reporting periods; original algorithm rows and metrics remain untouched.
A fund without algorithm output gets a synthetic classification row only while
its override is active. Raw CMF/NAV/portfolio data is never edited here.

Classifiers continue writing raw `categoria_fm` / `categoria_fi`, including new
periods. Public API and MCP category consumers read the effective views; the
private admin page reads raw suggestions plus the override so differences remain
visible. Internal migration detection retains algorithm categories; category
corrections do not retroactively recalculate money or flow adjustments. Dashboard
historical analyses already use current classifications: an override changes
that grouping, not historical observations. Database audit revision participates
in administrator cache keys across API workers. Classification-dependent public
responses revalidate HTTP caches; leaving Admin for Industria reloads client data.

The API validates category/type/group against the classifier catalogs, requires
a reason, serializes writes per fund and rejects stale expected versions with
409. Unlocking keeps audit history and reveals the latest automatic category.
Tests: `tests/test_classification_admin.py`; enable rollback-isolated local DB
checks with `RUN_WORKSPACE_DB_TESTS=1 PYTHONPATH=. .venv/bin/pytest
 tests/test_classification_admin.py`. Apply the migration and configure private
access in deployment before exposing the new Admin module; no production changes
are made by local provisioning.

### Ownership workspace

`/industry-workspace/ownership` provides paginated directory, quarterly movements
and concentration (`view=directory|movements|concentration`, quarter, exact AGF,
J/N holder type, search, state and BTG relationship filters). It reads reported FI
ownership with a bounded 60-second per-quarter cache. Distinct RUT/fund positions
aggregate duplicate rows only with valid percentages; invalid/incomplete shares
stay null. Concentration includes unidentified reported holders and rejects
incomplete or inconsistent totals. No HHI or currency-valued AUM is inferred.
Movements use the preceding calendar quarter and whole-fund reporting presence
before applying holder-type filters. Not-listed is not a sale, missing reports
are not zero, and deltas require both reported percentages. Counts remain distinct.
`/ownership/{rut}` exposes eight quarter-end periods with per-fund presence and
reporting coverage, exact ownership percentages, and fund/AGF count history.
No schema change is required. Tests: `tests/test_workspace_ownership.py`.

Ownership directory adds `currency=CLP`, `sort=aum|funds|admins|max_weight` and
`direction=asc|desc`, defaulting to descending estimated AUM before pagination.
The explicit NAV age selector accommodates gaps in the local time series. AUM is reported ownership percentage times the fund's summed series patrimonio
on one NAV snapshot at/before quarter end (maximum age `nav_days=7|31`, default 31). All series
on that snapshot must have nonnegative patrimonio and one known native currency;
no per-series carry-forward or FX conversion is used. Missing NAV/ownership stays
unvalued. Per-owner totals expose valued/unvalued/other-currency fund counts and
actual NAV dates, preserving zero and sorting unavailable values last. Other
currencies are excluded from that amount, not from the ownership directory.
The owner profile exposes the same estimate for its full reported fund set.
These are proportional estimates, not audited investor assets or beneficial-owner
wealth. Existing share/count history and movement semantics remain unchanged.

## Insiders / CMF Article 12 disclosures

`src/etl/insiders/` imports the public Article 12 transactions endpoint directly:
`https://www.cmfchile.cl/institucional/inc/tra/art_12_val.php`.
Explicit inclusive `desde`/`hasta` (`dd/mm/yyyy`), empty `soc`/`dias`, and **both**
`vig=VI` and `vig=NV` are requested in monthly windows from January 2020.
The explicit ranges filter transaction dates; communication dates are stored
separately and can be outside the requested transaction-date window.

Tables: `insider_transactions` (typed disclosures + raw cells, full-row fingerprint,
source occurrence, correction reference and current/superseded state),
`insider_imports` (successful atomic windows, source URLs/checksums/counts/times),
and `insider_codes` (Article 12 relationship, transaction, security and entity-status
labels from the CMF technical sheet, pages 3–5). Financial values use PostgreSQL
NUMERIC / Python Decimal. The source labels amounts as pesos; unit price may instead
be a rate, so it must not automatically be multiplied by quantity to infer cash.
Unknown codes remain verbatim and can be shown without a translated label.

VI/NV are source entity lists, not whether a transaction is valid. Historical
aliases can overlap: Camanchaca appeared under both its current and former name.
Identical fingerprints + occurrence across those lists merge into one disclosure,
with **all** `source_entities` preserved; VI/name takes display precedence.
Identical multiple rows within one list retain their multiplicity. Different
informants/roles can report the same economic transaction; do not sum this dataset
as a deduplicated trading tape. `is_amendment` is CMF's “Modifica Información Enviada”
flag, with a bitácora reference when exposed. There is no stable ID for every source
row, so no inferred old-to-new amendment linkage is claimed.

Each window validates both complete pages before any mutation, and commits both
lists together under an advisory lock. Repeated loads reactivate the same keys;
changed/removed rows become `is_current=false` and remain available for audit.
A slower older snapshot cannot overwrite a newer import. Invalid layouts,
truncated responses, ambiguous numeric fields and out-of-window dates fail the
tracked job; a genuine `SIN INFORMACION` page is accepted. Known malformed HTML in
observations is handled without including a nested cell's text in its parent.
Calendar dates accept both `dd/mm/yyyy` and CMF's `dd/mm/yyyy HH:MM:SS`
rendering, validating the complete value and retaining the original raw cells.
The time suffix does not change disclosure identity or date-window checks.

The `insiders_transactions` job runs daily **08:45 America/Santiago**, rechecking
100 days rounded back to month start plus any missing historical windows. On day 1
of each month it rechecks all history for older corrections/issuer aliases. The job
is registered in the normal worker; importing modules/using the CLI does not start
a scheduler or send emails. Manual local import:

```bash
PYTHONPATH=. .venv/bin/python -m src.etl.insiders.downloader --since 2020-01-01
# Bounded retry (inclusive dates)
PYTHONPATH=. .venv/bin/python -m src.etl.insiders.downloader --since 2023-02-01 --until 2023-02-28
# Daily plan, including missing historical months
PYTHONPATH=. .venv/bin/python -m src.etl.insiders.downloader --daily
```

The manual CLI rejects remote DB hosts. Deployment uses the usual migration and
worker rollout; this feature has only been loaded locally until explicitly deployed.

Read API:
- `GET /insiders/transactions`: current disclosures, newest first; date range and
  transaction/communication date basis, issuer/informant RUT (without DV), ticker,
  relationship, transaction/security type, preferred entity status, amendment and
  text filters; `limit` (max 500) / `offset`. Exact financial decimals are strings.
  Includes translated code labels, both source aliases and correction links.
- `GET /insiders/codes`: code dictionary + technical-sheet source link.
- `GET /insiders/coverage`: import windows, counts, source provenance and missing
  expected windows. Coverage concerns downloaded source snapshots, not proof that
  every legally required disclosure was filed.

`tests/test_insiders.py` covers parsing, exact decimals, null/zero distinctions,
malformed pages, duplicate multiplicity, source aliases, amendment replacement,
atomic rollback, stale fetches, schedule/catch-up, API filters and coverage.
Set `RUN_WORKSPACE_DB_TESTS=1` for local PostgreSQL temp-table/rollback checks.


### Insiders explorer

`src/api/insiders_explorer.py` supplies the Industria Insiders workspace:
`/insiders/directory` (company/informant RUT options and code labels),
`/insiders/explorer` (multi-select company/informant filters, search, both date bases,
source-list status, operation/security/role, amendments, owner-only filter, sorted
pagination plus monthly disclosure counts and leading companies), and
`/insiders/connections?rut=&as_of=` (reported FI participations).
Filtering is OR within each RUT list and AND between dimensions; max 50 RUTs per
list. Ordering/counts apply before pagination. Financial decimals remain strings.
VI/NV filtering includes all observed source aliases of merged disclosures.

Aportantes links require an exact numeric RUT body after removing formatting and
leading zeros; unidentified/invalid RUTs never link. Connections use the latest
universe-wide source quarter at or before the requested end date, with last-seen
history explicitly separate. They do not fall back to older holdings as current,
sum ownership percentages across funds, or identify beneficial ownership. The
informant directory marks historical matches with their last source date.
Fund navigation requires an active FI with NAV. Directory/owner indexes have a
60-second process-local cache; source imports remain the authority. These endpoints
reuse existing tables and need no additional migration beyond the Insiders schema.
Both disclosure endpoints enrich the returned page with `broker_name` and
`broker_name_source` using one exact normalized-RUT lookup in the existing SII
`emisores` registry. Raw `broker_rut` remains unchanged; unmatched or unidentified
brokers retain null names. This is the registry name, not a transaction-date name
history. No new scraper, migration, or changes to disclosure identity/counts.
Regression tests include multi-select intersections, sorting, empty pages, exact
decimals, source duplicates, alias status and as-of ownership joins.


### Ownership investment analysis and AGF comparison

`ownership_analysis.py` adds `/industry-workspace/ownership-analysis` (scoped
investment distribution and paginated quarterly activity), `/ownership-analysis/{rut}`
(eight quarters of estimates, current fund allocations and change decomposition),
and `/ownership-comparison` (two exact AGF names, one quarter, shared/exclusive RUTs
and a static combined scenario). All reuse the existing FI ownership and NAV tables;
no migration or new ETL job is required. Category filters use the latest effective
classification at or before the quarter, including manual overrides. Administrator
labels come from the current fund registry, not a historical corporate-group model.

Comparison identity is a normalized numeric RUT body, never fuzzy name matching.
Unidentified/invalid IDs are excluded from owner unions. The combined count is
A + B minus their intersection; amounts sum distinct fund/owner positions, not
client counts. This is not a beneficial-owner or economic look-through model.
No inter-fund holdings elimination, retention assumption or merger forecast.
Both AGFs share the requested quarter and native currency; funds without usable
NAV retain ownership relationships but no invented monetary estimate. Coverage
includes valued, missing and other-currency position counts. Global sorting in the
client precedes comparison pagination; the response includes fund drill-downs.

Estimated investments use ownership percentage × whole-fund NAV patrimonio at or
before the cutoff (7/31-day maximum). Reported fund totals above 100.01% preserve
source percentages but exclude their monetary valuation, consistently in the
directory, profile and new analytics. Missing or unlisted histories remain null.
BTG percentages and combined top-five concentration use known amounts only and
must be labelled as shares of observed/valued investments, not total wealth.
Changes decompose exactly as w0 × (A1 − A0) plus (w1 − w0) × A1, only for comparable
weights, currencies and NAVs. Patrimonio effects include fund flows as well as
valuation. Neither term is confirmed investor cash flow.
Tests: `tests/test_ownership_analysis.py`, `tests/test_workspace_ownership.py`.

### Calendar-month NNM rankings

`/industry-workspace/rankings/nnm-periods?window=calendar&year=YYYY` returns
YTD followed by disjoint calendar-month windows at the common FM/FI cutoff.
An optional `to_date` pins that cutoff; `year` is calendar-only and `from_date`
is rejected in this mode. Previous years end on December 31. Months after the
cutoff are omitted, never filled with zero. The last incomplete month includes
its actual end date and `truncated=true`. Currency, current eligible universe,
methods, classifications, adjustments and coverage follow the existing rankings.

`nnm_daily.flow_data` accepts optional `(key,start,end)` windows as well as the
existing `(key,start)` rolling windows. Bounded windows filter both daily facts
and confirmed FM migration adjustments by their own inclusive dates. FI bounded
windows use their own seven-day baseline lookback. Each fund kind is queried
once for the year; no new table, migration, or ETL is required. Tests in
`test_nnm_calendar.py` cover leap years, cutoffs, request validation and read-only
PostgreSQL reconciliation against standalone ranges. Enable these fixtures with
`RUN_WORKSPACE_DB_TESTS=1`. Deploy this API support before the monthly frontend.

### FI return coverage

Migration `d0e1f2a3b4c5` rebuilds `mv_rentabilidad_fi` for all active FI with
positive finite series NAV, nonnegative patrimonio and identified native currency,
including non-rescatables. The near-complete reference date now counts both FI
types and ignores future NAV dates. Existing exact-date 1D/1W/1M/1A/5A/YTD
baselines and additive NAV-plus-distributions accounting remain; missing, zero,
non-finite or different-currency baselines yield null per period. Confirmed series continuity is applied by the separate layer described below. Current zero-NAV series are excluded.

Dividend ticker mappings deduplicate normalized fund/series/ticker identities.
Distributions match the current series currency and ex-date within each period,
including its closing date and excluding later announcements' future ex-dates.
The migration restores the unique and return-sort indexes required by concurrent
scheduled refreshes. No additional job is needed. `/rankings/returns` also removes
its rescatable-only predicate; existing API shapes and currency filters remain.
Apply the migration before rolling out this API/frontend coverage change.
Validation: `tests/test_fi_return_coverage.py` (read-only PostgreSQL fixtures via
`RUN_WORKSPACE_DB_TESTS=1`) and `tests/test_workspace_rankings.py`, then local
Vinci Compass Small Cap Chile (7031) and Pionero (7010) API/UI series checks.

### Fund distribution history

Fund TAC history accepts `GET /mutual-funds/{run}/tac?full_history=true` to
return all stored monthly reports and their series for the detail calendar and
3Y/5Y charts. The default retains the last 24 reported periods for existing
consumers. This is a read-only query extension with no migration or new job.
Tests in `test_tac_history.py` cover the default and full-history paths over
seven years, keeping all series per period and isolating the requested fund.

`fund_distributions.py` implements `/mutual-funds/{run}/dividends` and
`/investment-funds/{run}/dividends`, with optional exact `serie`, `from_date` and
`to_date` filters. FI matches normalized official tickers to Bolsa dividend/reparto
events, deduplicating ticker aliases while excluding subscriptions and exchanges.
Dates use payment date, falling back to the reported limit date; both are returned.
Amounts/currencies remain original, without continuity-link rescaling or invented
fund-level payment totals. FM returns valid non-neutral reparto factors from CMF
cartola, leaving cash amounts null. Existing tables/jobs suffice; no migration.
Validation: `tests/test_fund_distributions.py` and real local FM/FI API requests.

### Confirmed FI series continuity

Migration `e1f2a3b4c5d6` adds `fi_series_link_sets`, `fi_series_links` and an
append-only `fi_series_link_audit`. Links represent confirmed whole-series renames
or conversions within one FI. They preserve original NAV and accept chains such
as B → A → O. Each link records its effective date, official source URL, reason,
and successor units per predecessor unit. Rename ratios must be one. The initial
7018 A → O link is supported by BTG's official notice effective 2026-09-07; no
other Small Cap links are inferred.

`/series-continuity/admin/fi/{run}` GET/PUT and POST `.../preview` reuse the existing
classification editor authentication. Whole-fund version checks and an advisory
transaction lock protect concurrent edits; the audit records the server-resolved
editor and complete before/after definitions, including removals. Validation
rejects cycles, branching/merging, backwards/future dates, overlapping positive
NAV histories, currency changes, invalid NAV and missing transition bases beyond
seven days. Candidate suggestions use adjacent reporting dates only and cannot
confirm a link. Public GET `/series-continuity/fi/{run}` exposes evidence/definitions
without editor identities or audit records.

SQL helpers `fi_series_segments`, `fi_nav_history`, `fi_series_distributions` and
`fi_linked_returns` traverse dated predecessors and divide prior NAV/distributions
by cumulative conversion ratios. Patrimonio is never rescaled. Distributions use
exact historical series/ticker mappings, deduplicate normalized ticker identities,
respect ex-dates and match native currency. No missing ticker mapping is guessed.

`v_rentabilidad_fi_continuous` overlays linked series on the existing daily return
MV; unchanged series retain the original path. All FI return consumers, including
MCP, rankings, industry and detail, use this view. Exact missing period bases remain
null. Ranking rows include `series_history` metadata. `v_fi_nav_canonical` and
`v_nnm_daily_fi_continuous` provide a single identity per chain for administrator
and ranking NNM, retaining original money amounts and existing coverage checks.
No new worker job or full MV rebuild is required when an editor saves a link.
Admin audit revisions invalidate workspace process caches across workers.

FI `/return-series` defaults to `linked=true`; false uses only the requested raw
series. Both modes use the same dividend accounting, include ex-dates without a
NAV observation, and require a valid base on/before the requested start within
seven days. Points include `source_serie`; currency discontinuities do not bridge.
Confirmed predecessor names are hidden from the current fund-detail series picker;
the raw `/nav` endpoint remains available for their original observations.

Apply the migration before deploying these API changes. Validation:
`RUN_WORKSPACE_DB_TESTS=1 PYTHONPATH=. .venv/bin/pytest -q tests/test_series_continuity.py`
plus FI return, source/daily NNM, calendar-window and classification-admin regressions.
Multi-step conversions, distribution scaling, unchanged raw data, zero artificial
NNM, stale edits and removal/audit are tested with rollback-isolated local fixtures.

### FM fund flow workspace

`fund_flows.py` exposes `/industry-workspace/fund-flows/fm/{run}` for the fund-detail
Flujos view. Native currency, rolling `period` (1D/1W/1M/3M/6M/1A/3A/5A/YTD), or
inclusive `from_date` + `to_date` produce one cached response with every series,
fund total, adjacent equal-length previous period, daily/weekly/monthly chart
buckets, and calendar-month/YTD totals through the selected cutoff. Presets use
calendar month/year subtraction plus one day, anchored to the latest fund cartola.
The available history starts in 2020; unavailable prior periods remain partial.

Series use validated paired nonnegative finite reported aportes/rescates in
`cartola_diaria`. Fund totals subtract confirmed target-fund migrations separately;
never allocate those adjustments to a series. Missing values remain null, genuine
zeros remain zero. Ratios use a positive opening NAV from one snapshot date within
seven days before the range. Missing calendar days or invalid flow observations
suppress full-period ratios and previous-period differences; cumulative ratios
stop at the first incomplete bucket. Coverage counts reported rows/dates, not
proof that all possible series were filed. Series transfers are not necessarily
external subscriptions/redemptions.

Category peers use the exact current effective classification tuple, active FM
with reported daily facts, same currency and dates, and exclude the selected fund.
Existing `mv_nnm_daily_fm` facts supply peers; boundary NAV comes from indexed daily
cartola queries. The category ratio and cumulative curve use one fixed cohort
with complete daily coverage and positive opening NAV. Monthly/YTD absolute
category NNM includes every active peer with NAV in the cutoff year, regardless
of opening-NAV availability; it excludes this fund and reconciles disjoint months.
The existing daily facts supply the calendar in one additional grouped query,
with confirmed migrations, reported-row and fund-day coverage. Missing months
remain null, never zero. This comparison replaces the frontend peer-ranking panel. No FX, average of fund percentages,
new table, migration or job. Cache follows the shared classification/data-minute
epoch. Deploy the API before the frontend. Tests: `tests/test_fund_flows.py`, with
`RUN_WORKSPACE_DB_TESTS=1` for read-only PostgreSQL category/accounting fixtures.

FI NNM rankings materialize the currency/date-filtered daily continuity facts
once, aggregate the requested windows, then join eligible funds at the end.
Do not move the eligibility join back into the FI observation CTE: PostgreSQL can
choose a nested-loop scan of every fund against the full continuity result and
hit the 20-second API timeout. The final join still excludes inactive funds and
supplies current classifications. FM's indexed daily-fact query is unchanged.
Regression coverage includes inactive FI, other currencies, nulls, gaps and
calendar-window reconciliation in the ranking/calendar tests.

Fund-flow date coverage uses the registry's `fecha_inicio_operaciones`: only days
on/after confirmed inception are expected. Category monthly/YTD denominators sum
per-fund expected days and exclude not-yet-started funds from the period count.
Responses expose `pre_inception_days` and category `started_funds` so clients can
explain adjustments. Missing official dates keep the full window; an earlier
historical observation conflicting with the registry date disables the adjustment.
The first downloaded date alone never proves inception (including new series).
Complete observed cash flows since inception do not invent opening NAV, full-window
ratios, or like-for-like previous-period differences. Tests cover midmonth starts,
pre-inception months, unknown/conflicting dates, invalid rows and post-start gaps.

### CMF fund-deposit registry (Nuevos Fondos)

`src/etl/fundDeposits` downloads the full CMF `deposito_fondos_mutuos.php`
registry daily at 08:35 America/Santiago using tracked job `fund_deposits`.
Migration `f2a3b4c5d6e7` adds `fund_deposits`, `fund_deposit_imports` and
`fund_deposit_revisions`. Initial and daily runs fetch the full registry, including
historical and inactive funds; no NAV requirement or 2020 cutoff applies.
Use `PYTHONPATH=. .venv/bin/python -m src.etl.fundDeposits.downloader` for a
local-only manual run (`--file PATH` accepts a previously fetched full response).

The parser validates all 19 column headers, complete HTML/table boundaries,
identities, dates, types and document URL hosts. CMF renders two identical tables;
these must agree and are imported once. The identity is (registration, RUN),
since registration alone is not unique in the source. Blank fund names remain
blank, with the UI showing the RUN instead. Empty document placeholders are
ignored; nonempty references without links are preserved with null URLs. Legacy
SVS document paths are normalized to official HTTPS CMF URLs and their exact
source URLs are retained. Deposit date is not an inferred fund-inception date.

Imports use an advisory lock and one transaction. Current data is upserted;
changed definitions/documents are retained as timestamped observed revisions.
Unchanged imports do not create revisions. Missing source entries are retained
with `source_present=false`, never inferred liquidated. Empty, malformed,
conflicting mirrored tables, or a snapshot losing over 20% of previous source
rows fail the job without replacing the last good snapshot. Import provenance
includes source URL, SHA-256, retrieval time and inserted/updated counts.

Read APIs: `/industry-workspace/new-funds` supports exact multi-administrator,
FM/FIR/FNR, status, search and inclusive dates on deposit or modification, with
stable newest-first pagination and filtered totals. `/new-funds/directory`
provides admin options, source date bounds, latest successful import and schedule.
Both query stored data; no on-demand CMF scraping. Test with
`RUN_WORKSPACE_DB_TESTS=1 PYTHONPATH=. .venv/bin/pytest -q tests/test_fund_deposits.py`.
DB tests use transaction-scoped temporary tables, leaving existing registry data
untouched. Deploy migration and import before exposing the frontend tab.

### Insurance investment portfolios

`src/etl/insurance` provides a Circular 1835 archive client and strict
parsers for all 12 families present in the public pilot: I debt, A shares/FI,
F FM, B property, X foreign investments, O other investments, P derivatives/repos,
C control, D descriptors, T payment schedules, E eligible cash flows, G collateral.
The original inspection-only pilot remains available. Run
`PYTHONPATH=. .venv/bin/python -m src.etl.insurance.pilot --download --local-reference`
to fetch the latest two published months with the existing `src/captcha.py`
helper. Omit `--download` to revalidate staged files without CMF/Gemini calls.
`--local-reference` enforces a local DB host and read-only transactions; labels
are looked up by exact identifiers. `--group CSVID` is the default; CSGEN uses
the same transport but requires its own live validation before rollout.

ZIPs, hashes, source URLs, retrieval times, JSON validation reports, fund holdings,
administrator/insurer totals, and monthly changes remain under ignored
`output/insurance-pilot/`. These pilot artifacts remain reproducible and are not
deleted by the persistent loader. Test with
`PYTHONPATH=. .venv/bin/pytest -q tests/test_insurance_pilot.py tests/test_insurance_public_families.py tests/test_insurance_workspace.py`.
Set `RUN_WORKSPACE_DB_TESTS=1` for transactional local integration fixtures.

Migration `a3b4c5d6e7f8` adds `insurance_archives` (original compressed ZIP bytes,
SHA, retrieval time and source URL), `insurance_imports` (active monthly snapshot
and report), `insurance_files` (including quarantines and reconciliation evidence),
and `insurance_records` (typed searchable fields plus every accepted raw field).
The initial local import is August and September 2026 CSVID only. Import explicit
staged ZIPs using `python -m src.etl.insurance.jobs --archive PATH` (repeatable).
The manual CLI rejects nonlocal DB hosts; scheduled workers use their configured DB.

`loader.py` validates complete insurer/family coverage, serializes concurrent imports
with an advisory lock, and activates replacements atomically. An unchanged archive
hash and parser version only update the last-check timestamp. Revisions retain old
archives/reports/file metadata but replace old parsed rows. A decline exceeding 20%
of accepted records stops replacement for review. Failed imports preserve the last
active snapshot. Bad files stay excluded; field-level syndicated-loan warnings are
retained without correcting reported participation, borrower identity or yield.

Tracked job `insurance_portfolios` runs daily at 08:55 America/Santiago in the
separate worker. It fetches the latest two published months, never earlier than
August 2026, using the existing CAPTCHA helper. Successful imports remove only
the job's staging download after the original archive is stored in PostgreSQL.
CSGEN remains blocked until independently validated against live source files.

Read API `/industry-workspace/insurance` provides `/directory`, `/summary`,
`/records`, `/records/{id}`, and `/coverage`. Filters include month, insurer RUT,
reported administrator RUT, asset class, instrument denomination, family, search,
and holdings/contracts/support section. Records are paginated and allowlisted for
sorting; row detail loads on demand. Coverage describes source files for the month
and insurer, independently of instrument/AGF filters. Personal mortgage-debtor
identifiers are omitted from public detail responses.

`/summary?include_breakdowns=true` additionally groups accepted primary holdings
by asset/currency and by month/currency for the dashboard's default Seguros
Panorama. Both reconcile to the corresponding CLP summary and honor the same
filters. Missing denomination stays a separate null bucket. These optional
breakdowns do not sum original-currency notionals or include complementary rows.
`metrics.unvalued_records` counts primary positions without a final valuation.
Instrument denomination is distinct from the source's final value in M$: the
parser multiplies that value by 1,000 once to persist CLP.

`/portfolio-heatmap` powers the unified Cartera matrix. Without `asset_class`,
columns are asset classes; with it, columns are instruments (24 per page, maximum
60). Filters include insurer, AGF, denomination, text and exact instrument type.
`insurance_portfolio.py` aggregates primary positions once per insurer/instrument,
returns all insurers, and keeps totals and heat scale over the full filtered
selection, independently of pagination. Source records remain accessible via detail.
Fondos uses its existing classification/RUN-specific endpoints inside Cartera.

`/portfolio-history` requires one insurer, supports the same 1–60 month range,
and returns 100 instruments per page (maximum 200). SQL selects identities before
building monthly points, including one baseline month; sorting by name, current
position, or a month in the selected value/delta/deltaPct mode applies to the whole
result. CSV is a frontend current-page export. No NAV lookup runs here.
Usable families required for comparison: I/X debt, A/X equities, B/X property,
O other assets, X subsidiaries; all relevant families for the all-class summary.
Validated files are usable, with approved count recovery also usable for A/F.
Missing or partial sources cannot produce zero holdings or fabricated exits.

Instrument identity uses exact identifier, type, issuer, series, denomination and
country. MHA/MHB mortgages, CLEAS, CS, DPL/DPC, OIE and BR identifiers are also
scoped by insurer; property uses insurer/municipality/city/roll, and O records use
insurer/type/identifier/asset code/currency. These conservative keys do not infer
cross-insurer relationships for local assets. Missing/placeholder identifiers
remain separate records, with no historical delta or inferred zero. No fuzzy
names or personal borrower identifiers are used to establish continuity.

`/fund-heatmap` aggregates accepted primary fund holdings by insurer, fund,
reported AGF and denomination, summing series/subportfolios once. It joins
`categoria_fm_effective` / `categoria_fi_effective` by exact `(kind, RUN)`,
choosing the latest classification up to today, including manual overrides.
This is explicitly **current** classification applied to a historical holding,
not an as-of insurer taxonomy. Never join only on RUN, name or similar ticker.
Private/foreign/unmatched funds remain unclassified and inside the observed
fund total. No active-fund or NAV requirement is imposed. Insurers with no
accepted fund positions remain in the directory with unavailable amounts,
not inferred zero investment. A/F/X file-quality flags accompany insurer rows.
The endpoint reads existing snapshots; no extra ETL or materialized copy is needed.

`/fund-history` provides an insurer/AGF/category-filtered monthly fund matrix,
with 1–60 month windows or explicit `from_month` and ending `period`. It batches
the selected snapshots plus one previous month for deltas. Fund identities
aggregate all selected insurer/series/subportfolio rows once; fund NAV is not
multiplied by the number of insurers. Leading pre-import months are omitted
from columns with explicit requested/loaded-month counts; internal gaps stay null.
Baseline-only funds remain visible for observed exits in a one-month window.
The dashboard now requests `include_fund_metrics=false` for this position-only
matrix, skipping NAV and Aportantes lookups. The default remains true for existing
API consumers requiring fund AUM/ownership; position and delta logic is identical.

`insurance_history.py` checks the insurer cohort observed for each fund. Both
domestic (F for FM, A for FI/FIP) and X files must be usable before absence
becomes zero or a consecutive-month delta is calculated. A/F approved count
recoveries are usable with warnings; X must be validated. Missing/quarantined
sources cannot generate an exit. Deltas describe reported valuation changes,
not flows, trading or returns.

Fund AUM uses actual NAV at that month-end, with the monthly industry view as
a dated fallback when an exact-close NAV is unavailable. It remains in its native
currency; multiple denominations stay separate. Ownership first uses same-date
CMF Aportantes percentages where all selected holders match and no AGF/currency
filter narrows the position. Otherwise it may estimate the selected holders'
share from summed units × the exact series NAV divided by whole-fund NAV,
requiring one common native currency and the same closing date. This works
without an FX rate for USD funds. A comparable CLP valuation/NAV ratio is a
fallback when series units cannot be matched. Estimates carry explicit sources.
Missing/stale/mixed-currency/nonpositive/incomplete bases and results outside
0–100% stay unavailable with reasons, never clamped. No fuzzy series matching,
current FX, or portfolio-implied AUM is used.

The dashboard's **Seguros** tab uses these endpoints independently of the fund NAV
universe. Values are CMF-reported CLP, displayed in MM CLP; denomination is not a
request to convert valuations. PROM maps to USD; CMF USD stays `USD_SEGURO`.
Derivative currency filters match either reported swap leg. Observed totals exclude
complementary holdings, contracts and auxiliary records, and are not certified AUM.
Optional complementary detail never changes those totals. Monthly valuation
comparisons retain each month's accepted coverage and are not labelled flows,
trades or investment returns.

Use the consolidated `Anexos_Tecnicos_Circular_N1835.pdf` in CMF's
`/sitio/seil/software-manual/sgsci/` directory. The individual PDFs linked from
`SGSCI_circ_1835.doc` have obsolete field offsets. Current public exports use
`<family>YYYYMM<V|G>.<insurer RUT>`, UTF-8, fixed character widths, and implied
numeric decimals. Widths: I=970, A=572, F=361, C=138, B=506, X=611, O=510,
P=587, D=108, T=80, E=178, G=300. X has detail types 2–5 and trailer 6;
P has detail types 2–6 and trailer 7. Explicit detail filler must be blank.
`extended_layouts.py` defines all fields for the additional families. Do not count
UTF-8 bytes as character positions or trim entire records. Older history needs
explicit versioned layouts before the proposed January 2020 backfill; dates
before December 2024 are rejected rather than silently using current offsets.
Live validation covers August/September 2026 CSVID. X type 4 was not observed;
the sole X type 5 source has nonblank filler and remains quarantined. Their
layouts have synthetic specification tests, not accepted real-source fixtures.
R (loans) and V (rental/mortgage arrears) are in the manual but absent from these
public archives; no data is inferred for them.

Validate header/filename/request identity and month, full record widths, numeric
fields and closing record counts. Never silently reassign an insurer or a period.
The parser stays strict by default; inspection mode permits only a numeric
trailer-count mismatch, retaining original counts/hashes and validating every
other field. `insurance/reconciliation.py` may recover the F11 (CFI/CFIP/CFIPOIND)
or F31 (CFM) category only when its positive final-value sum matches the separate
C control exactly. Unknown instrument variants, duplicate detail rows, missing
controls, negative values, or identity/date conflicts cannot recover a category.
No tolerance, rewriting of source records, or empty 0=0 match is used.

Recovered holdings have `count_warning_reconciled` quality; their file status is
`accepted_with_warning`, limited to the verified fund rows. A count-defective C
file is only `partially_reconciled`: unmatched control rows remain excluded.
Other defects and uncorroborated count mismatches remain quarantined. A control
file may itself have a count warning, but its category value must independently
agree with the separate detail file; this is evidence for warning-labelled use,
not certification of each series/quantity or the whole portfolio. JSON evidence
includes both files/hashes, exact values/residuals and accepted/excluded row counts.
`count-mismatch-review.csv` summarizes the review. Warnings propagate into
holdings, administrator totals and monthly changes; comparisons may use a
recovered fund category, never the excluded categories.

`records-<group>-<period>.jsonl.gz` exports every accepted parsed field with
file/archive/row hashes, insurer, period, record role, asset class and overlap flags.
Decimals are serialized as strings. `parser-coverage.csv` summarizes acceptance
per family/month. Count-only defects in new families stay quarantined; existing
A/F/C recovery rules are unchanged. An accepted layout is structural validation,
not certification of its financial content or overall portfolio completeness.

FM reconciliation
compares accepted F holdings with control F31 in CLP and exposes exact residuals;
this does not certify the entire portfolio. X fund buckets are also compared
with reported H61–H65 controls, with exact differences and missing controls
reported explicitly, without expanding count recovery. Final values in
I/A/F/B/X/O/C are M$ (thousands of CLP); retain original fields and units.
X debt purchase value is CLP, while its amortized/fair/impairment values are M$.
E cash-flow buckets are UF; T coupon amounts are percentages. P retains
separate assets/liabilities, notionals and currencies; G retains collateral
direction and original-currency/M$ amounts. Never promote these auxiliary or
derivative notionals to `final_value_clp` or sum them as insurer AUM.
Missing/rejected files cannot become zero holdings or inferred exits in monthly
comparisons. Quantity changes and valuation changes remain separate; neither is
labelled net flows or confirmed trading.

Preserve insurer RUT, the reported administrator RUT, public fund RUN, private
fund RUT, series, denomination, insurance plan/reserve backing, subportfolio,
source file/line and hashes. Administrator analysis must group by reported RUT;
current fund directories supply names, not an inferred historic administrator.
Public FI/FMs link to existing funds by (kind, RUN); private funds stay distinct.
Future filters should support AGF -> insurers -> funds/series and insurer -> AGFs.
Pilot AGF totals are observed holdings in accepted A/F/X files, not complete
insurance money at an AGF. X Chilean funds link by exact RUN and reported AGF;
overseas funds/ETFs remain distinct and are never matched by a similar name.
Both domestic and supplied foreign files must be usable in both periods before
inferring a fund exit. Do not add these positions to industry fund AUM.
Family final-value sums are explicitly non-additive diagnostics. `overlap_role`
flags OIND/OIED summaries vs details, leasing property vs receivables, and repo
underlyings vs contracts. Controls, descriptors, schedules and collateral are
auxiliary. Full portfolio aggregation still requires cross-file reconciliation;
no grand total is produced by the parser.

Insurance follow-ups and the 2026-10-20 source/storage audit are recorded in
`TODO.md` under "Insurance portfolios". CS/CSOIND syndicated loans are in I,
not the absent R loan family. Their structural acceptance does not imply usable
debtor identity or participation: the audit found omitted identities and
out-of-range percentages. The I00 checks and PostgreSQL size prototype were
one-off local audits, not new reconciliation rules. The persistent loader flags
the known syndicated-field defects; their financial reconciliation remains a TODO.

Insurance matrix grouping and fund investor drill-down:
`/insurance/portfolio-heatmap` supports `group_by=issuer` (exact reported issuer
RUT, with instrument identity fallback) and `issuer=RUT` for instrument columns.
Issuer drill-down keeps full filtered asset-class totals for insurer percentages;
`/portfolio-history` also accepts the issuer filter. No issuer identity is inferred
from a name or a private borrower field.
`/fund-history?include_investors=true` adds nested insurer monthly points using
`insurance_investors.add_investors` and the same `build_history` coverage rules.
The dashboard disables NAV lookups with `include_fund_metrics=false`. Weights use
that insurer's selected fund holdings, not industry or fund NAV. Missing-valued
positions suppress the denominator; partial source coverage prevents unsupported
deltas. Existing clients omit the flag and retain their original response size.
The directory's AGF picker lists only administrators represented in accepted
primary fund holdings for the selected month, excluding auxiliary/non-fund entries.

### Audited SURA envelope recovery

`insurance/envelope_recovery.py` narrowly handles RUT 96549050, CSVID August and
September 2026. A/C/F/O/X source files contain only details, with no identification
or trailer. Strict I and B headers must agree, and five positive buckets
(A00/B20/D20/E10/X10) must match C exactly before any recovery. Every recovered
A/F/O/X row must then belong to a known bucket whose positive total matches C;
unknown types, duplicates, malformed fields, mismatches and missing buckets fail
closed. No header/trailer is fabricated: source lines begin at 1, declared count
is NULL, original hashes/bytes remain intact. Rows carry
`envelope_warning_reconciled`; holdings files are `accepted_with_warning`, C is
`partially_reconciled` with only matched categories (J00 stays excluded).
This corroborates identity and totals, not each quantity/series or cash flows.
Future months/insurers require a separate audit. Parser version `public-1835-v3`
forces existing local snapshots to be reprocessed. History accepts recovered X/O
families only when marked `accepted_with_warning`; partial controls never imply
complete holdings. Original source regression fixtures and mutation tests are in
`tests/test_insurance_envelope_recovery.py`.

### FI detail return dates

`GET /investment-funds/{run}` reads `fi_detail_returns.latest_series_returns`:
each non-superseded series uses its own latest valid NAV on/before today and
returns that actual `fecha_calculo`. It reuses `fi_linked_returns` for confirmed
conversions, distributions, exact period baselines and currency continuity.
Missing/changed currency blocks affected periods; fund registry currency is not
substituted into historical NAV. Invalid/future endpoints are excluded. Fund
rankings still read the shared-date materialized view and are not changed by
this detail fallback. No source data or materialized-view refresh is needed.

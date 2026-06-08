# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

### Deploy (manual, until GitHub Actions is set up)
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

## Architecture

```
src/
├── api/                      # Public read API — CORS-open, cached (max-age=3600)
│   ├── deps.py               # Shared: Pagination (limit/offset) + CacheHook (Cache-Control header)
│   ├── funds.py              # /funds — FM list, detail, NAV history, portfolio (SII-enriched)
│   ├── investment_funds.py   # /investment-funds — FI list, detail, NAV history, portfolio (SII-enriched)
│   ├── rentability.py        # /rentability/fm + /rentability/fi — rankings from MVs
│   ├── categories.py         # /categories/fi — FI classifications
│   ├── shareholders.py       # /shareholders — fund/entity/admin/compare endpoints
│   ├── admins.py             # /admins — administradora list + detail (from mv_administradores)
│   └── router.py             # Assembles all sub-routers
├── mcp_server.py             # FastMCP server — 10 tools for fund-market intelligence (see MCP section)
├── sii/                      # SII (tax authority) company registry
│   ├── __init__.py
│   └── load_emisores.py      # Loads ~994k Chilean companies → emisores table
├── financialStatements/      # IFRS statements for all CMF-supervised companies
│   ├── downloaders/
│   │   └── financialStatementsDownloader.py
│   ├── loaders/
│   │   └── financial_statements.py
│   └── api.py                # FastAPI router — on-demand download trigger (auth-protected)
├── mutualFunds/
│   ├── downloaders/          # One class per CMF mutual-fund dataset, all extend BaseDownloader
│   │   ├── cartolaDownloader.py
│   │   ├── carterasDownloader.py
│   │   ├── identificationDownloader.py
│   │   ├── nemotecnicosDownloader.py
│   │   ├── bondsNemotecnicos.py
│   │   └── tacDownloader.py
│   ├── loaders/
│   │   ├── cartola.py
│   │   ├── carteras.py
│   │   ├── identidad.py
│   │   ├── nemotecnicos.py
│   │   ├── bonos.py
│   │   └── tac.py
│   └── mutualFundsCategories.py  # Fund classifier per Circular No. 7 (AFM 2025)
├── investmentFunds/          # Fondos de Inversión (FI) — CMF
│   ├── downloaders/
│   │   ├── nemotecnicosDownloader.py   # FI series tickers
│   │   ├── identidadDownloader.py      # Fund registry (FIRES + FINRE, VI + NV)
│   │   ├── valoresCuotaDownloader.py   # Daily NAV per fund (pestania=7)
│   │   ├── aportantesDownloader.py     # Quarterly shareholders + cuotas (pestania=27)
│   │   └── carterasDownloader.py       # Quarterly IFRS portfolio positions (NACI/EXT/MET_PART/FUT_FW)
│   ├── loaders/
│   │   ├── nemotecnicos.py
│   │   ├── identidad.py
│   │   ├── valores_cuota.py
│   │   ├── aportantes.py
│   │   ├── carteras.py
│   │   ├── entidades.py      # refresh_entidades() — canonical names from aportantes_fi
│   │   └── utils.py          # mark_has_data() helper
│   └── investmentFundsCategories.py  # FI classifier — 20 subcategories, IPSA-based size detection
├── bolsaSantiago/            # Bolsa de Santiago data sources
│   ├── downloaders/
│   │   └── dividendosDownloader.py   # Dividends + capital changes 1973→today
│   └── loaders/
│       └── dividendos.py
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
│       ├── emisores.py            # SIIEmisor — SII company registry (rut → razon_social)
│       ├── dividendos.py           # dividendos
│       ├── job_runs.py             # job_runs (scheduler execution history)
│       └── categoria_fi.py         # categoria_fi (FI fund classifications)
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
fly.toml                      # Fly.io app config (alternative deploy target)
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

Large/Small Cap detection uses `rut_emisor` overlap with IPSA ETF (run_fondo `10748`) — dynamic, no hardcoded tickers. Results persisted to `categoria_fi` table and refreshed on day 5 of each month.

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

Both pick the **most-populated date within the last 7 days** as reference, so CMF publish lag (typically 1-2 days) never reduces fund coverage. Note: raw CMF data occasionally has corrupt `valor_cuota` jumps for individual fund/series — the views reflect source data faithfully and do not mask these.

### `mv_administradores` materialized view

Unified administradora dimension derived entirely from existing tables (no new download). Joins `fondo_mutuo` (FM, has admin RUT) with `fondos_inversion` (FI, name only) via `LOWER(TRIM(nombre))` match — name matching works because both come from the same CMF source. ~50 rows, refreshed daily at 09:20. Columns: `rut`, `nombre`, `funds_fm`, `funds_fm_vigente`, `funds_fi`, `funds_fi_vigente`, `funds_total`.

### SII company registry (`emisores`)

The `emisores` table holds ~994k Chilean companies from the SII (tax authority) registry — `rut`, `dv`, `razon_social`. Loaded via `src/sii/load_emisores.py` from a tab-separated SII export (one-off, refreshed yearly). Used to resolve `rut_emisor` → company name in the portfolio endpoints.

**Coverage**: All SII RUTs are ≥ 50M (companies). Chilean individuals have lower RUTs, so loan/mortgage funds whose "issuers" are individual debtors won't match — by design. Match rates: **96%** on `cartera_naci` (FM), **73%** on `cartera_fi_nac` (FI; remainder are individual debtors).

### MCP server (`src/mcp_server.py`)

FastMCP server exposing 10 tools for AI-driven fund-market analysis. Runs as the `mcp` container (`mcp_worker.py`, SSE on port 8081), reverse-proxied by nginx at `/mcp/sse`. Connected to Claude.ai via Settings → Integrations. Tools:

| Tool | Purpose |
|---|---|
| `search_funds` | Find FM/FI funds by name or admin |
| `compare_funds` | Side-by-side returns for 2+ funds |
| `top_funds_by_return` | Rankings by 1D/1W/1M/1Y/5Y/YTD |
| `net_new_money_ranking` | Aportes − rescates by AGF or fund (FM only) |
| `get_fund_full_picture` | Identity, returns, flows, portfolio, shareholders |
| `get_administrator_full_picture` | AUM, market share, best/worst funds, flows, shareholders |
| `compare_administrators` | M&A view: shared shareholders, AUM, merge scenario |
| `get_shareholder_positions` | Track an institutional investor across funds/time |
| `potential_clients` | Market shareholders NOT in an admin's funds |
| `market_overview` | Total market snapshot: AUM, top AGFs, flows, top performers |

AUM figures are CLP. Net new money uses `cartola_diaria` generated columns. The `mcp` container only needs `DATABASE_URL`.

### Scheduler jobs (America/Santiago)

| Job ID | Schedule | Description |
|---|---|---|
| `bonds_tickers` | Daily 08:00 | FM bond tickers with fiscal rate |
| `fm_identity` | Daily 08:10 | MF fund identity register |
| `mf_tickers` | Daily 08:15 | MF series nemotecnicos |
| `fi_tickers` | Daily 08:15 | FI cuota tickers |
| `fi_identity` | Daily 08:20 | FI fund registry (rescatable/vigente) |
| `mf_daily_nav` | Daily 08:30 | MF daily cartola (NAV/AUM/flows) |
| `mf_rentabilidad` | Daily 09:15 | Refresh `mv_rentabilidad_fm` (FM returns) |
| `administradores` | Daily 09:20 | Refresh `mv_administradores` (admin fund counts) |
| `mf_portfolios` | Day 5 of month 09:00 | MF monthly investment portfolios |
| `mf_costs` | Day 5 of month 09:30 | MF monthly TAC costs |
| `dividends` | Daily 09:00 | Dividends + capital changes (Bolsa de Santiago) |
| `fi_daily_nav` | Daily 09:30 | FI daily NAV/AUM (vigente funds only) |
| `fi_rentabilidad` | Daily 10:00 | Refresh `mv_rentabilidad_fi` (FI returns) |
| `fi_shareholders` | Day 5 of month 10:00 | FI quarterly shareholders + cuotas (vigente only) |
| `fi_portfolios` | Day 5 of month 10:30 | FI quarterly IFRS portfolio positions (vigente only) |
| `fi_categories` | Day 5 of month 11:00 | FI fund classification → categoria_fi |

### API endpoints

All public endpoints return `Cache-Control: public, max-age=3600` and allow all CORS origins. Pagination via `?limit=50&offset=0` (max limit 500).

#### Public read API (`src/api/`)

| Endpoint | Description |
|---|---|
| `GET /funds` | List FM funds. Filters: `admin`, `tipo_fondo`, `vigente` |
| `GET /funds/{run}` | FM fund detail: identity + latest NAV per serie + rentability from MV |
| `GET /funds/{run}/nav` | FM NAV history for charts. Filters: `serie`, `from_date`, `to_date` |
| `GET /funds/{run}/portfolio` | FM latest quarter portfolio (naci + extr), SII-enriched `nombre_emisor`. Extra fields per position: `tir`, `fecha_vencimiento`, `cantidad_unidades`, `tipo_unidades`, `moneda_liquidacion`, `porcentaje_valor_par`, `tipo_interes`, `codigo_pais_emisor`, `situacion_instrumento`, `porcentaje_capital_emisor`, `porcentaje_activos_emisor`, `codigo_grupo_empresarial` |
| `GET /investment-funds` | List FI funds. Filters: `admin`, `rescatable`, `vigente` |
| `GET /investment-funds/{run}` | FI fund detail: identity + latest NAV + rentability |
| `GET /investment-funds/{run}/nav` | FI NAV history |
| `GET /investment-funds/{run}/portfolio` | FI latest quarter portfolio (nac + ext), SII-enriched, sorted by weight. Extra fields per position: `tir_val_par_precio`, `fecha_vencimiento`, `cant_unidades`, `tipo_unidades`, `cod_moneda_liquidacion`, `tipo_interes`, `pct_capital_emisor`, `pct_activo_emisor`, `situacion_instrumento`, `clasif_esf`, `cod_pais` |
| `GET /rentability/fm` | FM return rankings from `mv_rentabilidad_fm`. Sort: `r_1d/r_1w/r_1m/r_1y/r_5y/r_ytd` |
| `GET /rentability/fi` | FI return rankings from `mv_rentabilidad_fi`. Same sort options |
| `GET /categories/fi` | FI fund classifications. Filters: `categoria`, `tipo`, `admin` |
| `GET /admins` | List administradoras with FM+FI fund counts. Filter: `search` |
| `GET /admins/{rut}` | Single administradora by RUT |
| `GET /shareholders/fund/{run}` | Shareholder evolution for a fund across quarters |
| `GET /shareholders/entity/{rut}` | All fund positions held by a shareholder across time |
| `GET /shareholders/admin` | Top shareholders aggregated across an admin's funds. Required: `admin` |
| `GET /shareholders/compare` | Side-by-side admin comparison: shared holders, exclusives, merge summary. Required: `admin_a`, `admin_b` |

**Shareholder AUM formula**: `pct_propiedad / 100 × fund_aum`. Fund AUM is estimated as the median of `valorizacion_cierre × 100 / pct_activo_fondo` across all `cartera_fi_nac` + `cartera_fi_ext` positions for that fund/quarter.

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
| `fondos_inversion` | 1,641 | FI registry: run_fondo, administrador, rescatable, vigente, has_data |
| `nemotecnicos_fi` | 2,370 | FI cuota tickers |
| `valores_cuota_fi` | 2.9M+ | FI daily NAV/AUM 2020–2026, 531 MB |
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
from src.mutualFunds.downloaders.cartolaDownloader import CartolaDownloader
print(CartolaDownloader().run())
"

# Run FI classifier and save to DB
python -c "
from src.investmentFunds.investmentFundsCategories import run_and_save
print(run_and_save(), 'rows saved')
"

# Backfill FI carteras (quarterly IFRS portfolios, 2020→today)
python -c "
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from src.investmentFunds.downloaders.carterasDownloader import CarterasFIDownloader
print(CarterasFIDownloader().backfill())
"

# Backfill FI daily NAV (all funds, 2020→today)
python -c "
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from src.investmentFunds.downloaders.valoresCuotaDownloader import ValoresCuotaFIDownloader
print(ValoresCuotaFIDownloader().backfill())
"

# Backfill dividends (1973→today)
python -c "
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from src.bolsaSantiago.downloaders.dividendosDownloader import DividendosDownloader
print(DividendosDownloader().backfill())
"

# Fly.io — deploy
fly deploy

# Fly.io — connect to production Postgres
fly pg connect -a <pg-app-name>

# Fly.io — tail logs
fly logs

# Fly.io — dump local DB and restore to Fly
pg_dump $DATABASE_URL -Fc -f cmf_backup.dump
fly proxy 5433:5432 -a <pg-app-name>
pg_restore -h localhost -p 5433 -U postgres -d <db-name> cmf_backup.dump
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

Set locally via `.env`. On Fly, set via `fly secrets set KEY=value`.

When `BOLSA_COOKIES` / `BOLSA_CSRF` expire, update them without redeploying:
```bash
fly secrets set BOLSA_COOKIES="..." BOLSA_CSRF="..."
```

## Fly.io Notes

- Two process groups (`web` + `worker`) run on a single Fly Machine — defined in `fly.toml [processes]`.
- PostgreSQL lives in a separate `fly pg` app; connect via the private Fly network.
- Persistent volumes are not required — all state lives in Postgres.
- Downloaded files are deleted immediately after loading — no disk accumulation.
- `auto_stop_machines = false` in `fly.toml` — prevents the machine stopping overnight and missing scheduler jobs.
- To update Bolsa cookies/CSRF without redeploy: `fly secrets set BOLSA_COOKIES="..." BOLSA_CSRF="..."`

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Daily ETL pipeline that downloads public datasets from the Chilean CMF (Comisión para el Mercado Financiero) and Bolsa de Santiago, and loads them into a PostgreSQL database. Both the app and the database run on Fly.io.

## Stack

- **Language**: Python 3.11
- **Database**: PostgreSQL (Fly.io managed `fly pg` cluster)
- **HTTP**: `requests` (sync downloads)
- **Data parsing**: `pandas`, `beautifulsoup4`, `xlrd`
- **ORM / migrations**: `SQLAlchemy` (sync engine) + `Alembic`
- **Scheduling**: `APScheduler` (runs inside the process)
- **API**: FastAPI
- **Deployment**: Fly.io (`fly.toml` — not yet created)

## Architecture

```
src/
├── financialStatements/      # IFRS statements for all CMF-supervised companies
│   ├── downloaders/
│   │   └── financialStatementsDownloader.py
│   ├── loaders/
│   │   └── financial_statements.py
│   └── api.py                # FastAPI router — on-demand download trigger
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
│   │   └── aportantesDownloader.py     # Quarterly shareholders + cuotas (pestania=27)
│   └── loaders/
│       ├── nemotecnicos.py
│       ├── identidad.py
│       ├── valores_cuota.py
│       ├── aportantes.py
│       └── utils.py          # mark_has_data() helper
├── bolsaSantiago/            # Bolsa de Santiago data sources
│   ├── downloaders/
│   │   └── dividendosDownloader.py   # Dividends + capital changes 1973→today
│   └── loaders/
│       └── dividendos.py
├── db/
│   ├── engine.py             # SQLAlchemy engine + SessionLocal + Base
│   └── models/
│       ├─��� mutual_funds.py         # fondo_mutuo
│       ├── cartola.py              # cartola_diaria
│       ├── carteras.py             # cartera_naci/extr/opci/futu/opla
│       ├── nemotecnicos.py         # nemotecnicos (FM)
│       ├── bonos.py                # bonos_nemotecnicos
│       ├── tac.py                  # tac
│       ├── financial_statements.py # financial_statements
│       ├── fondos_inversion.py     # fondos_inversion + nemotecnicos_fi
│       ├── valores_cuota_fi.py     # valores_cuota_fi
│       ├── aportantes_fi.py        # aportantes_fi + cuotas_fi
│       └── dividendos.py           # dividendos
├── base.py                   # BaseDownloader + DownloadResult
├── categories.py             # Circular No. 7 category definitions + country tables
├── config.py                 # CMFUrl enum + env vars
├── http.py                   # make_session() factory
└── scheduler.py              # APScheduler wiring

alembic/                      # Migration scripts
tests/                        # pytest unit tests for loaders + classifiers
main.py                       # Entrypoint: starts FastAPI + scheduler
Dockerfile
.dockerignore
```

> **Note on layout**: each dataset domain is a self-contained package with its own
> `downloaders/` and `loaders/`. Financial statements are triggered on demand via
> `src/financialStatements/api.py`, not by the scheduler.

### Downloader interface

Every downloader extends `BaseDownloader` and exposes:

```python
def run(self) -> DownloadResult: ...                    # incremental — picks up from last DB date
def backfill(self, from_date) -> DownloadResult: ...    # historical population
```

Downloaded files are deleted immediately after successful load (`path.unlink(missing_ok=True)`).
The DB is the sole source of truth — ephemeral disk is just a staging area.

### `has_data` flag (investmentFunds downloaders)

`fondos_inversion.has_data` controls which FI funds are scraped:
- `NULL` — unknown, always attempt
- `True` — fund has data, always fetch
- `False` — non-vigente fund confirmed empty, **skip forever**

Set automatically on first fetch result. Vigente funds are never marked `False`.
This eliminates wasted requests for the ~750 non-vigente funds with no CMF portal data.

### Scheduler jobs (America/Santiago)

| Job ID | Schedule | Description |
|---|---|---|
| `bonds_tickers` | Daily 08:00 | FM bond tickers with fiscal rate |
| `fm_identity` | Daily 08:00 | MF fund identity register |
| `mf_tickers` | Daily 08:15 | MF series nemotecnicos |
| `fi_tickers` | Daily 08:15 | FI cuota tickers |
| `fi_identity` | Daily 08:20 | FI fund registry (rescatable/vigente) |
| `mf_daily_nav` | Daily 08:30 | MF daily cartola (NAV/AUM/flows) |
| `mf_portfolios` | Day 5 of month 09:00 | MF monthly investment portfolios |
| `mf_costs` | Day 5 of month 09:30 | MF monthly TAC costs |
| `dividends` | Daily 09:00 | Dividends + capital changes (Bolsa de Santiago) |
| `fi_daily_nav` | Daily 09:30 | FI daily NAV/AUM (vigente funds only) |
| `fi_shareholders` | Day 5 of month 10:00 | FI quarterly shareholders + cuotas (vigente only) |

### API endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Health check |
| `POST /financial-statements/download?inicio=YYYYMM&termino=YYYYMM` | Download + load IFRS statements. Add `&background=true` for async. |

### DB tables

| Table | Rows (approx) | Notes |
|---|---|---|
| `fondo_mutuo` | 1,340 | MF identity (447 vigentes, 893 terminated), 36 AGFs |
| `cartola_diaria` | 7M+ | Daily NAV/AUM/flows 2020–2026, 1.4 GB |
| `cartera_naci` | 2.1M+ | National portfolio positions |
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
| `valores_cuota_fi` | 2.8M+ | FI daily NAV/AUM 2020–2026, 531 MB |
| `aportantes_fi` | growing | FI quarterly top-12 shareholders with ownership % |
| `cuotas_fi` | growing | FI quarterly: cuotas emitidas/pagadas, valor libro |
| `dividendos` | 75,469 | Dividends + capital changes 1973–2026 (Bolsa de Santiago) |

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

# Run the scheduler locally
python main.py

# Run a specific downloader manually
python -c "
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from src.mutualFunds.downloaders.cartolaDownloader import CartolaDownloader
print(CartolaDownloader().run())
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
```

## Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | SQLAlchemy sync DSN e.g. `postgresql+psycopg2://user:pass@host/db` |
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

- The app runs as a single Fly Machine (always-on) with APScheduler inside.
- PostgreSQL lives in a separate `fly pg` app; connect via the private Fly network.
- Persistent volumes are not required — all state lives in Postgres.
- Downloaded files are deleted immediately after loading — no disk accumulation.
- Set `auto_stop_machines = false` in `fly.toml` to prevent the machine stopping overnight and missing scheduled jobs.
- `fly.toml` has not been created yet — run `fly launch` to generate it.

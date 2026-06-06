# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Daily ETL pipeline that downloads public datasets from the Chilean CMF (Comisión para el Mercado Financiero) and loads them into a PostgreSQL database. Both the app and the database run on Fly.io.

## Stack

- **Language**: Python 3.11
- **Database**: PostgreSQL (Fly.io managed `fly pg` cluster)
- **HTTP**: `requests` (sync downloads)
- **Data parsing**: `pandas`, `beautifulsoup4`, `xlrd`
- **ORM / migrations**: `SQLAlchemy` (sync engine) + `Alembic`
- **Scheduling**: `APScheduler` (runs inside the process)
- **API (future)**: FastAPI (wired but unused)
- **Deployment**: Fly.io (`fly.toml` — not yet created)

## Architecture

```
src/
├── downloaders/              # Cross-domain CMF datasets (not fund-specific)
│   └── financialStatementsDownloader.py  # IFRS statements for all CMF companies
├── loaders/                  # Loaders for cross-domain datasets
│   └── financial_statements.py
├── mutualFunds/
│   ├── downloaders/          # One class per CMF mutual-fund dataset, all extend BaseDownloader
│   │   ├── cartolaDownloader.py
│   │   ├── carterasDownloader.py
│   │   ├── identificationDownloader.py
│   │   ├── nemotecnicosDownloader.py
│   │   ├── bondsNemotecnicos.py
│   │   └── tacDownloader.py
│   ├── loaders/              # Parse raw files and upsert to DB
│   │   ├── cartola.py
│   │   ├── carteras.py
│   │   ├── identidad.py
│   │   ├── nemotecnicos.py
│   │   ├── bonos.py
│   │   └── tac.py
│   └── mutualFundsCategories.py  # Fund classifier per Circular No. 7 (AFM 2025)
├── db/
│   ├── engine.py             # SQLAlchemy engine + SessionLocal + Base
│   └── models/               # One file per domain
│       ├── mutual_funds.py   # fondo_mutuo
│       ├── cartola.py        # cartola_diaria
│       ├── carteras.py       # cartera_naci/extr/opci/futu/opla
│       ├── nemotecnicos.py   # nemotecnicos
│       ├── bonos.py          # bonos_nemotecnicos
│       ├── tac.py            # tac
│       └── financial_statements.py  # financial_statements
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

> **Note on layout**: fund-specific datasets live under `src/mutualFunds/`. Cross-domain
> datasets that cover all CMF-supervised companies (e.g. financial statements) live in the
> top-level `src/downloaders/` and `src/loaders/`.

### Downloader interface

Every downloader extends `BaseDownloader` and exposes:

```python
def run(self) -> DownloadResult: ...          # incremental — picks up from last DB date
def backfill(self, from_date) -> DownloadResult: ...  # historical population
```

### Scheduler jobs (America/Santiago)

| Job | Schedule | Description |
|---|---|---|
| `bonos_nemotecnicos` | Daily 08:00 | Bond nemotecnicos with fiscal rate |
| `fm_identidad` | Daily 08:00 | Fund identity register |
| `nemotecnicos` | Daily 08:15 | FM series nemotecnicos |
| `cartola_diaria` | Daily 08:30 | Daily fund cartola |
| `carteras` | Day 5 of month 09:00 | Monthly investment portfolios (5 types) |
| `tac` | Day 5 of month 09:30 | Monthly TAC costs |

### DB tables

| Table | Rows (approx) | Notes |
|---|---|---|
| `fondo_mutuo` | 1,340 | Fund identity, updated daily |
| `cartola_diaria` | 7M+ | Daily NAV/AUM/flows 2020–2026, 1.4 GB |
| `cartera_naci` | 2.2M+ | National portfolio positions — **columns currently NULL, needs rebuild** |
| `cartera_extr` | 325K+ | Foreign portfolio positions — **needs rebuild** |
| `cartera_opci` | ~155 | Options positions |
| `cartera_futu` | 133K+ | Futures/forwards positions — **needs rebuild** |
| `cartera_opla` | 0 | Always empty (OPLA never has data from CMF) |
| `nemotecnicos` | 2,432 | Series with tipo_serie classification |
| `bonos_nemotecnicos` | 1,254 | Bonds with fiscal interest rate |
| `tac` | 230K+ | Monthly TAC costs 2020–2026 |
| `financial_statements` | 1M+ | IFRS statements (quarterly) for all CMF companies 2009–2026 |

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

# Run fund classifier
python -m src.mutualFunds.mutualFundsCategories

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

Set locally via `.env`. On Fly, set via `fly secrets set KEY=value`.

## Fly.io Notes

- The app runs as a single Fly Machine (always-on) with APScheduler inside.
- PostgreSQL lives in a separate `fly pg` app; connect via the private Fly network.
- Persistent volumes are not required — all state lives in Postgres.
- Downloaded files (txt/xls/html) are written to the ephemeral container disk and lost on restart. This is fine since the DB is the source of truth.
- `fly.toml` has not been created yet — run `fly launch` to generate it.

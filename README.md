# CMF Downloader

ETL pipeline that downloads public fund datasets from the Chilean CMF (Comisión para el Mercado Financiero) and Bolsa de Santiago, and loads them into PostgreSQL.

---

## Done

### Infrastructure
- [x] Project structure: domain packages with `downloaders/`, `loaders/`, `db/models/`
- [x] `BaseDownloader` with incremental `run()` + historical `backfill()`
- [x] APScheduler (13 jobs) — daily + monthly + quarterly
- [x] **Scheduler decoupled from web process** — `web` (FastAPI) and `worker` (BlockingScheduler) run as independent OS processes via Fly.io `[processes]`
- [x] **Job run tracking** — every scheduler execution recorded in `job_runs` (status, duration, rows, errors)
- [x] **Retry logic** — all downloaders use `make_session()` with a `Retry` transport adapter (3 attempts, exponential backoff, 429/5xx)
- [x] Alembic migrations for all tables — `alembic/env.py` imports all models
- [x] `fly.toml` — web + worker process groups defined
- [x] Dockerfile + `.dockerignore`
- [x] FastAPI app with `/health` + `/financial-statements/download`
- [x] Delete downloaded files immediately after load (no disk accumulation)
- [x] `has_data` flag on `fondos_inversion` — skips non-vigente FI funds with no data

### Mutual Funds (FM)
- [x] **Identidad FM** — fund identity register (daily)
- [x] **Cartola Diaria** — daily NAV, AUM, flows per fund+series (2020–2026, ~7M rows, 1.4 GB). Composite index `(run_fondo, fecha)`. Generated columns: `monto_aportado`, `monto_rescatado`
- [x] **Carteras FM** — monthly portfolios: NACI, EXTR, OPCI, FUTU, OPLA (2020–2026, ~2.7M rows). Composite indexes `(run_fondo, periodo)`
- [x] **Nemotecnicos FM** — series codes with `tipo_serie` classification (APV, AFP, Institucional, etc.)
- [x] **Bonos Nemotecnicos** — bonds with fiscal interest rate
- [x] **TAC** — monthly cost rates (2020–2026, 230k rows)
- [x] **Financial Statements** — quarterly IFRS for all CMF companies (2009–2026, 2M rows, 432 MB). On-demand via API.

### Investment Funds (FI)
- [x] **Identidad FI** (`fondos_inversion`) — full CMF registry: 1,641 funds (FIRES + FINRE, vigentes + no vigentes), with `rescatable`, `vigente`, `has_data`
- [x] **Nemotecnicos FI** — cuota tickers (2,370 series)
- [x] **Valores Cuota FI** — daily NAV, AUM, investors (2020–2026, 2.9M rows, 531 MB)
- [x] **Aportantes FI** — quarterly top-12 shareholders with ownership % (2020–2026, 176k rows). Canonical names via `entidades` table
- [x] **Cuotas FI** — quarterly: cuotas emitidas/pagadas, valor libro (2020–2026)
- [x] **Carteras FI** — quarterly IFRS portfolio positions: NACI, EXT, MET_PART, FUT_FW (2020–2026, 888K+ rows domestic). Composite indexes `(run_fondo, periodo)`

### Bolsa de Santiago
- [x] **Dividendos** — dividends + capital changes for all instruments 1973–2026 (75k rows)

### Analysis
- [x] **FM Fund classifier** — classifies 169 FM funds per Circular No. 7 (AFM 2025)
- [x] **FI Fund classifier** (`investmentFundsCategories.py`) — 20 subcategories across Capital Privado, Inmobiliario, Infraestructura, Accionario (Large/Small Cap), Deuda, and Fondo de Fondos. Uses `pct_activo_fondo` portfolio weights + IPSA ETF for size detection. Results in `categoria_fi` table, refreshed quarterly
- [x] **Series classification** — `tipo_serie` derived from TAC `caracteristicas`
- [x] **Canonical entity names** — `entidades` table maps RUT → canonical name

---

## TODO

### Deploy
- [ ] **API authentication** — bearer token on `POST /financial-statements/download`
- [ ] **Deploy to Fly.io**
  ```bash
  fly launch
  fly pg create --region scl && fly pg attach
  fly secrets set GEMINI_API_KEY=... BOLSA_COOKIES=... BOLSA_CSRF=...

  # Migrate local DB (~3.3 GB, ~20 min at 300 Mbps)
  pg_dump $DATABASE_URL -Fc -f cmf_backup.dump
  fly proxy 5433:5432 -a <pg-app-name>
  pg_restore -h localhost -p 5433 -U postgres -d <db-name> cmf_backup.dump

  fly deploy
  ```

### DB Improvements
- [ ] **Alerting** — Slack/email webhook when `job_runs.status = 'error'`
- [ ] **Table partitioning** — partition `cartola_diaria`, `valores_cuota_fi`, `financial_statements` by year (requires data reload)
- [ ] **Materialized views** — latest NAV per fund, AUM by administrator (refresh daily)
- [ ] `VACUUM FULL` on cartera tables after backfill

### New Data Sources
- [ ] **Official AFM categorization** — actual Circular 7 category per fund as declared by administrator
- [ ] **Rentabilidad oficial** — CMF/AFM official return indices per fund category

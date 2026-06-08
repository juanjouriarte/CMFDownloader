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
- [x] **Rentability (FM)** — `mv_rentabilidad_fm` materialized view: total return 1D/1W/1M/1Y/5Y/YTD via `valor_cuota` × cumulative `factor_reparto`. Refreshed daily
- [x] **Rentability (FI rescatables)** — `mv_rentabilidad_fi` materialized view: NAV + dividends (CLP & USD currency-matched via `nemotecnicos_fi` → `dividendos`). Refreshed daily

### Public API (`src/api/`)
- [x] **CORS + caching** — all endpoints open (`allow_origins=["*"]`), `Cache-Control: public, max-age=3600` for Cloudflare edge caching
- [x] **Fund endpoints** — `GET /funds` (list + filter), `/funds/{run}` (detail), `/funds/{run}/nav` (chart data), `/funds/{run}/portfolio` (holdings)
- [x] **Investment fund endpoints** — `GET /investment-funds`, `/investment-funds/{run}`, `/investment-funds/{run}/nav`
- [x] **Rentability rankings** — `GET /rentability/fm` + `/rentability/fi` from materialized views, sortable by 1D/1W/1M/1Y/5Y/YTD
- [x] **FI categories** — `GET /categories/fi` with latest period per fund
- [x] **Shareholder endpoints**:
  - `GET /shareholders/fund/{run}` — evolution of holders in a fund across quarters
  - `GET /shareholders/entity/{rut}` — track one holder across all funds and time
  - `GET /shareholders/admin` — top holders aggregated across an admin's funds
  - `GET /shareholders/compare` — side-by-side admin comparison: shared holders, exclusives, merge AUM summary

### Deployment
- [x] **Deployed on Oracle Cloud Always Free** — 2x AMD VMs (1 OCPU / 1 GB RAM each), $0/month
  - `cmf-btg-db` (`146.181.47.236`) — PostgreSQL 16 on port 5433, system install (no Docker)
  - `cmf-btg-app` (`146.181.34.54`) — web + worker via `docker-compose`, port 8080
- [x] **DB restored** — 386 MB dump (6.9M rows in `cartola_diaria`) loaded via `pg_restore`
- [x] **API live** at `http://146.181.34.54:8080`
- [x] **`docker-compose.yml`** — web + worker as separate containers with `restart: always`

### Deploy commands
```bash
# SSH into app VM
ssh -i ~/.ssh/oracle_cmf.key ubuntu@146.181.34.54
cd ~/CMFDownloader

# Pull latest + migrate + restart
git pull origin main
sudo docker compose run --rm web alembic upgrade head
sudo docker compose up -d --build

# Check status
sudo docker compose ps
sudo docker compose logs -f
```

---

## TODO

### Security & Domain
- [ ] **Buy domain** (~$1/year on Namecheap for `.xyz`) 
- [ ] **Set up Cloudflare** — proxy in front of `cmf-btg-app`, free DDoS protection + SSL + caching
- [ ] **Lock Oracle firewall** — restrict port 8080 to Cloudflare IPs only (currently open to `0.0.0.0/0`)
- [ ] **GitHub Actions auto-deploy** — SSH on push to `main`, replace manual `git pull`

### DB Improvements
- [ ] **Alerting** — Slack/email webhook when `job_runs.status = 'error'`
- [ ] **Data-quality checks** — flag corrupt `valor_cuota` jumps in CMF source feed

### DB Improvements
- [ ] **Alerting** — Slack/email webhook when `job_runs.status = 'error'`
- [ ] **Table partitioning** — partition `cartola_diaria`, `valores_cuota_fi`, `financial_statements` by year (requires data reload)
- [ ] **Fix `factor_reparto` NaN → NULL** in cartola loader (data quality; views already filter NaN)
- [ ] **Data-quality checks** — flag corrupt `valor_cuota` jumps in CMF source feed
- [ ] `VACUUM FULL` on cartera tables after backfill

### New Data Sources
- [ ] **Official AFM categorization** — actual Circular 7 category per fund as declared by administrator
- [ ] **Rentabilidad oficial** — CMF/AFM official return indices per fund category

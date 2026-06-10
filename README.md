# CMF Downloader

ETL pipeline that downloads public fund datasets from the Chilean CMF (Comisión para el Mercado Financiero) and Bolsa de Santiago, loads them into PostgreSQL, and serves them through a public REST API plus an MCP server for AI-driven market analysis.

---

## Done

### Infrastructure
- [x] Project structure: domain packages with `downloaders/`, `loaders/`, `db/models/`
- [x] `BaseDownloader` with incremental `run()` + historical `backfill()`
- [x] APScheduler (16 jobs) — daily + monthly + quarterly
- [x] **Decoupled processes** — `web` (FastAPI), `worker` (BlockingScheduler), and `mcp` (FastMCP SSE) run as independent containers via `docker-compose`
- [x] **Job run tracking** — every scheduler execution recorded in `job_runs` (status, duration, rows, errors)
- [x] **Retry logic** — all downloaders use `make_session()` with a `Retry` transport adapter (3 attempts, exponential backoff, 429/5xx)
- [x] Alembic migrations for all tables — `alembic/env.py` imports all models
- [x] `docker-compose.yml` — web + worker + mcp containers (`fly.toml` kept as alternative)
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
- [x] **Valores Cuota FI** — daily NAV, AUM, investors (2020–2026, 2.9M rows, 531 MB). `flujo_neto` column: daily implied net flow `(cuotas_t − cuotas_{t-1}) × valor_libro_t` — pre-computed at load time, covering index on `(fecha, run_fondo, flujo_neto)`
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
- [x] **SII company registry** — `emisores` table: ~994k Chilean companies (rut → razon_social). Resolves `rut_emisor` → company name (96% on FM portfolios, 73% on FI)
- [x] **Admin dimension** — `mv_administradores` materialized view: FM+FI fund counts per administradora, joined by name. Refreshed daily
- [x] **Rentability (FM)** — `mv_rentabilidad_fm` materialized view: total return 1D/1W/1M/1Y/5Y/YTD via `valor_cuota` × cumulative `factor_reparto`. Refreshed daily
- [x] **Rentability (FI rescatables)** — `mv_rentabilidad_fi` materialized view: NAV + dividends (CLP & USD currency-matched via `nemotecnicos_fi` → `dividendos`). Refreshed daily
- [x] **MV ref date — 90% coverage threshold** — both rentability MVs pick the most recent date with ≥ 90% of max fund coverage (not just max count), so late-publishing funds don't anchor the whole market to an older date

### Public API (`src/api/`)
- [x] **CORS + caching** — all endpoints open (`allow_origins=["*"]`), `Cache-Control: public, max-age=3600` for edge caching
- [x] **Fund endpoints** — `GET /funds` (list + filter), `/funds/{run}` (detail), `/funds/{run}/nav` (chart data), `/funds/{run}/portfolio` (full holdings enriched with SII names + fund names from nemotecnicos — includes `tir`, `fecha_vencimiento`, `cantidad_unidades`, `tipo_unidades`, `moneda_liquidacion`, `porcentaje_valor_par`, `tipo_interes`, `codigo_pais_emisor`, `situacion_instrumento`, `porcentaje_capital_emisor`, `porcentaje_activos_emisor`, `codigo_grupo_empresarial`, `nombre_fondo_emisor`)
- [x] **Investment fund endpoints** — `GET /investment-funds`, `/investment-funds/{run}`, `/investment-funds/{run}/nav`, `/investment-funds/{run}/portfolio` (full holdings enriched with SII names + fund names — includes `tir_val_par_precio`, `fecha_vencimiento`, `cant_unidades`, `tipo_unidades`, `cod_moneda_liquidacion`, `tipo_interes`, `pct_capital_emisor`, `pct_activo_emisor`, `situacion_instrumento`, `clasif_esf`, `cod_pais`, `nombre_fondo_emisor`)
- [x] **Rentability rankings** — `GET /rentability/fm` + `/rentability/fi` from materialized views, sortable by 1D/1W/1M/1Y/5Y/YTD
- [x] **FI categories** — `GET /categories/fi` with latest period per fund
- [x] **Administradoras** — `GET /admins` (list + fund counts), `/admins/{rut}` (detail)
- [x] **Shareholder endpoints**:
  - `GET /shareholders/fund/{run}` — evolution of holders in a fund across quarters
  - `GET /shareholders/entity/{rut}` — track one holder across all funds and time
  - `GET /shareholders/admin` — top holders aggregated across an admin's funds
  - `GET /shareholders/compare` — side-by-side admin comparison: shared holders, exclusives, merge AUM summary

### MCP Server (`src/mcp_server.py`)
- [x] **FastMCP server with 15 tools** — `search_funds`, `compare_funds`, `top_funds_by_return` (+ `as_of_date`), `net_new_money_ranking` (FM: explicit daily aportes/rescates; FI rescatable: daily `flujo_neto`; FI non-rescatable: quarterly `cuotas_fi`; `rescatable` filter; `from_date`/`to_date`), `fi_equity_activity` (capital raises, returns, pending calls for non-rescatable FI; group by fund or AGF), `get_fund_full_picture` (+ enriched `top_positions`), `get_administrator_full_picture` (+ `top_fm_positions`), `compare_administrators`, `get_shareholder_positions`, `potential_clients`, `market_overview`, `get_fund_portfolio`, `top_emisores_in_market`, `emisor_fund_exposure` (domestic + foreign), `portfolio_overlap`
- [x] **Remote integration** — SSE on port 8081, reverse-proxied at `/mcp/sse`, connected to Claude.ai via Settings → Integrations
- [x] **AI-driven market intelligence** — M&A analysis, net new money rankings, shareholder overlap, prospecting, portfolio analysis, issuer exposure across the market

### Deployment
- [x] **Deployed on Oracle Cloud Always Free** — 2x AMD VMs (1 OCPU / 1 GB RAM each), $0/month
  - `cmf-btg-db` (`146.181.47.236`) — PostgreSQL 16 on port 5433, system install (no Docker)
  - `cmf-btg-app` (`146.181.34.54`) — web + worker + mcp via `docker-compose`
- [x] **DB restored** — 386 MB dump (6.9M rows in `cartola_diaria`) loaded via `pg_restore`
- [x] **HTTPS live** at `https://financial-cmf.ddns.net` — No-IP domain + nginx + Let's Encrypt
- [x] **nginx reverse proxy** — `/` → web (8080), `/mcp/` + `/messages/` → mcp (8081)
- [x] **`docker-compose.yml`** — web + worker + mcp as separate containers with `restart: always`

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

### Security
- [ ] **Lock Oracle firewall** — restrict ports 8080/8081 to nginx-only; expose only 443 publicly
- [ ] **GitHub Actions auto-deploy** — SSH on push to `main`, replace manual `git pull`
- [ ] **API_TOKEN** — currently blank (dev mode); set a real token on production

### DB Improvements
- [ ] **Alerting** — Slack/email webhook when `job_runs.status = 'error'`
- [ ] **Table partitioning** — partition `cartola_diaria`, `valores_cuota_fi`, `financial_statements` by year (requires data reload)
- [ ] **Data-quality checks** — flag corrupt `valor_cuota` jumps in CMF source feed

### New Data Sources
- [ ] **Official AFM categorization** — actual Circular 7 category per fund as declared by administrator
- [ ] **Rentabilidad oficial** — CMF/AFM official return indices per fund category

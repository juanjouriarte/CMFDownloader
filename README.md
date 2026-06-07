# CMF Downloader

ETL pipeline that downloads public fund datasets from the Chilean CMF (Comisión para el Mercado Financiero) and Bolsa de Santiago, and loads them into PostgreSQL.

---

## Done

### Infrastructure
- [x] Project structure: domain packages with `downloaders/`, `loaders/`, `db/models/`
- [x] `BaseDownloader` with incremental `run()` + historical `backfill()`
- [x] APScheduler (11 jobs) — daily + monthly + quarterly
- [x] Alembic migrations for all tables
- [x] Dockerfile + `.dockerignore`
- [x] FastAPI app with `/health` + `/financial-statements/download`
- [x] Delete downloaded files immediately after load (no disk accumulation)
- [x] `has_data` flag on `fondos_inversion` — skips non-vigente FI funds with no data

### Mutual Funds (FM)
- [x] **Identidad FM** — fund identity register (daily)
- [x] **Cartola Diaria** — daily NAV, AUM, flows per fund+series (2020–2026, ~7M rows, 1.4 GB)
- [x] **Carteras FM** — monthly portfolios: NACI, EXTR, OPCI, FUTU, OPLA (2020–2026, ~2.7M rows). Rebuilt with semantic column names, `nombre_fondo` dropped.
- [x] **Nemotecnicos FM** — series codes with `tipo_serie` classification (APV, AFP, Institucional, etc.)
- [x] **Bonos Nemotecnicos** — bonds with fiscal interest rate
- [x] **TAC** — monthly cost rates (2020–2026, 230k rows)
- [x] **Financial Statements** — quarterly IFRS for all CMF companies (2009–2026, 2M rows, 432 MB). On-demand via API.

### Investment Funds (FI)
- [x] **Identidad FI** (`fondos_inversion`) — full CMF registry: 1,641 funds (FIRES + FINRE, vigentes + no vigentes), with `rescatable`, `vigente`, `has_data`
- [x] **Nemotecnicos FI** — cuota tickers (2,370 series)
- [x] **Valores Cuota FI** — daily NAV, AUM, investors (2020–2026, 2.9M rows, 531 MB)
- [x] **Aportantes FI** — quarterly top-12 shareholders with ownership % (2020–2026, 176k rows). Canonical names via `entidades` table.
- [x] **Cuotas FI** — quarterly: cuotas emitidas/pagadas, valor libro (2020–2026)
- [x] **Carteras FI** — quarterly IFRS portfolio positions: NACI, EXT, MET_PART, FUT_FW (2020–2026, ~2.5 GB est.). Periodo stored as last day of quarter.

### Bolsa de Santiago
- [x] **Dividendos** — dividends + capital changes for all instruments 1973–2026 (75k rows)

### Analysis
- [x] **Fund classifier** — classifies 169 FM funds per Circular No. 7 (AFM 2025)
- [x] **Series classification** — `tipo_serie` derived from TAC `caracteristicas`
- [x] **Canonical entity names** — `entidades` table maps RUT → canonical name, resolving naming variants in `aportantes_fi`

---

## TODO

### Deploy
- [ ] **Deploy to Fly.io**
  ```bash
  fly launch
  fly pg create && fly pg attach
  fly secrets set GEMINI_API_KEY=... DATABASE_URL=... BOLSA_COOKIES=... BOLSA_CSRF=...
  fly deploy
  fly ssh console -C "alembic upgrade head"
  # then re-run backfills on production
  ```

### DB Improvements
- [ ] Add `run_fondo` index to `cartera_naci`, `cartera_extr`, `cartera_futu`
- [ ] Drop `nombre_fondo` + `administradora` from `tac` (redundant)
- [ ] `VACUUM FULL` on cartera tables
- [ ] Add retry logic to all downloaders (currently only cartola has it)

### New Data Sources
- [ ] **Official AFM categorization** — actual Circular 7 category per fund as declared by administrator
- [ ] **Rentabilidad oficial** — CMF/AFM official return indices per fund category

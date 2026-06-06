# CMF Downloader

ETL pipeline that downloads public mutual fund datasets from the Chilean CMF (Comisión para el Mercado Financiero) and loads them into PostgreSQL.

---

## Done

### Infrastructure
- [x] Project structure: `src/mutualFunds/downloaders/`, `loaders/`, `db/models/`
- [x] `BaseDownloader` with incremental `run()` + historical `backfill()`
- [x] Batched upserts (10k rows) with per-batch progress logging
- [x] APScheduler wiring with all jobs registered
- [x] Alembic migrations for all tables
- [x] Dockerfile + `.dockerignore` (excludes `downloads/`, `.venv`, `.env`)
- [x] FastAPI app skeleton with `/health` endpoint

### Downloaders
- [x] **Identidad FM** — fund identity register (daily)
- [x] **Cartola Diaria** — daily NAV, AUM, flows per fund+series (2020–2026, ~7M rows)
- [x] **Carteras** — monthly investment portfolios: NACI, EXTR, OPCI, FUTU, OPLA (2020–2026)
- [x] **Nemotecnicos FM** — series codes with `tipo_serie` classification
- [x] **Bonos Nemotecnicos** — bonds with fiscal interest rate
- [x] **TAC** — monthly Tasa de Administración de Costos (2020–2026, 230k rows)

### Analysis
- [x] **Fund classifier** (`mutualFundsCategories.py`) — classifies 169 funds per Circular No. 7 (AFM 2025) using May 2026 cartera data; outputs categoria, tipo, region, WAM, confidence
- [x] **Category definitions** (`categories.py`) — all 24 Circular 7 categories + Annex 2 country tables
- [x] **Series classification** (`nemotecnicos.tipo_serie`) — APV, AFP, Institucional, Fondos, Digital, Empleados, General derived from TAC `caracteristicas`

### DB Cleanup
- [x] Dropped redundant `cartola_diaria` columns: `nom_adm`, `run_adm`, `comision_inversion`, `comision_rescate` → freed 593 MB

---

## TODO

### Critical
- [ ] **Rebuild cartera tables** — the `2b01163a2b6a` migration incorrectly used ADD+DROP instead of RENAME, leaving all position columns NULL. Need to redesign models with proper `Numeric` types for financial columns, drop `nombre_fondo` (redundant), then re-run the cartera backfill.
- [ ] **Deploy to Fly.io**
  ```bash
  fly launch
  fly pg create && fly pg attach
  fly secrets set GEMINI_API_KEY=... DATABASE_URL=...
  fly deploy
  fly ssh console -C "alembic upgrade head"
  # then re-run backfills on production
  ```

### DB Improvements
- [ ] Add `run_fondo` index to `cartera_naci`, `cartera_extr`, `cartera_futu` for query performance
- [ ] Drop `nombre_fondo` from all cartera tables (redundant — join `fondo_mutuo`)
- [ ] Drop `nombre_fondo` + `administradora` from `tac` (redundant — `run_fondo` covers it)
- [ ] Consider dropping `cartera_opla` table (CMF never publishes data for this type)
- [ ] `VACUUM FULL` on cartera tables after rebuild

### New Data Sources
- [ ] **AGF Financial Statements** — quarterly balance sheets + P&L of each fund administrator; available at CMF `/estadisticas/raagl_seleccion.php`. High value for competitor analysis.
- [ ] **Official AFM categorization** — the actual Circular 7 category each fund is registered in, as declared by the administrator (more accurate than our derived classifier for fund-of-funds cases)
- [ ] **Rentabilidad oficial** — CMF/AFM official return indices per fund category (benchmark data)

### Code Quality
- [ ] Unit tests for loaders (especially TAC matching logic and `classify_serie()`)
- [ ] Open PR and merge `feature/cartola-downloader` → `main`
- [ ] Remove file-based intermediate storage for VPS (stream bytes → parse → DB, no disk write)

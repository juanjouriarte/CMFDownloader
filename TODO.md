# TODO

Working notes for the current cycle: fix known issues locally, add new endpoints, then debug production.
Not canonical — see `CLAUDE.md` for the durable project reference.

## Bugs found (fix locally first)

- [x] **`dividends` job silently no-ops when a future-dated dividend exists in DB.** — FIXED
  `DividendosDownloader.run()` (`src/etl/bolsaSantiago/downloaders/dividendosDownloader.py:66-73`)
  computed `from_year = max(last_year_in_db, current_year - 1)` and `to_year = current_year`.
  CMF/Bolsa pre-announces some dividend payment dates in advance, so `dividendos.fec_pago`
  could already contain a future year (confirmed locally: MAX = 2027-01-04 while "today" is
  2026-07-15). When `last_year_in_db > current_year`, `range(from_year, to_year+1)` was empty
  and the job returned `DownloadResult()` with zero downloads/errors — looked like success,
  did nothing. Fix: clamp `last_year = min(last_year, current_year)` before computing
  `from_year`. Verified against live Bolsa API: 6273 rows upserted for 2026 with 0 errors.
  On branch `fix/dividends-future-date-bug`, not yet merged.

- [ ] **Minor/latent**: `uq_dividendo (nemo, fec_pago, descrip_vc)` doesn't dedupe rows where
  any of those 3 columns is NULL (Postgres NULL-inequality semantics) — found one pre-existing
  duplicate pair (`CHILE-T`, 2013-01-03), unrelated to today's fix. Not actively growing right
  now; not fixed.

## Daily job smoke test (2026-07-15, against live CMF/Bolsa)

Ran manually against local dev DB — no CMF page-structure breakage found:

| Job | Result |
|---|---|
| `ref_codes_refresh` | ✅ 432 rows (152 country / 118 currency / 162 instrument) |
| `bonds_tickers` | ✅ 1282 upserted |
| `fm_identity` | ✅ 1344 upserted |
| `mf_tickers` | ✅ 2420 upserted |
| `fi_tickers` | ✅ 2372 upserted |
| `fi_identity` | ✅ 1657 upserted |
| `fi_daily_nav` | ✅ 11790 upserted (984 funds) |
| `dividends` | ⚠️ ran, 0 errors, but see bug above — false green |
| `mf_daily_nav` (cartola) | ⏳ untested — needs `GEMINI_API_KEY` for CAPTCHA solving |

## New endpoints

- [ ] TBD — waiting on requirements

## Production (deferred until local work is done)

- [ ] `financial-cmf.ddns.net` is not resolving via DNS at all right now (direct IP to
      146.181.34.54:80/443 works fine — nginx itself is healthy).
- [ ] App VM (146.181.34.54) containers (web/worker/mcp) are all up and `/health` returns
      200 internally, but the app VM cannot reach the DB VM on `10.0.0.42:5433`
      (connection refused).
- [ ] `~/.ssh/oracle_cmf.key` is not authorized on the DB VM (146.181.47.236) — got
      `Permission denied (publickey)`, though port 22 is open. Need the correct key/method
      before Postgres can be inspected there.
- User says this is a known issue — not chasing it until local work (bug fixes + new
  endpoints) is done.

# TODO

Working notes for the current cycle: fix known issues locally, add new endpoints, then debug production.
Not canonical — see `CLAUDE.md` for the durable project reference.

## Bugs found (fix locally first)

- [ ] **Investigate the 164 unmatched fund identifiers in the TAC import.**
  The July and August 2026 TAC refreshes each reported 164 `run_fondo` values that
  could not be matched to the local mutual-fund identity data. Determine whether
  these are obsolete funds, identifier-format differences, or missing identities,
  then document and implement the appropriate reconciliation.

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

- [ ] **Not fixing for now (user decision)**: `uq_dividendo (nemo, fec_pago, descrip_vc)` has
  268 duplicate groups / 559 rows where `fec_pago IS NULL`. Turned out bigger than first
  thought — most are NOT bugs, they're legitimate distinct historical capital-change events
  (e.g. recurring "EMISION 1 X 8 LIB." actions on different dates) only correctly
  distinguished by `fec_lim`, which isn't in the unique key. A couple of true exact duplicates
  do exist (e.g. `CHILE-T` 2013-01-03, `UNDURRAGA`/`fec_lim=1979-04-06`). Real fix would need
  `fec_lim` added to `uq_dividendo` plus a migration to delete true dupes first — data-integrity
  risk, deliberately not doing this right now.

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
| `dividends` | ⚠️ ran, 0 errors, but see bug above — false green (fixed, see above) |
| `mf_daily_nav` (cartola) | ✅ CAPTCHA solved via Gemini, 120175 rows upserted, data through 2026-07-16 across 444 funds |

**All 9 scheduled daily/incremental jobs now confirmed working against live CMF/Bolsa. No sign of breakage from any CMF page change.**

## New endpoints

- [ ] TBD — waiting on requirements

## Production (deferred until local work is done)

- [ ] **Deploy the Insiders data pipeline after local validation.** Apply the new
  `insider_*` table migrations, load both CMF VI/NV lists from January 2020,
  verify `/insiders/coverage`, and restart the worker to register the daily
  08:45 job. Deploy the Insiders explorer API before the frontend tab; validate
  exact-RUT aportantes links and date filters on the remote database.

- [ ] **Repeat the validated local data repair on the remote server.**
  Deploy the FI NAV job's rolling three-calendar-month window (including older
  gaps within that window) and the idempotent monthly TAC replacement fix.
  Back up the remote database, then rerun the data jobs from **2026-07-01**:
  FM/FI NAV, portfolios, TAC, dividends, identities, classifications and FI
  aportantes/cuotas. Also repair FI NAV for June 2026, rebuild June's FM
  classifications and reload May 2026 TAC to replace duplicate/stale imports.
  Use a server-specific runner: `scripts/reload_local_data.py`
  deliberately rejects remote databases. Refresh NNM, industry, returns and
  administrator summaries; verify daily NAV coverage, TAC duplicate counts and
  source availability for quarterly FI reports. Restart the remote worker so
  scheduled jobs use the new code, and verify the next scheduled execution.
  This is pending remote work; the current repairs are local only.

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

- [ ] **Review BTG Small Cap's earlier series history before adding more links.**
  Local RUN 7018 series A has a reporting gap from 2025-04-09 to 2026-08-20.
  The documented A → O rename on 2026-09-07 is implemented; it does not resolve
  that older gap. Verify CMF/BTG documents and source observations for any earlier
  transitions (including U), without inferring a link from similar NAV alone.
  Keep affected exact-date 1A/YTD returns unavailable until their bases are established.

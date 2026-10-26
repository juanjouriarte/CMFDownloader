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

- [x] **Replace the remote database with the refreshed local snapshot (2026-10-26).**
  Recreated `cmf` directly at the user's explicit request, without backing up or
  retaining the old server database. All 47 table counts matched the snapshot;
  all seven materialized views were rebuilt. Public readiness, Industria, returns,
  NNM, ranking-library and Insiders API checks passed; web, MCP and worker resumed.
  Temporary server import archives were removed. NAV reaches October 25 with
  partial recent coverage; FI portfolios remain June because September returned
  no holdings. The two known isolated historical FM NAV gaps remain.
  Evidence: ignored `output/data-refresh-20261026/` reports.
- [x] **Deploy the remaining application changes (2026-10-26).** Backend PR #48
  deployed migrations through `a3b4c5d6e7f8`, insurance/fund-deposit APIs and jobs,
  FI detail returns, FM's 14-day NAV lookback and CAPTCHA fallback. Dashboard
  PR #4 is published at `https://btg-dashboard-psi.vercel.app`. The backend's
  ten live API checks passed, and the new jobs are registered at 08:35/08:55.
- [ ] **Verify the next scheduled executions** of `fund_deposits`,
  `insurance_portfolios` and the updated FM NAV job.
- [ ] **Improve insurance summary performance on the production VM.** The first
  September summary with currency breakdowns took 39.47 seconds; the fixed-income
  issuer matrix took 13.93 seconds. Both returned HTTP 200. Reduce repeated scans
  while preserving filters, coverage and the CLP valuation contract.

- [ ] `financial-cmf.ddns.net` is not resolving via DNS at all right now (direct IP to
      146.181.34.54:80/443 works fine — nginx itself is healthy).
- [x] App-to-database connectivity verified on 2026-10-26; public `/ready` reports
      the database available, and database-backed API checks pass.
- [x] Database VM SSH access verified on 2026-10-26 using
      `~/Downloads/ssh-key-2026-06-08 (2).key` with user `ubuntu`.
- User says this is a known issue — not chasing it until local work (bug fixes + new
  endpoints) is done.

- [ ] **Review BTG Small Cap's earlier series history before adding more links.**
  Local RUN 7018 series A has a reporting gap from 2025-04-09 to 2026-08-20.
  The documented A → O rename on 2026-09-07 is implemented; it does not resolve
  that older gap. Verify CMF/BTG documents and source observations for any earlier
  transitions (including U), without inferring a link from similar NAV alone.
  Keep affected exact-date 1A/YTD returns unavailable until their bases are established.

## Insurance portfolios — follow-up audit (2026-10-20)

Scope: local CSVID public exports for August/September 2026, 33 insurers and
12 file families per month. Insurance tables/data were restored to production on
2026-10-26; the insurance application code and UI were subsequently deployed via
backend PR #48 and dashboard PR #4 on the same date.
These are reported positions, not necessarily distinct instruments or loans.

The local Seguros view, four persistence tables, and daily worker job are now
implemented using these two months only for the initial load. Source-level issues
below remain unresolved. Missing debtor/lead identities, unusable participation
and zero syndicated yields now carry field-level warnings in the loader and UI.
Measured local storage after loading both months: approximately 702 MiB for
records/indexes, 27 MiB for retained ZIPs, and 1 MiB for import/file metadata
(about 365 MiB per month on average; revisions, WAL and backups are additional).

| Coverage | August 2026 | September 2026 |
|---|---:|---:|
| Accepted records, all families | 159,058 | 170,592 |
| Entire files quarantined | 17 | 14 |
| Additional partially usable control files | 4 | 3 |
| Additional fund files accepted with a count warning | 2 | 2 |
| Syndicated-loan positions (`CS` + `CSOIND`) | 594 + 2 | 612 + 2 |
| Insurers reporting syndicated loans | 11 | 11 |
| Syndicated totals matching usable `I00` controls | 9 / 11 | 10 / 11 |

Passing structural validation does not certify each financial field. The
syndicated-loan checks below were an additional audit, not a new validation rule
in the loader. Do not label all accepted records as financially reconciled.

### Syndicated loans — before using debtor/participation analytics

- [ ] **Investigate absent debtor identity.** Every accepted CS/CSOIND position
  has a zero `syndicated_debtor_rut` and blank `syndicated_debtor_name` in both
  months. Determine whether the public export suppresses these fields or the
  source reporting omits them. Do not invent identities or silently replace
  debtor RUT with the issuer/lead bank RUT. If nemotécnico-based identification
  is considered later, validate its documented construction and exact mapping.

- [ ] **Review participation percentages outside the usable range.**
  In `i202609v.96571890` (Confuturo), source lines 2632–2637 contain
  239.70%, 239.70%, 239.70%, 239.70%, 122.92%, and 641.19%.
  These are the actual five-character source values at columns 705–709,
  decoded using the specified two decimal places; they are not a UI calculation.
  Penta Vida's two CSOIND positions report 0.00% in each month:
  `i202608v.96812960` lines 7108–7109 and `i202609v.96812960` lines 7048–7049.
  Establish explicit field-level quality flags before charts, concentration,
  weighted participation or loan-size calculations. Do not clamp to 100% or
  change decimal scaling without evidence.

- [ ] **Review other incomplete syndicated fields.** Lead bank RUT/name are
  missing in three August and two September positions. Purchase yield is
  reported as zero in seven positions each month; establish applicability
  before interpreting zero as a real yield. All observed syndicated maturity
  dates parsed as valid calendar dates.

- [ ] **Make syndicated `I00` reconciliation repeatable in the pipeline.**
  This audit found exact agreement for every available usable insurer/control
  pair: nine in August, ten in September, with no residual differences.
  August Principal (`96588080`) cannot be checked because its C file reports
  the wrong month. Zurich (`99185000`) cannot be checked in either month because
  only F11/F31 in its count-defective C files have been approved for use.
  The observed `I00` reconciliation includes both CS and CSOIND; omitting
  Penta's CSOIND leaves CLP 6,822,328,000 / 6,990,580,000 residuals respectively.
  A matching aggregate does not validate debtor identity or participation.
  CSOIND also has an O-file representation: avoid counting both in total AUM.

### Quarantined source files — investigate without relaxing validation globally

- [ ] **Identity and reporting-month conflicts.**
  `c202608v.96588080` (Principal) reports June in its header.
  `f202608v.99588060` and `f202609v.99588060` (Chubb Vida filenames)
  contain another entity's header RUT (`99225000`).
  `p202609v.99025000` also has a header/request mismatch.
  Confirm against re-downloads or independent CMF evidence; never silently
  reassign the entity or month.

- [ ] **Missing or invalid identification/trailer records.**
  Sura (`96549050`) August/September 2026 A/F/O/X holdings recovered with
  warnings after five I/B identity-anchor controls plus exact per-category
  reconciliation. C is partial: J00 has no matching detail and stays excluded.
  Missing headers/trailers are preserved, not repaired. Investigate source
  revisions and audit later months before extending this limited recovery.
  BUPA (`76282191`) G files begin with record type 4 instead of 1 in both
  months. Zurich `g202609v.99185000` is missing its closing trailer.
  Investigate source revisions and whether a documented export variation exists.

- [ ] **Unreconciled closing counts.** Remaining count-only quarantines:

  | Month | Files |
  |---|---|
  | August | `c202608v.99588060`, `e202608v.99588060`, `g202608v.96588080`, `o202608v.96588080`, `o202608v.99185000`, `o202608v.99588060` |
  | September | `c202609v.77205281`, `e202609v.99588060`, `g202609v.96571890`, `o202609v.76263414` |

  The Chubb E files contain only header/trailer but declare one record.
  Distinguish empty-file count defects from missing investment detail.
  Recover additional categories only after independent reconciliation; retain
  warnings, original bytes/hashes and declared/actual counts.

- [ ] **Malformed fields, blank records and layout ambiguity.**
  SAVE `b202608v.76034737` line 2 has depreciation `00000-14635`, which is not
  a valid numeric representation under the current layout.
  `t202608v.96812960` contains zero-length records among 80-character records.
  Consorcio `x202608v.99012000` line 566 and `x202609v.99012000` line 569
  contain `SINO` in the foreign-subsidiary record's reserved filler.
  Determine whether the latter is a source error or a layout variant before
  accepting it. One bad detail currently quarantines its entire file, including
  otherwise parseable positions; consider evidence-backed partial recovery
  with explicit coverage, not blanket whitespace/padding cleanup.

- [ ] **Review the remaining portions of partially recovered files.**
  A total of 124 fund positions were recovered by exact F11 reconciliation
  (SAVE August 4, Zurich August 5, Zurich September 5, BICE September 110).
  Their warnings remain. Non-fund rows and unmatched categories in the seven
  partially reconciled C files are still excluded. Do not treat partial
  reconciliation as approval of the whole file.

### Completeness and aggregation before DB/UI rollout

- [ ] **Validate CSGEN against real general-insurer downloads.** Current live
  checks cover life insurers only; shared layouts and synthetic group checks
  are not a substitute for validating general-insurer source files.
- [ ] **Add versioned historical layouts.** Current parsing rejects periods
  before December 2024. January 2020 onward is not yet a validated backfill.
- [ ] **Resolve unobserved layouts and missing source families.** X type 4
  (foreign property) is absent from both months; X type 5 has no accepted live
  example because the only source fails filler validation. Both have synthetic
  specification tests. R loans and V rental/mortgage arrears appear in the
  manual but not these public ZIPs; determine availability without assuming
  their economic balances are zero. Syndicated CS loans are already in I and
  are a separate case from the absent R family.
- [ ] **Complete cross-file reconciliation and a non-duplicating AUM model.**
  Resolve OIND/OIED summaries versus detail, leased properties versus leasing
  receivables, repo contracts versus underlyings, and collateral. Keep controls,
  cash-flow schedules, descriptors and derivative notionals separate from AUM.
  Existing overlap flags document the relationships but do not implement a
  fully reconciled insurer grand total.
- [ ] **Define semantic field-quality checks.** Keep structural acceptance,
  financial control agreement and individual field usability separate in the
  database/API. Preserve reported zero, missing and invalid values distinctly.
- [ ] **Review download reliability.** The earlier pilot encountered transient
  HTTP 500 failures from the CAPTCHA model. Preserve bounded retries and report
  failed downloads; do not mark a missing monthly archive as an empty portfolio.

### Monthly storage — measured prototype, not the final schema

Measured locally on 2026-10-20 with disposable PostgreSQL TEMP tables, one month
at a time, rolled back/removed afterward. The prototype used core typed columns
plus JSONB for remaining parsed fields, and three B-tree indexes (ID,
filing/source line, family/instrument/currency). File metadata was represented
by a filing ID; source ZIPs were kept outside PostgreSQL.
Sizes below are decimal MB (1 MB = 1,000,000 bytes).

| Storage | August | September |
|---|---:|---:|
| Accepted records | 159,058 | 170,592 |
| Downloaded source ZIP | 13.69 MB | 13.76 MB |
| Compressed parsed JSONL | 38.76 MB | 39.65 MB |
| Uncompressed parsed JSONL | 454.95 MB | 463.66 MB |
| PostgreSQL table, TOAST and auxiliary storage | 319.04 MB | 323.10 MB |
| Three indexes | 8.34 MB | 8.95 MB |
| PostgreSQL total | **327.38 MB** | **332.05 MB** |

- [ ] **Benchmark the final schema before backfilling.** At the prototype's
  scale, one year of current-volume CSVID snapshots is about 4 GB, before
  revisions, backups, WAL, additional indexes or CSGEN. Use roughly 0.5 GB/month
  (6 GB/year) as initial database planning headroom, not a final capacity promise.
  Compare typed family tables versus a large JSONB payload; normalize repeated
  identities/provenance and keep source archives outside the database.
  A daily polling job must not append another copy of an unchanged monthly ZIP.

Audit artifacts (local, gitignored; figures and actionable cases are preserved
above so this TODO remains useful without the artifacts):

- [Parser report](output/insurance-pilot/report.json)
- [Family coverage](output/insurance-pilot/parser-coverage.csv)
- [Count-recovery evidence](output/insurance-pilot/count-mismatch-review.csv)
- [Syndicated-loan audit](output/insurance-pilot/syndicated-audit.json)
- [Participation source evidence](output/insurance-pilot/syndicated-participation-issues.json)
- [PostgreSQL size measurements](output/insurance-pilot/storage-probe.json)

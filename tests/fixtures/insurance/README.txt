CMF Circular 1835 public export fixtures, August 2026, CSVID.

Source: https://www.cmfchile.cl/institucional/estadisticas/merc_seguros/cartera_inversiones/dcisgv/descarga_cartera_inv.php?tipoentidad=CSVID&fnAjax=descarga&peri=202608
Original ZIP SHA-256 prefix: ea2a6573f339.

Each file retains the original insurer identification and first investment/control
record only. The trailer count has been changed to 000003 for this trimmed fixture.
The debt fixture contains a public bank bond, not a personal mortgage/debtor record.
The fund/fi and fund/fip subdirectories contain one public/private fund record,
respectively, with the same three-record trimming convention.
These are parser test inputs, never dashboard data or authoritative totals.

count_mismatch/ contains the unmodified SAVE A/C files from the same source ZIP.
Their original incorrect trailer counts are intentionally preserved for recovery
tests: A declares 7 records but has 6; C declares 13 but has 14. A's four fund
holdings sum to C's F11 amount, 1,647,244 thousands of CLP.

Layouts: Anexos_Tecnicos_Circular_N1835.pdf, sections B.1, B.2, B.3, B.8.

public_families/ adds real source examples for B/D/E/G/O/T, P detail types 2–6,
and X detail types 2–3. The same three-record trimming convention applies;
X/P trailers retain their actual type (6/7). manifest.json records original file
and archive hashes, source line and unmodified detail-row hash. The B example
is a non-residential property. There are no loan/borrower R or V files here.
All figures describe corporate insurer holdings.

No X type 4 (foreign property) was found in the two source months. The sole
X type 5 (foreign subsidiary) source file fails blank-padding validation.
These two layouts have specification-based synthetic tests, not accepted live
fixtures. This limitation must remain visible when describing parser coverage.

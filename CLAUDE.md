# Project memory

## Data source: migrating Teradata → Essbase

The `financial_agent` data backend is being switched **from Teradata to Essbase**.
Future financial-data queries should target the **Essbase multidimensional cube**
(MDX against the outline), **not** Teradata/MSSQL SQL.

- **Canonical cube structure:** [`essbase_outline.md`](essbase_outline.md)
  (indented hierarchy + member formulas) and
  [`essbase_outline_parent_child.csv`](essbase_outline_parent_child.csv)
  (loadable parent/child build file). Treat these as the source of truth for the
  Essbase outline. Re-extract/refresh them if the cube changes.
- **Cube = 10 dimensions:**
  - `Time` (Time) — years 2007–2025, each year → H1/H2 → quarters …
  - `Measure` (Accounts, Label only) — 23 members; `Current`, `OP` + ~20 derived
    members with MDX formulas (YTD, variances, %, vs Draft/ForecastV2, etc.)
  - `Currency` (None) — NTD K, USD K
  - `Sector Total` (None, Never share) — Assy / Test / Material / EMS / Estate / Other
  - `Site Group` (None) — 18 group members
  - `Site Org` (None) — 10 org members
  - `Scenario` (None) — Actual, Draft, Draft & Final (ƒ), ForecastV1–V4
  - `Filings` (None, Label only) — TW_Filing, US_Filing
  - `Period` (Attribute, Text) — H1(Q1,Q2)/H2; **associated dim = Time**
  - `Year` (Attribute, Text) — 2007–2025; **associated dim = Time**
- The outline was extracted from the Essbase Outline-editor HTML, so some branches
  were **collapsed** (leaf members not captured). Collapsed nodes are marked
  `collapsed:N` in both files — see them before assuming a member list is complete.

### Essbase REST client (scaffold — DONE)
- `financial_agent/essbase_client.py` — **`EssbaseClient`** wraps the Essbase 21c
  REST endpoint `POST {base}/applications/{app}/databases/{db}/mdx?format=JSON`
  (HTTP Basic auth). `execute_mdx()` returns raw JSON; `mdx_to_records()` /
  `mdx_to_long_df()` flatten the grid (`metadata.{page,column,row}` + `data`) to a
  tidy long table. Reads `FA_ESB_*` from `.env` via `config.RUNTIME` (lazy
  `requests` import; CLI `python essbase_client.py [MDX] [--raw|--csv]`).
- `financial_agent/config.py` — added `esb_uri/app/db/user/pwd/verify_tls/timeout`
  + `esb_configured` (mirrors the `td_*` block).
- `financial_agent/.env` — `FA_ESB_*` commented placeholders (tracked file = no
  secrets; real values go in a local .env or real env vars).
- `financial_agent/tests/test_essbase_client.py` — offline tests (parser/URL/
  config‑gating), pandas‑free.

### Live cube — VERIFIED (smoketest ran, real data returned)
- `financial_agent/essbase_smoketest.py` — stdlib-only connection probe. Confirmed:
  the cube is reachable over **HTTPS on :9001** (plain http → server sends a TLS
  alert), needs `Accept: application/octet-stream` + `format=JSON`; over TLS it
  returns **standard HTTP/1.1**, so urllib/`requests` parse it directly (a
  raw-socket fallback for header-less HTTP/0.9 streams exists but isn't needed on
  the happy path). Flags: `--insecure` / `--cafile` (self-signed CA),
  `--raw-values` (formatValues off → clean doubles), `--legacy-tls`, `--proxy`.
- **Real grid shape differs from the offline guess** in
  `essbase_client.mdx_to_records()`:
    `metadata.{row,column,page}` = **dimension NAME strings** (not member tuples);
    `data[0]` = column-member header row (leading `""` per row-dim);
    `data[i]` = `[<row members…>, <values…>]`.
  → The CORRECT parser for this real shape now lives in `essbase_grid.py`
    (`parse_mdx_grid`). When wiring the agent, port THIS, not the old
    `mdx_to_records` (rework/retire that one).

### Streamlit pivot / hierarchy viz (DONE — demo)
- `financial_agent/essbase_grid.py` — pure-Python (pandas optional): `parse_mdx_grid`
  (grid→long records), `load_parent_map`/`member_path`/`aggregate_values`
  (hierarchy from `essbase_outline_parent_child.csv`), `to_long_df`.
- `financial_agent/essbase_pivot_app.py` — `streamlit run` app: Plotly
  treemap/sunburst/icicle + MultiIndex pivot table (+ optional AgGrid tree).
  Defaults to a built-in SAMPLE (offline) or `Essbase live` (reuses smoketest fetch).
- `financial_agent/tests/test_essbase_grid.py` — offline tests (6, pass).
- Next (when asked): wire into agent / `EssbaseClient`.

### Remaining touchpoints (NOT done — do only when asked)
- `financial_agent/essbase.py` — `to_pivot_ready` reshaping; can consume
  `mdx_to_long_df()` output once wired.
- `financial_agent/agent.py` — DB schema tool (~L836) still describes the
  "Teradata（Essbase 多維寬表）" structure; DB-query tool (~L2279) still targets
  MSSQL. Swap these to call `EssbaseClient` when migrating the agent.
- `financial_agent/config.py` — `mcp_args` still defaults to `mcp_server_mssql.py`.

> Status: connection **VERIFIED against the live cube** (smoketest returns real
> data); the real grid shape is now known and parsed by `essbase_grid.parse_mdx_grid`
> (the older `essbase_client.mdx_to_records` assumed a different shape — rework when
> wiring). A Streamlit pivot/treemap demo (`essbase_pivot_app.py`) consumes it.
> Still **not wired into the agent** (agent.py DB tools untouched — do when asked).

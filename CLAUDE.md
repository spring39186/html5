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

### Remaining touchpoints (NOT done — do only when asked)
- `financial_agent/essbase.py` — `to_pivot_ready` reshaping; can consume
  `mdx_to_long_df()` output once wired.
- `financial_agent/agent.py` — DB schema tool (~L836) still describes the
  "Teradata（Essbase 多維寬表）" structure; DB-query tool (~L2279) still targets
  MSSQL. Swap these to call `EssbaseClient` when migrating the agent.
- `financial_agent/config.py` — `mcp_args` still defaults to `mcp_server_mssql.py`.

> Status: REST client is scaffolded + tested offline, but **not yet wired into the
> agent** and **not run against a live cube**. Verify `mdx_to_records()` field
> mapping against a real response (the Oracle doc page is bot‑blocked, so the grid
> shape was taken from Oracle's MDX-Provider JSON spec + may vary by version).

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

### Current code touchpoints to change when switching the backend
- `financial_agent/essbase.py` — `to_pivot_ready` / pivot reshaping of the Essbase
  long table; already Essbase-aware.
- `financial_agent/agent.py` — DB schema tool (~L836) currently describes a
  "Teradata（Essbase 多維寬表）" structure; the DB-query tool (~L2279) targets MSSQL.
- `financial_agent/config.py` — `mcp_args` defaults to `mcp_server_mssql.py`
  (the MSSQL MCP server). Essbase access will replace/supplement this.

> Note: nothing was migrated yet — this is a recorded intent + the extracted
> outline. Do the actual Teradata→Essbase code switch only when asked.

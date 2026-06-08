"""
chart_schema.py — Chart specification validation and normalisation
==================================================================
Validates and normalises chart specs produced by the synthesiser agent,
ensuring only clean, type-safe specs reach the visualisation pipeline.

Public API
----------
validate_chart(spec)          → (bool, str)   True/reason
normalize_chart(spec)         → dict           coerced copy
validate_and_fix(charts)      → (valid_list, error_strings)
SYNTHESIS_V2_SYSTEM           str constant    synthesiser system prompt
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Allowed chart types (canonical names)
# ---------------------------------------------------------------------------

_VALID_TYPES = {"line", "bar", "pie"}

# ---------------------------------------------------------------------------
# Alias map: various user-facing strings → canonical chart type
# ---------------------------------------------------------------------------

_ALIAS_MAP: dict[str, str] = {
    # bar variants
    "bar chart": "bar",
    "bar graph": "bar",
    "barchart": "bar",
    "bargraph": "bar",
    "長條": "bar",
    "長條圖": "bar",
    "柱狀": "bar",
    "柱狀圖": "bar",
    "直方圖": "bar",
    "column": "bar",
    "column chart": "bar",
    # line variants
    "line chart": "line",
    "line graph": "line",
    "linechart": "line",
    "linegraph": "line",
    "折線": "line",
    "折線圖": "line",
    "趨勢圖": "line",
    # pie variants
    "pie chart": "pie",
    "piechart": "pie",
    "圓餅": "pie",
    "圓餅圖": "pie",
    "餅圖": "pie",
    "donut": "pie",
    "doughnut": "pie",
}


# ---------------------------------------------------------------------------
# Numeric coercion
# ---------------------------------------------------------------------------

def _to_number(value: Any) -> float | None:
    """
    Try to coerce *value* to a float.

    Handles:
    - int/float already → pass through
    - str with commas like "3,103,836" → 3103836.0
    - str with currency symbols / percent signs → strip then parse
    Returns None on failure.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip()
        # Remove common non-numeric decorations: currency symbols, percent, spaces
        cleaned = re.sub(r"[^\d,.\-eE]", "", cleaned)
        # Remove commas used as thousands separators
        cleaned = cleaned.replace(",", "")
        if cleaned == "" or cleaned == "-":
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _is_string_ish(value: Any) -> bool:
    """Return True for values that serve as row labels (str, int rendered as label)."""
    return isinstance(value, str)


# ---------------------------------------------------------------------------
# 1. validate_chart
# ---------------------------------------------------------------------------

def validate_chart(spec: dict) -> tuple[bool, str]:
    """
    Validate a chart specification dict.

    Requirements
    ------------
    - ``title``      : non-empty str
    - ``chart_type`` : one of {"line", "bar", "pie"} (case-sensitive, post-alias)
    - ``data``       : non-empty list of dicts, each row containing
                         >= 1 string-ish label key  AND  >= 1 numeric value

    Parameters
    ----------
    spec : dict

    Returns
    -------
    (True, "")             if valid
    (False, reason_str)    if invalid
    """
    if not isinstance(spec, dict):
        return False, "spec must be a dict"

    # title
    title = spec.get("title")
    if not isinstance(title, str) or not title.strip():
        return False, "title must be a non-empty string"

    # chart_type
    ct = spec.get("chart_type")
    if not isinstance(ct, str) or ct not in _VALID_TYPES:
        return False, (
            f"chart_type must be one of {sorted(_VALID_TYPES)}, got {ct!r}"
        )

    # data
    data = spec.get("data")
    if not isinstance(data, list) or len(data) == 0:
        return False, "data must be a non-empty list"

    for row_idx, row in enumerate(data):
        if not isinstance(row, dict):
            return False, f"data[{row_idx}] is not a dict"
        has_label = any(_is_string_ish(v) for v in row.values())
        has_numeric = any(_to_number(v) is not None for v in row.values())
        if not has_label:
            return False, (
                f"data[{row_idx}] has no string-ish label key"
            )
        if not has_numeric:
            return False, (
                f"data[{row_idx}] has no numeric value"
            )

    return True, ""


# ---------------------------------------------------------------------------
# 2. normalize_chart
# ---------------------------------------------------------------------------

def normalize_chart(spec: dict) -> dict:
    """
    Return a normalised copy of *spec*.

    Coercions applied
    -----------------
    1. ``chart_type`` lowercased, then alias-mapped to canonical name.
    2. ``title`` cast to str (or "" if absent).
    3. Each data row: drop rows with no numeric value; parse comma-formatted
       number strings like "3,103,836" to float.

    The original dict is not mutated.

    Parameters
    ----------
    spec : dict

    Returns
    -------
    dict — normalised spec
    """
    result: dict = {}

    # title
    raw_title = spec.get("title", "")
    result["title"] = str(raw_title) if raw_title is not None else ""

    # chart_type
    raw_ct = spec.get("chart_type", "")
    if isinstance(raw_ct, str):
        ct_lower = raw_ct.strip().lower()
        # try alias map (longest match first to handle "bar chart" before "bar")
        mapped = _ALIAS_MAP.get(ct_lower)
        if mapped is None:
            # Try prefix / single-word canonical
            mapped = ct_lower if ct_lower in _VALID_TYPES else ct_lower
        result["chart_type"] = mapped
    else:
        result["chart_type"] = str(raw_ct)

    # description (pass through)
    if "description" in spec:
        result["description"] = spec["description"]

    # data — normalise rows
    raw_data = spec.get("data", [])
    if not isinstance(raw_data, list):
        raw_data = []

    normalised_rows: list[dict] = []
    for row in raw_data:
        if not isinstance(row, dict):
            continue
        new_row: dict = {}
        has_numeric = False
        for k, v in row.items():
            num = _to_number(v)
            if num is not None:
                new_row[k] = num
                has_numeric = True
            else:
                # keep as-is (string labels etc.)
                new_row[k] = v
        if has_numeric:
            normalised_rows.append(new_row)
        # rows with no numeric value are silently dropped

    result["data"] = normalised_rows

    # pass through any other keys untouched
    for k, v in spec.items():
        if k not in result:
            result[k] = v

    return result


# ---------------------------------------------------------------------------
# 3. validate_and_fix
# ---------------------------------------------------------------------------

def validate_and_fix(
    charts: list[dict],
) -> tuple[list[dict], list[str]]:
    """
    Normalise each chart spec, keep valid ones, collect errors for invalid.

    Pipeline per chart: normalize_chart → validate_chart.

    Parameters
    ----------
    charts : list of raw chart spec dicts

    Returns
    -------
    (valid_charts, errors)
      valid_charts : list[dict]  — normalised specs that passed validation
      errors       : list[str]   — human-readable error strings for rejected specs
    """
    valid: list[dict] = []
    errors: list[str] = []

    for idx, raw in enumerate(charts):
        normed = normalize_chart(raw)
        ok, reason = validate_chart(normed)
        if ok:
            valid.append(normed)
        else:
            title = raw.get("title") or f"chart[{idx}]"
            errors.append(f"[chart {idx}] '{title}': {reason}")

    return valid, errors


# ---------------------------------------------------------------------------
# 4. SYNTHESIS_V2_SYSTEM prompt constant
# ---------------------------------------------------------------------------

SYNTHESIS_V2_SYSTEM: str = """你是「證據整合者」（Evidence Integrator）。
你的角色是「整合器」，不是搜尋者、不是分析師，也不持有任何外部知識。

【核心規則 — 絕對不可違反】
1. **禁止使用外部知識**：你只能使用「證據區塊 [E1]、[E2]…」中實際出現的資訊。
   任何未出現在證據中的數字、事實、名稱，一律不得出現在輸出中。
2. **禁止捏造數字**：不可推算、估算、內插或憑印象填補任何缺漏數值。
3. **強制引用**：報告中每一個結論、每一個數字，都必須以 [E1]、[E2]、[E3]… 等
   「證據編號」標明出處（至少引用一個證據 ID）。
4. **資料不足時誠實聲明**：若證據不足以回答某問題，應在報告中說明「資料不足」，
   絕不可硬湊或推測。
5. **圖表與表格數據來源**：charts/tables 的 data 欄位必須直接來自證據編號中的數字，
   並在 description 中標明引用的證據（例如「數據來自 [E2][E5]」）。
6. **最終報告語言**：report 欄位全程使用繁體中文書寫。

【輸出格式】只輸出有效 JSON，不含任何 markdown 圍欄或額外文字：
{
  "report": "完整 Markdown 分析報告（繁體中文）。結論引用 [E1]、[E2] 等。",
  "tables": [
    { "title": "表格標題", "data": [ {"欄位": 值, ...}, ... ] }
  ],
  "charts": [
    {
      "title": "圖表標題",
      "chart_type": "line | bar | pie",
      "description": "說明此圖呈現內容及數據來源，例如：數據來自 [E3][E4]",
      "data": [ {"label/年度...": 值, ...}, ... ]
    }
  ]
}

【補充說明】
- charts 不需要時給空陣列 []；tables 不需要時給空陣列 []。
- chart_type 只能是 "line"、"bar"、"pie" 之一。
- 圖表 data 中的數值必須是純數字（不含貨幣符號或千分位逗號）。
- 若同一指標出現於多個證據且數值一致，可合併引用；若不一致，在報告中指出差異。
"""

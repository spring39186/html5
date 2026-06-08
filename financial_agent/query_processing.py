"""
query_processing.py  –  Phase 4 & 5 of the cross-lingual Financial RAG pipeline
=================================================================================
Phase 4 – Query Translation & Expansion
Phase 5 – Financial Entity Extraction

Design notes
------------
* No network / LLM is called directly; callers inject an ``llm_call`` callable
  with signature:  llm_call(messages: list[dict], temperature: float = 0.1) -> str
* Imports are stdlib-only so the module can be py_compiled in any environment.
* JSON extraction follows the same robust pattern used in agent.py:
  strip ```json fences → find outermost {...} → json.loads.
* Every public function has a safe fallback so transient LLM errors never
  bubble up to the caller.
"""

import json
import re
from typing import Optional


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Strip markdown fences and parse the outermost JSON object.

    Raises ValueError if no valid JSON object can be found.
    Mirrors the implementation in agent.py for consistency.
    """
    text = (text or "").strip()

    # 1. Try to peel off ```json ... ``` or ``` ... ``` fences first
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start : end + 1]
        else:
            raise ValueError(f"No JSON object found in text: {text[:200]!r}")

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse error: {exc}  (candidate: {candidate[:200]!r})") from exc


# ---------------------------------------------------------------------------
# Phase 4 – Query Translation & Expansion
# ---------------------------------------------------------------------------

_TRANSLATE_SYSTEM = (
    "You are a financial terminology translator. "
    "Translate the search request into concise professional financial English. "
    "Requirements: "
    "Preserve company names. "
    "Preserve years. "
    "Preserve stock symbols. "
    "Preserve fiscal periods. "
    "Use standard financial terminology. "
    "Output JSON only."
)

_TRANSLATE_USER_TMPL = (
    'Translate the following query and expand it with financial synonyms.\n'
    'Query: {query}\n\n'
    'Return a JSON object with exactly these keys:\n'
    '{{\n'
    '  "translated_query": "<concise professional English translation>",\n'
    '  "expanded_terms": ["<synonym1>", "<synonym2>", ...]\n'
    '}}\n'
    'Examples of synonyms to include when relevant:\n'
    '  Operating Margin / Operating Profit Margin / Operating Income Margin\n'
    '  CAPEX / Capital Expenditure / Capital Spending\n'
    '  Revenue / Net Sales / Turnover\n'
    '  Net Income / Net Profit / Earnings\n'
    '  Gross Margin / Gross Profit Margin\n'
    '  EPS / Earnings Per Share\n'
    '  ROE / Return on Equity\n'
    '  ROA / Return on Assets\n'
    '  EBITDA / Earnings Before Interest Taxes Depreciation Amortization\n'
    'Only include synonyms relevant to the query.'
)


def translate_and_expand_query(query: str, llm_call) -> dict:
    """Phase 4: Translate a (possibly non-English) financial query to professional
    English and expand it with standard financial synonyms.

    Parameters
    ----------
    query:    Raw user query (any language).
    llm_call: Injected callable – llm_call(messages, temperature) -> str.

    Returns
    -------
    dict with keys:
        "translated_query" – str  (professional English)
        "expanded_terms"   – list[str]
    Never raises; falls back to {"translated_query": query, "expanded_terms": []}
    on any error.
    """
    _fallback = {"translated_query": query, "expanded_terms": []}
    try:
        messages = [
            {"role": "system", "content": _TRANSLATE_SYSTEM},
            {"role": "user",   "content": _TRANSLATE_USER_TMPL.format(query=query)},
        ]
        raw = llm_call(messages, temperature=0.1)
        result = _extract_json(raw)

        translated = result.get("translated_query", "").strip()
        expanded   = result.get("expanded_terms", [])

        # Validate types; degrade gracefully on bad shapes
        if not isinstance(translated, str) or not translated:
            return _fallback
        if not isinstance(expanded, list):
            expanded = []

        return {"translated_query": translated, "expanded_terms": expanded}

    except Exception:  # noqa: BLE001
        return _fallback


# ---------------------------------------------------------------------------
# Phase 5 – Financial Entity Extraction
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = (
    "You are a financial information extractor. "
    "Extract structured entities from the user query. "
    "Output JSON only."
)

_EXTRACT_USER_TMPL = (
    'Extract financial entities from the following query.\n'
    'Query: {query}\n\n'
    'Return a JSON object with exactly these keys (use null for unknown values):\n'
    '{{\n'
    '  "company":   "<company name or null>",\n'
    '  "ticker":    "<stock ticker symbol or null>",\n'
    '  "year":      <4-digit integer year or null>,\n'
    '  "quarter":   "<Q1|Q2|Q3|Q4 or null>",\n'
    '  "metric":    "<financial metric name or null>",\n'
    '  "currency":  "<USD|TWD|JPY|EUR|... or null>",\n'
    '  "geography": "<country or region or null>"\n'
    '}}'
)

# Keys that must appear in the entity dict (with None as default)
_ENTITY_KEYS = ("company", "ticker", "year", "quarter", "metric", "currency", "geography")

_RE_YEAR    = re.compile(r"\b((?:19|20)\d{2})\b")
_RE_QUARTER = re.compile(r"\b(Q[1-4])\b", re.IGNORECASE)


def _regex_fallback(query: str) -> dict:
    """Extract year and quarter with regex; everything else defaults to None."""
    base: dict = {k: None for k in _ENTITY_KEYS}

    year_m = _RE_YEAR.search(query)
    if year_m:
        base["year"] = int(year_m.group(1))

    quarter_m = _RE_QUARTER.search(query)
    if quarter_m:
        base["quarter"] = quarter_m.group(1).upper()

    return base


def extract_entities(query: str, llm_call) -> dict:
    """Phase 5: Extract structured financial entities from a query.

    Strategy
    --------
    1. Run regex fallback to capture year / quarter unconditionally.
    2. Call LLM for full entity extraction.
    3. Merge: start from regex result, overlay any non-null LLM values.
       This ensures year/quarter survive even when the LLM call fails.

    Parameters
    ----------
    query:    Raw user query.
    llm_call: Injected callable.

    Returns
    -------
    dict with keys: company, ticker, year, quarter, metric, currency, geography
    Values may be None.  Never raises.
    """
    # Step 1 – regex baseline (always succeeds)
    entities = _regex_fallback(query)

    # Step 2 – LLM extraction
    try:
        messages = [
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user",   "content": _EXTRACT_USER_TMPL.format(query=query)},
        ]
        raw = llm_call(messages, temperature=0.1)
        llm_result = _extract_json(raw)

        # Step 3 – merge: overlay non-null LLM values onto regex baseline
        for key in _ENTITY_KEYS:
            llm_val = llm_result.get(key)
            if llm_val is not None:
                # Coerce year to int if the LLM returned a string
                if key == "year":
                    try:
                        entities[key] = int(llm_val)
                    except (TypeError, ValueError):
                        pass  # keep regex value
                else:
                    entities[key] = llm_val

    except Exception:  # noqa: BLE001
        pass  # entities already has regex-derived values; LLM failure is silent

    return entities


# ---------------------------------------------------------------------------
# Helper – map entities to a ChromaDB metadata where-filter
# ---------------------------------------------------------------------------

def entities_to_chroma_filter(entities: dict) -> Optional[dict]:
    """Build a ChromaDB metadata ``where`` filter from extracted entities.

    Only the keys ``company``, ``year``, and ``quarter`` are mapped
    (the three most common metadata fields stored on ChromaDB documents).

    Returns
    -------
    A dict suitable for passing as ``where=`` to ``collection.query()``,
    or ``None`` if none of the three fields are present.

    Single condition   → {"field": {"$eq": value}}
    Multiple conditions → {"$and": [{...}, ...]}
    """
    conditions = []

    if entities.get("company"):
        conditions.append({"company": {"$eq": entities["company"]}})
    if entities.get("year") is not None:
        conditions.append({"year": {"$eq": int(entities["year"])}})
    if entities.get("quarter"):
        conditions.append({"quarter": {"$eq": entities["quarter"]}})

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}

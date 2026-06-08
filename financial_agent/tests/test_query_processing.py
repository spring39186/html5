"""
tests/test_query_processing.py
================================
Self-contained test suite for query_processing.py.
Stdlib-only; run with:
    python3 tests/test_query_processing.py

Tests
-----
1. translate_and_expand_query – happy path (Chinese query, canned LLM JSON)
2. extract_entities – happy path (TSMC 2024 Q3, canned LLM JSON)
3. Garbage LLM response – translate fallback + regex entity fallback
4. entities_to_chroma_filter – various shapes
"""

import json
import sys
import os

# Allow running from the repo root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from query_processing import (
    translate_and_expand_query,
    extract_entities,
    entities_to_chroma_filter,
    _extract_json,
)


# ---------------------------------------------------------------------------
# Stub factories
# ---------------------------------------------------------------------------

def make_stub(response: str):
    """Return a stub llm_call that always returns `response`."""
    def stub(messages, temperature=0.1):  # noqa: ARG001
        return response
    return stub


def make_raising_stub(exc=RuntimeError("LLM unavailable")):
    """Return a stub that simulates an LLM failure by raising."""
    def stub(messages, temperature=0.1):  # noqa: ARG001
        raise exc
    return stub


# ---------------------------------------------------------------------------
# Test 1 – translate_and_expand_query: happy path
# ---------------------------------------------------------------------------

def test_translate_happy_path():
    """Chinese query → translated English + expanded synonyms."""
    query = "幫我找2024年的營業利益率與資本支出"

    canned_json = json.dumps({
        "translated_query": "2024 Operating Profit Margin and Capital Expenditure (CAPEX)",
        "expanded_terms": [
            "Operating Margin",
            "Operating Profit Margin",
            "Operating Income Margin",
            "CAPEX",
            "Capital Expenditure",
            "Capital Spending",
        ],
    })
    stub = make_stub(canned_json)
    result = translate_and_expand_query(query, stub)

    assert isinstance(result, dict), "result must be a dict"
    assert result["translated_query"] == "2024 Operating Profit Margin and Capital Expenditure (CAPEX)", \
        f"unexpected translated_query: {result['translated_query']!r}"

    terms = result["expanded_terms"]
    assert isinstance(terms, list), "expanded_terms must be a list"
    assert len(terms) >= 4, f"expected at least 4 expanded terms, got {len(terms)}"

    # Check key synonyms are present
    terms_lower = [t.lower() for t in terms]
    assert any("operating margin" in t or "operating profit margin" in t for t in terms_lower), \
        f"Operating Margin synonym missing from {terms}"
    assert any("capex" in t or "capital expenditure" in t for t in terms_lower), \
        f"CAPEX synonym missing from {terms}"

    print("  [PASS] test_translate_happy_path")


# ---------------------------------------------------------------------------
# Test 2 – translate_and_expand_query: JSON wrapped in markdown fence
# ---------------------------------------------------------------------------

def test_translate_markdown_fence():
    """LLM wraps response in ```json fences – should still parse."""
    query = "What is the gross margin for 2023?"
    inner = json.dumps({
        "translated_query": "2023 Gross Profit Margin",
        "expanded_terms": ["Gross Margin", "Gross Profit Margin"],
    })
    canned = f"```json\n{inner}\n```"
    result = translate_and_expand_query(query, make_stub(canned))
    assert result["translated_query"] == "2023 Gross Profit Margin"
    assert "Gross Margin" in result["expanded_terms"]
    print("  [PASS] test_translate_markdown_fence")


# ---------------------------------------------------------------------------
# Test 3 – extract_entities: happy path (TSMC 2024 Q3 毛利率)
# ---------------------------------------------------------------------------

def test_extract_entities_happy_path():
    """TSMC 2024 Q3 毛利率 → structured entities."""
    query = "TSMC 2024 Q3 毛利率"

    canned_json = json.dumps({
        "company":   "TSMC",
        "ticker":    "TSM",
        "year":      2024,
        "quarter":   "Q3",
        "metric":    "Gross Margin",
        "currency":  None,
        "geography": "Taiwan",
    })
    result = extract_entities(query, make_stub(canned_json))

    assert result["company"] == "TSMC",         f"company: {result['company']!r}"
    assert result["ticker"]  == "TSM",          f"ticker: {result['ticker']!r}"
    assert result["year"]    == 2024,           f"year: {result['year']!r}"
    assert result["quarter"] == "Q3",           f"quarter: {result['quarter']!r}"
    assert result["metric"]  == "Gross Margin", f"metric: {result['metric']!r}"
    assert result["geography"] == "Taiwan",     f"geography: {result['geography']!r}"
    assert result["currency"] is None,          f"currency should be None: {result['currency']!r}"

    print("  [PASS] test_extract_entities_happy_path")


# ---------------------------------------------------------------------------
# Test 4 – extract_entities: year as string from LLM (coercion)
# ---------------------------------------------------------------------------

def test_extract_entities_year_string_coercion():
    """LLM may return year as string – must coerce to int."""
    query = "Apple 2023 revenue"
    canned_json = json.dumps({
        "company": "Apple",
        "ticker": "AAPL",
        "year": "2023",   # string – must become int
        "quarter": None,
        "metric": "Revenue",
        "currency": "USD",
        "geography": "United States",
    })
    result = extract_entities(query, make_stub(canned_json))
    assert result["year"] == 2023, f"year should be int 2023, got {result['year']!r}"
    assert isinstance(result["year"], int), "year must be int"
    print("  [PASS] test_extract_entities_year_string_coercion")


# ---------------------------------------------------------------------------
# Test 5 – Garbage LLM response → fallbacks
# ---------------------------------------------------------------------------

def test_garbage_llm_fallback():
    """Both functions must fall back gracefully on garbage LLM output."""
    garbage_stub = make_stub("Sorry, I cannot answer that right now. No JSON here!")

    # --- translate fallback ---
    query = "2024 Q3 operating margin"
    tr = translate_and_expand_query(query, garbage_stub)
    assert tr["translated_query"] == query, \
        f"fallback translated_query should equal original query, got {tr['translated_query']!r}"
    assert tr["expanded_terms"] == [], \
        f"fallback expanded_terms should be [], got {tr['expanded_terms']!r}"

    # --- entity regex fallback ---
    # Input contains 2024 and Q3 – regex must still capture them
    ent = extract_entities(query, garbage_stub)
    assert ent["year"] == 2024, \
        f"regex fallback should extract year=2024, got {ent['year']!r}"
    assert ent["quarter"] == "Q3", \
        f"regex fallback should extract quarter=Q3, got {ent['quarter']!r}"
    # Fields with no regex rule must be None
    assert ent["company"]   is None, f"company should be None, got {ent['company']!r}"
    assert ent["ticker"]    is None, f"ticker should be None, got {ent['ticker']!r}"
    assert ent["metric"]    is None, f"metric should be None, got {ent['metric']!r}"
    assert ent["currency"]  is None, f"currency should be None, got {ent['currency']!r}"
    assert ent["geography"] is None, f"geography should be None, got {ent['geography']!r}"

    print("  [PASS] test_garbage_llm_fallback")


# ---------------------------------------------------------------------------
# Test 6 – Raising LLM stub → same fallbacks
# ---------------------------------------------------------------------------

def test_raising_stub_fallback():
    """Functions must not propagate exceptions from llm_call."""
    err_stub = make_raising_stub()

    query = "Samsung 2022 Q2 ROE"
    tr = translate_and_expand_query(query, err_stub)
    assert tr["translated_query"] == query
    assert tr["expanded_terms"] == []

    ent = extract_entities(query, err_stub)
    assert ent["year"]    == 2022, f"year: {ent['year']!r}"
    assert ent["quarter"] == "Q2", f"quarter: {ent['quarter']!r}"

    print("  [PASS] test_raising_stub_fallback")


# ---------------------------------------------------------------------------
# Test 7 – entities_to_chroma_filter: various shapes
# ---------------------------------------------------------------------------

def test_entities_to_chroma_filter_all_none():
    """All None → return None."""
    ent = {k: None for k in ("company", "ticker", "year", "quarter", "metric", "currency", "geography")}
    result = entities_to_chroma_filter(ent)
    assert result is None, f"expected None, got {result!r}"
    print("  [PASS] test_entities_to_chroma_filter_all_none")


def test_entities_to_chroma_filter_single_field():
    """Only company → single condition (no $and wrapper)."""
    ent = {k: None for k in ("company", "ticker", "year", "quarter", "metric", "currency", "geography")}
    ent["company"] = "TSMC"
    result = entities_to_chroma_filter(ent)
    assert result == {"company": {"$eq": "TSMC"}}, f"unexpected: {result!r}"
    print("  [PASS] test_entities_to_chroma_filter_single_field")


def test_entities_to_chroma_filter_two_fields():
    """company + year → $and with two conditions."""
    ent = {k: None for k in ("company", "ticker", "year", "quarter", "metric", "currency", "geography")}
    ent["company"] = "TSMC"
    ent["year"] = 2024
    result = entities_to_chroma_filter(ent)
    assert "$and" in result, f"expected $and, got {result!r}"
    conds = result["$and"]
    assert len(conds) == 2, f"expected 2 conditions, got {len(conds)}"
    assert {"company": {"$eq": "TSMC"}} in conds
    assert {"year": {"$eq": 2024}} in conds
    print("  [PASS] test_entities_to_chroma_filter_two_fields")


def test_entities_to_chroma_filter_full():
    """company + year + quarter → $and with three conditions."""
    ent = {
        "company": "TSMC", "ticker": "TSM", "year": 2024, "quarter": "Q3",
        "metric": "Gross Margin", "currency": None, "geography": "Taiwan",
    }
    result = entities_to_chroma_filter(ent)
    assert "$and" in result, f"expected $and, got {result!r}"
    conds = result["$and"]
    assert len(conds) == 3, f"expected 3 conditions, got {len(conds)}"
    assert {"company": {"$eq": "TSMC"}} in conds
    assert {"year": {"$eq": 2024}} in conds
    assert {"quarter": {"$eq": "Q3"}} in conds
    print("  [PASS] test_entities_to_chroma_filter_full")


def test_entities_to_chroma_filter_year_only():
    """Only year → single condition."""
    ent = {k: None for k in ("company", "ticker", "year", "quarter", "metric", "currency", "geography")}
    ent["year"] = 2023
    result = entities_to_chroma_filter(ent)
    assert result == {"year": {"$eq": 2023}}, f"unexpected: {result!r}"
    print("  [PASS] test_entities_to_chroma_filter_year_only")


# ---------------------------------------------------------------------------
# Test 8 – _extract_json edge cases
# ---------------------------------------------------------------------------

def test_extract_json_plain():
    """Plain JSON string without fences."""
    d = _extract_json('{"a": 1, "b": "hello"}')
    assert d == {"a": 1, "b": "hello"}
    print("  [PASS] test_extract_json_plain")


def test_extract_json_with_preamble():
    """LLM adds prose before the JSON object."""
    text = 'Sure, here is the answer:\n{"x": 42}\nHope that helps!'
    d = _extract_json(text)
    assert d == {"x": 42}
    print("  [PASS] test_extract_json_with_preamble")


def test_extract_json_fence():
    """```json fence parsing."""
    text = '```json\n{"key": "value"}\n```'
    d = _extract_json(text)
    assert d == {"key": "value"}
    print("  [PASS] test_extract_json_fence")


def test_extract_json_no_json():
    """Raises ValueError when there is no JSON object."""
    import traceback
    try:
        _extract_json("This is plain text with no JSON.")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("  [PASS] test_extract_json_no_json")


# ---------------------------------------------------------------------------
# Test 9 – merge: LLM overlays regex baseline
# ---------------------------------------------------------------------------

def test_extract_entities_merge():
    """LLM non-null values must override regex values; null LLM values keep regex."""
    # Query has 2021 Q1 in text (regex captures them)
    query = "What was Apple 2021 Q1 revenue?"
    # LLM returns 2021, Q1 matching regex, plus company/metric
    # but year comes back from LLM as 2021 (int) too
    canned_json = json.dumps({
        "company":   "Apple",
        "ticker":    "AAPL",
        "year":      2021,
        "quarter":   "Q1",
        "metric":    "Revenue",
        "currency":  "USD",
        "geography": None,
    })
    ent = extract_entities(query, make_stub(canned_json))
    assert ent["company"]  == "Apple"
    assert ent["year"]     == 2021
    assert ent["quarter"]  == "Q1"
    assert ent["metric"]   == "Revenue"
    assert ent["currency"] == "USD"
    assert ent["geography"] is None  # LLM returned null, remains None
    print("  [PASS] test_extract_entities_merge")


def test_extract_entities_llm_null_keeps_regex():
    """If LLM returns null for year/quarter but regex found them, keep regex values."""
    query = "Show me 2019 Q4 data"
    # LLM returns null for year and quarter (bad extraction)
    canned_json = json.dumps({
        "company": None, "ticker": None, "year": None, "quarter": None,
        "metric": "data", "currency": None, "geography": None,
    })
    ent = extract_entities(query, make_stub(canned_json))
    assert ent["year"]    == 2019, f"regex year should be kept: {ent['year']!r}"
    assert ent["quarter"] == "Q4", f"regex quarter should be kept: {ent['quarter']!r}"
    assert ent["metric"]  == "data"  # LLM non-null, overlaid
    print("  [PASS] test_extract_entities_llm_null_keeps_regex")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    tests = [
        test_translate_happy_path,
        test_translate_markdown_fence,
        test_extract_entities_happy_path,
        test_extract_entities_year_string_coercion,
        test_garbage_llm_fallback,
        test_raising_stub_fallback,
        test_entities_to_chroma_filter_all_none,
        test_entities_to_chroma_filter_single_field,
        test_entities_to_chroma_filter_two_fields,
        test_entities_to_chroma_filter_full,
        test_entities_to_chroma_filter_year_only,
        test_extract_json_plain,
        test_extract_json_with_preamble,
        test_extract_json_fence,
        test_extract_json_no_json,
        test_extract_entities_merge,
        test_extract_entities_llm_null_keeps_regex,
    ]

    print(f"\nRunning {len(tests)} tests...\n")
    failed = []
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"  [FAIL] {t.__name__}: {exc}")
            traceback.print_exc()
            failed.append(t.__name__)

    print()
    if failed:
        print(f"FAILED ({len(failed)}/{len(tests)}): {', '.join(failed)}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    run_all()

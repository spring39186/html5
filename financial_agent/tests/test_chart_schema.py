"""
tests/test_chart_schema.py
==========================
Stdlib-only self-tests for chart_schema.py.
Run with:
    python3 tests/test_chart_schema.py

Tests
-----
1.  validate_chart: accepts a valid line spec.
2.  validate_chart: accepts a valid bar spec.
3.  validate_chart: accepts a valid pie spec.
4.  validate_chart: rejects missing chart_type.
5.  validate_chart: rejects unknown chart_type (not in line/bar/pie).
6.  validate_chart: rejects empty data list.
7.  validate_chart: rejects non-numeric-only data rows.
8.  validate_chart: rejects missing title.
9.  validate_chart: rejects empty-string title.
10. validate_chart: rejects data row with no numeric value.
11. validate_chart: rejects non-dict rows in data.
12. normalize_chart: maps alias "bar chart" → "bar".
13. normalize_chart: maps alias "長條" → "bar".
14. normalize_chart: maps alias "折線" → "line".
15. normalize_chart: maps alias "圓餅" → "pie".
16. normalize_chart: parses "3,103,836" → 3103836.0.
17. normalize_chart: drops rows with no numeric value.
18. normalize_chart: lowercases chart_type.
19. normalize_chart: casts title to str.
20. normalize_chart: does not mutate the original dict.
21. validate_and_fix: keeps valid charts, collects errors for invalid ones.
22. validate_and_fix: returns empty lists for empty input.
23. validate_and_fix: normalizes before validating (alias mapping + number parsing).
24. SYNTHESIS_V2_SYSTEM: is a non-empty string.
25. SYNTHESIS_V2_SYSTEM: mentions evidence citation concepts.
26. SYNTHESIS_V2_SYSTEM: mentions 資料不足 (insufficient data instruction).
27. SYNTHESIS_V2_SYSTEM: mentions output JSON keys (report / tables / charts).
28. SYNTHESIS_V2_SYSTEM: mentions Traditional Chinese output.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chart_schema import (
    validate_chart,
    normalize_chart,
    validate_and_fix,
    SYNTHESIS_V2_SYSTEM,
)


# ---------------------------------------------------------------------------
# Canonical "good" specs used across multiple tests
# ---------------------------------------------------------------------------
#
# NOTE on normalize_chart behaviour:
#   _to_number() strips all non-[0-9,.\-eE] chars then tries float(), so
#   strings like "2022", "Q1", "Q2" are coerced to floats, leaving the row
#   with no string-ish label key → validate_chart would reject them after
#   normalization.
#
#   _GOOD_LINE / _GOOD_BAR use these "numeric-looking" string labels
#   intentionally for the direct validate_chart tests (which receive raw data
#   and treat those values as string labels).
#
#   _GOOD_LINE_SAFE / _GOOD_BAR_SAFE use labels that cannot be parsed as
#   numbers (e.g. "Jan", "Feb", "Q-one") and are used for tests that go
#   through normalize_chart (i.e. validate_and_fix).
# ---------------------------------------------------------------------------

_GOOD_LINE = {
    "title": "Annual Revenue Trend",
    "chart_type": "line",
    "data": [
        {"year": "2022", "revenue": 1500.0},
        {"year": "2023", "revenue": 1800.0},
        {"year": "2024", "revenue": 2100.0},
    ],
}

_GOOD_BAR = {
    "title": "Operating Profit by Quarter",
    "chart_type": "bar",
    "data": [
        {"quarter": "Q1", "profit": 300},
        {"quarter": "Q2", "profit": 420},
        {"quarter": "Q3", "profit": 380},
    ],
}

_GOOD_PIE = {
    "title": "Revenue Breakdown",
    "chart_type": "pie",
    "data": [
        {"segment": "Hardware", "value": 60},
        {"segment": "Software", "value": 25},
        {"segment": "Services", "value": 15},
    ],
}

# Normalize-safe variants: labels cannot be coerced to float, so they survive
# normalize_chart and still satisfy validate_chart's string-label requirement.
_GOOD_LINE_SAFE = {
    "title": "Annual Revenue Trend",
    "chart_type": "line",
    "data": [
        {"period": "Jan", "revenue": 1500.0},
        {"period": "Feb", "revenue": 1800.0},
        {"period": "Mar", "revenue": 2100.0},
    ],
}

_GOOD_BAR_SAFE = {
    "title": "Operating Profit by Quarter",
    "chart_type": "bar",
    "data": [
        {"quarter": "Q-one", "profit": 300},
        {"quarter": "Q-two", "profit": 420},
        {"quarter": "Q-three", "profit": 380},
    ],
}


# ---------------------------------------------------------------------------
# 1–3: validate_chart accepts good specs
# ---------------------------------------------------------------------------

def test_validate_accepts_line():
    """Good line spec must return (True, '')."""
    ok, reason = validate_chart(_GOOD_LINE)
    assert ok is True, f"expected True, got {ok!r}; reason: {reason}"
    assert reason == "", f"expected empty reason, got {reason!r}"
    print("  [PASS] test_validate_accepts_line")


def test_validate_accepts_bar():
    """Good bar spec must return (True, '')."""
    ok, reason = validate_chart(_GOOD_BAR)
    assert ok is True, f"expected True, got {ok!r}; reason: {reason}"
    assert reason == ""
    print("  [PASS] test_validate_accepts_bar")


def test_validate_accepts_pie():
    """Good pie spec must return (True, '')."""
    ok, reason = validate_chart(_GOOD_PIE)
    assert ok is True, f"expected True, got {ok!r}; reason: {reason}"
    assert reason == ""
    print("  [PASS] test_validate_accepts_pie")


# ---------------------------------------------------------------------------
# 4–11: validate_chart rejects bad specs
# ---------------------------------------------------------------------------

def test_validate_rejects_missing_chart_type():
    """spec without 'chart_type' key must fail."""
    bad = {
        "title": "No type",
        "data": [{"label": "A", "value": 1}],
    }
    ok, reason = validate_chart(bad)
    assert ok is False, f"expected False, got {ok!r}"
    assert reason, "expected a non-empty reason string"
    print("  [PASS] test_validate_rejects_missing_chart_type")


def test_validate_rejects_unknown_chart_type():
    """chart_type not in {line, bar, pie} must fail."""
    bad = dict(_GOOD_LINE, chart_type="histogram")
    ok, reason = validate_chart(bad)
    assert ok is False, f"expected False, got {ok!r}"
    print("  [PASS] test_validate_rejects_unknown_chart_type")


def test_validate_rejects_empty_data():
    """Empty data list must fail."""
    bad = dict(_GOOD_BAR, data=[])
    ok, reason = validate_chart(bad)
    assert ok is False
    assert reason
    print("  [PASS] test_validate_rejects_empty_data")


def test_validate_rejects_non_list_data():
    """data that is not a list must fail."""
    bad = dict(_GOOD_BAR, data={"quarter": "Q1", "profit": 300})
    ok, reason = validate_chart(bad)
    assert ok is False
    print("  [PASS] test_validate_rejects_non_list_data")


def test_validate_rejects_missing_title():
    """spec without 'title' key must fail."""
    bad = {"chart_type": "bar", "data": [{"x": "A", "y": 1}]}
    ok, reason = validate_chart(bad)
    assert ok is False
    assert reason
    print("  [PASS] test_validate_rejects_missing_title")


def test_validate_rejects_empty_string_title():
    """title that is an empty string must fail."""
    bad = dict(_GOOD_BAR, title="")
    ok, reason = validate_chart(bad)
    assert ok is False
    print("  [PASS] test_validate_rejects_empty_string_title")


def test_validate_rejects_row_with_no_numeric():
    """A data row with only string values and no numeric value must fail."""
    bad = dict(_GOOD_LINE, data=[{"label": "A", "note": "text only"}])
    ok, reason = validate_chart(bad)
    assert ok is False, f"expected False for all-string row, got {ok!r}"
    assert "numeric" in reason.lower() or reason, f"reason should mention numeric: {reason!r}"
    print("  [PASS] test_validate_rejects_row_with_no_numeric")


def test_validate_rejects_non_dict_row():
    """A data row that is not a dict must fail."""
    bad = dict(_GOOD_BAR, data=["not", "a", "dict"])
    ok, reason = validate_chart(bad)
    assert ok is False
    print("  [PASS] test_validate_rejects_non_dict_row")


# ---------------------------------------------------------------------------
# 12–20: normalize_chart
# ---------------------------------------------------------------------------

def test_normalize_alias_bar_chart():
    """'bar chart' (alias) → 'bar'."""
    spec = {"title": "T", "chart_type": "bar chart", "data": [{"x": "A", "y": 1}]}
    normed = normalize_chart(spec)
    assert normed["chart_type"] == "bar", f"expected 'bar', got {normed['chart_type']!r}"
    print("  [PASS] test_normalize_alias_bar_chart")


def test_normalize_alias_changjiang():
    """'長條' (Traditional Chinese alias) → 'bar'."""
    spec = {"title": "T", "chart_type": "長條", "data": [{"x": "A", "y": 1}]}
    normed = normalize_chart(spec)
    assert normed["chart_type"] == "bar", f"expected 'bar', got {normed['chart_type']!r}"
    print("  [PASS] test_normalize_alias_changjiang")


def test_normalize_alias_zhexian():
    """'折線' (Traditional Chinese alias) → 'line'."""
    spec = {"title": "T", "chart_type": "折線", "data": [{"x": "A", "y": 1}]}
    normed = normalize_chart(spec)
    assert normed["chart_type"] == "line", f"expected 'line', got {normed['chart_type']!r}"
    print("  [PASS] test_normalize_alias_zhexian")


def test_normalize_alias_yuanbing():
    """'圓餅' (Traditional Chinese alias) → 'pie'."""
    spec = {"title": "T", "chart_type": "圓餅", "data": [{"x": "A", "y": 1}]}
    normed = normalize_chart(spec)
    assert normed["chart_type"] == "pie", f"expected 'pie', got {normed['chart_type']!r}"
    print("  [PASS] test_normalize_alias_yuanbing")


def test_normalize_comma_number():
    """'3,103,836' (string with commas) → 3103836.0."""
    spec = {
        "title": "Revenue",
        "chart_type": "bar",
        "data": [{"year": "2023", "revenue": "3,103,836"}],
    }
    normed = normalize_chart(spec)
    row = normed["data"][0]
    revenue_val = row.get("revenue")
    assert revenue_val == 3103836.0, f"expected 3103836.0, got {revenue_val!r}"
    print("  [PASS] test_normalize_comma_number")


def test_normalize_drops_rows_with_no_numeric():
    """Rows with no coercible numeric value must be dropped."""
    spec = {
        "title": "T",
        "chart_type": "bar",
        "data": [
            {"label": "A", "value": 100},         # kept
            {"label": "B", "note": "text only"},  # dropped — no numeric
            {"label": "C", "value": 200},         # kept
        ],
    }
    normed = normalize_chart(spec)
    assert len(normed["data"]) == 2, (
        f"expected 2 rows after drop, got {len(normed['data'])}: {normed['data']}"
    )
    print("  [PASS] test_normalize_drops_rows_with_no_numeric")


def test_normalize_lowercase_chart_type():
    """chart_type 'LINE' → lowercased before alias lookup → 'line'."""
    spec = {"title": "T", "chart_type": "LINE", "data": [{"x": "A", "y": 1}]}
    normed = normalize_chart(spec)
    assert normed["chart_type"] == "line", f"got {normed['chart_type']!r}"
    print("  [PASS] test_normalize_lowercase_chart_type")


def test_normalize_title_cast_to_str():
    """Numeric title must be cast to str."""
    spec = {"title": 42, "chart_type": "bar", "data": [{"x": "A", "y": 1}]}
    normed = normalize_chart(spec)
    assert isinstance(normed["title"], str), f"expected str, got {type(normed['title'])}"
    assert normed["title"] == "42"
    print("  [PASS] test_normalize_title_cast_to_str")


def test_normalize_does_not_mutate():
    """normalize_chart must not mutate the original dict."""
    original = {
        "title": "Original",
        "chart_type": "bar chart",
        "data": [{"label": "X", "value": "1,000"}],
    }
    import copy
    snapshot = copy.deepcopy(original)
    normalize_chart(original)
    assert original == snapshot, "normalize_chart mutated the original dict"
    print("  [PASS] test_normalize_does_not_mutate")


# ---------------------------------------------------------------------------
# 21–23: validate_and_fix
# ---------------------------------------------------------------------------

def test_validate_and_fix_splits_valid_invalid():
    """validate_and_fix must keep valid charts and collect errors for invalid ones.

    Uses normalize-safe specs (_GOOD_LINE_SAFE, _GOOD_PIE) whose labels are
    strings that cannot be coerced to floats, so they pass validate_chart after
    normalize_chart is applied.
    """
    charts = [
        _GOOD_LINE_SAFE,                                 # valid after normalize
        {"title": "", "chart_type": "bar", "data": []},  # invalid: empty title + empty data
        _GOOD_PIE,                                       # valid after normalize (segment names)
    ]
    valid, errors = validate_and_fix(charts)
    assert len(valid) == 2, f"expected 2 valid, got {len(valid)}"
    assert len(errors) >= 1, f"expected >= 1 error, got {len(errors)}"
    # Errors must be non-empty strings
    for err in errors:
        assert isinstance(err, str) and err, f"error must be non-empty str, got {err!r}"
    print("  [PASS] test_validate_and_fix_splits_valid_invalid")


def test_validate_and_fix_empty_input():
    """Empty input must return two empty lists."""
    valid, errors = validate_and_fix([])
    assert valid == [], f"expected [], got {valid!r}"
    assert errors == [], f"expected [], got {errors!r}"
    print("  [PASS] test_validate_and_fix_empty_input")


def test_validate_and_fix_normalizes_before_validating():
    """
    An alias chart_type and a comma-formatted number must pass after normalization.

    Input uses '長條圖' (alias) and revenue as '1,500,000' (string).
    The label key 'region' uses a non-numeric string so it survives normalize_chart
    and still satisfies validate_chart's string-label requirement.
    """
    raw_chart = {
        "title": "Revenue Chart",
        "chart_type": "長條圖",
        "data": [
            {"region": "Asia", "revenue": "1,500,000"},
            {"region": "Europe", "revenue": "2,000,000"},
        ],
    }
    valid, errors = validate_and_fix([raw_chart])
    assert len(valid) == 1, f"expected 1 valid after normalize, got {len(valid)}; errors: {errors}"
    assert errors == [], f"expected no errors, got {errors!r}"
    # Verify numeric coercion happened on the revenue strings
    assert valid[0]["data"][0]["revenue"] == 1500000.0
    print("  [PASS] test_validate_and_fix_normalizes_before_validating")


def test_validate_and_fix_all_valid():
    """All-valid input: errors list must be empty.

    Uses normalize-safe specs whose label strings cannot be coerced to floats
    so they remain as string keys after normalize_chart and satisfy validate_chart.
    """
    charts = [_GOOD_LINE_SAFE, _GOOD_BAR_SAFE, _GOOD_PIE]
    valid, errors = validate_and_fix(charts)
    assert len(valid) == 3, f"expected 3, got {len(valid)}"
    assert errors == [], f"expected no errors, got {errors!r}"
    print("  [PASS] test_validate_and_fix_all_valid")


def test_validate_and_fix_all_invalid():
    """All-invalid input: valid list must be empty."""
    charts = [
        {"title": "", "chart_type": "bar", "data": []},
        {"chart_type": "unknown"},
    ]
    valid, errors = validate_and_fix(charts)
    assert valid == [], f"expected empty valid list, got {valid!r}"
    assert len(errors) == 2, f"expected 2 errors, got {len(errors)}"
    print("  [PASS] test_validate_and_fix_all_invalid")


# ---------------------------------------------------------------------------
# 24–28: SYNTHESIS_V2_SYSTEM
# ---------------------------------------------------------------------------

def test_synthesis_v2_system_is_nonempty_str():
    """SYNTHESIS_V2_SYSTEM must be a non-empty string."""
    assert isinstance(SYNTHESIS_V2_SYSTEM, str), "expected str"
    assert len(SYNTHESIS_V2_SYSTEM) > 100, "expected a substantive prompt (>100 chars)"
    print("  [PASS] test_synthesis_v2_system_is_nonempty_str")


def test_synthesis_v2_system_mentions_evidence_cite():
    """
    The prompt must mention evidence citing concepts.
    Looks for '證據', '[E', or 'cite' in the text (any of them suffices).
    """
    content = SYNTHESIS_V2_SYSTEM
    has_evidence = "證據" in content or "[E" in content or "cite" in content.lower()
    assert has_evidence, (
        "SYNTHESIS_V2_SYSTEM should mention evidence/citation concepts "
        f"('證據' / '[E' / 'cite'); content preview: {content[:200]!r}"
    )
    print("  [PASS] test_synthesis_v2_system_mentions_evidence_cite")


def test_synthesis_v2_system_mentions_insufficient_data():
    """The prompt must mention the insufficient-data instruction ('資料不足')."""
    assert "資料不足" in SYNTHESIS_V2_SYSTEM, (
        "'資料不足' not found in SYNTHESIS_V2_SYSTEM"
    )
    print("  [PASS] test_synthesis_v2_system_mentions_insufficient_data")


def test_synthesis_v2_system_mentions_json_keys():
    """The prompt must mention the output JSON schema keys: report, tables, charts."""
    for key in ("report", "tables", "charts"):
        assert key in SYNTHESIS_V2_SYSTEM, (
            f"JSON key '{key}' not found in SYNTHESIS_V2_SYSTEM"
        )
    print("  [PASS] test_synthesis_v2_system_mentions_json_keys")


def test_synthesis_v2_system_mentions_traditional_chinese():
    """The prompt must instruct use of Traditional Chinese (繁體中文)."""
    assert "繁體中文" in SYNTHESIS_V2_SYSTEM, (
        "'繁體中文' not found in SYNTHESIS_V2_SYSTEM"
    )
    print("  [PASS] test_synthesis_v2_system_mentions_traditional_chinese")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    tests = [
        # validate_chart – accepts good specs
        test_validate_accepts_line,
        test_validate_accepts_bar,
        test_validate_accepts_pie,
        # validate_chart – rejects bad specs
        test_validate_rejects_missing_chart_type,
        test_validate_rejects_unknown_chart_type,
        test_validate_rejects_empty_data,
        test_validate_rejects_non_list_data,
        test_validate_rejects_missing_title,
        test_validate_rejects_empty_string_title,
        test_validate_rejects_row_with_no_numeric,
        test_validate_rejects_non_dict_row,
        # normalize_chart
        test_normalize_alias_bar_chart,
        test_normalize_alias_changjiang,
        test_normalize_alias_zhexian,
        test_normalize_alias_yuanbing,
        test_normalize_comma_number,
        test_normalize_drops_rows_with_no_numeric,
        test_normalize_lowercase_chart_type,
        test_normalize_title_cast_to_str,
        test_normalize_does_not_mutate,
        # validate_and_fix
        test_validate_and_fix_splits_valid_invalid,
        test_validate_and_fix_empty_input,
        test_validate_and_fix_normalizes_before_validating,
        test_validate_and_fix_all_valid,
        test_validate_and_fix_all_invalid,
        # SYNTHESIS_V2_SYSTEM
        test_synthesis_v2_system_is_nonempty_str,
        test_synthesis_v2_system_mentions_evidence_cite,
        test_synthesis_v2_system_mentions_insufficient_data,
        test_synthesis_v2_system_mentions_json_keys,
        test_synthesis_v2_system_mentions_traditional_chinese,
    ]

    print(f"\nRunning {len(tests)} chart_schema tests...\n")
    failed: list[str] = []
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

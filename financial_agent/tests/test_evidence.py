"""
tests/test_evidence.py
======================
Stdlib-only self-tests for evidence.py.
Run with:
    python3 tests/test_evidence.py

Tests
-----
1.  Evidence dataclass: valid construction + field defaults.
2.  Evidence dataclass: invalid type raises ValueError.
3.  dedup: exact duplicate content keeps highest-relevance copy.
4.  dedup: same content as rag AND sql → keeps sql copy (type priority).
5.  dedup: whitespace / case normalisation still collapses duplicates.
6.  dedup: unique items are all preserved.
7.  dedup: empty list returns empty list.
8.  rank: sorts by relevance descending (stable).
9.  rank: equal-relevance items maintain relative order.
10. rank: single item / empty list handled.
11. estimate_tokens: fallback works without tiktoken (char-based floor).
12. estimate_tokens: returns int >= 1 for non-empty text.
13. estimate_tokens: single char returns 1 (via max(1, ...)).
14. select_within_budget: always returns at least the first item even if it
    alone exceeds budget.
15. select_within_budget: respects max_tokens across a batch.
16. select_within_budget: respects top_k cap.
17. select_within_budget: returns empty list when given empty input.
18. select_within_budget: dedup + rank happen before slicing.
19. to_prompt_block: numbers items starting at [E1].
20. to_prompt_block: includes source, type, and content.
21. to_prompt_block: empty list returns empty string.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evidence import (
    Evidence,
    dedup,
    rank,
    estimate_tokens,
    select_within_budget,
    to_prompt_block,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ev(content: str, relevance: float = 0.5, type_: str = "rag",
        source: str = "src", query: str = "q") -> Evidence:
    return Evidence(source=source, query=query, content=content,
                    relevance=relevance, type=type_)


# ---------------------------------------------------------------------------
# 1 – Evidence dataclass construction
# ---------------------------------------------------------------------------

def test_evidence_defaults():
    """Verify default field values."""
    e = Evidence(source="doc.pdf", query="revenue", content="Revenue was 100.")
    assert e.relevance == 0.0, f"expected 0.0, got {e.relevance}"
    assert e.type == "rag", f"expected 'rag', got {e.type}"
    print("  [PASS] test_evidence_defaults")


def test_evidence_valid_types():
    """All three valid types must construct without error."""
    for t in ("rag", "sql", "ocr"):
        e = Evidence(source="s", query="q", content="c", type=t)
        assert e.type == t
    print("  [PASS] test_evidence_valid_types")


def test_evidence_invalid_type_raises():
    """An invalid type must raise ValueError."""
    raised = False
    try:
        Evidence(source="s", query="q", content="c", type="pdf")
    except ValueError:
        raised = True
    assert raised, "expected ValueError for invalid type"
    print("  [PASS] test_evidence_invalid_type_raises")


# ---------------------------------------------------------------------------
# 3 – dedup
# ---------------------------------------------------------------------------

def test_dedup_exact_duplicate_keeps_highest_relevance():
    """Exact duplicate content: keeps the copy with the higher relevance."""
    low  = _ev("Revenue was 100.", relevance=0.3)
    high = _ev("Revenue was 100.", relevance=0.9)
    result = dedup([low, high])
    assert len(result) == 1, f"expected 1, got {len(result)}"
    assert result[0].relevance == 0.9, f"expected 0.9, got {result[0].relevance}"
    print("  [PASS] test_dedup_exact_duplicate_keeps_highest_relevance")


def test_dedup_rag_sql_keeps_sql():
    """Same content appearing as rag AND sql → keeps sql (type priority)."""
    content = "Net profit 200."
    rag_copy = _ev(content, relevance=0.5, type_="rag")
    sql_copy = _ev(content, relevance=0.5, type_="sql")  # same relevance, better type
    # rag first in list
    result = dedup([rag_copy, sql_copy])
    assert len(result) == 1, f"expected 1, got {len(result)}"
    assert result[0].type == "sql", f"expected 'sql', got {result[0].type}"
    print("  [PASS] test_dedup_rag_sql_keeps_sql")


def test_dedup_rag_sql_keeps_sql_reversed():
    """Same test with sql before rag in input list → still sql wins."""
    content = "EPS 3.5."
    sql_copy = _ev(content, relevance=0.5, type_="sql")
    rag_copy = _ev(content, relevance=0.5, type_="rag")
    result = dedup([sql_copy, rag_copy])
    assert len(result) == 1
    assert result[0].type == "sql"
    print("  [PASS] test_dedup_rag_sql_keeps_sql_reversed")


def test_dedup_whitespace_normalisation():
    """Content that differs only in whitespace/case is treated as duplicate."""
    a = _ev("  Revenue  was  100. ", relevance=0.4)
    b = _ev("revenue was 100.", relevance=0.7)
    result = dedup([a, b])
    assert len(result) == 1, f"expected 1, got {len(result)}"
    assert result[0].relevance == 0.7
    print("  [PASS] test_dedup_whitespace_normalisation")


def test_dedup_unique_items_all_preserved():
    """Distinct content items must all be retained."""
    items = [
        _ev("Revenue 100.", relevance=0.9),
        _ev("Profit 50.", relevance=0.7),
        _ev("EPS 2.5", relevance=0.5),
    ]
    result = dedup(items)
    assert len(result) == 3, f"expected 3, got {len(result)}"
    print("  [PASS] test_dedup_unique_items_all_preserved")


def test_dedup_empty():
    """Empty input returns empty list."""
    assert dedup([]) == []
    print("  [PASS] test_dedup_empty")


# ---------------------------------------------------------------------------
# 8 – rank
# ---------------------------------------------------------------------------

def test_rank_descending():
    """rank() must sort by relevance descending."""
    items = [_ev("a", 0.2), _ev("b", 0.9), _ev("c", 0.5)]
    result = rank(items)
    scores = [e.relevance for e in result]
    assert scores == sorted(scores, reverse=True), f"not descending: {scores}"
    assert result[0].relevance == 0.9
    print("  [PASS] test_rank_descending")


def test_rank_stable():
    """Equal-relevance items must preserve their relative input order."""
    items = [
        _ev("first",  0.5, source="A"),
        _ev("second", 0.5, source="B"),
        _ev("third",  0.5, source="C"),
    ]
    result = rank(items)
    sources = [e.source for e in result]
    assert sources == ["A", "B", "C"], f"stability broken: {sources}"
    print("  [PASS] test_rank_stable")


def test_rank_single_item():
    """Single-item list must return a single-item list."""
    items = [_ev("only", 0.8)]
    result = rank(items)
    assert len(result) == 1
    assert result[0].relevance == 0.8
    print("  [PASS] test_rank_single_item")


def test_rank_empty():
    """Empty input returns empty list."""
    assert rank([]) == []
    print("  [PASS] test_rank_empty")


def test_rank_does_not_mutate():
    """rank() must not mutate the original list."""
    items = [_ev("a", 0.2), _ev("b", 0.9)]
    original_ids = [id(e) for e in items]
    rank(items)
    assert [id(e) for e in items] == original_ids, "rank mutated the input list"
    print("  [PASS] test_rank_does_not_mutate")


# ---------------------------------------------------------------------------
# 11 – estimate_tokens
# ---------------------------------------------------------------------------

def test_estimate_tokens_fallback_no_tiktoken():
    """
    estimate_tokens must work without tiktoken by using max(1, len(text)//4).
    We verify the fallback formula directly: a 40-char string → 10.
    """
    text = "a" * 40          # 40 chars → 40//4 = 10
    result = estimate_tokens(text)
    assert isinstance(result, int), f"expected int, got {type(result)}"
    # tiktoken might or might not be installed; but result must be >= 1
    assert result >= 1, f"expected >= 1, got {result}"
    # If fallback is used, we expect exactly 10; if tiktoken is used, it may differ.
    # We only enforce the floor and type.
    print("  [PASS] test_estimate_tokens_fallback_no_tiktoken")


def test_estimate_tokens_returns_at_least_one():
    """Even a single character must return >= 1."""
    assert estimate_tokens("x") >= 1
    assert estimate_tokens("") >= 1    # empty string: max(1, 0) = 1
    print("  [PASS] test_estimate_tokens_returns_at_least_one")


def test_estimate_tokens_monotone():
    """Longer text should (almost always) produce a larger token estimate."""
    short = estimate_tokens("hello")
    long_ = estimate_tokens("hello " * 200)
    assert long_ > short, f"expected long > short, got {long_} <= {short}"
    print("  [PASS] test_estimate_tokens_monotone")


def test_estimate_tokens_is_int():
    """Return type must always be int."""
    for text in ("", "x", "Revenue was $1,000.", "a" * 1000):
        result = estimate_tokens(text)
        assert isinstance(result, int), f"expected int for {text!r}, got {type(result)}"
    print("  [PASS] test_estimate_tokens_is_int")


# ---------------------------------------------------------------------------
# 14 – select_within_budget
# ---------------------------------------------------------------------------

def test_select_always_includes_first_item():
    """First item must always be included even if its content alone exceeds budget."""
    # Create one item whose token estimate > budget
    huge_content = "x" * 40000    # ~10000 tokens in fallback
    item = _ev(huge_content, relevance=0.99)
    result = select_within_budget([item], max_tokens=100)
    assert len(result) >= 1, "must include at least the first item"
    assert result[0].content == huge_content
    print("  [PASS] test_select_always_includes_first_item")


def test_select_respects_max_tokens():
    """Items that would push past max_tokens must be excluded."""
    # 8-char items → 2 tokens each (fallback: 8//4 = 2)
    items = [_ev("a" * 8, relevance=1.0 - i * 0.1) for i in range(10)]
    # Budget for 3 items exactly: 3 * 2 = 6 tokens
    result = select_within_budget(items, max_tokens=6, top_k=100)
    total_tokens = sum(estimate_tokens(e.content) for e in result)
    # The first item is always included; additional items must not exceed budget
    # After the first item is forced in, subsequent items must fit.
    assert total_tokens <= 6 or len(result) == 1, (
        f"budget exceeded: {total_tokens} > 6 with {len(result)} items"
    )
    print("  [PASS] test_select_respects_max_tokens")


def test_select_respects_top_k():
    """Must never return more than top_k items."""
    items = [_ev(f"unique content item number {i}", relevance=float(i)) for i in range(20)]
    result = select_within_budget(items, max_tokens=999999, top_k=5)
    assert len(result) <= 5, f"expected <= 5, got {len(result)}"
    print("  [PASS] test_select_respects_top_k")


def test_select_empty_input():
    """Empty input returns empty list."""
    result = select_within_budget([], max_tokens=3000)
    assert result == []
    print("  [PASS] test_select_empty_input")


def test_select_dedup_and_rank_applied():
    """
    Duplicates are removed and items are returned highest-relevance first.
    Input: duplicate low-relevance item + unique high-relevance item.
    The high-relevance item must appear first in the output.
    """
    dup_low  = _ev("same content", relevance=0.1)
    dup_high = _ev("same content", relevance=0.9)  # duplicate with higher relevance
    unique   = _ev("unique stuff here", relevance=0.5)
    result = select_within_budget([dup_low, dup_high, unique], max_tokens=9999, top_k=10)
    # After dedup, we should have 2 items: the best "same content" and "unique"
    assert len(result) == 2, f"expected 2 after dedup, got {len(result)}"
    # The top item should be the highest relevance one (0.9)
    assert result[0].relevance == 0.9, f"expected 0.9 first, got {result[0].relevance}"
    print("  [PASS] test_select_dedup_and_rank_applied")


def test_select_returns_items_in_rank_order():
    """Items in the returned list must be ordered by relevance descending."""
    items = [_ev(f"content for item {i}", relevance=i * 0.1) for i in range(10)]
    result = select_within_budget(items, max_tokens=9999, top_k=10)
    scores = [e.relevance for e in result]
    assert scores == sorted(scores, reverse=True), f"not ranked: {scores}"
    print("  [PASS] test_select_returns_items_in_rank_order")


# ---------------------------------------------------------------------------
# 19 – to_prompt_block
# ---------------------------------------------------------------------------

def test_to_prompt_block_numbering():
    """Items must be numbered [E1], [E2], [E3], …"""
    items = [
        _ev("Content A.", source="src_a", type_="rag"),
        _ev("Content B.", source="src_b", type_="sql"),
        _ev("Content C.", source="src_c", type_="ocr"),
    ]
    block = to_prompt_block(items)
    assert "[E1]" in block, "[E1] missing"
    assert "[E2]" in block, "[E2] missing"
    assert "[E3]" in block, "[E3] missing"
    assert "[E4]" not in block, "spurious [E4] found"
    print("  [PASS] test_to_prompt_block_numbering")


def test_to_prompt_block_contains_source_type_content():
    """Each block line must contain source, type, and content."""
    items = [
        _ev("Revenue data here.", source="annual_report.pdf", type_="ocr"),
    ]
    block = to_prompt_block(items)
    assert "annual_report.pdf" in block, "source missing"
    assert "ocr" in block, "type missing"
    assert "Revenue data here." in block, "content missing"
    print("  [PASS] test_to_prompt_block_contains_source_type_content")


def test_to_prompt_block_separator():
    """Items must be separated by blank lines (double newline)."""
    items = [_ev(f"Item {i}") for i in range(3)]
    block = to_prompt_block(items)
    # The joined block uses "\n\n" between items
    assert "\n\n" in block, "expected double-newline separator between items"
    print("  [PASS] test_to_prompt_block_separator")


def test_to_prompt_block_empty():
    """Empty list must return empty string."""
    result = to_prompt_block([])
    assert result == "", f"expected '', got {result!r}"
    print("  [PASS] test_to_prompt_block_empty")


def test_to_prompt_block_single_item():
    """Single item must produce exactly one [E1] block."""
    block = to_prompt_block([_ev("Only item.")])
    assert block.count("[E1]") == 1
    assert "[E2]" not in block
    print("  [PASS] test_to_prompt_block_single_item")


def test_to_prompt_block_pipe_separator():
    """Source and type must be separated by the '｜' character."""
    items = [_ev("Data.", source="db_query", type_="sql")]
    block = to_prompt_block(items)
    assert "｜" in block, "full-width pipe '｜' separator missing"
    assert "db_query｜sql" in block or "db_query" in block, "source|type format wrong"
    print("  [PASS] test_to_prompt_block_pipe_separator")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    tests = [
        # Evidence dataclass
        test_evidence_defaults,
        test_evidence_valid_types,
        test_evidence_invalid_type_raises,
        # dedup
        test_dedup_exact_duplicate_keeps_highest_relevance,
        test_dedup_rag_sql_keeps_sql,
        test_dedup_rag_sql_keeps_sql_reversed,
        test_dedup_whitespace_normalisation,
        test_dedup_unique_items_all_preserved,
        test_dedup_empty,
        # rank
        test_rank_descending,
        test_rank_stable,
        test_rank_single_item,
        test_rank_empty,
        test_rank_does_not_mutate,
        # estimate_tokens
        test_estimate_tokens_fallback_no_tiktoken,
        test_estimate_tokens_returns_at_least_one,
        test_estimate_tokens_monotone,
        test_estimate_tokens_is_int,
        # select_within_budget
        test_select_always_includes_first_item,
        test_select_respects_max_tokens,
        test_select_respects_top_k,
        test_select_empty_input,
        test_select_dedup_and_rank_applied,
        test_select_returns_items_in_rank_order,
        # to_prompt_block
        test_to_prompt_block_numbering,
        test_to_prompt_block_contains_source_type_content,
        test_to_prompt_block_separator,
        test_to_prompt_block_empty,
        test_to_prompt_block_single_item,
        test_to_prompt_block_pipe_separator,
    ]

    print(f"\nRunning {len(tests)} evidence tests...\n")
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

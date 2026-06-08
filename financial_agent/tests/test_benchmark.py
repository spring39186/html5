"""
tests/test_benchmark.py — Self-tests for benchmark.py (Phase 10)
=================================================================
Stdlib-only. No pytest required — run directly:
    python3 tests/test_benchmark.py
All tests are hand-checked against known values.
"""

from __future__ import annotations

import math
import sys
import os
import time

# Allow running from repo root or from inside tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from benchmark import (
    cache_hit_ratio,
    compare_systems,
    evaluate,
    mrr,
    ndcg_at_k,
    pages_per_second,
    recall_at_k,
    Timer,
)


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _approx_equal(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def _assert(condition: bool, msg: str) -> None:
    if not condition:
        raise AssertionError(msg)


# ─────────────────────────────────────────────────────────────────
# recall_at_k
# ─────────────────────────────────────────────────────────────────

def test_recall_at_k_basic() -> None:
    """Relevant doc is at rank 2 — not in top-1, in top-5."""
    retrieved = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    relevant = {"b"}

    _assert(
        _approx_equal(recall_at_k(retrieved, relevant, k=1), 0.0),
        "recall@1 should be 0 when the only relevant doc is at rank 2",
    )
    _assert(
        _approx_equal(recall_at_k(retrieved, relevant, k=5), 1.0),
        "recall@5 should be 1 when the only relevant doc is in top-5",
    )


def test_recall_at_k_partial() -> None:
    """Two relevant docs, one found in top-3."""
    retrieved = ["x", "y", "z"]
    relevant = {"y", "w"}  # "w" not retrieved at all

    score = recall_at_k(retrieved, relevant, k=3)
    # 1 hit / 2 relevant = 0.5
    _assert(_approx_equal(score, 0.5), f"Expected 0.5, got {score}")


def test_recall_at_k_empty_relevant() -> None:
    _assert(
        _approx_equal(recall_at_k(["a", "b"], set(), k=5), 0.0),
        "recall should be 0.0 for empty relevant set",
    )


def test_recall_at_k_k_larger_than_list() -> None:
    retrieved = ["a", "b"]
    relevant = {"a"}
    _assert(
        _approx_equal(recall_at_k(retrieved, relevant, k=100), 1.0),
        "k larger than list length should still work",
    )


# ─────────────────────────────────────────────────────────────────
# mrr
# ─────────────────────────────────────────────────────────────────

def test_mrr_first_rank() -> None:
    retrieved = ["a", "b", "c"]
    relevant = {"a"}
    _assert(_approx_equal(mrr(retrieved, relevant), 1.0), "rank-1 → mrr 1.0")


def test_mrr_second_rank() -> None:
    """Classic: first relevant at rank 2 → mrr = 0.5."""
    retrieved = ["x", "b", "c"]
    relevant = {"b"}
    score = mrr(retrieved, relevant)
    _assert(_approx_equal(score, 0.5), f"rank-2 → mrr 0.5, got {score}")


def test_mrr_third_rank() -> None:
    retrieved = ["x", "y", "c"]
    relevant = {"c"}
    score = mrr(retrieved, relevant)
    expected = 1.0 / 3.0
    _assert(
        _approx_equal(score, expected, tol=1e-9),
        f"rank-3 → mrr {expected:.6f}, got {score}",
    )


def test_mrr_not_found() -> None:
    retrieved = ["x", "y", "z"]
    relevant = {"q"}
    _assert(_approx_equal(mrr(retrieved, relevant), 0.0), "no match → mrr 0.0")


def test_mrr_empty_list() -> None:
    _assert(_approx_equal(mrr([], {"a"}), 0.0), "empty retrieved → mrr 0.0")


# ─────────────────────────────────────────────────────────────────
# ndcg_at_k
# ─────────────────────────────────────────────────────────────────

def test_ndcg_perfect_ranking() -> None:
    """All relevant docs ranked first → NDCG = 1.0."""
    retrieved = ["a", "b", "c", "x", "y"]
    relevant = {"a", "b", "c"}
    score = ndcg_at_k(retrieved, relevant, k=5)
    _assert(_approx_equal(score, 1.0), f"perfect ranking → ndcg 1.0, got {score}")


def test_ndcg_zero_when_no_match() -> None:
    retrieved = ["x", "y", "z"]
    relevant = {"a", "b"}
    score = ndcg_at_k(retrieved, relevant, k=5)
    _assert(_approx_equal(score, 0.0), f"no match → ndcg 0.0, got {score}")


def test_ndcg_empty_relevant() -> None:
    score = ndcg_at_k(["a", "b"], set(), k=5)
    _assert(_approx_equal(score, 0.0), "empty relevant → ndcg 0.0")


def test_ndcg_single_relevant_at_rank_2() -> None:
    """
    retrieved = ["x", "a"], relevant = {"a"}, k=5
    DCG  = 1/log2(3)  (position 2, discount = log2(2+1) = log2(3))
    IDCG = 1/log2(2)  (ideal: "a" at rank 1)
    NDCG = DCG / IDCG = log2(2) / log2(3)
    """
    retrieved = ["x", "a", "y", "z", "w"]
    relevant = {"a"}
    score = ndcg_at_k(retrieved, relevant, k=5)
    expected = math.log2(2) / math.log2(3)
    _assert(
        _approx_equal(score, expected, tol=1e-9),
        f"single relevant at rank 2 → ndcg {expected:.6f}, got {score}",
    )
    _assert(0.0 < score < 1.0, "ndcg must be strictly between 0 and 1 here")


def test_ndcg_in_range() -> None:
    """NDCG must always be in [0, 1]."""
    retrieved = ["a", "c", "b", "d", "e"]
    relevant = {"b", "c"}
    score = ndcg_at_k(retrieved, relevant, k=5)
    _assert(0.0 <= score <= 1.0, f"ndcg out of [0,1]: {score}")


def test_ndcg_k_cutoff() -> None:
    """Relevant doc at rank 6 should not affect ndcg@5."""
    retrieved = ["x", "y", "z", "w", "v", "a"]
    relevant = {"a"}
    score = ndcg_at_k(retrieved, relevant, k=5)
    _assert(_approx_equal(score, 0.0), f"relevant beyond k → ndcg@5 = 0, got {score}")


# ─────────────────────────────────────────────────────────────────
# evaluate
# ─────────────────────────────────────────────────────────────────

def test_evaluate_keys() -> None:
    """evaluate() must return exactly the right keys for ks=(5, 10)."""
    result = evaluate(["a", "b"], {"a"}, ks=(5, 10))
    expected_keys = {"recall@5", "recall@10", "mrr", "ndcg@5", "ndcg@10"}
    _assert(set(result.keys()) == expected_keys, f"unexpected keys: {set(result.keys())}")


def test_evaluate_keys_custom_ks() -> None:
    result = evaluate(["a"], {"a"}, ks=(1, 3))
    expected_keys = {"recall@1", "recall@3", "mrr", "ndcg@1", "ndcg@3"}
    _assert(set(result.keys()) == expected_keys, f"unexpected keys for custom ks: {set(result.keys())}")


def test_evaluate_values_consistent() -> None:
    """Values must be consistent with individual metric calls."""
    retrieved = ["x", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    relevant = {"b"}
    result = evaluate(retrieved, relevant, ks=(5, 10))

    _assert(
        _approx_equal(result["recall@5"], recall_at_k(retrieved, relevant, 5)),
        "recall@5 mismatch",
    )
    _assert(
        _approx_equal(result["recall@10"], recall_at_k(retrieved, relevant, 10)),
        "recall@10 mismatch",
    )
    _assert(_approx_equal(result["mrr"], mrr(retrieved, relevant)), "mrr mismatch")
    _assert(
        _approx_equal(result["ndcg@5"], ndcg_at_k(retrieved, relevant, 5)),
        "ndcg@5 mismatch",
    )
    _assert(
        _approx_equal(result["ndcg@10"], ndcg_at_k(retrieved, relevant, 10)),
        "ndcg@10 mismatch",
    )


# ─────────────────────────────────────────────────────────────────
# compare_systems
# ─────────────────────────────────────────────────────────────────

def test_compare_systems_single_query() -> None:
    """One system, one query — averaged metrics == per-query metrics."""
    retrieved = ["a", "b", "c"]
    relevant = {"b"}
    systems = {"sys_a": [(retrieved, relevant)]}
    result = compare_systems(systems, ks=(5,))
    per_query = evaluate(retrieved, relevant, ks=(5,))

    for key, val in per_query.items():
        _assert(
            _approx_equal(result["sys_a"][key], val, tol=1e-9),
            f"compare_systems single-query mismatch on {key}: "
            f"{result['sys_a'][key]} != {val}",
        )


def test_compare_systems_averaging() -> None:
    """Two queries whose mrr values are 1.0 and 0.5 → average 0.75."""
    systems = {
        "current": [
            (["a", "b"], {"a"}),   # mrr = 1.0
            (["x", "b"], {"b"}),   # mrr = 0.5
        ]
    }
    result = compare_systems(systems, ks=(5,))
    expected_mrr = (1.0 + 0.5) / 2  # 0.75
    _assert(
        _approx_equal(result["current"]["mrr"], expected_mrr, tol=1e-9),
        f"averaged mrr should be 0.75, got {result['current']['mrr']}",
    )


def test_compare_systems_multiple_systems() -> None:
    """'current' should have lower avg mrr than 'hybrid'."""
    systems = {
        "current": [
            (["x", "a"], {"a"}),   # mrr = 0.5
        ],
        "hybrid": [
            (["a", "x"], {"a"}),   # mrr = 1.0
        ],
    }
    result = compare_systems(systems, ks=(5,))
    _assert("current" in result and "hybrid" in result, "both systems must be in result")
    _assert(
        result["current"]["mrr"] < result["hybrid"]["mrr"],
        "hybrid should have higher mrr than current",
    )


def test_compare_systems_empty_case_list() -> None:
    """Empty query list returns zero metrics without crashing."""
    result = compare_systems({"empty_sys": []}, ks=(5,))
    _assert("empty_sys" in result, "empty system must be present")
    for val in result["empty_sys"].values():
        _assert(_approx_equal(val, 0.0), f"expected 0.0 for empty, got {val}")


# ─────────────────────────────────────────────────────────────────
# Timer
# ─────────────────────────────────────────────────────────────────

def test_timer_measures_elapsed() -> None:
    """Timer.elapsed should be >= 0 and reflect a real (short) delay."""
    with Timer() as t:
        time.sleep(0.05)
    _assert(t.elapsed >= 0.04, f"elapsed too small: {t.elapsed}")
    _assert(t.elapsed < 5.0, f"elapsed unreasonably large: {t.elapsed}")


def test_timer_zero_work() -> None:
    """A noop block should still record a non-negative elapsed time."""
    with Timer() as t:
        pass
    _assert(t.elapsed >= 0.0, f"elapsed negative: {t.elapsed}")


# ─────────────────────────────────────────────────────────────────
# pages_per_second
# ─────────────────────────────────────────────────────────────────

def test_pages_per_second_basic() -> None:
    pps = pages_per_second(10, 2.0)
    _assert(_approx_equal(pps, 5.0), f"10 pages / 2 s = 5.0, got {pps}")


def test_pages_per_second_zero_seconds() -> None:
    pps = pages_per_second(100, 0.0)
    _assert(_approx_equal(pps, 0.0), f"0 seconds → 0.0, got {pps}")


def test_pages_per_second_negative_seconds() -> None:
    pps = pages_per_second(5, -1.0)
    _assert(_approx_equal(pps, 0.0), f"negative seconds → 0.0, got {pps}")


# ─────────────────────────────────────────────────────────────────
# cache_hit_ratio
# ─────────────────────────────────────────────────────────────────

def test_cache_hit_ratio_basic() -> None:
    ratio = cache_hit_ratio(75, 100)
    _assert(_approx_equal(ratio, 0.75), f"75/100 = 0.75, got {ratio}")


def test_cache_hit_ratio_all_hits() -> None:
    ratio = cache_hit_ratio(50, 50)
    _assert(_approx_equal(ratio, 1.0), f"50/50 = 1.0, got {ratio}")


def test_cache_hit_ratio_zero_total() -> None:
    ratio = cache_hit_ratio(0, 0)
    _assert(_approx_equal(ratio, 0.0), f"0/0 → 0.0, got {ratio}")


def test_cache_hit_ratio_zero_hits() -> None:
    ratio = cache_hit_ratio(0, 100)
    _assert(_approx_equal(ratio, 0.0), f"0/100 = 0.0, got {ratio}")


# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        # recall_at_k
        test_recall_at_k_basic,
        test_recall_at_k_partial,
        test_recall_at_k_empty_relevant,
        test_recall_at_k_k_larger_than_list,
        # mrr
        test_mrr_first_rank,
        test_mrr_second_rank,
        test_mrr_third_rank,
        test_mrr_not_found,
        test_mrr_empty_list,
        # ndcg_at_k
        test_ndcg_perfect_ranking,
        test_ndcg_zero_when_no_match,
        test_ndcg_empty_relevant,
        test_ndcg_single_relevant_at_rank_2,
        test_ndcg_in_range,
        test_ndcg_k_cutoff,
        # evaluate
        test_evaluate_keys,
        test_evaluate_keys_custom_ks,
        test_evaluate_values_consistent,
        # compare_systems
        test_compare_systems_single_query,
        test_compare_systems_averaging,
        test_compare_systems_multiple_systems,
        test_compare_systems_empty_case_list,
        # Timer
        test_timer_measures_elapsed,
        test_timer_zero_work,
        # pages_per_second
        test_pages_per_second_basic,
        test_pages_per_second_zero_seconds,
        test_pages_per_second_negative_seconds,
        # cache_hit_ratio
        test_cache_hit_ratio_basic,
        test_cache_hit_ratio_all_hits,
        test_cache_hit_ratio_zero_total,
        test_cache_hit_ratio_zero_hits,
    ]

    for test_fn in tests:
        test_fn()

    print("ALL TESTS PASSED")

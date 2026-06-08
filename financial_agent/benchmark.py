"""
benchmark.py — Phase 10: Retrieval + OCR + System Metrics
==========================================================
Stdlib-only. No numpy, no third-party dependencies.

Retrieval Metrics
-----------------
All retrieval functions operate on:
  retrieved_ids : list[str]  — ranked best-first
  relevant_ids  : set[str]   — ground-truth relevant document IDs

Functions
---------
recall_at_k(retrieved_ids, relevant_ids, k) -> float
mrr(retrieved_ids, relevant_ids) -> float
ndcg_at_k(retrieved_ids, relevant_ids, k) -> float
evaluate(retrieved_ids, relevant_ids, ks=(5, 10)) -> dict
compare_systems(results_by_system, ks=(5, 10)) -> dict

OCR / System Metrics
--------------------
Timer            — context manager; .elapsed gives wall-clock seconds
pages_per_second(num_pages, seconds) -> float
cache_hit_ratio(hits, total) -> float
"""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from typing import Generator


# ═══════════════════════════════════════════════════════════════
# 1. Retrieval Metrics
# ═══════════════════════════════════════════════════════════════


def recall_at_k(
    retrieved_ids: list[str],
    relevant_ids: set[str],
    k: int,
) -> float:
    """
    Fraction of relevant documents found in the top-k retrieved results.

    Returns 0.0 when relevant_ids is empty.
    """
    if not relevant_ids:
        return 0.0
    top_k = set(retrieved_ids[:k])
    hits = len(top_k & relevant_ids)
    return hits / len(relevant_ids)


def mrr(
    retrieved_ids: list[str],
    relevant_ids: set[str],
) -> float:
    """
    Mean Reciprocal Rank — reciprocal of the rank of the first relevant
    document.  Returns 0.0 if no relevant document appears in the list.

    Rank is 1-based: first position → 1/1 = 1.0,
                      second position → 1/2 = 0.5, etc.
    """
    for rank, doc_id in enumerate(retrieved_ids, start=1):
        if doc_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    retrieved_ids: list[str],
    relevant_ids: set[str],
    k: int,
) -> float:
    """
    Normalised Discounted Cumulative Gain at k with binary gains.

    Gain   : 1 if the document at position i is relevant, else 0.
    DCG    : Σ gain_i / log2(i + 1)   (positions 1-indexed, log2(2)=1 at pos 1)
    Ideal  : DCG computed on the best possible ranking (all relevant first).
    NDCG@k : DCG@k / IDCG@k  (0.0 when IDCG is 0).
    """
    if not relevant_ids:
        return 0.0

    top_k = retrieved_ids[:k]

    # Actual DCG
    dcg = 0.0
    for i, doc_id in enumerate(top_k, start=1):
        if doc_id in relevant_ids:
            dcg += 1.0 / math.log2(i + 1)

    # Ideal DCG: place all relevant docs first, up to k
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))

    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def evaluate(
    retrieved_ids: list[str],
    relevant_ids: set[str],
    ks: tuple[int, ...] = (5, 10),
) -> dict[str, float]:
    """
    Compute a bundle of retrieval metrics for a single query.

    Returns a dict with keys:
      "recall@{k}"  for each k in ks
      "mrr"
      "ndcg@{k}"    for each k in ks

    Example with ks=(5, 10):
      {"recall@5": ..., "recall@10": ..., "mrr": ..., "ndcg@5": ..., "ndcg@10": ...}
    """
    result: dict[str, float] = {}
    for k in ks:
        result[f"recall@{k}"] = recall_at_k(retrieved_ids, relevant_ids, k)
    result["mrr"] = mrr(retrieved_ids, relevant_ids)
    for k in ks:
        result[f"ndcg@{k}"] = ndcg_at_k(retrieved_ids, relevant_ids, k)
    return result


def compare_systems(
    results_by_system: dict[str, list[tuple[list[str], set[str]]]],
    ks: tuple[int, ...] = (5, 10),
) -> dict[str, dict[str, float]]:
    """
    Average retrieval metrics across multiple queries for each system.

    Parameters
    ----------
    results_by_system : {system_name: [(retrieved_ids, relevant_ids), ...]}
        Each entry is a (retrieved_ids, relevant_ids) pair for one query.
    ks : tuple of cut-off ranks for recall and NDCG.

    Returns
    -------
    {system_name: averaged_metrics_dict}

    Useful for comparing, e.g., "current" vs "hybrid" retrieval strategies.
    """
    comparison: dict[str, dict[str, float]] = {}
    for system_name, query_cases in results_by_system.items():
        if not query_cases:
            # Return zero metrics for an empty case list
            comparison[system_name] = evaluate([], set(), ks)
            continue

        # Accumulate
        totals: dict[str, float] = {}
        for retrieved_ids, relevant_ids in query_cases:
            metrics = evaluate(retrieved_ids, relevant_ids, ks)
            for key, val in metrics.items():
                totals[key] = totals.get(key, 0.0) + val

        n = len(query_cases)
        comparison[system_name] = {key: total / n for key, total in totals.items()}

    return comparison


# ═══════════════════════════════════════════════════════════════
# 2. OCR / System Metrics
# ═══════════════════════════════════════════════════════════════


class Timer:
    """
    Context manager that measures wall-clock elapsed time.

    Usage
    -----
    with Timer() as t:
        do_work()
    print(t.elapsed)   # seconds as float
    """

    def __init__(self) -> None:
        self._start: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *_args: object) -> None:
        self.elapsed = time.monotonic() - self._start


def pages_per_second(num_pages: int, seconds: float) -> float:
    """
    OCR throughput: pages processed divided by elapsed seconds.

    Returns 0.0 when seconds <= 0 to avoid ZeroDivisionError.
    """
    if seconds <= 0:
        return 0.0
    return num_pages / seconds


def cache_hit_ratio(hits: int, total: int) -> float:
    """
    Fraction of cache lookups that were hits.

    Returns 0.0 when total <= 0.
    """
    if total <= 0:
        return 0.0
    return hits / total

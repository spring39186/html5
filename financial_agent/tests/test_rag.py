"""
tests/test_rag.py  —  stdlib-only self-test for rag.py
=======================================================
Run from the financial_agent directory:
    python3 tests/test_rag.py

Tests cover:
1.  rewrite_query — happy-path: subqueries capped, HyDE appended.
2.  rewrite_query — garbage LLM response: fallback to [query].
3.  rag_search — multi-query fusion produces deduped top_k, unique ids,
                 length <= top_k, where_filter passed through to dense_search.
4.  rag_search — stub cross_encode (reverses order): rerank changes ordering.
5.  Cost-knob: max_subqueries limits dense_search call count (counter stub).
6.  to_evidence — builds Evidence objects with correct fields.
"""

import sys
import os

# Allow importing sibling modules when running from repo root or tests/
_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_HERE)
for _p in (_AGENT_DIR, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json

from rag import (
    RagConfig,
    RagDeps,
    rewrite_query,
    rag_search,
    to_evidence,
)


# ─────────────────────────────────────────────────────────────
# Stub helpers
# ─────────────────────────────────────────────────────────────

def _llm_ok(messages, temperature=0.1):
    """Returns a well-formed JSON with 3 rewrites and a hyde passage."""
    return json.dumps({
        "rewrites": [
            "TSMC revenue FY2023",
            "TSMC net income Q4 2023",
            "Taiwan Semiconductor earnings per share 2023",
            "TSMC gross margin fiscal year 2023",  # 4th — should be capped to max_subqueries
        ],
        "hyde": "In fiscal year 2023 TSMC reported revenue of NT$2.16 trillion with operating margin of 42%.",
    })


def _llm_garbage(messages, temperature=0.1):
    """Returns non-JSON garbage to trigger the fallback path."""
    return "Sorry I cannot help with that request today!!"


def _make_dense_search(hits_map: dict, call_counter: list | None = None,
                       captured_filters: list | None = None):
    """
    Factory: returns a dense_search stub.
    hits_map: {subquery_string -> list[hit_dict]}  (exact-match on subquery)
    When the subquery isn't in hits_map, returns a generic set of 5 docs.
    call_counter: if provided, its first element is incremented per call.
    captured_filters: if provided, appended with the where_filter per call.
    """
    def dense_search(query: str, n: int, where_filter=None):
        if call_counter is not None:
            call_counter[0] += 1
        if captured_filters is not None:
            captured_filters.append(where_filter)

        if query in hits_map:
            return hits_map[query][:n]

        # Default: 5 docs with ids derived from the query hash
        h = abs(hash(query)) % 10000
        return [
            {"id": f"doc_{h}_{i}", "text": f"text for {query} chunk {i}",
             "metadata": {"source_file": "file.pdf", "chunk_index": i}, "score": 1.0 - i * 0.05}
            for i in range(min(n, 5))
        ]
    return dense_search


# ─────────────────────────────────────────────────────────────
# Test 1: rewrite_query — happy path
# ─────────────────────────────────────────────────────────────

def test_rewrite_query_happy():
    cfg = RagConfig(max_subqueries=3, use_hyde=True)
    deps = RagDeps(llm_call=_llm_ok, dense_search=_make_dense_search({}))

    result = rewrite_query("TSMC 2023年財務表現", deps, cfg)

    rewrites = result["rewrites"]
    all_queries = result["all_queries"]
    hyde = result["hyde"]

    # rewrites capped to max_subqueries=3
    assert len(rewrites) == 3, f"Expected 3 rewrites, got {len(rewrites)}: {rewrites}"
    # HyDE should be non-empty
    assert isinstance(hyde, str) and hyde.strip(), "Expected non-empty hyde"
    # all_queries = rewrites + 1 hyde = 4 total
    assert len(all_queries) == 4, (
        f"Expected 4 all_queries (3 rewrites + 1 hyde), got {len(all_queries)}: {all_queries}"
    )
    # hyde is the last item in all_queries
    assert all_queries[-1] == hyde.strip(), "HyDE should be last in all_queries"
    # All rewrites should be strings
    for r in rewrites:
        assert isinstance(r, str) and r.strip(), f"Rewrite should be non-empty string: {r!r}"

    print("  [PASS] test_rewrite_query_happy")


# ─────────────────────────────────────────────────────────────
# Test 2: rewrite_query — garbage LLM → fallback
# ─────────────────────────────────────────────────────────────

def test_rewrite_query_fallback():
    raw_query = "What is TSMC operating margin 2023?"
    cfg = RagConfig(max_subqueries=3, use_hyde=True)
    deps = RagDeps(llm_call=_llm_garbage, dense_search=_make_dense_search({}))

    result = rewrite_query(raw_query, deps, cfg)

    assert result["rewrites"] == [raw_query], (
        f"Fallback should return [original_query], got {result['rewrites']}"
    )
    assert result["hyde"] == "", f"Fallback hyde should be '', got {result['hyde']!r}"
    assert result["all_queries"] == [raw_query], (
        f"Fallback all_queries should be [original_query], got {result['all_queries']}"
    )

    print("  [PASS] test_rewrite_query_fallback")


# ─────────────────────────────────────────────────────────────
# Test 3: rag_search — multi-query fusion, dedup, top_k, where_filter
# ─────────────────────────────────────────────────────────────

def test_rag_search_basic():
    # Two subqueries that share some doc ids (to test dedup via RRF)
    hits_q1 = [
        {"id": "doc_A", "text": "revenue text A", "metadata": {"source_file": "r.pdf"}, "score": 0.9},
        {"id": "doc_B", "text": "revenue text B", "metadata": {"source_file": "r.pdf"}, "score": 0.8},
        {"id": "doc_C", "text": "revenue text C", "metadata": {"source_file": "r.pdf"}, "score": 0.7},
    ]
    hits_q2 = [
        {"id": "doc_B", "text": "revenue text B", "metadata": {"source_file": "r.pdf"}, "score": 0.85},
        {"id": "doc_D", "text": "revenue text D", "metadata": {"source_file": "r.pdf"}, "score": 0.75},
        {"id": "doc_E", "text": "revenue text E", "metadata": {"source_file": "r.pdf"}, "score": 0.65},
    ]
    hits_map = {
        "TSMC revenue FY2023": hits_q1,
        "TSMC net income Q4 2023": hits_q2,
        "Taiwan Semiconductor earnings per share 2023": hits_q1,
        # hyde query will also land here or in default
    }

    captured_filters = []
    dense_search = _make_dense_search(hits_map, captured_filters=captured_filters)

    cfg = RagConfig(max_subqueries=3, dense_candidates=10, top_k=4,
                    use_hyde=True, use_rerank=False)
    deps = RagDeps(llm_call=_llm_ok, dense_search=dense_search)

    wf = {"file_name": "report.pdf"}
    result = rag_search("TSMC revenue 2023", deps, cfg, where_filter=wf)

    hits = result["hits"]

    # top_k cap
    assert len(hits) <= cfg.top_k, f"Hits exceed top_k={cfg.top_k}: got {len(hits)}"
    # All ids unique (no dups)
    ids = [h["id"] for h in hits]
    assert len(ids) == len(set(ids)), f"Duplicate ids in hits: {ids}"
    # All hits have required keys
    for h in hits:
        for key in ("id", "text", "metadata", "score"):
            assert key in h, f"Hit missing key '{key}': {h}"

    # where_filter must have been passed to every dense_search call
    for f in captured_filters:
        assert f == wf, f"where_filter not passed correctly to dense_search: {f!r}"

    print("  [PASS] test_rag_search_basic")


# ─────────────────────────────────────────────────────────────
# Test 4: rag_search — cross_encode reverses order
# ─────────────────────────────────────────────────────────────

def test_rag_search_rerank():
    """
    Verify that cross_encode actually changes the final ordering.

    Setup:
      - dense_search always returns [doc_A, doc_B, doc_C] in that order.
      - All three subqueries return the same list, so RRF converges on
        doc_A > doc_B > doc_C (no rerank → first hit is doc_A).
      - Our stub cross_encode assigns the *highest* score to the *last*
        passage it receives (i.e. score[i] = i).  The passages arrive in
        RRF order (A first), so doc_C (last passage, index 2) gets the
        highest score and should bubble up to position 0.
    """
    hits_all = [
        {"id": "doc_A", "text": "text A", "metadata": {}, "score": 0.95},
        {"id": "doc_B", "text": "text B", "metadata": {}, "score": 0.90},
        {"id": "doc_C", "text": "text C", "metadata": {}, "score": 0.85},
    ]

    def dense_search(query, n, where_filter=None):
        return hits_all[:n]

    def cross_encode_ascending(query: str, passages: list) -> list:
        """Score[i] = i  →  last passage gets the highest score."""
        return [float(i) for i in range(len(passages))]

    # First confirm WITHOUT rerank that the order is A, B, C
    cfg_no_rerank = RagConfig(max_subqueries=2, dense_candidates=10, top_k=3,
                              use_hyde=False, use_rerank=False)
    deps_no_rerank = RagDeps(llm_call=_llm_ok, dense_search=dense_search)
    result_no_rerank = rag_search("TSMC revenue 2023", deps_no_rerank, cfg_no_rerank)
    ids_no_rerank = [h["id"] for h in result_no_rerank["hits"]]
    assert ids_no_rerank[0] == "doc_A", (
        f"Without rerank expected doc_A first, got {ids_no_rerank[0]}"
    )

    # Now WITH rerank — cross_encode_ascending promotes doc_C to first
    cfg_rerank = RagConfig(max_subqueries=2, dense_candidates=10, top_k=3,
                           use_hyde=False, use_rerank=True, rerank_top_n=10)
    deps_rerank = RagDeps(
        llm_call=_llm_ok,
        dense_search=dense_search,
        cross_encode=cross_encode_ascending,
    )

    result = rag_search("TSMC revenue 2023", deps_rerank, cfg_rerank)
    hits = result["hits"]

    assert len(hits) >= 1, "Expected at least one hit after rerank"
    # doc_C is the last passage (index 2) and receives score 2.0 — the highest
    first_id = hits[0]["id"]
    assert first_id == "doc_C", (
        f"After rerank (ascending scores) expected doc_C first, got {first_id}. "
        f"Full order: {[h['id'] for h in hits]}"
    )

    print("  [PASS] test_rag_search_rerank")


# ─────────────────────────────────────────────────────────────
# Test 5: Cost knob — max_subqueries limits dense_search call count
# ─────────────────────────────────────────────────────────────

def test_max_subqueries_limits_calls():
    call_counter = [0]
    dense_search = _make_dense_search({}, call_counter=call_counter)

    for max_sq in (1, 2, 3):
        call_counter[0] = 0

        # use_hyde=True: effective calls = min(rewrites, max_sq) + 1 (hyde)
        # But rewrite_query caps rewrites to max_subqueries first.
        # LLM returns 4 rewrites; after cap → max_sq rewrites + possibly 1 hyde.
        cfg = RagConfig(max_subqueries=max_sq, use_hyde=True, dense_candidates=5, top_k=3)
        deps = RagDeps(llm_call=_llm_ok, dense_search=dense_search)

        rag_search("test query", deps, cfg)

        # all_queries has at most max_sq + 1 (hyde) entries.
        expected_max_calls = max_sq + 1
        assert call_counter[0] <= expected_max_calls, (
            f"max_subqueries={max_sq}: expected <= {expected_max_calls} dense_search calls, "
            f"got {call_counter[0]}"
        )

    # use_hyde=False: calls should equal exactly max_subqueries
    call_counter[0] = 0
    cfg_no_hyde = RagConfig(max_subqueries=2, use_hyde=False, dense_candidates=5, top_k=3)
    deps_no_hyde = RagDeps(llm_call=_llm_ok, dense_search=_make_dense_search({}, call_counter))
    rag_search("test query", deps_no_hyde, cfg_no_hyde)
    assert call_counter[0] == 2, (
        f"use_hyde=False, max_subqueries=2: expected exactly 2 calls, got {call_counter[0]}"
    )

    print("  [PASS] test_max_subqueries_limits_calls")


# ─────────────────────────────────────────────────────────────
# Test 6: to_evidence — builds Evidence objects
# ─────────────────────────────────────────────────────────────

def test_to_evidence():
    hits = [
        {"id": "doc_1", "text": "TSMC revenue NT$2.16 trillion",
         "metadata": {"source_file": "tsmc_2023.pdf", "chunk_index": 0}, "score": 0.92},
        {"id": "doc_2", "text": "Net income attributable to shareholders",
         "metadata": {"file_name": "tsmc_income.pdf", "chunk_index": 1}, "score": 0.78},
        {"id": "doc_3", "text": "EPS basic 32.34",
         "metadata": {}, "score": 0.61},
    ]
    query = "What is TSMC revenue 2023?"

    evs = to_evidence(hits, query)

    assert len(evs) == 3, f"Expected 3 Evidence objects, got {len(evs)}"

    # Check first Evidence
    ev0 = evs[0]
    assert ev0.source == "tsmc_2023.pdf", f"Expected source_file fallback, got {ev0.source!r}"
    assert ev0.content == hits[0]["text"], f"Content mismatch: {ev0.content!r}"
    assert ev0.relevance == 0.92, f"Relevance mismatch: {ev0.relevance}"
    assert ev0.query == query, f"Query mismatch: {ev0.query!r}"
    assert ev0.type == "rag", f"Type should be 'rag', got {ev0.type!r}"

    # Second: file_name fallback
    ev1 = evs[1]
    assert ev1.source == "tsmc_income.pdf", (
        f"Expected file_name fallback, got {ev1.source!r}"
    )

    # Third: no source_file/file_name → falls back to doc id
    ev2 = evs[2]
    assert ev2.source == "doc_3", f"Expected id fallback, got {ev2.source!r}"
    assert ev2.relevance == 0.61

    print("  [PASS] test_to_evidence")


# ─────────────────────────────────────────────────────────────
# Test 7: rag_search debug mode
# ─────────────────────────────────────────────────────────────

def test_rag_search_debug():
    hits_all = [
        {"id": f"doc_{i}", "text": f"text {i}", "metadata": {}, "score": 1.0 - i * 0.1}
        for i in range(5)
    ]

    def dense_search(query, n, where_filter=None):
        return hits_all[:n]

    cfg = RagConfig(max_subqueries=2, use_hyde=False, dense_candidates=5, top_k=3)
    deps = RagDeps(llm_call=_llm_ok, dense_search=dense_search)

    result = rag_search("debug test", deps, cfg, debug=True)

    assert "hits" in result, "Result must have 'hits'"
    assert "debug" in result, "Result must have 'debug' when debug=True"
    dbg = result["debug"]
    for key in ("rewrites", "hyde", "all_queries", "per_subquery_hits", "rrf_ranking", "reranked_ids"):
        assert key in dbg, f"Debug dict missing key '{key}'"

    print("  [PASS] test_rag_search_debug")


# ─────────────────────────────────────────────────────────────
# Test 8: rag_search — default cfg (None) uses RagConfig defaults
# ─────────────────────────────────────────────────────────────

def test_rag_search_default_cfg():
    def dense_search(query, n, where_filter=None):
        return [{"id": "doc_x", "text": "some text", "metadata": {}, "score": 0.5}]

    deps = RagDeps(llm_call=_llm_ok, dense_search=dense_search)
    result = rag_search("some query", deps, cfg=None)

    assert "hits" in result, "Result must have 'hits' key"
    assert isinstance(result["hits"], list), "hits must be a list"

    print("  [PASS] test_rag_search_default_cfg")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running rag.py test suite...\n")

    test_rewrite_query_happy()
    test_rewrite_query_fallback()
    test_rag_search_basic()
    test_rag_search_rerank()
    test_max_subqueries_limits_calls()
    test_to_evidence()
    test_rag_search_debug()
    test_rag_search_default_cfg()

    print("\nALL TESTS PASSED")

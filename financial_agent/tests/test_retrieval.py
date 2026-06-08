"""
tests/test_retrieval.py
========================
Self-contained test suite for retrieval.py (Phase 3 Hybrid Retriever).
Stdlib-only; run with:
    python3 tests/test_retrieval.py

Tests
-----
1.  reciprocal_rank_fusion – basic ordering, single list, empty input.
2.  tokenize – English, CJK (Chinese), mixed text, empty string.
3.  HybridRetriever.index + BM25-only search (no dense_results).
4.  HybridRetriever.search with dense_results only (empty BM25 index).
5.  HybridRetriever.search hybrid: revenue/營收 doc rises to top.
6.  HybridRetriever.search with expanded_terms.
7.  HybridRetriever.search with metadata filters.
8.  HybridRetriever.search debug=True populates all expected keys.
9.  HybridRetriever.search with dense_results whose ids are NOT in the BM25 index.
10. HybridRetriever cross-encoder absent → silent skip, results still returned.
"""

from __future__ import annotations

import sys
import os

# Allow running from any working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from retrieval import reciprocal_rank_fusion, tokenize, HybridRetriever


# ──────────────────────────────────────────────────────────────────────────────
# Toy corpus: ~6 docs, mix of English / Chinese finance snippets
# ──────────────────────────────────────────────────────────────────────────────

CORPUS = [
    {
        "id": "doc_revenue_en",
        "text": "Annual revenue increased 15% to USD 8.5 billion. Revenue growth was driven by strong product demand and market expansion.",
        "metadata": {"lang": "en", "topic": "revenue"},
    },
    {
        "id": "doc_revenue_zh",
        "text": "本公司2023年度營收達新台幣2,500億元，較去年同期成長18%。營收成長主要來自半導體業務的強勁需求。",
        "metadata": {"lang": "zh", "topic": "revenue"},
    },
    {
        "id": "doc_cost_en",
        "text": "Operating expenses decreased by 8% due to cost reduction initiatives and supply chain optimisation.",
        "metadata": {"lang": "en", "topic": "cost"},
    },
    {
        "id": "doc_profit_en",
        "text": "Net profit margin improved to 22.4% in Q3 2023, driven by higher operating leverage and lower interest expenses.",
        "metadata": {"lang": "en", "topic": "profit"},
    },
    {
        "id": "doc_capex_en",
        "text": "Capital expenditure (CAPEX) for fiscal year 2023 totalled USD 3.2 billion, allocated primarily to semiconductor fabrication capacity.",
        "metadata": {"lang": "en", "topic": "capex"},
    },
    {
        "id": "doc_roe_zh",
        "text": "股東權益報酬率（ROE）為18.5%，反映出公司有效運用股東資金的能力，優於同業平均水準。",
        "metadata": {"lang": "zh", "topic": "roe"},
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ids(results: list[dict]) -> list[str]:
    return [r["id"] for r in results]


def _build_retriever(corpus=None) -> HybridRetriever:
    r = HybridRetriever()
    r.index(corpus or CORPUS)
    return r


def _make_dense_results(doc_ids: list[str], corpus=None) -> list[dict]:
    """Build a fake dense_results list preserving given order."""
    corpus = corpus or CORPUS
    by_id = {d["id"]: d for d in corpus}
    results = []
    for i, doc_id in enumerate(doc_ids):
        doc = by_id[doc_id]
        results.append({
            "id": doc_id,
            "text": doc["text"],
            "metadata": doc["metadata"],
            "score": 1.0 - i * 0.05,
        })
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Test 1 – reciprocal_rank_fusion
# ──────────────────────────────────────────────────────────────────────────────

def test_rrf_basic_ordering():
    """Doc appearing at rank 1 in both lists must score highest."""
    rankings = [
        ["alpha", "beta", "gamma"],
        ["alpha", "gamma", "beta"],
    ]
    result = reciprocal_rank_fusion(rankings)
    ids = [doc_id for doc_id, _ in result]
    scores = {doc_id: sc for doc_id, sc in result}

    # alpha is rank-1 in both → highest score
    assert ids[0] == "alpha", f"alpha should be first, got {ids[0]!r}"
    # Scores must be descending
    sc_list = [scores[i] for i in ids]
    assert sc_list == sorted(sc_list, reverse=True), "scores not descending"
    print("  [PASS] test_rrf_basic_ordering")


def test_rrf_single_list():
    """Single ranking list should preserve original order."""
    ranking = ["x", "y", "z"]
    result = reciprocal_rank_fusion([ranking])
    ids = [doc_id for doc_id, _ in result]
    assert ids == ["x", "y", "z"], f"expected ['x','y','z'], got {ids}"
    print("  [PASS] test_rrf_single_list")


def test_rrf_empty():
    """Empty input must return an empty list (not raise)."""
    result = reciprocal_rank_fusion([])
    assert result == [], f"expected [], got {result!r}"
    result2 = reciprocal_rank_fusion([[]])
    assert result2 == [], f"expected [] for single empty list, got {result2!r}"
    print("  [PASS] test_rrf_empty")


def test_rrf_score_formula():
    """RRF score = sum of 1/(k+rank_pos) across lists."""
    k = 60
    # One list with two items; manual computation
    result = reciprocal_rank_fusion([["a", "b"]], k=k)
    score_a = 1.0 / (k + 1)
    score_b = 1.0 / (k + 2)
    result_map = dict(result)
    assert abs(result_map["a"] - score_a) < 1e-12, f"score_a: {result_map['a']} vs {score_a}"
    assert abs(result_map["b"] - score_b) < 1e-12, f"score_b: {result_map['b']} vs {score_b}"
    print("  [PASS] test_rrf_score_formula")


def test_rrf_hand_crafted_ordering():
    """
    Hand-crafted scenario:
      List A ranks: doc1, doc2, doc3
      List B ranks: doc2, doc3, doc1
    Expected RRF:
      doc1 = 1/61 + 1/63 = 0.016393... + 0.015873... = 0.032267...
      doc2 = 1/62 + 1/61 = 0.016129... + 0.016393... = 0.032523...
      doc3 = 1/63 + 1/62 = 0.015873... + 0.016129... = 0.032002...
    Order: doc2 > doc1 > doc3
    """
    result = reciprocal_rank_fusion(
        [["doc1", "doc2", "doc3"], ["doc2", "doc3", "doc1"]],
        k=60,
    )
    ids = [doc_id for doc_id, _ in result]
    assert ids[0] == "doc2", f"doc2 should be first; got {ids}"
    assert ids[1] == "doc1", f"doc1 should be second; got {ids}"
    assert ids[2] == "doc3", f"doc3 should be third; got {ids}"
    print("  [PASS] test_rrf_hand_crafted_ordering")


# ──────────────────────────────────────────────────────────────────────────────
# Test 2 – tokenize
# ──────────────────────────────────────────────────────────────────────────────

def test_tokenize_english():
    tokens = tokenize("Revenue growth was strong")
    assert isinstance(tokens, list), "tokenize must return a list"
    joined = " ".join(tokens).lower()
    assert "revenue" in joined, f"'revenue' missing from {tokens}"
    assert "growth" in joined, f"'growth' missing from {tokens}"
    print("  [PASS] test_tokenize_english")


def test_tokenize_cjk():
    """CJK characters must be emitted as individual tokens."""
    tokens = tokenize("營收成長")
    assert isinstance(tokens, list)
    # Each character should be its own token
    for ch in "營收成長":
        assert ch in tokens, f"CJK char {ch!r} not found in {tokens}"
    print("  [PASS] test_tokenize_cjk")


def test_tokenize_mixed():
    """Mixed English + Chinese text must produce tokens from both."""
    text = "revenue 營收 growth"
    tokens = tokenize(text)
    joined = " ".join(tokens)
    assert "revenue" in joined.lower() or any("revenue" in t.lower() for t in tokens), \
        f"'revenue' missing from {tokens}"
    assert "營" in tokens or "營收" in tokens, f"CJK '營' missing from {tokens}"
    print("  [PASS] test_tokenize_mixed")


def test_tokenize_empty():
    """Empty string must return an empty list (not raise)."""
    tokens = tokenize("")
    assert tokens == [], f"expected [], got {tokens!r}"
    print("  [PASS] test_tokenize_empty")


def test_tokenize_deterministic():
    """Same input must always yield the same output."""
    text = "Net profit margin 2023 Q4"
    assert tokenize(text) == tokenize(text), "tokenize must be deterministic"
    print("  [PASS] test_tokenize_deterministic")


# ──────────────────────────────────────────────────────────────────────────────
# Test 3 – BM25-only search (no dense_results)
# ──────────────────────────────────────────────────────────────────────────────

def test_bm25_only_revenue_query():
    """BM25-only mode: 'revenue' query must surface the revenue docs."""
    retriever = _build_retriever()
    result = retriever.search("revenue", dense_results=None, top_k=6)
    assert "results" in result
    ids = _ids(result["results"])
    assert len(ids) > 0, "expected at least one result"
    # Revenue docs must appear before non-revenue docs at the top
    top_2 = set(ids[:2])
    assert "doc_revenue_en" in top_2 or "doc_revenue_zh" in top_2, \
        f"revenue doc missing from top-2: {ids}"
    print("  [PASS] test_bm25_only_revenue_query")


def test_bm25_only_empty_index():
    """Empty index + no dense results → empty results (no crash)."""
    retriever = HybridRetriever()
    retriever.index([])
    result = retriever.search("revenue", dense_results=None, top_k=5)
    assert result["results"] == [], f"expected [], got {result['results']!r}"
    print("  [PASS] test_bm25_only_empty_index")


def test_bm25_respects_top_k():
    """Result list must never exceed top_k."""
    retriever = _build_retriever()
    result = retriever.search("2023 financial results", top_k=3)
    assert len(result["results"]) <= 3, \
        f"expected ≤ 3 results, got {len(result['results'])}"
    print("  [PASS] test_bm25_respects_top_k")


# ──────────────────────────────────────────────────────────────────────────────
# Test 4 – dense_results only (empty BM25 index)
# ──────────────────────────────────────────────────────────────────────────────

def test_dense_only_no_bm25_index():
    """With no indexed docs, dense_results should drive the ranking."""
    retriever = HybridRetriever()
    retriever.index([])   # empty index

    # Simulate ChromaDB returning revenue docs first
    dense = _make_dense_results(["doc_revenue_en", "doc_profit_en", "doc_cost_en"])
    result = retriever.search("revenue", dense_results=dense, top_k=3)
    ids = _ids(result["results"])
    assert "doc_revenue_en" in ids, f"doc_revenue_en missing: {ids}"
    # Because it was ranked first in dense, RRF should keep it near the top
    assert ids[0] == "doc_revenue_en", f"doc_revenue_en should be #1; got {ids}"
    print("  [PASS] test_dense_only_no_bm25_index")


# ──────────────────────────────────────────────────────────────────────────────
# Test 5 – Hybrid search: revenue doc on top with and without dense_results
# ──────────────────────────────────────────────────────────────────────────────

def test_hybrid_revenue_on_top_with_dense():
    """
    Hybrid mode: revenue docs should surface when query = 'revenue 營收'.
    Dense results ordered so that the revenue doc is ranked first.
    """
    retriever = _build_retriever()

    # Dense ranking puts revenue_en first (simulating ChromaDB vector hit)
    dense = _make_dense_results([
        "doc_revenue_en", "doc_revenue_zh", "doc_profit_en",
        "doc_cost_en", "doc_capex_en", "doc_roe_zh",
    ])
    result = retriever.search("revenue 營收", dense_results=dense, top_k=6)
    ids = _ids(result["results"])

    assert len(ids) > 0, "expected results"
    assert ids[0] in ("doc_revenue_en", "doc_revenue_zh"), \
        f"revenue doc should be #1 in hybrid mode; got {ids}"
    print("  [PASS] test_hybrid_revenue_on_top_with_dense")


def test_hybrid_revenue_on_top_without_dense():
    """
    BM25-only: 'revenue' query must return a revenue doc in the top-2.
    Also verifies Chinese '營收' query surfaces the Chinese revenue doc.
    """
    retriever = _build_retriever()

    # English query
    res_en = retriever.search("revenue annual growth", dense_results=None, top_k=6)
    ids_en = _ids(res_en["results"])
    assert "doc_revenue_en" in ids_en[:2], \
        f"doc_revenue_en missing from top-2 for English query: {ids_en}"

    # Chinese query
    res_zh = retriever.search("營收成長", dense_results=None, top_k=6)
    ids_zh = _ids(res_zh["results"])
    assert "doc_revenue_zh" in ids_zh[:2], \
        f"doc_revenue_zh missing from top-2 for Chinese query: {ids_zh}"

    print("  [PASS] test_hybrid_revenue_on_top_without_dense")


# ──────────────────────────────────────────────────────────────────────────────
# Test 6 – expanded_terms boost correct document
# ──────────────────────────────────────────────────────────────────────────────

def test_expanded_terms_boost_revenue():
    """
    expanded_terms containing 'revenue' / '營收' should help surface
    the revenue documents even when the base query is generic.
    """
    retriever = _build_retriever()
    result = retriever.search(
        "financial performance",
        expanded_terms=["revenue", "net sales", "turnover", "營收"],
        dense_results=None,
        top_k=6,
    )
    ids = _ids(result["results"])
    # Revenue docs should appear somewhere in the results
    assert any(i in ids for i in ("doc_revenue_en", "doc_revenue_zh")), \
        f"no revenue doc in results: {ids}"
    print("  [PASS] test_expanded_terms_boost_revenue")


# ──────────────────────────────────────────────────────────────────────────────
# Test 7 – metadata filters
# ──────────────────────────────────────────────────────────────────────────────

def test_filters_restrict_to_lang_en():
    """Filtering by lang='en' must exclude Chinese-language docs."""
    retriever = _build_retriever()
    result = retriever.search(
        "revenue growth",
        dense_results=None,
        filters={"lang": "en"},
        top_k=6,
    )
    for doc in result["results"]:
        assert doc["metadata"].get("lang") == "en", \
            f"non-en doc slipped through filter: {doc['id']!r} metadata={doc['metadata']!r}"
    print("  [PASS] test_filters_restrict_to_lang_en")


def test_filters_restrict_to_topic_revenue():
    """Filtering by topic='revenue' must return only revenue-topic docs."""
    retriever = _build_retriever()
    result = retriever.search(
        "2023",
        dense_results=None,
        filters={"topic": "revenue"},
        top_k=6,
    )
    for doc in result["results"]:
        assert doc["metadata"].get("topic") == "revenue", \
            f"unexpected topic in {doc['id']!r}: {doc['metadata']!r}"
    print("  [PASS] test_filters_restrict_to_topic_revenue")


def test_filters_on_dense_results():
    """Filters must also be applied to dense_results (not in BM25 index)."""
    retriever = HybridRetriever()
    retriever.index([])  # empty BM25

    dense = _make_dense_results([
        "doc_revenue_en", "doc_revenue_zh", "doc_profit_en",
        "doc_cost_en", "doc_capex_en", "doc_roe_zh",
    ])
    result = retriever.search(
        "revenue",
        dense_results=dense,
        filters={"lang": "zh"},
        top_k=6,
    )
    for doc in result["results"]:
        assert doc["metadata"].get("lang") == "zh", \
            f"non-zh doc slipped through filter: {doc['id']!r}"
    print("  [PASS] test_filters_on_dense_results")


# ──────────────────────────────────────────────────────────────────────────────
# Test 8 – debug=True populates all expected keys
# ──────────────────────────────────────────────────────────────────────────────

def test_debug_mode_keys_populated():
    """debug=True must include 'debug' dict with all required keys."""
    retriever = _build_retriever()
    dense = _make_dense_results(["doc_revenue_en", "doc_profit_en"])
    result = retriever.search("revenue", dense_results=dense, top_k=5, debug=True)

    assert "debug" in result, "debug key missing from result"
    dbg = result["debug"]

    required_keys = {
        "vector_hits", "bm25_hits", "rrf_ranking",
        "reranked_results", "selected_context",
    }
    missing = required_keys - set(dbg.keys())
    assert not missing, f"debug dict missing keys: {missing}"

    # vector_hits must match dense_results order
    assert dbg["vector_hits"] == ["doc_revenue_en", "doc_profit_en"], \
        f"vector_hits mismatch: {dbg['vector_hits']!r}"

    # bm25_hits must be a list of strings
    assert isinstance(dbg["bm25_hits"], list), "bm25_hits must be a list"
    for item in dbg["bm25_hits"]:
        assert isinstance(item, str), f"bm25_hits item must be str, got {type(item)}"

    # rrf_ranking is a list of (id, score) tuples
    assert isinstance(dbg["rrf_ranking"], list), "rrf_ranking must be a list"
    if dbg["rrf_ranking"]:
        first = dbg["rrf_ranking"][0]
        assert len(first) == 2, f"rrf_ranking items must be 2-tuples, got {first!r}"

    # selected_context is a list of result ids
    result_ids = [r["id"] for r in result["results"]]
    assert dbg["selected_context"] == result_ids, \
        f"selected_context {dbg['selected_context']} ≠ result ids {result_ids}"

    print("  [PASS] test_debug_mode_keys_populated")


def test_debug_mode_false_no_debug_key():
    """debug=False (default) must NOT include 'debug' key."""
    retriever = _build_retriever()
    result = retriever.search("revenue", debug=False)
    assert "debug" not in result, "debug key should be absent when debug=False"
    print("  [PASS] test_debug_mode_false_no_debug_key")


# ──────────────────────────────────────────────────────────────────────────────
# Test 9 – dense_results with ids NOT in BM25 index
# ──────────────────────────────────────────────────────────────────────────────

def test_dense_only_ids_not_in_bm25():
    """
    If dense_results contain docs that were never indexed in BM25,
    they must still appear in the fused results (via dense lookup).
    """
    retriever = _build_retriever(CORPUS[:2])  # only index first 2 docs

    # Dense results include docs 3-5 which are NOT in the BM25 index
    dense = _make_dense_results([
        "doc_revenue_en",  # IS in index
        "doc_capex_en",    # NOT in BM25 index
        "doc_roe_zh",      # NOT in BM25 index
    ])
    result = retriever.search("capital expenditure ROE", dense_results=dense, top_k=5)
    ids = _ids(result["results"])

    # All three must appear (dense lookup bridges the gap)
    assert "doc_capex_en" in ids, f"doc_capex_en missing: {ids}"
    assert "doc_roe_zh" in ids, f"doc_roe_zh missing: {ids}"
    print("  [PASS] test_dense_only_ids_not_in_bm25")


# ──────────────────────────────────────────────────────────────────────────────
# Test 10 – CrossEncoder absent → silent skip, results still returned
# ──────────────────────────────────────────────────────────────────────────────

def test_cross_encoder_absent_silent_skip():
    """
    When cross_encoder_name is set but sentence_transformers is absent,
    reranking must be skipped silently — results still returned normally.
    """
    retriever = HybridRetriever(cross_encoder_name="cross-encoder/ms-marco-MiniLM-L-6-v2")
    retriever.index(CORPUS)
    dense = _make_dense_results(["doc_revenue_en", "doc_profit_en", "doc_cost_en"])

    # This must not raise even if sentence_transformers is not installed
    result = retriever.search("revenue", dense_results=dense, top_k=5)
    assert "results" in result, "results key missing"
    assert len(result["results"]) > 0, "expected at least one result"

    # If CE import failed, the flag must be set so subsequent calls skip immediately
    if retriever._ce_failed:
        # Call again — must not attempt import a second time
        result2 = retriever.search("revenue", dense_results=dense, top_k=3)
        assert "results" in result2

    print("  [PASS] test_cross_encoder_absent_silent_skip")


# ──────────────────────────────────────────────────────────────────────────────
# Test 11 – result schema validation
# ──────────────────────────────────────────────────────────────────────────────

def test_result_schema():
    """Every result item must have id, text, metadata, score."""
    retriever = _build_retriever()
    dense = _make_dense_results(["doc_revenue_en", "doc_profit_en"])
    result = retriever.search("revenue margin", dense_results=dense, top_k=4)

    for item in result["results"]:
        assert "id" in item,       f"'id' missing from result: {item!r}"
        assert "text" in item,     f"'text' missing from result: {item!r}"
        assert "metadata" in item, f"'metadata' missing from result: {item!r}"
        assert "score" in item,    f"'score' missing from result: {item!r}"
        assert isinstance(item["id"], str),    f"id must be str: {item['id']!r}"
        assert isinstance(item["text"], str),  f"text must be str: {item['text']!r}"
        assert isinstance(item["metadata"], dict), f"metadata must be dict: {item['metadata']!r}"
        assert isinstance(item["score"], float), f"score must be float: {item['score']!r}"
    print("  [PASS] test_result_schema")


# ──────────────────────────────────────────────────────────────────────────────
# Test 12 – Re-index resets state
# ──────────────────────────────────────────────────────────────────────────────

def test_reindex_resets_state():
    """Calling index() twice should replace the previous index cleanly."""
    retriever = HybridRetriever()

    # Index first batch
    retriever.index(CORPUS[:3])
    res1 = retriever.search("revenue", top_k=6)
    ids1 = _ids(res1["results"])

    # Re-index with a different corpus (only revenue docs)
    retriever.index(CORPUS[:2])
    res2 = retriever.search("ROE", top_k=6)
    ids2 = _ids(res2["results"])

    # After re-index, only 2 docs exist — ROE doc should not appear
    assert "doc_roe_zh" not in ids2, \
        f"doc_roe_zh should not be in re-indexed results: {ids2}"
    assert len(ids2) <= 2, f"expected ≤ 2 results after re-index, got {ids2}"
    print("  [PASS] test_reindex_resets_state")


# ──────────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────────

def run_all():
    tests = [
        # RRF
        test_rrf_basic_ordering,
        test_rrf_single_list,
        test_rrf_empty,
        test_rrf_score_formula,
        test_rrf_hand_crafted_ordering,
        # tokenize
        test_tokenize_english,
        test_tokenize_cjk,
        test_tokenize_mixed,
        test_tokenize_empty,
        test_tokenize_deterministic,
        # BM25-only
        test_bm25_only_revenue_query,
        test_bm25_only_empty_index,
        test_bm25_respects_top_k,
        # Dense-only
        test_dense_only_no_bm25_index,
        # Hybrid
        test_hybrid_revenue_on_top_with_dense,
        test_hybrid_revenue_on_top_without_dense,
        # expanded_terms
        test_expanded_terms_boost_revenue,
        # filters
        test_filters_restrict_to_lang_en,
        test_filters_restrict_to_topic_revenue,
        test_filters_on_dense_results,
        # debug
        test_debug_mode_keys_populated,
        test_debug_mode_false_no_debug_key,
        # dense ids not in BM25
        test_dense_only_ids_not_in_bm25,
        # cross-encoder absent
        test_cross_encoder_absent_silent_skip,
        # schema
        test_result_schema,
        # re-index
        test_reindex_resets_state,
    ]

    print(f"\nRunning {len(tests)} retrieval tests...\n")
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

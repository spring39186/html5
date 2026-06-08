"""
hybrid_retriever — 混合式財務文件檢索模組（Phase 3）
======================================================
設計說明（Traditional Chinese）

架構概念
---------
本模組實現「稠密向量 + 稀疏 BM25 → RRF 融合 → 可選交叉編碼器重排」的
三層混合檢索流水線，作為 Financial RAG 系統的 Phase 3 元件。

元件說明
---------
1. reciprocal_rank_fusion(rankings, k)
   接收多個排序列表（id 列表，最佳在前），回傳以 RRF 分數合併後的排序。
   公式：score(doc) = Σ 1 / (k + rank_position)，rank_position 從 1 起算。

2. tokenize(text)
   先嘗試 tiktoken（OpenAI 分詞器）；若未安裝，則用正則表達式分詞：
   - 小寫化
   - 保留 CJK 字元為單一 token（支援中文/日文/韓文）
   - 以非字母數字字元切分其餘文字

3. HybridRetriever
   - index(docs)：建立 BM25 索引，docs 格式為 {"id", "text", "metadata"}。
     優先使用 rank_bm25.BM25Okapi；若未安裝則自動切換到純 Python
     實現的 BM25 Okapi（TF × IDF，含 k1/b 參數）。
   - search(query, expanded_terms, dense_results, top_k, filters, debug)：
     * dense_results 由呼叫端傳入（解耦 ChromaDB）—— 格式與 collection.query()
       回傳後整理成的 {"id","text","metadata","score"} 列表相同。
     * 計算 BM25 排名後與稠密排名透過 RRF 融合。
     * 若 cross_encoder_name 已設定，懶載入 CrossEncoder 進行重排。
     * 回傳 {"results": [...]} 或（debug=True 時）附帶 "debug" 字典。

與 agent.py 的整合方式
-----------------------
在 agent.py 的 search_knowledge_base() 中，collection.query() 的結果
可包裝成 dense_results 後傳入 HybridRetriever.search()：

    raw = collection.query(query_texts=[query], n_results=n, ...)
    dense_results = [
        {"id": id_, "text": doc, "metadata": meta, "score": 1.0 - dist}
        for id_, doc, meta, dist in zip(
            raw["ids"][0], raw["documents"][0],
            raw["metadatas"][0], raw["distances"][0]
        )
    ]
    result = retriever.search(query, dense_results=dense_results, top_k=10)

這樣既保留原有 ChromaDB 向量嵌入，又能疊加 BM25 精確詞彙匹配，
同時不修改任何現有檔案。

備援行為
---------
- rank_bm25 缺失 → MiniBM25（純 Python，約 95% 功能相容）自動啟用
- tiktoken 缺失 → regex tokenizer（CJK 感知）自動啟用
- sentence_transformers 缺失 → 跳過 CrossEncoder 重排，靜默繼續
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any

# ═══════════════════════════════════════════════════════════════
# 1. RRF 融合
# ═══════════════════════════════════════════════════════════════

def reciprocal_rank_fusion(
    rankings: list[list[str]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """
    Reciprocal Rank Fusion across multiple ranked id-lists.

    Parameters
    ----------
    rankings : list of ranked id-lists, best-first.
    k        : smoothing constant (default 60, per original RRF paper).

    Returns
    -------
    List of (id, rrf_score) tuples sorted by score descending.
    Scores are additive across lists: score += 1 / (k + rank_position),
    where rank_position starts at 1.
    """
    scores: dict[str, float] = defaultdict(float)
    for ranked_list in rankings:
        for pos, doc_id in enumerate(ranked_list, start=1):
            scores[doc_id] += 1.0 / (k + pos)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ═══════════════════════════════════════════════════════════════
# 2. Tokenizer
# ═══════════════════════════════════════════════════════════════

# CJK Unicode ranges for individual-character tokenisation.
_CJK_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Extension A
    (0x20000, 0x2A6DF), # CJK Extension B
    (0x2A700, 0x2B73F), # CJK Extension C
    (0x2B740, 0x2B81F), # CJK Extension D
    (0x2B820, 0x2CEAF), # CJK Extension E
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x2F800, 0x2FA1F), # CJK Compatibility Ideographs Supplement
    (0x3040, 0x309F),   # Hiragana
    (0x30A0, 0x30FF),   # Katakana
    (0xAC00, 0xD7AF),   # Hangul Syllables
]


def _is_cjk(char: str) -> bool:
    cp = ord(char)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _regex_tokenize(text: str) -> list[str]:
    """
    Regex-based fallback tokenizer.
    - Lowercases the entire text first.
    - Splits CJK characters out as individual tokens.
    - Splits remaining text on non-alphanumeric boundaries.
    - Filters empty strings.
    """
    text = text.lower()
    tokens: list[str] = []
    # Walk character-by-character, grouping ASCII/Latin words and
    # emitting CJK characters one at a time.
    buf: list[str] = []
    for ch in text:
        if _is_cjk(ch):
            if buf:
                tokens.extend(t for t in re.split(r"[^a-z0-9]+", "".join(buf)) if t)
                buf = []
            tokens.append(ch)
        else:
            buf.append(ch)
    if buf:
        tokens.extend(t for t in re.split(r"[^a-z0-9]+", "".join(buf)) if t)
    return tokens


def tokenize(text: str) -> list[str]:
    """
    Tokenize text for BM25 indexing.

    Tries tiktoken (cl100k_base) first for consistent sub-word tokens.
    Falls back to a CJK-aware regex tokenizer when tiktoken is absent.
    """
    try:
        import tiktoken  # noqa: PLC0415
        enc = tiktoken.get_encoding("cl100k_base")
        # Decode each token back to string and lower-case.
        return [enc.decode([tid]).lower() for tid in enc.encode(text)]
    except (ImportError, Exception):  # noqa: BLE001
        return _regex_tokenize(text)


# ═══════════════════════════════════════════════════════════════
# 3. Pure-Python BM25 Okapi (fallback when rank_bm25 absent)
# ═══════════════════════════════════════════════════════════════

class _MiniBM25:
    """
    Minimal BM25 Okapi implementation in pure Python.
    Mirrors the rank_bm25.BM25Okapi interface used by HybridRetriever.

    Parameters
    ----------
    corpus : list of token-lists (pre-tokenised documents)
    k1     : term-frequency saturation (default 1.5)
    b      : length normalisation (default 0.75)
    """

    def __init__(
        self,
        corpus: list[list[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.n_docs = len(corpus)

        # Document lengths and average
        self.doc_lens = [len(doc) for doc in corpus]
        self.avgdl = sum(self.doc_lens) / max(self.n_docs, 1)

        # Inverted index: term → {doc_idx: tf}
        self.tf: dict[str, dict[int, int]] = defaultdict(dict)
        for idx, doc in enumerate(corpus):
            counts = Counter(doc)
            for term, cnt in counts.items():
                self.tf[term][idx] = cnt

        # IDF: log((N - df + 0.5) / (df + 0.5) + 1)
        self.idf: dict[str, float] = {}
        for term, postings in self.tf.items():
            df = len(postings)
            self.idf[term] = math.log(
                (self.n_docs - df + 0.5) / (df + 0.5) + 1.0
            )

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        """Return BM25 score for each document given query_tokens."""
        scores = [0.0] * self.n_docs
        for term in query_tokens:
            if term not in self.idf:
                continue
            idf_val = self.idf[term]
            postings = self.tf[term]
            for doc_idx, tf_val in postings.items():
                dl = self.doc_lens[doc_idx]
                denom = tf_val + self.k1 * (
                    1 - self.b + self.b * dl / max(self.avgdl, 1)
                )
                scores[doc_idx] += idf_val * (tf_val * (self.k1 + 1)) / denom
        return scores


# ═══════════════════════════════════════════════════════════════
# 4. HybridRetriever
# ═══════════════════════════════════════════════════════════════

class HybridRetriever:
    """
    Hybrid sparse+dense retriever with optional cross-encoder reranking.

    Parameters
    ----------
    cross_encoder_name : HuggingFace model name for CrossEncoder reranking.
                         Set to None (default) to disable reranking entirely.
    """

    def __init__(self, cross_encoder_name: str | None = None) -> None:
        self.cross_encoder_name = cross_encoder_name
        self._cross_encoder: Any = None          # lazily loaded
        self._ce_failed: bool = False            # remember import failure

        # Indexed state
        self._docs: dict[str, dict] = {}        # id → {"id","text","metadata"}
        self._bm25: _MiniBM25 | Any | None = None  # BM25 index
        self._id_order: list[str] = []          # doc ids in index order

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index(self, docs: list[dict]) -> None:
        """
        Build an in-memory BM25 index over the provided documents.

        Parameters
        ----------
        docs : list of {"id": str, "text": str, "metadata": dict}
        """
        self._docs = {d["id"]: d for d in docs}
        self._id_order = [d["id"] for d in docs]
        tokenised_corpus = [tokenize(d["text"]) for d in docs]

        # Try rank_bm25 first, fall back to _MiniBM25
        try:
            from rank_bm25 import BM25Okapi  # noqa: PLC0415
            self._bm25 = BM25Okapi(tokenised_corpus)
        except ImportError:
            self._bm25 = _MiniBM25(tokenised_corpus)

    # ------------------------------------------------------------------
    # Searching
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        expanded_terms: list[str] | None = None,
        dense_results: list[dict] | None = None,
        top_k: int = 10,
        filters: dict | None = None,
        debug: bool = False,
    ) -> dict:
        """
        Hybrid search: BM25 + dense → RRF → optional rerank.

        Parameters
        ----------
        query          : Natural-language query string.
        expanded_terms : Additional query terms (e.g. from query expansion).
        dense_results  : Caller-supplied dense hits, best-first.
                         Format: list of {"id","text","metadata","score"}.
                         Pass None or [] for BM25-only mode.
        top_k          : Maximum number of results to return.
        filters        : Exact-match metadata filters applied to candidate ids.
                         e.g. {"file_name": "report_2023.pdf"}
        debug          : If True, include a "debug" key in the return dict.

        Returns
        -------
        {"results": [{id, text, metadata, score}, ...]}
        or {"results": [...], "debug": {...}} when debug=True.
        """
        dense_results = dense_results or []
        expanded_terms = expanded_terms or []

        # ── 1. BM25 ranking ─────────────────────────────────────────
        bm25_ranked: list[str] = []
        if self._bm25 is not None and self._id_order:
            bm25_query = query
            if expanded_terms:
                bm25_query = query + " " + " ".join(expanded_terms)
            q_tokens = tokenize(bm25_query)
            scores = self._bm25.get_scores(q_tokens)
            # Sort by score descending, pair with ids
            scored_ids = sorted(
                zip(self._id_order, scores),
                key=lambda x: x[1],
                reverse=True,
            )
            bm25_ranked = [doc_id for doc_id, _ in scored_ids if _ > 0.0]
            # Docs with score==0 still appear in the list so RRF can use them,
            # but we only include non-zero for useful ranking.
            if not bm25_ranked:
                # All scores are 0 — emit all anyway so fusion still works
                bm25_ranked = [doc_id for doc_id, _ in scored_ids]

        # ── 2. Dense ranking (ids only, order preserved) ─────────────
        dense_ranked: list[str] = [r["id"] for r in dense_results]

        # ── 3. Apply filters to candidate pool ───────────────────────
        def _passes_filter(doc_id: str) -> bool:
            if not filters:
                return True
            # Look up metadata from index or dense results
            meta: dict = {}
            if doc_id in self._docs:
                meta = self._docs[doc_id].get("metadata", {})
            else:
                for dr in dense_results:
                    if dr["id"] == doc_id:
                        meta = dr.get("metadata", {})
                        break
            return all(meta.get(k) == v for k, v in filters.items())

        if filters:
            bm25_ranked = [i for i in bm25_ranked if _passes_filter(i)]
            dense_ranked = [i for i in dense_ranked if _passes_filter(i)]

        # ── 4. RRF fusion ────────────────────────────────────────────
        rankings_to_fuse: list[list[str]] = []
        if dense_ranked:
            rankings_to_fuse.append(dense_ranked)
        if bm25_ranked:
            rankings_to_fuse.append(bm25_ranked)

        if rankings_to_fuse:
            rrf_ranking = reciprocal_rank_fusion(rankings_to_fuse)
        else:
            rrf_ranking = []

        # ── 5. Assemble candidate docs ───────────────────────────────
        # Build a lookup for dense-only docs (not in BM25 index)
        dense_lookup: dict[str, dict] = {r["id"]: r for r in dense_results}

        def _get_doc(doc_id: str) -> dict | None:
            if doc_id in self._docs:
                return self._docs[doc_id]
            if doc_id in dense_lookup:
                dr = dense_lookup[doc_id]
                return {"id": dr["id"], "text": dr["text"], "metadata": dr.get("metadata", {})}
            return None

        # Candidate list: all RRF-ranked ids (up to a generous cutoff)
        max_candidates = max(top_k * 3, 30)
        candidate_ids = [doc_id for doc_id, _ in rrf_ranking[:max_candidates]]

        # ── 6. Optional CrossEncoder reranking ───────────────────────
        reranked_ids: list[str] = []
        if self.cross_encoder_name and candidate_ids and not self._ce_failed:
            reranked_ids = self._rerank(query, candidate_ids, dense_lookup)

        final_ids = reranked_ids if reranked_ids else candidate_ids

        # ── 7. Build result list ──────────────────────────────────────
        rrf_score_map = dict(rrf_ranking)
        results: list[dict] = []
        for doc_id in final_ids[:top_k]:
            doc = _get_doc(doc_id)
            if doc is None:
                continue
            results.append({
                "id": doc_id,
                "text": doc["text"],
                "metadata": doc.get("metadata", {}),
                "score": rrf_score_map.get(doc_id, 0.0),
            })

        # ── 8. Return ─────────────────────────────────────────────────
        out: dict = {"results": results}
        if debug:
            out["debug"] = {
                "vector_hits": dense_ranked,
                "bm25_hits": bm25_ranked,
                "rrf_ranking": rrf_ranking,
                "reranked_results": reranked_ids,
                "selected_context": [r["id"] for r in results],
            }
        return out

    # ------------------------------------------------------------------
    # Cross-encoder reranking (lazy import)
    # ------------------------------------------------------------------

    def _rerank(
        self,
        query: str,
        candidate_ids: list[str],
        dense_lookup: dict[str, dict],
    ) -> list[str]:
        """Attempt CrossEncoder reranking; silently skip on import failure."""
        if self._ce_failed:
            return []
        try:
            if self._cross_encoder is None:
                from sentence_transformers import CrossEncoder  # noqa: PLC0415
                self._cross_encoder = CrossEncoder(self.cross_encoder_name)
        except Exception:  # noqa: BLE001
            self._ce_failed = True
            return []

        pairs: list[tuple[str, str]] = []
        valid_ids: list[str] = []
        for doc_id in candidate_ids:
            if doc_id in self._docs:
                text = self._docs[doc_id]["text"]
            elif doc_id in dense_lookup:
                text = dense_lookup[doc_id]["text"]
            else:
                continue
            pairs.append((query, text))
            valid_ids.append(doc_id)

        if not pairs:
            return []

        try:
            ce_scores = self._cross_encoder.predict(pairs)
        except Exception:  # noqa: BLE001
            self._ce_failed = True
            return []

        ranked = sorted(zip(valid_ids, ce_scores), key=lambda x: x[1], reverse=True)
        return [doc_id for doc_id, _ in ranked]

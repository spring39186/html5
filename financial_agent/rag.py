"""
rag.py — Production Multilingual RAG Orchestrator
==================================================
Composes retrieval.py, query_processing.py, and evidence.py into a
unified multi-query RAG pipeline with:
  - LLM-driven query rewriting / HyDE
  - Multi-subquery dense search + RRF fusion
  - Optional cross-encoder reranking
  - Evidence dataclass integration

All heavy dependencies (sentence-transformers, chromadb, tiktoken, …)
are injected as callables so the module is importable with stdlib only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

# ═══════════════════════════════════════════════════════════════
# 1. Query-Rewrite System Prompt
# ═══════════════════════════════════════════════════════════════

QUERY_REWRITE_SYSTEM: str = (
    "You are a financial search query optimizer. "
    "Given a user question in ANY language, produce retrieval-optimized English subqueries "
    "and a HyDE passage (hypothetical document embedding). "
    "\n\n"
    "Rules:\n"
    "1. Rewrite into the wording likely found in an English financial document (annual reports, "
    "   earnings releases, SEC filings, investor presentations).\n"
    "2. Decompose multi-part questions into 2–4 concise, focused English subqueries.\n"
    "3. Preserve company names, ticker symbols, years (e.g. FY2024), quarters (Q1–Q4), "
    "   and specific financial metric names exactly.\n"
    "4. Use standard financial terminology "
    "   (e.g. 'revenue' not 'sales amount', 'net income' not 'final profit', "
    "   'operating margin', 'EPS', 'CAPEX', 'EBITDA', 'ROE', 'ROA').\n"
    "5. Do NOT answer the question — output retrieval queries only.\n"
    "6. The 'hyde' field must be a short (2–4 sentences) hypothetical English answer paragraph "
    "   written as if it appears in a financial report. This is used for embedding-based recall.\n"
    "7. Output valid JSON only — no markdown fences, no explanation:\n"
    '   {"rewrites": ["focused english subquery 1", "subquery 2", ...],\n'
    '    "hyde": "a short hypothetical English answer paragraph for embedding-based recall"}'
)


# ═══════════════════════════════════════════════════════════════
# 2. RagConfig — cost-control knobs
# ═══════════════════════════════════════════════════════════════

@dataclass
class RagConfig:
    """Cost-control and tuning knobs for the RAG pipeline."""
    max_subqueries: int = 3        # cap on rewrites (+ HyDE) fed to dense_search
    dense_candidates: int = 20     # n_results per dense_search call
    top_k: int = 8                 # final hits returned from rag_search
    rerank_top_n: int = 20         # how many RRF candidates to pass to cross-encoder
    use_hyde: bool = True          # whether to use the HyDE passage as an extra query
    use_rerank: bool = False       # whether to call cross_encode when available
    max_evidence_tokens: int = 3000  # token budget for select_within_budget


# ═══════════════════════════════════════════════════════════════
# 3. RagDeps — injected callables
# ═══════════════════════════════════════════════════════════════

@dataclass
class RagDeps:
    """
    All external dependencies injected as callables so the module is
    fully offline-testable without any heavy imports.
    """
    llm_call: Callable[[list, float], str]
    # (messages: list[dict], temperature: float) -> str  [for query rewrite]

    dense_search: Callable[[str, int, Optional[dict]], list]
    # (query: str, n: int, where_filter: dict|None) -> list[{"id","text","metadata","score"}]

    cross_encode: Optional[Callable[[str, list], list]] = None
    # (query: str, passages: list[str]) -> list[float]  — None = skip rerank


# ═══════════════════════════════════════════════════════════════
# 4. Internal JSON helpers
# ═══════════════════════════════════════════════════════════════

def _extract_json(text: str) -> dict:
    """Strip markdown fences and parse the outermost JSON object."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start: end + 1]
        else:
            raise ValueError(f"No JSON object found: {text[:200]!r}")
    return json.loads(candidate)


# ═══════════════════════════════════════════════════════════════
# 5. rewrite_query
# ═══════════════════════════════════════════════════════════════

def rewrite_query(query: str, deps: RagDeps, cfg: RagConfig) -> dict:
    """
    Call the LLM to rewrite *query* into English subqueries and a HyDE passage.

    Parameters
    ----------
    query : raw user query (any language)
    deps  : injected dependencies
    cfg   : pipeline configuration

    Returns
    -------
    dict with keys:
        "rewrites" : list[str]  (English subqueries, capped to cfg.max_subqueries)
        "hyde"     : str        (hypothetical answer passage; "" on failure)
        "all_queries": list[str] (rewrites + optional hyde; used by rag_search)
    """
    _fallback = {"rewrites": [query], "hyde": "", "all_queries": [query]}
    try:
        messages = [
            {"role": "system", "content": QUERY_REWRITE_SYSTEM},
            {"role": "user",   "content": query},
        ]
        raw = deps.llm_call(messages, 0.1)
        parsed = _extract_json(raw)

        rewrites = parsed.get("rewrites", [])
        if not isinstance(rewrites, list) or not rewrites:
            return _fallback
        # Ensure all elements are non-empty strings
        rewrites = [r for r in rewrites if isinstance(r, str) and r.strip()]
        if not rewrites:
            return _fallback

        hyde = parsed.get("hyde", "")
        if not isinstance(hyde, str):
            hyde = ""

        # Cap subqueries
        rewrites = rewrites[: cfg.max_subqueries]

        # Build combined query list (hyde appended as extra retrieval query)
        all_queries: list[str] = list(rewrites)
        if cfg.use_hyde and hyde.strip():
            all_queries.append(hyde.strip())

        return {"rewrites": rewrites, "hyde": hyde, "all_queries": all_queries}

    except Exception:  # noqa: BLE001
        return _fallback


# ═══════════════════════════════════════════════════════════════
# 6. rag_search  — the full pipeline
# ═══════════════════════════════════════════════════════════════

def rag_search(
    query: str,
    deps: RagDeps,
    cfg: Optional[RagConfig] = None,
    where_filter: Optional[dict] = None,
    debug: bool = False,
) -> dict:
    """
    Full multilingual RAG pipeline:
      a) rewrite_query  → English subqueries (+ optional HyDE)
      b) dense_search   → candidate hits per subquery
      c) RRF fusion     → unified ranking across subqueries
      d) optional cross-encoder rerank
      e) return top_k hits

    Parameters
    ----------
    query        : raw user query (any language)
    deps         : RagDeps with injected callables
    cfg          : RagConfig (defaults used when None)
    where_filter : ChromaDB metadata filter forwarded to dense_search
    debug        : if True, include a "debug" key in the return dict

    Returns
    -------
    {
      "hits": [{"id","text","metadata","score"}, ...],   # top_k results
      "debug": {...}                                      # only when debug=True
    }
    """
    if cfg is None:
        cfg = RagConfig()

    # ── a) Query rewrite ─────────────────────────────────────
    rewrite_result = rewrite_query(query, deps, cfg)
    all_queries = rewrite_result["all_queries"]
    rewrites = rewrite_result["rewrites"]

    # ── b) Dense search per subquery ─────────────────────────
    # Accumulate: id → best doc dict; per-subquery ranked id lists for RRF
    doc_pool: dict[str, dict] = {}           # id → {"id","text","metadata","score"}
    per_subquery_rankings: list[list[str]] = []
    per_subquery_hits: dict[str, list[str]] = {}

    for subq in all_queries:
        try:
            hits = deps.dense_search(subq, cfg.dense_candidates, where_filter)
        except Exception:  # noqa: BLE001
            hits = []
        ranked_ids: list[str] = []
        for h in hits:
            doc_id = h.get("id")
            if not doc_id:
                continue
            ranked_ids.append(doc_id)
            if doc_id not in doc_pool:
                doc_pool[doc_id] = {
                    "id":       doc_id,
                    "text":     h.get("text", ""),
                    "metadata": h.get("metadata") or {},
                    "score":    h.get("score", 0.0),
                }
        per_subquery_rankings.append(ranked_ids)
        per_subquery_hits[subq] = ranked_ids

    # ── c) RRF fusion ────────────────────────────────────────
    # Lazy import — retrieval.py is a project file (always present).
    from retrieval import reciprocal_rank_fusion  # noqa: PLC0415

    if per_subquery_rankings:
        rrf_ranking: list[tuple[str, float]] = reciprocal_rank_fusion(per_subquery_rankings)
    else:
        rrf_ranking = []

    # ── d) Optional cross-encoder rerank ─────────────────────
    # Take top rerank_top_n from RRF, then reorder by cross-encoder scores.
    top_rrf_ids = [doc_id for doc_id, _ in rrf_ranking[: cfg.rerank_top_n]]
    rrf_score_map = dict(rrf_ranking)

    reranked_ids: list[str] = []
    if cfg.use_rerank and deps.cross_encode is not None and top_rrf_ids:
        passages = [doc_pool[doc_id]["text"] for doc_id in top_rrf_ids
                    if doc_id in doc_pool]
        valid_ids = [doc_id for doc_id in top_rrf_ids if doc_id in doc_pool]
        if passages:
            try:
                ce_scores = deps.cross_encode(query, passages)
                ranked = sorted(
                    zip(valid_ids, ce_scores),
                    key=lambda x: x[1],
                    reverse=True,
                )
                reranked_ids = [doc_id for doc_id, _ in ranked]
            except Exception:  # noqa: BLE001
                reranked_ids = top_rrf_ids
        else:
            reranked_ids = top_rrf_ids
    else:
        reranked_ids = top_rrf_ids

    # ── e) Build final hits ───────────────────────────────────
    final_ids = reranked_ids[: cfg.top_k]
    hits: list[dict] = []
    seen: set[str] = set()
    for doc_id in final_ids:
        if doc_id in seen:
            continue
        seen.add(doc_id)
        doc = doc_pool.get(doc_id)
        if doc is None:
            continue
        hits.append({
            "id":       doc_id,
            "text":     doc["text"],
            "metadata": doc["metadata"],
            "score":    rrf_score_map.get(doc_id, doc.get("score", 0.0)),
        })

    out: dict = {"hits": hits}
    if debug:
        out["debug"] = {
            "rewrites":          rewrites,
            "hyde":              rewrite_result.get("hyde", ""),
            "all_queries":       all_queries,
            "per_subquery_hits": per_subquery_hits,
            "rrf_ranking":       rrf_ranking,
            "reranked_ids":      reranked_ids,
        }
    return out


# ═══════════════════════════════════════════════════════════════
# 7. to_evidence — convert rag_search hits to Evidence objects
# ═══════════════════════════════════════════════════════════════

def to_evidence(hits: list, query: str) -> list:
    """
    Convert rag_search hit dicts into Evidence objects.

    Lazy-imports evidence.Evidence so this module stays stdlib-only at
    import time.

    Parameters
    ----------
    hits  : list of dicts from rag_search (each has id/text/metadata/score)
    query : the original user query (stored as Evidence.query)

    Returns
    -------
    list[evidence.Evidence]
    """
    from evidence import Evidence  # noqa: PLC0415

    result = []
    for h in hits:
        meta = h.get("metadata") or {}
        source = (
            meta.get("source_file")
            or meta.get("file_name")
            or h.get("id", "unknown")
        )
        ev = Evidence(
            source=source,
            query=query,
            content=h.get("text", ""),
            relevance=float(h.get("score", 0.0)),
            type="rag",
        )
        result.append(ev)
    return result

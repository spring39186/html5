"""
retrieval_debug.py — Phase 7: Retrieval Observability
======================================================
Stdlib-only.  Zero cost when disabled.

Design Principles
-----------------
- The module-level toggle ``is_enabled()`` reads the ``FA_RETRIEVAL_DEBUG``
  environment variable (any non-empty / truthy string enables it).
- ``maybe_new()`` returns **None** when disabled so that callers can write:

      dbg = maybe_new()
      if dbg is not None:
          dbg.vector_hits = ...

  and incur zero overhead on the hot path in production.

- ``RetrievalDebug.to_dict()`` produces a plain JSON-serialisable dict so
  it can be stored verbatim in the LangGraph ``AgentState`` under the key
  ``"retrieval_debug"``.

Environment Variable
--------------------
FA_RETRIEVAL_DEBUG  — set to any non-empty string to enable (e.g. "1", "true").
                      Unset or empty → disabled (default).

LangGraph AgentState Integration
---------------------------------
Recommended AgentState field::

    from typing import TypedDict, Optional
    class AgentState(TypedDict):
        ...
        retrieval_debug: Optional[dict]   # stores RetrievalDebug.to_dict()

In a retrieval node::

    from retrieval_debug import maybe_new
    dbg = maybe_new()
    # ... run retrieval, populate dbg fields ...
    state["retrieval_debug"] = dbg.to_dict() if dbg is not None else None
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════
# 1. Module-level toggle
# ═══════════════════════════════════════════════════════════════

_ENV_VAR = "FA_RETRIEVAL_DEBUG"


def is_enabled() -> bool:
    """
    Return True when the ``FA_RETRIEVAL_DEBUG`` env var is set to a
    non-empty string; False otherwise.

    Callers MUST NOT cache this value at import time so that tests can
    toggle it freely via ``os.environ``.
    """
    return bool(os.environ.get(_ENV_VAR, "").strip())


# ═══════════════════════════════════════════════════════════════
# 2. RetrievalDebug dataclass
# ═══════════════════════════════════════════════════════════════

@dataclass
class RetrievalDebug:
    """
    Captures every intermediate state of the retrieval pipeline for a
    single query so engineers can diagnose ranking, fusion, and context
    selection behaviour without affecting production performance when
    the toggle is off.

    Fields
    ------
    translated_query   : Query after language translation (Phase 2).
    expanded_terms     : Synonym / expansion terms added to the query.
    entities           : Structured entities extracted from the query
                         (company, year, quarter, metric, …).
    vector_hits        : Ranked doc IDs returned by the dense/vector search.
    bm25_hits          : Ranked doc IDs returned by BM25 sparse search.
    rrf_ranking        : Fused ranking from Reciprocal Rank Fusion,
                         as a list of (id, score) pairs or just IDs.
    reranked_results   : Doc IDs after CrossEncoder reranking (may be empty).
    selected_context   : Final doc IDs / chunks sent to the LLM as context.
    """

    translated_query: str = ""
    expanded_terms: list = field(default_factory=list)
    entities: dict = field(default_factory=dict)
    vector_hits: list = field(default_factory=list)
    bm25_hits: list = field(default_factory=list)
    rrf_ranking: list = field(default_factory=list)
    reranked_results: list = field(default_factory=list)
    selected_context: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Return a JSON-serialisable dict representation.

        The output is suitable for storage in LangGraph ``AgentState``::

            state["retrieval_debug"] = dbg.to_dict()

        and for logging / tracing pipelines that require plain dicts.
        """
        return {
            "translated_query": self.translated_query,
            "expanded_terms": list(self.expanded_terms),
            "entities": dict(self.entities),
            "vector_hits": list(self.vector_hits),
            "bm25_hits": list(self.bm25_hits),
            "rrf_ranking": list(self.rrf_ranking),
            "reranked_results": list(self.reranked_results),
            "selected_context": list(self.selected_context),
        }


# ═══════════════════════════════════════════════════════════════
# 3. Factory
# ═══════════════════════════════════════════════════════════════

def maybe_new(enabled: bool | None = None) -> RetrievalDebug | None:
    """
    Return a fresh ``RetrievalDebug`` instance when debug capture is active,
    or **None** when it is disabled.

    Parameters
    ----------
    enabled : bool | None
        Explicit override.  When None (default) the value is read from
        ``is_enabled()`` / ``FA_RETRIEVAL_DEBUG`` env var.
        Pass ``True`` to force-enable (e.g. in unit tests without setting
        the env var); pass ``False`` to force-disable.

    Usage
    -----
    ::

        dbg = maybe_new()
        if dbg is not None:
            dbg.translated_query = translated
            dbg.vector_hits = [r["id"] for r in dense_results]
        ...
        # Store in state — safe whether dbg is None or a dataclass:
        state["retrieval_debug"] = dbg.to_dict() if dbg is not None else None
    """
    if enabled is None:
        enabled = is_enabled()
    if not enabled:
        return None
    return RetrievalDebug()

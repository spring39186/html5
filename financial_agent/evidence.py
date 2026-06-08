"""
evidence.py — Structured evidence management for Financial RAG agent
=====================================================================
Provides deduplication, ranking, token-budgeting, and prompt rendering
for evidence items gathered from RAG, SQL, and OCR pipelines.

Public API
----------
Evidence          dataclass — one piece of retrieved evidence
dedup(items)      remove near-duplicate content; prefer sql over rag
rank(items)       stable-sort by relevance desc
estimate_tokens   char-based with optional tiktoken cl100k_base
select_within_budget  dedup → rank → token-budget → top-k cap
to_prompt_block   render as numbered [E1] … block for synthesizer
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List

# ---------------------------------------------------------------------------
# 1. Evidence dataclass
# ---------------------------------------------------------------------------

_VALID_TYPES = {"rag", "sql", "ocr"}

@dataclass
class Evidence:
    """One piece of retrieved evidence from any collection pathway."""
    source: str
    query: str
    content: str
    relevance: float = 0.0
    type: str = "rag"          # one of {"rag", "sql", "ocr"}

    def __post_init__(self) -> None:
        if self.type not in _VALID_TYPES:
            raise ValueError(f"Evidence.type must be one of {_VALID_TYPES}, got {self.type!r}")


# ---------------------------------------------------------------------------
# 2. Helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lowercase + collapse whitespace — used for content-level dedup."""
    return re.sub(r"\s+", " ", text.lower()).strip()


def _content_hash(text: str) -> str:
    return hashlib.sha256(_normalise(text).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 3. dedup
# ---------------------------------------------------------------------------

_TYPE_PRIORITY = {"sql": 0, "ocr": 1, "rag": 2}


def dedup(items: List[Evidence]) -> List[Evidence]:
    """
    Remove near-duplicate Evidence items.

    Two items are considered duplicates when their normalised content hashes
    match.  Among duplicates the one with the *highest relevance* is kept;
    ties are broken by type priority (sql > ocr > rag).

    Parameters
    ----------
    items : list of Evidence

    Returns
    -------
    Deduplicated list (order: first occurrence of each unique hash, but
    the representative for each group is the highest-relevance copy).
    """
    # bucket[hash] = best Evidence so far
    bucket: dict[str, Evidence] = {}
    # preserve insertion order for first-seen hashes
    order: list[str] = []

    for item in items:
        h = _content_hash(item.content)
        if h not in bucket:
            bucket[h] = item
            order.append(h)
        else:
            existing = bucket[h]
            # prefer higher relevance; break ties by type priority (lower = better)
            if (item.relevance, -_TYPE_PRIORITY.get(item.type, 99)) > \
               (existing.relevance, -_TYPE_PRIORITY.get(existing.type, 99)):
                bucket[h] = item

    return [bucket[h] for h in order]


# ---------------------------------------------------------------------------
# 4. rank
# ---------------------------------------------------------------------------

def rank(items: List[Evidence]) -> List[Evidence]:
    """
    Stable sort by relevance descending.

    Parameters
    ----------
    items : list of Evidence

    Returns
    -------
    New list sorted by relevance (highest first).  The original list is
    untouched.  Equal-relevance items maintain their relative order.
    """
    return sorted(items, key=lambda e: e.relevance, reverse=True)


# ---------------------------------------------------------------------------
# 5. estimate_tokens
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_tiktoken_encoder():
    """Lazily load and cache the cl100k_base tiktoken encoder."""
    try:
        import tiktoken  # noqa: PLC0415
        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001
        return None


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for *text*.

    Tries tiktoken cl100k_base (lazy, cached).  Falls back to the
    conservative approximation ``max(1, len(text) // 4)`` when tiktoken
    is unavailable or fails.

    Parameters
    ----------
    text : str

    Returns
    -------
    int — estimated token count (>= 1)
    """
    enc = _get_tiktoken_encoder()
    if enc is not None:
        try:
            return max(1, len(enc.encode(text)))
        except Exception:  # noqa: BLE001
            pass
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# 6. select_within_budget
# ---------------------------------------------------------------------------

def select_within_budget(
    items: List[Evidence],
    max_tokens: int = 3000,
    top_k: int = 12,
) -> List[Evidence]:
    """
    Select evidence items that fit within a token budget.

    Pipeline: dedup → rank → greedily take whole items until the token
    budget OR top_k is exhausted.  The first (highest-relevance) item is
    always included, even if it alone exceeds the budget.

    Parameters
    ----------
    items     : list of Evidence (may be empty)
    max_tokens: hard token cap across all selected items (default 3000)
    top_k     : maximum number of items to return (default 12)

    Returns
    -------
    list of Evidence — never empty when *items* is non-empty.
    """
    if not items:
        return []

    candidates = rank(dedup(items))
    selected: List[Evidence] = []
    used_tokens = 0

    for idx, ev in enumerate(candidates):
        if idx >= top_k:
            break
        cost = estimate_tokens(ev.content)
        # Always include the first item regardless of budget
        if idx == 0 or used_tokens + cost <= max_tokens:
            selected.append(ev)
            used_tokens += cost
        # Once budget exceeded (after first item), stop
        elif used_tokens >= max_tokens:
            break

    return selected


# ---------------------------------------------------------------------------
# 7. to_prompt_block
# ---------------------------------------------------------------------------

def to_prompt_block(items: List[Evidence]) -> str:
    """
    Render a list of Evidence items as a numbered citation block.

    Format::

        [E1] (source｜type) content…
        [E2] (source｜type) content…
        …

    The synthesizer can reference items as [E1], [E2], etc.

    Parameters
    ----------
    items : list of Evidence

    Returns
    -------
    str — ready-to-embed prompt section
    """
    lines: list[str] = []
    for i, ev in enumerate(items, start=1):
        header = f"[E{i}] ({ev.source}｜{ev.type})"
        lines.append(f"{header} {ev.content}")
    return "\n\n".join(lines)

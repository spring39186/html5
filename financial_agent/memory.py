"""
memory.py — Lightweight semantic memory for the financial RAG agent
====================================================================
Provides a ``ConversationMemory`` dataclass that persists across turns of a
conversation so that:

  * Already-parsed PDFs are NOT re-parsed on follow-up questions,
    eliminating redundant OCR and vectorisation calls.
  * Resolved entities (company names, fiscal years, ticker symbols) are
    recalled automatically so the planner/synthesiser does not have to
    rediscover them on every turn.
  * The memory can be serialised to a plain JSON dict and reconstructed
    from it, enabling persistence between sessions (file, Redis, etc.).

Module-level imports: stdlib ONLY — ``dataclasses``, ``json``.
No third-party dependencies.

Integration sketch (see module docstring at bottom for full details):

    mem = ConversationMemory()

    # When a PDF is first parsed:
    if not mem.is_parsed(file_name):
        result = parse_financial_pdf(...)
        mem.mark_parsed(file_name)
        mem.remember_entities({"company": "ACME Corp", "years": [2024]})

    # Before calling the planner/synthesiser:
    context_snippet = summarize_for_prompt(mem)   # inject into system prompt

    # Persist across HTTP requests:
    state = mem.to_dict()           # store in session / DB
    mem2 = ConversationMemory.from_dict(state)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set


# ---------------------------------------------------------------------------
# ConversationMemory dataclass
# ---------------------------------------------------------------------------

@dataclass
class ConversationMemory:
    """Per-conversation memory store for the financial RAG agent.

    Attributes
    ----------
    parsed_files : set[str]
        Names of PDF/document files that have already been OCR-processed and
        vectorised into the knowledge base during this session.  Prevents
        redundant parse calls on follow-up questions.
    entities : dict
        Resolved named entities discovered while processing documents.
        Common keys: "company" (str), "years" (list[int]),
        "tickers" (list[str]).  Lists are union-merged on update so no
        information is lost across turns.
    file_year_map : dict[str, str | int]
        Maps each parsed file name to the fiscal year detected in its content
        (e.g. ``{"report_2024.pdf": 2024}``).  Helps the planner locate the
        correct document when the user asks about a specific year.
    notes : list[str]
        Free-form notes appended during processing (e.g. "page 3 is scanned",
        "translation normalised to EN").  Useful for debugging and for giving
        the synthesiser extra context.
    """

    parsed_files: Set[str] = field(default_factory=set)
    entities: Dict[str, Any] = field(default_factory=dict)
    file_year_map: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Parsed-file tracking
    # ------------------------------------------------------------------

    def mark_parsed(self, file: str) -> None:
        """Record that *file* has been parsed and vectorised.

        Subsequent calls to ``is_parsed(file)`` return True, allowing
        callers to skip redundant OCR and embedding work.
        """
        self.parsed_files.add(file)

    def is_parsed(self, file: str) -> bool:
        """Return True if *file* has already been parsed in this session."""
        return file in self.parsed_files

    # ------------------------------------------------------------------
    # Entity management
    # ------------------------------------------------------------------

    def remember_entities(self, d: Dict[str, Any]) -> None:
        """Merge *d* into the entity store.

        Merge strategy:
        - For list-valued keys: union (deduplication preserves order of
          first occurrence, new items appended at end).
        - For scalar-valued keys: the incoming value overwrites the existing
          one (last-write wins — callers should pass the most precise value).
        - New keys are simply added.

        Example
        -------
        >>> mem = ConversationMemory()
        >>> mem.remember_entities({"company": "ACME", "years": [2023]})
        >>> mem.remember_entities({"years": [2023, 2024], "tickers": ["ACME"]})
        >>> mem.entities
        {"company": "ACME", "years": [2023, 2024], "tickers": ["ACME"]}
        """
        for key, value in d.items():
            if isinstance(value, list):
                existing = self.entities.get(key)
                if isinstance(existing, list):
                    # Union: keep order, no duplicates
                    seen = set()
                    merged = []
                    for item in existing + value:
                        if item not in seen:
                            seen.add(item)
                            merged.append(item)
                    self.entities[key] = merged
                else:
                    # Key did not exist or was not a list — replace
                    self.entities[key] = list(value)
            else:
                # Scalar: last-write-wins
                self.entities[key] = value

    def recall_entities(self) -> Dict[str, Any]:
        """Return a shallow copy of the current entity store."""
        return dict(self.entities)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise memory to a JSON-compatible plain dict.

        The ``parsed_files`` set is converted to a sorted list so the
        representation is stable across runs (useful for tests and diffs).
        """
        return {
            "parsed_files": sorted(self.parsed_files),
            "entities": dict(self.entities),
            "file_year_map": dict(self.file_year_map),
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationMemory":
        """Reconstruct a ``ConversationMemory`` from a plain dict.

        Accepts the output of ``to_dict()`` (or any compatible mapping).
        Missing keys fall back to empty defaults, so partial dicts are safe.

        Parameters
        ----------
        data : dict
            Plain dict, typically deserialised from JSON.

        Returns
        -------
        ConversationMemory
        """
        obj = cls()
        obj.parsed_files = set(data.get("parsed_files", []))
        obj.entities = dict(data.get("entities", {}))
        obj.file_year_map = dict(data.get("file_year_map", {}))
        obj.notes = list(data.get("notes", []))
        return obj


# ---------------------------------------------------------------------------
# summarize_for_prompt
# ---------------------------------------------------------------------------

def summarize_for_prompt(mem: ConversationMemory) -> str:
    """Produce a short context snippet for injection into a planner/synthesiser prompt.

    The string is intentionally compact (suitable as a few lines in a system
    prompt) and bilingual (Chinese labels, English/original values) so both
    Chinese-first and English-first models can parse it.

    Parameters
    ----------
    mem : ConversationMemory
        The current session memory.

    Returns
    -------
    str
        A human-readable summary such as::

            已解析檔案: report_2024.pdf, report_2023.pdf；
            已知公司: ACME Corp；已知年度: 2023, 2024；
            已知代號: ACME
    """
    parts: List[str] = []

    # Parsed files
    if mem.parsed_files:
        files_str = ", ".join(sorted(mem.parsed_files))
        parts.append(f"已解析檔案: {files_str}")
    else:
        parts.append("已解析檔案: （無）")

    # Entities
    entities = mem.recall_entities()

    company = entities.get("company")
    if company:
        parts.append(f"已知公司: {company}")

    years = entities.get("years")
    if years:
        years_str = ", ".join(str(y) for y in years)
        parts.append(f"已知年度: {years_str}")

    tickers = entities.get("tickers")
    if tickers:
        tickers_str = ", ".join(str(t) for t in tickers)
        parts.append(f"已知代號: {tickers_str}")

    # Any extra entity keys beyond the standard three
    extra_keys = [k for k in entities if k not in ("company", "years", "tickers")]
    for k in extra_keys:
        v = entities[k]
        if isinstance(v, list):
            v = ", ".join(str(i) for i in v)
        parts.append(f"{k}: {v}")

    # File-year map (only if populated)
    if mem.file_year_map:
        fym_parts = [f"{fn}→{yr}" for fn, yr in mem.file_year_map.items()]
        parts.append(f"檔案年度對照: {', '.join(fym_parts)}")

    # Notes (truncated to keep the prompt lean)
    if mem.notes:
        note_preview = mem.notes[-1]   # show only the latest note to save tokens
        if len(note_preview) > 120:
            note_preview = note_preview[:120] + "…"
        parts.append(f"備註: {note_preview}")

    return "；\n".join(parts)

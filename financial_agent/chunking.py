"""
chunking.py — Phase 2: Structural Chunking + Rich Metadata
===========================================================

Replaces the RecursiveCharacterTextSplitter in agent.py's parse_financial_pdf()
with structure-aware chunking that treats Markdown tables as atomic units and
attaches rich metadata to every chunk.

The existing ingestion pipeline in agent.py uses::

    collection.add(
        ids=[...],
        documents=[...],
        metadatas=[...],
    )

This module's output maps directly:

    chunks = chunk_markdown(text, source_file="report.pdf")
    collection.add(
        ids       = [c["id"]       for c in chunks],   # c["id"]
        documents = [c["text"]     for c in chunks],   # c["text"]
        metadatas = [c["metadata"] for c in chunks],   # c["metadata"]
    )

ChromaDB requires all metadata values to be scalar (str / int / float / bool /
None). This module guarantees that invariant — no lists or dicts are ever
written into the metadata dict.

Backward-compatibility note
---------------------------
The legacy agent.py metadatas used only {"file_name", "chunk_index",
"total_chunks"}.  Those three keys are preserved (source_file → "source_file"
instead of "file_name" — pass source_file as base_metadata["file_name"] when
calling from agent.py if you need the exact old key name).

Environment / import constraints
---------------------------------
- No internet access, no Ollama.
- `langchain_text_splitters` is imported lazily and only as a performance
  optimisation — the module works identically without it.
- All top-level imports are from the Python 3 standard library only.

Usage::

    from chunking import chunk_markdown, infer_doc_metadata

    chunks = chunk_markdown(
        text          = full_text,          # Markdown output of OCR pipeline
        source_file   = "迅銷2024_eng.pdf",
        base_metadata = {"file_name": "迅銷2024_eng.pdf"},  # merged first
        chunk_size    = 1200,
        chunk_overlap = 150,
    )
    # chunks: list[{"id": str, "text": str, "metadata": dict}]
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Regex that matches page-marker headings produced by the OCR pipeline.
#: Example: "## 第 3 頁"
_PAGE_MARKER_RE = re.compile(r"^##\s*第\s*(\d+)\s*頁\s*$", re.MULTILINE)

#: Matches any Markdown heading (ATX style: one to six '#' characters).
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)", re.MULTILINE)

#: A line belongs to a Markdown table when it starts with '|' (after stripping).
_TABLE_LINE_RE = re.compile(r"^\s*\|")

#: A separator row looks like |---|--- (contains only '|', '-', ':', spaces).
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s\-:\|]+\|?\s*$")

#: CJK Unicode ranges used by language detection heuristic.
_CJK_RANGES = [
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x3400, 0x4DBF),    # CJK Extension A
    (0x3040, 0x309F),    # Hiragana
    (0x30A0, 0x30FF),    # Katakana
    (0xAC00, 0xD7AF),    # Hangul Syllables
    (0x20000, 0x2A6DF),  # CJK Extension B
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
]

_HIRAGANA_RANGE = (0x3040, 0x309F)
_KATAKANA_RANGE = (0x30A0, 0x30FF)
_HANGUL_RANGE   = (0xAC00, 0xD7AF)


# ---------------------------------------------------------------------------
# 1. Metadata inference
# ---------------------------------------------------------------------------

def infer_doc_metadata(source_file: str, text: str) -> dict:
    """
    Heuristically extract document-level metadata from the filename and the
    first 500 characters of the text.

    Heuristics applied (in order, documented inline):

    company
        Take the filename stem (no extension), strip trailing digits, underscores,
        and common suffixes ("_eng", "_en", "_zh", "_report"), then return the
        remaining prefix.  Example: "迅銷2024_eng" → "迅銷".

    year
        Search for a 4-digit year ``\\b(19|20)\\d{2}\\b`` in the filename first,
        then in the first 500 characters of the text.  Returns the first match
        as ``int``.

    quarter
        Search for ``Q[1-4]`` (case-insensitive) in the filename then text.

    document_type
        "annual_report" when "annual", "年報", or "年度" appears anywhere in
        filename or first 500 chars; otherwise "financial_doc".

    language_original
        Sample the first 1 000 characters of the text.
        - Hiragana / Katakana characters → "ja"
        - CJK characters (non-Japanese) → "zh"
        - Otherwise → "en"
        (Hangul → "ko" is also detected for completeness.)

    Returns
    -------
    dict with keys: company, year, quarter, document_type, language_original.
    All values are str, int, or None (scalars suitable for ChromaDB metadata).
    """
    stem = Path(source_file).stem

    # ── Company ──────────────────────────────────────────────────────────────
    # Strip common English suffixes and trailing digits / separators.
    company_raw = re.sub(
        r"(_eng|_en|_zh|_report|_annual|_financial|_doc)\b", "",
        stem, flags=re.IGNORECASE
    )
    # Remove trailing digits, underscores, hyphens, and whitespace.
    company_raw = re.sub(r"[\d_\-\s]+$", "", company_raw).strip()
    company: Optional[str] = company_raw if company_raw else None

    # ── Year ─────────────────────────────────────────────────────────────────
    # Use digit-boundary lookarounds instead of \b so the pattern fires even
    # when a 4-digit year is directly adjacent to CJK characters (e.g.
    # "迅銷2024_eng" — CJK chars are \w in Python so \b does not fire between
    # a CJK character and a digit).
    _year_re = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
    year_match = _year_re.search(stem) or _year_re.search(text[:500])
    year: Optional[int] = int(year_match.group(1)) if year_match else None

    # ── Quarter ───────────────────────────────────────────────────────────────
    # Use character-class lookarounds instead of \b: \b fails when Q is
    # immediately preceded by '_' (also a \w char), e.g. "_Q3_" in a filename.
    _quarter_re = re.compile(r"(?<![A-Za-z0-9])(Q[1-4])(?![0-9])", re.IGNORECASE)
    q_match = _quarter_re.search(stem) or _quarter_re.search(text[:500])
    quarter: Optional[str] = q_match.group(1).upper() if q_match else None

    # ── Document type ─────────────────────────────────────────────────────────
    _annual_keywords = re.compile(r"annual|年報|年度", re.IGNORECASE)
    doc_type_haystack = stem + " " + text[:500]
    document_type = (
        "annual_report" if _annual_keywords.search(doc_type_haystack)
        else "financial_doc"
    )

    # ── Language ──────────────────────────────────────────────────────────────
    sample = text[:1000]
    has_hiragana = any(
        _HIRAGANA_RANGE[0] <= ord(c) <= _HIRAGANA_RANGE[1] for c in sample
    )
    has_katakana = any(
        _KATAKANA_RANGE[0] <= ord(c) <= _KATAKANA_RANGE[1] for c in sample
    )
    has_hangul = any(
        _HANGUL_RANGE[0] <= ord(c) <= _HANGUL_RANGE[1] for c in sample
    )
    has_cjk = any(
        any(lo <= ord(c) <= hi for lo, hi in _CJK_RANGES)
        for c in sample
    )

    if has_hiragana or has_katakana:
        language_original: Optional[str] = "ja"
    elif has_hangul:
        language_original = "ko"
    elif has_cjk:
        language_original = "zh"
    else:
        language_original = "en"

    return {
        "company":           company,
        "year":              year,
        "quarter":           quarter,
        "document_type":     document_type,
        "language_original": language_original,
    }


# ---------------------------------------------------------------------------
# 2. Low-level text segmentation helpers
# ---------------------------------------------------------------------------

def _is_table_line(line: str) -> bool:
    """Return True if the line appears to be part of a Markdown table."""
    return bool(_TABLE_LINE_RE.match(line))


def _block_has_separator(lines: list[str]) -> bool:
    """Return True if any line in `lines` looks like a table separator row."""
    return any(_TABLE_SEP_RE.match(ln) for ln in lines)


def _split_into_segments(page_text: str) -> list[dict]:
    """
    Split page text into a list of segments. Each segment is one of:
      - {"type": "table",   "text": str, "heading": str|None}
      - {"type": "prose",   "text": str, "heading": str|None}

    Algorithm
    ---------
    Walk line-by-line and accumulate lines. Whenever we enter or exit a
    contiguous block of table lines (lines starting with '|'), we flush the
    current accumulator as a segment of the appropriate type.

    A "table block" is only confirmed as a real Markdown table when at least
    one separator row (|---|) is present in the block.  A block of '|' lines
    WITHOUT a separator is treated as prose (edge case: code blocks, etc.).

    Headings (ATX: lines starting with #) are extracted and stored as
    ``heading`` on the next segment they precede.
    """
    lines = page_text.splitlines()

    segments: list[dict] = []
    prose_buf: list[str] = []
    table_buf: list[str] = []
    in_table = False
    current_heading: Optional[str] = None  # nearest heading seen so far

    def _flush_prose(buf: list[str], heading: Optional[str]) -> None:
        text = "\n".join(buf).strip()
        if text:
            segments.append({"type": "prose", "text": text, "heading": heading})

    def _flush_table(buf: list[str], heading: Optional[str]) -> None:
        text = "\n".join(buf).strip()
        if not text:
            return
        if _block_has_separator(buf):
            segments.append({"type": "table", "text": text, "heading": heading})
        else:
            # Treat as prose — no valid separator row.
            segments.append({"type": "prose", "text": text, "heading": heading})

    for line in lines:
        if _is_table_line(line):
            if not in_table:
                # Transition prose → table: flush prose accumulator.
                _flush_prose(prose_buf, current_heading)
                prose_buf = []
                in_table = True
            table_buf.append(line)
        else:
            if in_table:
                # Transition table → prose: flush table accumulator.
                _flush_table(table_buf, current_heading)
                table_buf = []
                in_table = False
            # Update heading tracker.
            h_match = _HEADING_RE.match(line)
            if h_match:
                current_heading = h_match.group(2).strip()
            prose_buf.append(line)

    # Flush whatever remains.
    if in_table:
        _flush_table(table_buf, current_heading)
    else:
        _flush_prose(prose_buf, current_heading)

    return segments


# ---------------------------------------------------------------------------
# 3. Greedy packing with overlap
# ---------------------------------------------------------------------------

def _pack_segments(
    segments: list[dict],
    chunk_size: int,
    chunk_overlap: int,
) -> list[dict]:
    """
    Greedily pack segments into chunks of approximately ``chunk_size`` chars.

    Rules
    -----
    - A **table** segment is always kept atomic — it is NEVER split, even if
      it exceeds ``chunk_size`` on its own.
    - **Prose** segments are packed greedily: keep adding until the next
      addition would push the current chunk over ``chunk_size``, then flush.
    - **Overlap**: when flushing a prose-only chunk, carry the trailing
      ``chunk_overlap`` characters into the next chunk's opening text.
    - A table always starts a new chunk (flush whatever we have first).
    - After a table, the following prose begins a fresh chunk (no overlap from
      tables since tables should remain intact and isolated).

    Returns
    -------
    list of {"text": str, "heading": str|None, "has_table": bool}
    """
    chunks: list[dict] = []
    current_parts: list[str] = []   # lines/blocks collected for the current chunk
    current_size: int = 0
    current_heading: Optional[str] = None
    current_has_table: bool = False
    overlap_tail: str = ""          # trailing text carried from previous chunk

    def _flush(carry_overlap: bool = True) -> None:
        nonlocal current_parts, current_size, current_heading, current_has_table, overlap_tail
        text = "\n\n".join(p for p in current_parts if p).strip()
        if text:
            chunks.append({
                "text":      text,
                "heading":   current_heading,
                "has_table": current_has_table,
            })
            if carry_overlap and not current_has_table and chunk_overlap > 0:
                overlap_tail = text[-chunk_overlap:]
            else:
                overlap_tail = ""
        current_parts = []
        current_size = 0
        current_heading = None
        current_has_table = False

    for seg in segments:
        seg_text = seg["text"]
        seg_len  = len(seg_text)
        seg_head = seg["heading"]

        if seg["type"] == "table":
            # Always flush before adding a table so it starts its own chunk.
            if current_parts:
                _flush(carry_overlap=False)
            # The table itself becomes its own chunk (atomic, no overlap).
            overlap_tail = ""
            chunks.append({
                "text":      seg_text,
                "heading":   seg_head if seg_head else current_heading,
                "has_table": True,
            })
            # Reset for the next chunk; no overlap out of a table.
            current_heading = seg_head  # inherit heading for next prose
            overlap_tail = ""

        else:  # prose
            # Add overlap tail at the start of a fresh chunk.
            effective_start = overlap_tail if (not current_parts and overlap_tail) else ""

            if current_size + seg_len + len(effective_start) > chunk_size and current_parts:
                # Flushing would make room; flush then start fresh.
                _flush(carry_overlap=True)
                effective_start = overlap_tail if overlap_tail else ""

            if not current_parts and effective_start:
                current_parts.append(effective_start)
                current_size += len(effective_start)
                overlap_tail = ""

            current_parts.append(seg_text)
            current_size += seg_len
            if seg_head:
                current_heading = seg_head

    if current_parts:
        _flush(carry_overlap=False)

    return chunks


# ---------------------------------------------------------------------------
# 4. Public API
# ---------------------------------------------------------------------------

def chunk_markdown(
    text: str,
    source_file: str,
    base_metadata: Optional[dict] = None,
    chunk_size: int = 1200,
    chunk_overlap: int = 150,
) -> list[dict]:
    """
    Structurally chunk a Markdown document produced by the OCR pipeline.

    Parameters
    ----------
    text : str
        Full Markdown text where each page is prefixed with ``## 第 N 頁``
        and pages are joined by ``\\n\\n---\\n\\n``.  After optional English
        normalisation the page markers remain intact.

    source_file : str
        Identifier used to construct chunk IDs and the ``source_file``
        metadata field.  Typically the PDF filename.

    base_metadata : dict, optional
        Extra metadata merged into every chunk's metadata dict *before*
        the per-chunk fields are set (per-chunk fields win on collision).
        Useful for passing legacy keys like ``{"file_name": "report.pdf"}``.

    chunk_size : int
        Approximate maximum character count per chunk (default 1 200).
        Tables that exceed this limit are kept whole anyway.

    chunk_overlap : int
        Number of characters from the end of a prose chunk to carry into
        the start of the following prose chunk (default 150).
        Tables never contribute overlap.

    Returns
    -------
    list of dict, each containing:
    ``{"id": str, "text": str, "metadata": dict}``

    The ``metadata`` dict contains these scalar keys (ChromaDB-safe):

    =========== ================================================================
    Key         Description
    =========== ================================================================
    source_file str  — value of the *source_file* parameter
    page        int|None — 1-based page number inferred from ``## 第 N 頁``
    section     str|None — nearest preceding ATX heading text inside this chunk
    document_type str — "annual_report" or "financial_doc"
    language_original str|None — "zh", "ja", "ko", or "en"
    language_normalized str — always "en" (post-normalisation target)
    company     str|None — heuristically extracted from filename
    year        int|None — e.g. 2024
    quarter     str|None — e.g. "Q3"
    chunk_index int — 0-based position among all chunks for this document
    total_chunks int — total number of chunks produced
    has_table   bool — True when the chunk contains a Markdown table
    =========== ================================================================

    Integration with ``collection.add``
    ------------------------------------
    ::

        chunks = chunk_markdown(text, source_file="report.pdf",
                                base_metadata={"file_name": "report.pdf"})
        collection.add(
            ids       = [c["id"]       for c in chunks],
            documents = [c["text"]     for c in chunks],
            metadatas = [c["metadata"] for c in chunks],
        )
    """
    base_metadata = base_metadata or {}

    # ── Infer document-level metadata ────────────────────────────────────────
    doc_meta = infer_doc_metadata(source_file, text)

    # ── Split document into per-page regions ─────────────────────────────────
    # Strategy: find all page-marker positions; the text between two consecutive
    # markers belongs to the earlier marker's page.
    page_regions: list[tuple[Optional[int], str]] = []

    markers = list(_PAGE_MARKER_RE.finditer(text))
    if markers:
        for i, m in enumerate(markers):
            page_num = int(m.group(1))
            # Content starts after the marker line and its newline.
            content_start = m.end()
            content_end   = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            page_content  = text[content_start:content_end].strip()
            # Remove page-separator "---" lines at the boundaries.
            page_content  = re.sub(r"^\s*---\s*", "", page_content).strip()
            page_content  = re.sub(r"\s*---\s*$", "", page_content).strip()
            page_regions.append((page_num, page_content))
    else:
        # No page markers — treat the whole document as a single page.
        page_regions.append((None, text))

    # ── Process each page ─────────────────────────────────────────────────────
    raw_chunks: list[dict] = []  # {"text", "heading", "has_table", "page"}

    for page_num, page_text in page_regions:
        if not page_text.strip():
            continue
        segments = _split_into_segments(page_text)
        packed   = _pack_segments(segments, chunk_size, chunk_overlap)
        for c in packed:
            c["page"] = page_num
            raw_chunks.append(c)

    # ── Assign ids and build final metadata ───────────────────────────────────
    total = len(raw_chunks)
    result: list[dict] = []

    for idx, rc in enumerate(raw_chunks):
        # Build metadata: base first (lowest priority), then doc-level, then
        # per-chunk fields (highest priority, will override anything above).
        meta: dict = {}
        meta.update(base_metadata)          # caller-supplied base
        meta.update(doc_meta)               # document-level inference

        # Per-chunk overrides (always scalar):
        meta["source_file"]         = str(source_file)
        meta["page"]                = rc["page"]         # int or None
        meta["section"]             = rc.get("heading")  # str or None
        meta["language_normalized"] = "en"
        meta["chunk_index"]         = idx
        meta["total_chunks"]        = total
        meta["has_table"]           = bool(rc["has_table"])

        # Guarantee all values are scalars (ChromaDB-safe).
        meta = _ensure_scalar_metadata(meta)

        result.append({
            "id":       f"{source_file}_chunk_{idx}",
            "text":     rc["text"],
            "metadata": meta,
        })

    return result


# ---------------------------------------------------------------------------
# 5. Metadata scalar enforcement
# ---------------------------------------------------------------------------

def _ensure_scalar_metadata(meta: dict) -> dict:
    """
    Return a copy of *meta* where every value is a ChromaDB-safe scalar
    (str, int, float, bool, or None).  Lists and dicts are JSON-serialised
    to strings.  Other non-scalar types are coerced with ``str()``.
    """
    import json as _json  # stdlib only

    safe: dict = {}
    for k, v in meta.items():
        if v is None or isinstance(v, (str, int, float, bool)):
            safe[k] = v
        elif isinstance(v, (list, dict)):
            safe[k] = _json.dumps(v, ensure_ascii=False)
        else:
            safe[k] = str(v)
    return safe

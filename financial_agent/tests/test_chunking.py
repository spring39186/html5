"""
tests/test_chunking.py
======================
Self-contained test suite for chunking.py (Phase 2: Structural Chunking).

Stdlib only.  Run with::

    python3 tests/test_chunking.py
    # or, from the financial_agent/ root:
    python3 -m pytest tests/test_chunking.py -v  (if pytest is installed)

Tests
-----
1.  Table atomicity — the full Markdown table appears intact in exactly one chunk.
2.  No table row is ever split across two chunks.
3.  Page numbers are attached correctly (page 1 vs page 2 chunks).
4.  All required metadata keys are present in every chunk.
5.  All metadata values are scalars (str / int / float / bool / None) — ChromaDB-safe.
6.  Chunk ids are unique and sequential (f"{source_file}_chunk_{i}").
7.  total_chunks is consistent across all chunks.
8.  has_table flag is True only on the table chunk; False on prose chunks.
9.  base_metadata values are forwarded into every chunk.
10. infer_doc_metadata extracts year + company from "迅銷2024_eng.pdf".
11. infer_doc_metadata: document_type = "annual_report" when keyword present.
12. infer_doc_metadata: quarter extracted from filename and from text.
13. infer_doc_metadata: language_original detection (zh / ja / en).
14. Large table exceeding chunk_size is kept whole (atomic).
15. Document with no page markers → page=None in metadata.
16. chunk_markdown with no content returns empty list (no crash).
17. section heading is tracked correctly in metadata.
"""

from __future__ import annotations

import os
import sys

# Allow running from any directory — insert the financial_agent root on sys.path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chunking import chunk_markdown, infer_doc_metadata

# ---------------------------------------------------------------------------
# Shared sample document
# ---------------------------------------------------------------------------

# Two-page markdown:
#   Page 1: prose + a proper Markdown table (with separator row).
#   Page 2: prose headings only (no table).
SAMPLE_MD = """\
## 第 1 頁

# Annual Financial Report

This is the executive summary for fiscal year 2024.
The company showed strong performance across all segments.

## Revenue Overview

Revenue grew significantly compared to the prior year.

| Quarter   | Revenue (M) | YoY Growth |
|-----------|-------------|------------|
| Q1 2024   | 1,234       | +12.5%     |
| Q2 2024   | 1,456       | +15.2%     |
| Q3 2024   | 1,678       | +18.1%     |
| Q4 2024   | 1,890       | +22.3%     |
| Full Year | 6,258       | +17.1%     |

---

## 第 2 頁

# Cost Structure

Operating expenses remained well controlled throughout the year.

## Key Metrics

Profit margin improved across all business segments in 2024.
The management team is focused on sustainable long-term growth.
"""

# The exact table text as it appears in SAMPLE_MD (no leading/trailing blank lines).
FULL_TABLE = (
    "| Quarter   | Revenue (M) | YoY Growth |\n"
    "|-----------|-------------|------------|\n"
    "| Q1 2024   | 1,234       | +12.5%     |\n"
    "| Q2 2024   | 1,456       | +15.2%     |\n"
    "| Q3 2024   | 1,678       | +18.1%     |\n"
    "| Q4 2024   | 1,890       | +22.3%     |\n"
    "| Full Year | 6,258       | +17.1%     |"
)

# A single data row that must not be split across chunks.
TABLE_ROW_SAMPLE = "| Q3 2024   | 1,678       | +18.1%     |"

SOURCE_FILE = "迅銷2024_eng.pdf"

REQUIRED_METADATA_KEYS = {
    "source_file",
    "page",
    "section",
    "document_type",
    "language_original",
    "language_normalized",
    "company",
    "year",
    "quarter",
    "chunk_index",
    "total_chunks",
    "has_table",
}

SCALAR_TYPES = (str, int, float, bool, type(None))

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_table_atomicity():
    """Test 1 + 2: Table must appear intact in exactly one chunk; no row is split."""
    chunks = chunk_markdown(SAMPLE_MD, source_file=SOURCE_FILE)

    # The full table text must appear verbatim inside exactly one chunk.
    table_chunks = [c for c in chunks if FULL_TABLE in c["text"]]
    _assert(
        len(table_chunks) == 1,
        f"Expected the full table to appear in exactly 1 chunk, got {len(table_chunks)}. "
        f"Chunks: {[c['text'][:80] for c in chunks]}"
    )

    # No individual table row may appear in more than one chunk.
    for row in [
        "| Q1 2024   | 1,234       | +12.5%     |",
        "| Q2 2024   | 1,456       | +15.2%     |",
        TABLE_ROW_SAMPLE,
        "| Q4 2024   | 1,890       | +22.3%     |",
        "| Full Year | 6,258       | +17.1%     |",
    ]:
        containing = [c for c in chunks if row in c["text"]]
        _assert(
            len(containing) == 1,
            f"Table row {row!r} found in {len(containing)} chunks (must be exactly 1)."
        )

    print("  PASS: test_table_atomicity")


def test_page_numbers():
    """Test 3: Page numbers must be attached to the correct chunks."""
    chunks = chunk_markdown(SAMPLE_MD, source_file=SOURCE_FILE)

    page1_chunks = [c for c in chunks if c["metadata"]["page"] == 1]
    page2_chunks = [c for c in chunks if c["metadata"]["page"] == 2]

    _assert(len(page1_chunks) >= 1, "Expected at least one chunk from page 1.")
    _assert(len(page2_chunks) >= 1, "Expected at least one chunk from page 2.")

    # The table belongs to page 1.
    table_chunk = next(c for c in chunks if FULL_TABLE in c["text"])
    _assert(
        table_chunk["metadata"]["page"] == 1,
        f"Table chunk should be on page 1, got page {table_chunk['metadata']['page']}."
    )

    # Page 2 content should have page=2.
    for c in page2_chunks:
        _assert(
            "Cost Structure" in c["text"] or "Key Metrics" in c["text"] or "Profit margin" in c["text"],
            f"Page 2 chunk doesn't look right: {c['text'][:80]!r}"
        )

    print("  PASS: test_page_numbers")


def test_required_metadata_keys():
    """Test 4: All required metadata keys must be present in every chunk."""
    chunks = chunk_markdown(SAMPLE_MD, source_file=SOURCE_FILE)
    _assert(len(chunks) > 0, "chunk_markdown returned no chunks.")
    for i, c in enumerate(chunks):
        missing = REQUIRED_METADATA_KEYS - set(c["metadata"].keys())
        _assert(
            not missing,
            f"Chunk {i} is missing metadata keys: {missing}"
        )
    print("  PASS: test_required_metadata_keys")


def test_metadata_scalar_values():
    """Test 5: All metadata values must be ChromaDB-safe scalars."""
    chunks = chunk_markdown(SAMPLE_MD, source_file=SOURCE_FILE)
    for i, c in enumerate(chunks):
        for k, v in c["metadata"].items():
            _assert(
                isinstance(v, SCALAR_TYPES),
                f"Chunk {i} metadata[{k!r}] = {v!r} (type {type(v).__name__}) is not a scalar."
            )
    print("  PASS: test_metadata_scalar_values")


def test_unique_sequential_ids():
    """Test 6 + 7: IDs are unique, sequential, and total_chunks is consistent."""
    chunks = chunk_markdown(SAMPLE_MD, source_file=SOURCE_FILE)
    ids = [c["id"] for c in chunks]

    # Unique.
    _assert(len(ids) == len(set(ids)), f"Chunk IDs are not unique: {ids}")

    # Sequential format.
    for i, (cid, chunk) in enumerate(zip(ids, chunks)):
        expected = f"{SOURCE_FILE}_chunk_{i}"
        _assert(cid == expected, f"Chunk {i} id={cid!r}, expected {expected!r}.")

    # chunk_index matches position.
    for i, c in enumerate(chunks):
        _assert(
            c["metadata"]["chunk_index"] == i,
            f"Chunk {i} has chunk_index={c['metadata']['chunk_index']}."
        )

    # total_chunks is consistent.
    total = len(chunks)
    for i, c in enumerate(chunks):
        _assert(
            c["metadata"]["total_chunks"] == total,
            f"Chunk {i} has total_chunks={c['metadata']['total_chunks']}, expected {total}."
        )

    print("  PASS: test_unique_sequential_ids")


def test_has_table_flag():
    """Test 8: has_table is True only for the chunk containing the table."""
    chunks = chunk_markdown(SAMPLE_MD, source_file=SOURCE_FILE)
    table_chunk = next((c for c in chunks if FULL_TABLE in c["text"]), None)
    _assert(table_chunk is not None, "Could not find the table chunk.")
    _assert(
        table_chunk["metadata"]["has_table"] is True,
        f"Table chunk has_table={table_chunk['metadata']['has_table']!r}, expected True."
    )
    for c in chunks:
        if FULL_TABLE not in c["text"]:
            _assert(
                c["metadata"]["has_table"] is False,
                f"Non-table chunk {c['id']} has has_table={c['metadata']['has_table']!r}."
            )
    print("  PASS: test_has_table_flag")


def test_base_metadata_forwarding():
    """Test 9: base_metadata values appear in every chunk's metadata."""
    base = {"file_name": "legacy_key.pdf", "department": "Finance", "version": 3}
    chunks = chunk_markdown(
        "## 第 1 頁\n\nSome content here.",
        source_file="test.pdf",
        base_metadata=base,
    )
    _assert(len(chunks) > 0, "No chunks produced.")
    for i, c in enumerate(chunks):
        for k, v in base.items():
            _assert(
                c["metadata"].get(k) == v,
                f"Chunk {i} metadata[{k!r}]={c['metadata'].get(k)!r}, expected {v!r}."
            )
    print("  PASS: test_base_metadata_forwarding")


def test_infer_metadata_from_filename():
    """Test 10: infer_doc_metadata extracts year and company from '迅銷2024_eng.pdf'."""
    meta = infer_doc_metadata("迅銷2024_eng.pdf", "")

    _assert(
        meta["year"] == 2024,
        f"Expected year=2024, got {meta['year']!r}. "
        "Hint: \\b word-boundary fails adjacent to CJK chars."
    )
    _assert(
        meta["company"] == "迅銷",
        f"Expected company='迅銷', got {meta['company']!r}."
    )
    # All values must still be scalars.
    for k, v in meta.items():
        _assert(isinstance(v, SCALAR_TYPES), f"infer meta[{k!r}]={v!r} is not a scalar.")

    print("  PASS: test_infer_metadata_from_filename")


def test_document_type_annual_report():
    """Test 11: document_type = 'annual_report' when trigger keyword present."""
    meta_fn = infer_doc_metadata("annual_report_2024.pdf", "")
    _assert(
        meta_fn["document_type"] == "annual_report",
        f"'annual' in filename → expected 'annual_report', got {meta_fn['document_type']!r}."
    )

    meta_text = infer_doc_metadata("report.pdf", "This is the 年報 for 2024.")
    _assert(
        meta_text["document_type"] == "annual_report",
        f"'年報' in text → expected 'annual_report', got {meta_text['document_type']!r}."
    )

    meta_plain = infer_doc_metadata("quarterly_results.pdf", "Q1 2024 results.")
    _assert(
        meta_plain["document_type"] == "financial_doc",
        f"No annual keyword → expected 'financial_doc', got {meta_plain['document_type']!r}."
    )

    print("  PASS: test_document_type_annual_report")


def test_quarter_extraction():
    """Test 12: Quarter extracted from filename and from text."""
    # _Q3_ — underscore-bounded, \b would fail
    meta_fn = infer_doc_metadata("report_Q3_2023.pdf", "")
    _assert(
        meta_fn["quarter"] == "Q3",
        f"Expected quarter='Q3' from filename, got {meta_fn['quarter']!r}."
    )

    # From text
    meta_text = infer_doc_metadata("report.pdf", "Results for Q2 2024 were exceptional.")
    _assert(
        meta_text["quarter"] == "Q2",
        f"Expected quarter='Q2' from text, got {meta_text['quarter']!r}."
    )

    # None when absent
    meta_none = infer_doc_metadata("report.pdf", "No quarter mentioned here.")
    _assert(
        meta_none["quarter"] is None,
        f"Expected quarter=None, got {meta_none['quarter']!r}."
    )

    print("  PASS: test_quarter_extraction")


def test_language_detection():
    """Test 13: language_original heuristic detects zh / ja / en."""
    meta_zh = infer_doc_metadata("test.pdf", "公司在2024年實現了強勁的收入增長，各業務板塊表現良好。")
    _assert(
        meta_zh["language_original"] == "zh",
        f"CJK text → expected 'zh', got {meta_zh['language_original']!r}."
    )

    meta_ja = infer_doc_metadata("test.pdf", "売上収益は前年比で大幅に増加した。ひらがなが含まれています。")
    _assert(
        meta_ja["language_original"] == "ja",
        f"Hiragana text → expected 'ja', got {meta_ja['language_original']!r}."
    )

    meta_en = infer_doc_metadata("test.pdf", "Revenue grew by 12 percent year over year in fiscal 2024.")
    _assert(
        meta_en["language_original"] == "en",
        f"English text → expected 'en', got {meta_en['language_original']!r}."
    )

    print("  PASS: test_language_detection")


def test_large_table_kept_whole():
    """Test 14: A table exceeding chunk_size is still kept as a single atomic chunk."""
    # Build a large table with 40 rows → well over the 300-char chunk_size we'll use.
    rows = ["| Item | Value | Notes |", "|------|-------|-------|"]
    for i in range(40):
        rows.append(f"| item_{i:02d} | {i * 100:6d} | note_{i} |")
    big_table = "\n".join(rows)

    md = f"## 第 1 頁\n\n# Big Table\n\n{big_table}\n"
    chunks = chunk_markdown(md, source_file="big.pdf", chunk_size=300, chunk_overlap=50)

    # Find the chunk that contains the last row.
    last_row = f"| item_39 | {39 * 100:6d} | note_39 |"
    first_row = f"| item_00 | {0 * 100:6d} | note_0 |"

    containing_last = [c for c in chunks if last_row in c["text"]]
    containing_first = [c for c in chunks if first_row in c["text"]]

    _assert(
        len(containing_last) == 1,
        f"Last table row should be in exactly 1 chunk, found in {len(containing_last)}."
    )
    _assert(
        len(containing_first) == 1,
        f"First table row should be in exactly 1 chunk, found in {len(containing_first)}."
    )
    _assert(
        containing_last[0]["id"] == containing_first[0]["id"],
        "First and last table rows are in different chunks — table was split!"
    )
    _assert(
        containing_last[0]["metadata"]["has_table"] is True,
        "Large table chunk should have has_table=True."
    )

    print("  PASS: test_large_table_kept_whole")


def test_no_page_markers():
    """Test 15: Document without page markers → page=None in all chunks."""
    plain_md = "# Introduction\n\nSome content without any page markers.\n\n## Details\n\nMore content."
    chunks = chunk_markdown(plain_md, source_file="plain.pdf")
    _assert(len(chunks) > 0, "Expected at least one chunk from a plain document.")
    for i, c in enumerate(chunks):
        _assert(
            c["metadata"]["page"] is None,
            f"Chunk {i} page={c['metadata']['page']!r}, expected None (no page markers)."
        )
    print("  PASS: test_no_page_markers")


def test_empty_content():
    """Test 16: Empty or whitespace-only text returns an empty list without crashing."""
    result = chunk_markdown("", source_file="empty.pdf")
    _assert(result == [], f"Empty text → expected [], got {result!r}.")

    result2 = chunk_markdown("   \n\n\t  ", source_file="empty.pdf")
    _assert(result2 == [], f"Whitespace-only text → expected [], got {result2!r}.")

    print("  PASS: test_empty_content")


def test_section_heading_tracking():
    """Test 17: 'section' metadata is the nearest preceding ATX heading in the chunk."""
    md = """\
## 第 1 頁

# Top Level

Some prose under top level.

## Sub Section Alpha

Content under alpha.

## Sub Section Beta

Content under beta.
"""
    chunks = chunk_markdown(md, source_file="headings.pdf", chunk_size=80, chunk_overlap=0)

    # Find the chunk containing "Content under alpha."
    alpha_chunks = [c for c in chunks if "Content under alpha" in c["text"]]
    _assert(len(alpha_chunks) >= 1, "Expected a chunk with 'Content under alpha'.")
    for c in alpha_chunks:
        _assert(
            c["metadata"]["section"] is not None,
            f"Chunk with 'alpha' content has section=None; expected a heading string."
        )
        _assert(
            isinstance(c["metadata"]["section"], str),
            f"section should be str, got {type(c['metadata']['section']).__name__}."
        )

    # Find chunk containing "Content under beta."
    beta_chunks = [c for c in chunks if "Content under beta" in c["text"]]
    _assert(len(beta_chunks) >= 1, "Expected a chunk with 'Content under beta'.")

    print("  PASS: test_section_heading_tracking")


def test_collection_add_mapping():
    """
    Verify the exact mapping used for collection.add() integration.

    The three list comprehensions below must produce valid inputs for::

        collection.add(
            ids       = [c["id"]       for c in chunks],
            documents = [c["text"]     for c in chunks],
            metadatas = [c["metadata"] for c in chunks],
        )
    """
    chunks = chunk_markdown(SAMPLE_MD, source_file=SOURCE_FILE)
    _assert(len(chunks) > 0, "No chunks to map.")

    ids       = [c["id"]       for c in chunks]
    documents = [c["text"]     for c in chunks]
    metadatas = [c["metadata"] for c in chunks]

    _assert(len(ids) == len(documents) == len(metadatas),
            "ids / documents / metadatas lengths differ.")

    for i, (cid, doc, meta) in enumerate(zip(ids, documents, metadatas)):
        _assert(isinstance(cid, str) and cid, f"ids[{i}] is not a non-empty str: {cid!r}.")
        _assert(isinstance(doc, str) and doc.strip(), f"documents[{i}] is empty.")
        for k, v in meta.items():
            _assert(isinstance(v, SCALAR_TYPES),
                    f"metadatas[{i}][{k!r}]={v!r} is not a ChromaDB-safe scalar.")

    print("  PASS: test_collection_add_mapping")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all_tests():
    tests = [
        test_table_atomicity,
        test_page_numbers,
        test_required_metadata_keys,
        test_metadata_scalar_values,
        test_unique_sequential_ids,
        test_has_table_flag,
        test_base_metadata_forwarding,
        test_infer_metadata_from_filename,
        test_document_type_annual_report,
        test_quarter_extraction,
        test_language_detection,
        test_large_table_kept_whole,
        test_no_page_markers,
        test_empty_content,
        test_section_heading_tracking,
        test_collection_add_mapping,
    ]

    print(f"Running {len(tests)} tests for chunking.py ...\n")
    failures = []

    for test_fn in tests:
        try:
            test_fn()
        except AssertionError as exc:
            failures.append((test_fn.__name__, str(exc)))
            print(f"  FAIL: {test_fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append((test_fn.__name__, f"{type(exc).__name__}: {exc}"))
            print(f"  ERROR: {test_fn.__name__}: {type(exc).__name__}: {exc}")

    print()
    if failures:
        print(f"FAILED: {len(failures)}/{len(tests)} tests failed.")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    run_all_tests()

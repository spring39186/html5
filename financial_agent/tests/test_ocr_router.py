"""
tests/test_ocr_router.py — stdlib-only self-tests for ocr_router.py
=====================================================================
Run with:  python3 tests/test_ocr_router.py
All tests use only the Python standard library; no third-party packages.
Prints "ALL TESTS PASSED" on success, raises AssertionError on failure.
"""
from __future__ import annotations

import sys
import os

# Allow running from any working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ocr_router import (
    GENERIC_OCR_PROMPT,
    MIN_CHARS_FOR_USABLE,
    HIGH_DIGIT_RATIO,
    MIN_TABLE_MARKERS,
    text_layer_quality,
    should_use_vision,
    transcribe_page,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _assert(condition: bool, msg: str) -> None:
    if not condition:
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# 1. GENERIC_OCR_PROMPT content checks
# ---------------------------------------------------------------------------

def test_prompt_forbids_classification() -> None:
    """Prompt must instruct the model NOT to classify the document type."""
    prompt_lower = GENERIC_OCR_PROMPT.lower()
    # Must contain a negative instruction about classification
    _assert(
        "do not" in prompt_lower or "not" in prompt_lower,
        "GENERIC_OCR_PROMPT must contain negative instructions (e.g. 'do NOT')",
    )
    # Must reference not judging / not classifying
    _assert(
        "classif" in prompt_lower or "judge" in prompt_lower or "label" in prompt_lower,
        "GENERIC_OCR_PROMPT must forbid classification or labelling of document type",
    )


def test_prompt_forbids_fabrication() -> None:
    """Prompt must instruct the model NOT to fabricate or invent data."""
    prompt_lower = GENERIC_OCR_PROMPT.lower()
    _assert(
        "fabricat" in prompt_lower or "invent" in prompt_lower or "never" in prompt_lower,
        "GENERIC_OCR_PROMPT must forbid fabrication/invention of data",
    )


def test_prompt_requires_verbatim_transcription() -> None:
    """Prompt must ask for faithful / verbatim output."""
    prompt_lower = GENERIC_OCR_PROMPT.lower()
    _assert(
        "transcri" in prompt_lower or "verbatim" in prompt_lower or "faithful" in prompt_lower,
        "GENERIC_OCR_PROMPT must require faithful transcription",
    )


def test_prompt_requires_unclear_marker() -> None:
    """Prompt must instruct use of [unclear] for unreadable chars."""
    _assert(
        "[unclear]" in GENERIC_OCR_PROMPT,
        "GENERIC_OCR_PROMPT must specify [unclear] marker for unreadable content",
    )


def test_prompt_requires_markdown_tables() -> None:
    """Prompt must mention Markdown tables for table content."""
    _assert(
        "markdown" in GENERIC_OCR_PROMPT.lower() and "table" in GENERIC_OCR_PROMPT.lower(),
        "GENERIC_OCR_PROMPT must instruct reproduction of tables as Markdown tables",
    )


def test_prompt_forbids_summarisation() -> None:
    """Prompt must forbid summarisation or condensation."""
    prompt_lower = GENERIC_OCR_PROMPT.lower()
    _assert(
        "summar" in prompt_lower or "condense" in prompt_lower or "delete" in prompt_lower,
        "GENERIC_OCR_PROMPT must forbid summarisation/condensation",
    )


# ---------------------------------------------------------------------------
# 2. text_layer_quality
# ---------------------------------------------------------------------------

def test_quality_empty_text() -> None:
    q = text_layer_quality("")
    _assert(q["chars"] == 0, "Empty text: chars should be 0")
    _assert(q["digit_ratio"] == 0.0, "Empty text: digit_ratio should be 0.0")
    _assert(q["has_table_markers"] is False, "Empty text: has_table_markers should be False")
    _assert(q["usable"] is False, "Empty text: usable should be False")


def test_quality_short_text() -> None:
    """Text shorter than MIN_CHARS_FOR_USABLE must be marked unusable."""
    short = "a" * (MIN_CHARS_FOR_USABLE - 1)
    q = text_layer_quality(short)
    _assert(q["chars"] == len(short), "chars mismatch for short text")
    _assert(q["usable"] is False, f"Text with {len(short)} chars must not be usable")


def test_quality_clean_prose_usable() -> None:
    """Long clean prose text should be usable."""
    prose = (
        "The company reported strong growth across all business segments. "
        "Revenue increased by double digits compared to the previous fiscal year. "
        "Operating margins expanded due to improved cost discipline and product mix. "
        "Management reiterated full-year guidance and announced a new share buyback programme."
    )
    _assert(len(prose) >= MIN_CHARS_FOR_USABLE, "Prose fixture must be long enough")
    q = text_layer_quality(prose)
    _assert(q["usable"] is True, "Clean prose should be usable")
    _assert(q["digit_ratio"] < HIGH_DIGIT_RATIO, "Clean prose should have low digit ratio")


def test_quality_number_dense_text() -> None:
    """A page full of numeric table data should trigger has_table_markers."""
    # Simulate a financial table row with many numbers
    numeric_text = "\n".join(
        [
            "Revenue       123,456   234,567   345,678   456,789",
            "Net Income     12,345    23,456    34,567    45,678",
            "EPS             1.23      2.34      3.45      4.56",
            "Gross Margin   45.67%    46.78%    47.89%    48.90%",
            "Operating CF   98,765   109,876   120,987   132,098",
        ]
        * 5  # repeat to exceed MIN_CHARS_FOR_USABLE
    )
    _assert(len(numeric_text) >= MIN_CHARS_FOR_USABLE, "Numeric fixture must be long enough")
    q = text_layer_quality(numeric_text)
    _assert(q["usable"] is True, "Number-dense usable text should be marked usable")
    _assert(q["has_table_markers"] is True, "Number-dense text should have table markers")


def test_quality_returns_required_keys() -> None:
    q = text_layer_quality("hello world")
    for key in ("chars", "digit_ratio", "has_table_markers", "usable"):
        _assert(key in q, f"text_layer_quality must return key '{key}'")


# ---------------------------------------------------------------------------
# 3. should_use_vision
# ---------------------------------------------------------------------------

def test_vision_forced() -> None:
    """force=True must always return (True, 'forced') regardless of text."""
    good_text = "A" * 200  # long, clean text
    use_v, reason = should_use_vision(good_text, page_has_images=False, force=True)
    _assert(use_v is True, "force=True must return use_vision=True")
    _assert(reason == "forced", f"Expected reason='forced', got '{reason}'")


def test_vision_empty_text_scanned_page() -> None:
    """Empty / very short text layer means scanned page → use vision."""
    use_v, reason = should_use_vision("", page_has_images=False)
    _assert(use_v is True, "Empty text layer must trigger vision")
    _assert(reason == "no_text_layer", f"Expected 'no_text_layer', got '{reason}'")


def test_vision_short_text_scanned_page() -> None:
    """Text shorter than MIN_CHARS_FOR_USABLE → vision."""
    short = "x" * (MIN_CHARS_FOR_USABLE - 1)
    use_v, reason = should_use_vision(short, page_has_images=True)
    _assert(use_v is True, "Short/scanned text must trigger vision")
    _assert(reason == "no_text_layer", f"Expected 'no_text_layer', got '{reason}'")


def test_vision_clean_prose_no_images() -> None:
    """Good text layer, no images → use text, not vision."""
    prose = (
        "The board of directors approved the quarterly dividend payment. "
        "Shareholders of record as of the record date will receive the distribution. "
        "The company continues to invest in research and development activities. "
        "Capital expenditure guidance remains unchanged for the fiscal year."
    )
    _assert(len(prose) >= MIN_CHARS_FOR_USABLE, "Prose fixture must pass MIN_CHARS")
    use_v, reason = should_use_vision(prose, page_has_images=False)
    _assert(use_v is False, "Clean prose without images must use text layer")
    _assert(reason == "use_text_layer", f"Expected 'use_text_layer', got '{reason}'")


def test_vision_number_dense_with_images() -> None:
    """Dense numeric text + embedded images → vision (financial table heuristic)."""
    numeric_text = "\n".join(
        [
            "Revenue       123,456   234,567   345,678   456,789",
            "Net Income     12,345    23,456    34,567    45,678",
            "EPS             1.23      2.34      3.45      4.56",
            "Gross Margin   45.67%    46.78%    47.89%    48.90%",
            "Operating CF   98,765   109,876   120,987   132,098",
        ]
        * 5
    )
    _assert(len(numeric_text) >= MIN_CHARS_FOR_USABLE, "Numeric fixture must pass MIN_CHARS")
    use_v, reason = should_use_vision(numeric_text, page_has_images=True)
    _assert(use_v is True, "Number-dense text with images must trigger vision")
    _assert(reason == "dense_tables", f"Expected 'dense_tables', got '{reason}'")


def test_vision_number_dense_without_images() -> None:
    """Dense numeric text but NO images → use text layer (no image to improve on)."""
    numeric_text = "\n".join(
        [
            "Revenue       123,456   234,567   345,678   456,789",
            "Net Income     12,345    23,456    34,567    45,678",
            "EPS             1.23      2.34      3.45      4.56",
            "Gross Margin   45.67%    46.78%    47.89%    48.90%",
            "Operating CF   98,765   109,876   120,987   132,098",
        ]
        * 5
    )
    use_v, reason = should_use_vision(numeric_text, page_has_images=False)
    # dense_tables only fires when page_has_images is True
    _assert(use_v is False, "Number-dense text without images should use text layer")
    _assert(reason == "use_text_layer", f"Expected 'use_text_layer', got '{reason}'")


# ---------------------------------------------------------------------------
# 4. transcribe_page — injected vision_fn stub
# ---------------------------------------------------------------------------

class _VisionCounter:
    """Stub that records how many times it was called."""

    def __init__(self, return_value: str = "## Vision OCR Output\n\nSome content."):
        self.call_count = 0
        self._return_value = return_value

    def __call__(self, page_idx: int) -> str:
        self.call_count += 1
        return self._return_value


def test_transcribe_uses_vision_for_empty_text() -> None:
    """Empty text layer → vision_fn is called."""
    stub = _VisionCounter()
    md, method = transcribe_page(
        page_idx=0,
        text_layer="",
        page_has_images=True,
        vision_fn=stub,
    )
    _assert(stub.call_count == 1, "vision_fn must be called once for empty text layer")
    _assert(method == "vision", f"Expected method='vision', got '{method}'")
    _assert(md == stub._return_value, "markdown must equal vision_fn return value")


def test_transcribe_skips_vision_for_clean_prose() -> None:
    """Clean, long prose text → vision_fn is NOT called."""
    prose = (
        "The company reported a significant increase in annual revenue. "
        "This was driven by strong performance in the Asia-Pacific region. "
        "The management team expressed confidence in the outlook for next year. "
        "Dividend growth remained aligned with the company's capital return policy."
    )
    _assert(len(prose) >= MIN_CHARS_FOR_USABLE, "Prose fixture must pass MIN_CHARS")
    stub = _VisionCounter()
    md, method = transcribe_page(
        page_idx=2,
        text_layer=prose,
        page_has_images=False,
        vision_fn=stub,
    )
    _assert(stub.call_count == 0, "vision_fn must NOT be called for clean prose")
    _assert(method == "text", f"Expected method='text', got '{method}'")
    _assert(md == prose, "markdown must equal the raw text_layer")


def test_transcribe_force_vision_overrides_good_text() -> None:
    """force_vision=True must call vision_fn even on clean text."""
    prose = "A" * 300
    stub = _VisionCounter(return_value="Forced vision result")
    md, method = transcribe_page(
        page_idx=1,
        text_layer=prose,
        page_has_images=False,
        vision_fn=stub,
        force_vision=True,
    )
    _assert(stub.call_count == 1, "vision_fn must be called when force_vision=True")
    _assert(method == "vision", f"Expected method='vision', got '{method}'")
    _assert(md == "Forced vision result", "markdown must equal vision_fn return value")


def test_transcribe_vision_for_dense_tables_with_images() -> None:
    """Dense numeric text + page_has_images=True → vision_fn called."""
    dense = "\n".join(
        [
            "Revenue       123,456   234,567   345,678   456,789",
            "Net Income     12,345    23,456    34,567    45,678",
            "EPS             1.23      2.34      3.45      4.56",
        ]
        * 6
    )
    stub = _VisionCounter(return_value="## Table\n| A | B |\n|---|---|\n| 1 | 2 |")
    md, method = transcribe_page(
        page_idx=3,
        text_layer=dense,
        page_has_images=True,
        vision_fn=stub,
    )
    _assert(stub.call_count == 1, "vision_fn must be called for dense table + images")
    _assert(method == "vision", f"Expected method='vision', got '{method}'")


def test_transcribe_page_idx_forwarded_to_vision_fn() -> None:
    """page_idx must be forwarded correctly to vision_fn."""
    received_idxs = []

    def capturing_vision_fn(page_idx: int) -> str:
        received_idxs.append(page_idx)
        return "result"

    transcribe_page(
        page_idx=7,
        text_layer="",   # empty → vision
        page_has_images=False,
        vision_fn=capturing_vision_fn,
    )
    _assert(received_idxs == [7], f"Expected page_idx=7 forwarded, got {received_idxs}")


# ---------------------------------------------------------------------------
# 5. Module constants are present and typed correctly
# ---------------------------------------------------------------------------

def test_module_constants() -> None:
    _assert(isinstance(MIN_CHARS_FOR_USABLE, int), "MIN_CHARS_FOR_USABLE must be int")
    _assert(isinstance(HIGH_DIGIT_RATIO, float), "HIGH_DIGIT_RATIO must be float")
    _assert(isinstance(MIN_TABLE_MARKERS, int), "MIN_TABLE_MARKERS must be int")
    _assert(isinstance(GENERIC_OCR_PROMPT, str), "GENERIC_OCR_PROMPT must be str")
    _assert(MIN_CHARS_FOR_USABLE > 0, "MIN_CHARS_FOR_USABLE must be positive")
    _assert(0.0 < HIGH_DIGIT_RATIO < 1.0, "HIGH_DIGIT_RATIO must be in (0, 1)")
    _assert(MIN_TABLE_MARKERS > 0, "MIN_TABLE_MARKERS must be positive")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        # GENERIC_OCR_PROMPT
        test_prompt_forbids_classification,
        test_prompt_forbids_fabrication,
        test_prompt_requires_verbatim_transcription,
        test_prompt_requires_unclear_marker,
        test_prompt_requires_markdown_tables,
        test_prompt_forbids_summarisation,
        # text_layer_quality
        test_quality_empty_text,
        test_quality_short_text,
        test_quality_clean_prose_usable,
        test_quality_number_dense_text,
        test_quality_returns_required_keys,
        # should_use_vision
        test_vision_forced,
        test_vision_empty_text_scanned_page,
        test_vision_short_text_scanned_page,
        test_vision_clean_prose_no_images,
        test_vision_number_dense_with_images,
        test_vision_number_dense_without_images,
        # transcribe_page
        test_transcribe_uses_vision_for_empty_text,
        test_transcribe_skips_vision_for_clean_prose,
        test_transcribe_force_vision_overrides_good_text,
        test_transcribe_vision_for_dense_tables_with_images,
        test_transcribe_page_idx_forwarded_to_vision_fn,
        # constants
        test_module_constants,
    ]

    failed = []
    for test_fn in tests:
        try:
            test_fn()
        except AssertionError as exc:
            failed.append((test_fn.__name__, str(exc)))
            print(f"  FAIL  {test_fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append((test_fn.__name__, repr(exc)))
            print(f"  ERROR {test_fn.__name__}: {exc}")
        else:
            print(f"  ok    {test_fn.__name__}")

    if failed:
        print(f"\n{len(failed)} test(s) FAILED.")
        sys.exit(1)
    else:
        print("\nALL TESTS PASSED")

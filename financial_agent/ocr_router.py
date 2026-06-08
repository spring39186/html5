"""
ocr_router.py — Smart OCR routing: text layer vs. vision-LLM
=============================================================
Reduces vision-LLM cost by using a PDF page's embedded text layer whenever
it is usable, and falling back to vision-OCR only when necessary (scanned
pages, image-heavy tables, etc.).

Module-level imports: stdlib ONLY. No fitz, no OpenAI, no third-party libs.
Any heavy dependency is lazy-imported inside the functions that actually need
it — keeping this module fully testable offline.

Public API
----------
    GENERIC_OCR_PROMPT           — str constant for vision transcription
    text_layer_quality(text)     -> dict
    should_use_vision(text_layer, page_has_images, force) -> (bool, str)
    transcribe_page(page_idx, text_layer, page_has_images,
                    vision_fn, force_vision)             -> (str, str)

Routing thresholds (module constants — edit here to tune without touching
logic):
    MIN_CHARS_FOR_USABLE    = 80     chars needed before text layer is "real"
    HIGH_DIGIT_RATIO        = 0.15   fraction of digit chars → dense-numeric
    MIN_TABLE_MARKERS       = 3      how many numeric runs = "has table"
"""

from __future__ import annotations

import re
from typing import Callable, Tuple

# ---------------------------------------------------------------------------
# Tunable thresholds — change ONLY here; logic uses these names everywhere
# ---------------------------------------------------------------------------
MIN_CHARS_FOR_USABLE: int = 80      # minimum characters for a usable text layer
HIGH_DIGIT_RATIO: float = 0.15      # digit char fraction above which the page is "number-dense"
MIN_TABLE_MARKERS: int = 3          # how many runs of ≥2 consecutive digits signals table content

# ---------------------------------------------------------------------------
# 1. GENERIC_OCR_PROMPT
# ---------------------------------------------------------------------------
GENERIC_OCR_PROMPT: str = (
    "You are a faithful document transcription assistant. "
    "Your ONLY job is to transcribe exactly what you see in the image into text or Markdown. "
    "\n\n"
    "STRICT RULES — follow each without exception:\n"
    "1. Do NOT judge, classify, or label the document type "
    "(do NOT say 'this is an income statement' or 'balance sheet' or any category).\n"
    "2. Do NOT delete, summarise, condense, or rearrange any content. "
    "Every line, number, label, header, and footnote must appear in the output.\n"
    "3. Tables → reproduce as Markdown tables (| col | col | …). "
    "Preserve column alignment and all cell values exactly as they appear.\n"
    "4. Plain text → transcribe verbatim, preserving paragraph breaks.\n"
    "5. Charts / graphs → describe only the VISIBLE elements "
    "(axis labels, legend entries, bar heights, trend direction) "
    "without inventing any numbers that are not clearly printed in the image.\n"
    "6. Any character or value you cannot read clearly → mark it as [unclear].\n"
    "7. NEVER fabricate, guess, or reconstruct missing data. "
    "If a cell is blank, leave it blank in the table.\n"
    "8. Do NOT add commentary, analysis, interpretation, or financial terminology "
    "beyond what is literally printed in the image.\n"
    "9. Output Markdown only — no preamble, no explanation, no sign-off."
)

# ---------------------------------------------------------------------------
# 2. text_layer_quality
# ---------------------------------------------------------------------------

def text_layer_quality(text: str) -> dict:
    """Analyse the quality of a PDF page's embedded text layer.

    Parameters
    ----------
    text : str
        Raw text extracted from the PDF page (e.g. via ``fitz page.get_text()``).

    Returns
    -------
    dict with keys:
        chars           int   — total character count (whitespace included)
        digit_ratio     float — fraction of chars that are ASCII digits (0–9)
        has_table_markers bool — True when ≥ MIN_TABLE_MARKERS runs of 2+
                                  consecutive digits are found (heuristic for
                                  tables / numeric columns)
        usable          bool  — True when chars >= MIN_CHARS_FOR_USABLE
                                  and the page is not obviously garbage
    """
    chars = len(text)

    # Digit ratio: count ASCII digit characters
    digit_count = sum(1 for c in text if c.isdigit())
    digit_ratio = digit_count / chars if chars > 0 else 0.0

    # Table markers: runs of two or more consecutive digit characters
    # (e.g. "1,234" or "56.78" produce runs; single digits in prose are common)
    numeric_runs = re.findall(r"\d{2,}", text)
    has_table_markers = len(numeric_runs) >= MIN_TABLE_MARKERS

    # Usability: enough characters and not predominantly non-printable/garbage.
    # "Garbage" heuristic: if more than 40% of characters are non-ASCII and
    # non-alphanumeric, it is likely mojibake from a badly encoded PDF.
    if chars >= MIN_CHARS_FOR_USABLE:
        printable_count = sum(1 for c in text if c.isprintable())
        garbage_fraction = 1.0 - (printable_count / chars)
        usable = garbage_fraction < 0.40
    else:
        usable = False

    return {
        "chars": chars,
        "digit_ratio": round(digit_ratio, 4),
        "has_table_markers": has_table_markers,
        "usable": usable,
    }


# ---------------------------------------------------------------------------
# 3. should_use_vision
# ---------------------------------------------------------------------------

def should_use_vision(
    text_layer: str,
    page_has_images: bool,
    force: bool = False,
) -> Tuple[bool, str]:
    """Decide whether to call the vision-LLM for this page.

    Parameters
    ----------
    text_layer      : str   — raw text extracted from the PDF page
    page_has_images : bool  — True when the page contains embedded images
                              (from ``fitz page.get_images()``)
    force           : bool  — if True, always use vision regardless

    Returns
    -------
    (use_vision: bool, reason: str)
        reason is one of: "forced" | "no_text_layer" | "dense_tables" |
                          "use_text_layer"
    """
    # Hard override
    if force:
        return (True, "forced")

    quality = text_layer_quality(text_layer)

    # Scanned / blank page — text layer is absent or too short/garbled
    if not quality["usable"]:
        return (True, "no_text_layer")

    # Usable text BUT heavy on numbers AND the page has embedded images:
    # financial tables embedded as images are often better read via vision.
    if (
        (quality["digit_ratio"] >= HIGH_DIGIT_RATIO or quality["has_table_markers"])
        and page_has_images
    ):
        return (True, "dense_tables")

    # Default: trust the text layer — zero vision-LLM cost
    return (False, "use_text_layer")


# ---------------------------------------------------------------------------
# 4. transcribe_page
# ---------------------------------------------------------------------------

def transcribe_page(
    page_idx: int,
    text_layer: str,
    page_has_images: bool,
    vision_fn: Callable[[int], str],
    force_vision: bool = False,
) -> Tuple[str, str]:
    """Transcribe a single PDF page using the cheapest suitable method.

    Parameters
    ----------
    page_idx        : int
        Zero-based page index (forwarded to vision_fn so the caller can
        render / fetch the correct image).
    text_layer      : str
        Raw text extracted from the PDF page (e.g. ``page.get_text()``).
    page_has_images : bool
        Whether the page contains embedded bitmap images
        (e.g. ``bool(page.get_images())``).
    vision_fn       : Callable[[int], str]
        Injected callable: ``vision_fn(page_idx) -> markdown_str``.
        Called ONLY when the router decides vision is needed.
        The caller is responsible for rendering the page to an image,
        encoding it, and invoking the vision-LLM.
    force_vision    : bool
        If True, skip routing and always call vision_fn.

    Returns
    -------
    (markdown: str, method: str)
        method is "vision" when vision_fn was called, "text" when the text
        layer was returned directly.
    """
    use_vision, _reason = should_use_vision(text_layer, page_has_images, force=force_vision)

    if use_vision:
        markdown = vision_fn(page_idx)
        return (markdown, "vision")
    else:
        return (text_layer, "text")

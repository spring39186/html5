"""
ocr_pipeline.py — Concurrent OCR + Translation Pipeline (Phase 1)
===================================================================
Fully decoupled from agent.py: all external calls (LLM, fitz) are
injected as callables so this module imports with stdlib only.

Module-level imports: stdlib only (concurrent.futures, hashlib, os).
`fitz` (PyMuPDF) is lazy-imported inside process_pdf() only.

Public API
----------
    file_hash(path)                                         -> str
    auto_workers(num_pages, max_cap, min_floor)             -> int
    process_pages(num_pages, render_page_fn, ocr_call,
                  translate_call, normalize, max_workers)   -> str
    process_pdf(pdf_path, ocr_call, translate_call,
                normalize, cache_dir, max_workers)          -> str

Constants
---------
    TRANSLATION_SYSTEM_PROMPT   — ready-to-use system prompt for translate_call
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import os
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Module constant — agent.py uses this when constructing translate_call
# ---------------------------------------------------------------------------
TRANSLATION_SYSTEM_PROMPT = (
    "You are a financial document translator. "
    "Translate the Markdown content into professional financial English. "
    "Requirements: "
    "Preserve all numbers exactly. "
    "Preserve all percentages exactly. "
    "CRITICAL: Do NOT convert or rescale numeric magnitude units. "
    "Keep East-Asian magnitude units verbatim as the original characters "
    "(億, 万, 萬, 兆, 百万, 千) — do NOT render them as English scale words. "
    "Never write 億 as 'billion' (億 = hundred-million, NOT billion); keep the "
    "original number and its original unit character untouched. "
    "Preserve all tables exactly. "
    "Preserve Markdown structure exactly. "
    "Preserve headings hierarchy. "
    "Preserve page references. "
    "Do not summarize. "
    "Do not explain. "
    "Output translated Markdown only."
)


# ---------------------------------------------------------------------------
# 1. file_hash
# ---------------------------------------------------------------------------
def file_hash(path: str) -> str:
    """Return the SHA-256 hex digest of the file at *path*.

    If the file cannot be read (permission error, too large to read in one
    shot, etc.) we fall back to hashing the string ``"{path}:{size}:{mtime}"``
    so callers always get a stable, deterministic key.
    """
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(65536), b""):
                h.update(block)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        try:
            stat = os.stat(path)
            fallback = f"{path}:{stat.st_size}:{stat.st_mtime}"
        except Exception:  # noqa: BLE001
            fallback = path
        return hashlib.sha256(fallback.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 2. auto_workers
# ---------------------------------------------------------------------------
def auto_workers(
    num_pages: int,
    max_cap: int = 10,
    min_floor: int = 5,
) -> int:
    """Choose a sensible ThreadPoolExecutor worker count.

    Heuristic (in order of precedence):
    1. ``FA_OCR_WORKERS`` env-var — hard override (useful for API rate-limit
       or memory constraints that the code cannot auto-detect).
    2. Upper bound: ``min(num_pages, os.cpu_count() * 2, max_cap)``
       — no point having more threads than pages; CPU×2 accounts for the
       I/O-heavy nature of OCR/translation network calls; max_cap is a
       safety ceiling (API quotas, VRAM).
    3. Lower bound: ``min(min_floor, num_pages)``
       — always use at least this many workers (or fewer if the document
       has fewer pages than min_floor).

    Args:
        num_pages:  Total number of pages in the document.
        max_cap:    Hard upper ceiling (default 10).  Callers processing
                    many-page documents against a rate-limited API should
                    lower this or set FA_OCR_WORKERS.
        min_floor:  Minimum workers unless num_pages is smaller (default 5).

    Returns:
        Worker count clamped to [effective_min, max_cap].
    """
    # Env-var override — useful for rate-limit / memory tuning at deploy time
    env_val = os.environ.get("FA_OCR_WORKERS", "").strip()
    if env_val.isdigit():
        override = int(env_val)
        # Still clamp to num_pages so we do not spin idle threads
        return max(1, min(override, num_pages))

    cpu = os.cpu_count() or 1
    # I/O-bound: 2× CPU is a reasonable starting point for network calls
    cpu_bound = cpu * 2

    upper = min(num_pages, cpu_bound, max_cap)
    # lower must never exceed max_cap (e.g. when min_floor > max_cap)
    lower = min(min_floor, num_pages, max_cap)

    return max(lower, min(upper, max_cap))


# ---------------------------------------------------------------------------
# 3. process_pages — CORE, fully testable without fitz or an LLM
# ---------------------------------------------------------------------------
def process_pages(
    num_pages: int,
    render_page_fn: Callable[[int], object],
    ocr_call: Callable[[int, object], str],
    translate_call: Callable[[int, str], str],
    normalize: bool = True,
    max_workers: Optional[int] = None,
) -> str:
    """Run OCR (and optional translation) concurrently over all pages.

    Per-page pipeline (each runs in its own thread, without waiting for
    other pages):
        img = render_page_fn(page_idx)
        md  = ocr_call(page_idx, img)
        if normalize:
            md = translate_call(page_idx, md)

    Pages are **submitted** in order 0..num_pages-1 via
    ``concurrent.futures.ThreadPoolExecutor`` and results are **assembled**
    in page order after all futures complete, so the output is always
    deterministic regardless of completion order.

    Per-page exceptions are caught and inlined as
    ``_（第 N 頁失敗：<error>）_`` so a single bad page never crashes the
    entire run.

    Args:
        num_pages:       Number of pages to process.
        render_page_fn:  ``(page_idx: int) -> img`` — renders one page to
                         whatever representation ocr_call expects (e.g. PNG
                         bytes, a PIL Image, a base64 string).
        ocr_call:        ``(page_idx: int, img) -> str`` — calls the vision
                         model and returns raw Markdown for the page.
        translate_call:  ``(page_idx: int, markdown: str) -> str`` — calls
                         the translation model; only invoked when
                         ``normalize=True``.
        normalize:       If True, each page's OCR output is translated
                         before assembly.  Default True.
        max_workers:     Thread-pool size.  None → auto_workers(num_pages).

    Returns:
        Full-document Markdown string:
        ``## 第 1 頁\\n\\n<content>\\n\\n---\\n\\n## 第 2 頁\\n\\n...``
    """
    if num_pages <= 0:
        return ""

    workers = max_workers if max_workers is not None else auto_workers(num_pages)

    def _process_one(page_idx: int) -> str:
        """Worker: render → OCR → (translate) for a single page."""
        try:
            img = render_page_fn(page_idx)
            md = ocr_call(page_idx, img)
            if normalize:
                md = translate_call(page_idx, md)
            return md
        except Exception as exc:  # noqa: BLE001
            return f"_（第 {page_idx + 1} 頁失敗：{exc}）_"

    # Submit all pages; keep futures in submission order so we can collect
    # results in page order without sorting.
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_process_one, p) for p in range(num_pages)]
        # Collect in order — result() blocks until that page is done
        results = [f.result() for f in futures]

    sections = [f"## 第 {p + 1} 頁\n\n{md}" for p, md in enumerate(results)]
    return "\n\n---\n\n".join(sections)


# ---------------------------------------------------------------------------
# 4. process_pdf — thin wrapper; lazy-imports fitz
# ---------------------------------------------------------------------------
def process_pdf(
    pdf_path: str,
    ocr_call: Callable[[int, bytes], str],
    translate_call: Callable[[int, str], str],
    normalize: bool = True,
    cache_dir: str = "cache",
    max_workers: Optional[int] = None,
) -> str:
    """OCR + translate a PDF file, with content-addressed caching.

    Cache key is ``file_hash(pdf_path)`` (SHA-256 of the file bytes),
    **not** the filename.  This means:
    - Re-uploading the same content under a different name hits the cache.
    - A new version of the same filename correctly misses the cache.

    Cache path: ``{cache_dir}/{sha256}.md``

    If the cache file exists it is returned immediately; no fitz, no LLM.

    Otherwise:
    1. ``fitz`` (PyMuPDF) is lazy-imported here so the rest of the module
       stays stdlib-only.
    2. Each page is rendered to PNG bytes at 2.5× scale (matches the
       quality used in the original agent.py).
    3. ``process_pages`` is called with those PNG bytes and the injected
       callables.
    4. The result is written to the cache file then returned.

    Args:
        pdf_path:       Absolute (or relative) path to the PDF.
        ocr_call:       ``(page_idx, img_bytes: bytes) -> str`` — the caller
                        (agent.py) wraps the vision model here.
        translate_call: ``(page_idx, markdown: str) -> str`` — the caller
                        wraps the coder/translation model here.
        normalize:      Translate each page when True (default True).
        cache_dir:      Directory for ``.md`` cache files (created if absent).
        max_workers:    Thread-pool size; None → auto_workers.

    Returns:
        Full-document Markdown string (from cache or freshly generated).

    Raises:
        Exception: propagated from fitz.open() if the PDF cannot be opened.
    """
    os.makedirs(cache_dir, exist_ok=True)
    digest = file_hash(pdf_path)
    cache_path = os.path.join(cache_dir, f"{digest}.md")

    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            return fh.read()

    # Lazy import — keeps module importable without PyMuPDF installed
    import fitz  # noqa: PLC0415  (lazy import by design)

    doc = fitz.open(pdf_path)
    try:
        num_pages = len(doc)

        # Build a closure that captures `doc` and renders page p to PNG bytes
        def render_page(p: int) -> bytes:
            page = doc.load_page(p)
            pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
            return pix.tobytes("png")

        full_text = process_pages(
            num_pages=num_pages,
            render_page_fn=render_page,
            ocr_call=ocr_call,
            translate_call=translate_call,
            normalize=normalize,
            max_workers=max_workers,
        )
    finally:
        doc.close()

    with open(cache_path, "w", encoding="utf-8") as fh:
        fh.write(full_text)

    return full_text

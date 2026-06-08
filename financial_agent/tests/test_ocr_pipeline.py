"""
tests/test_ocr_pipeline.py
==========================
Stdlib-only self-test for ocr_pipeline.py.

Run from the project root:
    python3 tests/test_ocr_pipeline.py
Or via py_compile first:
    python3 -m py_compile ocr_pipeline.py tests/test_ocr_pipeline.py && \
    python3 tests/test_ocr_pipeline.py
"""

import hashlib
import os
import sys
import tempfile
import threading
import time

# Ensure the project root is on the path when running from any cwd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ocr_pipeline
from ocr_pipeline import auto_workers, file_hash, process_pages

# ---------------------------------------------------------------------------
# Stub callables
# ---------------------------------------------------------------------------

def stub_render(p: int) -> str:
    """Returns a fake 'image' string (no real rendering needed)."""
    return f"img{p}"


def stub_ocr(p: int, img: object) -> str:
    return f"OCR page {p} revenue 100"


def stub_translate(p: int, md: str) -> str:
    return md + " [EN]"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def assert_eq(label: str, actual, expected) -> None:
    if actual != expected:
        raise AssertionError(
            f"FAIL [{label}]\n  expected: {expected!r}\n  actual  : {actual!r}"
        )


def assert_true(label: str, condition: bool, msg: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL [{label}]{': ' + msg if msg else ''}")


# ---------------------------------------------------------------------------
# Test 1 — process_pages: correct page order with normalize=True
# ---------------------------------------------------------------------------
def test_page_order_normalize_true() -> None:
    num_pages = 5
    result = process_pages(num_pages, stub_render, stub_ocr, stub_translate, normalize=True)
    sections = result.split("\n\n---\n\n")
    assert_eq("page count", len(sections), num_pages)

    for i, section in enumerate(sections):
        expected_heading = f"## 第 {i + 1} 頁"
        assert_true(
            f"heading page {i + 1}",
            section.startswith(expected_heading),
            f"section starts with: {section[:40]!r}",
        )
        # Translation applied
        assert_true(
            f"translation page {i + 1}",
            "[EN]" in section,
            f"[EN] not found in: {section!r}",
        )
        # OCR content present
        assert_true(
            f"ocr content page {i + 1}",
            f"OCR page {i}" in section,
            f"OCR text not found in: {section!r}",
        )


# ---------------------------------------------------------------------------
# Test 2 — process_pages: normalize=False skips translation
# ---------------------------------------------------------------------------
def test_normalize_false_skips_translation() -> None:
    result = process_pages(3, stub_render, stub_ocr, stub_translate, normalize=False)
    assert_true(
        "no [EN] when normalize=False",
        "[EN]" not in result,
        f"[EN] found unexpectedly in result: {result!r}",
    )
    assert_true(
        "OCR text present when normalize=False",
        "OCR page 0 revenue 100" in result,
    )


# ---------------------------------------------------------------------------
# Test 3 — per-page exception is captured inline, whole run does NOT crash
# ---------------------------------------------------------------------------
def test_per_page_exception_captured() -> None:
    bad_page = 2  # 0-based

    def ocr_sometimes_fails(p: int, img: object) -> str:
        if p == bad_page:
            raise ValueError("simulated OCR failure")
        return f"OCR page {p} revenue 100"

    result = process_pages(5, stub_render, ocr_sometimes_fails, stub_translate, normalize=True)
    sections = result.split("\n\n---\n\n")
    assert_eq("still 5 sections", len(sections), 5)

    # The bad page should contain the inline failure marker
    bad_section = sections[bad_page]
    assert_true(
        "failure marker present",
        "失敗" in bad_section,
        f"failure marker not found in section: {bad_section!r}",
    )
    assert_true(
        "page number in marker",
        f"第 {bad_page + 1} 頁" in bad_section,
        f"page number not found: {bad_section!r}",
    )

    # Other pages should be fine
    for p in range(5):
        if p == bad_page:
            continue
        assert_true(
            f"good page {p + 1} intact",
            f"OCR page {p}" in sections[p],
        )


# ---------------------------------------------------------------------------
# Test 4 — file_hash is deterministic for the same content
# ---------------------------------------------------------------------------
def test_file_hash_deterministic() -> None:
    content = b"Hello financial world 1234567890"
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f1:
        f1.write(content)
        path1 = f1.name
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f2:
        f2.write(content)
        path2 = f2.name

    try:
        h1a = file_hash(path1)
        h1b = file_hash(path1)  # same call, same file — must be identical
        h2 = file_hash(path2)   # different path, same content — must match

        assert_eq("same file hash stable", h1a, h1b)
        assert_eq("same content different path", h1a, h2)

        # Verify it matches manual sha256
        expected = hashlib.sha256(content).hexdigest()
        assert_eq("sha256 correct", h1a, expected)
    finally:
        os.unlink(path1)
        os.unlink(path2)


# ---------------------------------------------------------------------------
# Test 5 — file_hash fallback for non-existent path
# ---------------------------------------------------------------------------
def test_file_hash_nonexistent_fallback() -> None:
    fake_path = "/tmp/does_not_exist_xyz987.pdf"
    h = file_hash(fake_path)
    assert_true("fallback hash is a non-empty string", isinstance(h, str) and len(h) > 0)
    # Two calls must agree
    h2 = file_hash(fake_path)
    assert_eq("fallback is deterministic", h, h2)


# ---------------------------------------------------------------------------
# Test 6 — auto_workers bounds
# ---------------------------------------------------------------------------
def test_auto_workers_bounds() -> None:
    # Remove env override if set, so we test the pure heuristic
    old_env = os.environ.pop("FA_OCR_WORKERS", None)
    try:
        # 3 pages → workers must not exceed 3
        w3 = auto_workers(3)
        assert_true("3 pages → workers ≤ 3", w3 <= 3, f"got {w3}")
        assert_true("3 pages → workers ≥ 1", w3 >= 1, f"got {w3}")

        # 100 pages → workers must not exceed max_cap=10
        w100 = auto_workers(100)
        assert_true("100 pages → workers ≤ 10", w100 <= 10, f"got {w100}")
        assert_true("100 pages → workers ≥ 1", w100 >= 1, f"got {w100}")

        # 1 page → must be 1
        w1 = auto_workers(1)
        assert_true("1 page → workers = 1", w1 == 1, f"got {w1}")

        # custom max_cap honoured
        w_cap = auto_workers(50, max_cap=3)
        assert_true("custom max_cap=3 honoured", w_cap <= 3, f"got {w_cap}")

        # min_floor honoured when pages > floor
        w_floor = auto_workers(100, max_cap=10, min_floor=5)
        assert_true("min_floor=5 honoured", w_floor >= 5, f"got {w_floor}")

        # min_floor is clamped by num_pages
        w_few = auto_workers(2, max_cap=10, min_floor=5)
        assert_true("min_floor clamped by num_pages", w_few <= 2, f"got {w_few}")

    finally:
        if old_env is not None:
            os.environ["FA_OCR_WORKERS"] = old_env


# ---------------------------------------------------------------------------
# Test 7 — auto_workers env override
# ---------------------------------------------------------------------------
def test_auto_workers_env_override() -> None:
    os.environ["FA_OCR_WORKERS"] = "3"
    try:
        w = auto_workers(100)
        assert_eq("env override FA_OCR_WORKERS=3", w, 3)
    finally:
        del os.environ["FA_OCR_WORKERS"]


# ---------------------------------------------------------------------------
# Test 8 — concurrency: max concurrent threads > 1 when num_pages > 1
# ---------------------------------------------------------------------------
def test_concurrency() -> None:
    """Verify that multiple pages are processed in parallel.

    We measure the peak number of simultaneously-active worker threads by
    using a shared counter protected by a lock.  A brief sleep inside the
    OCR stub ensures the threads actually overlap.
    """
    num_pages = 6
    lock = threading.Lock()
    active_counter = [0]      # current in-flight count
    peak_active = [0]         # maximum observed simultaneously active

    def concurrent_ocr(p: int, img: object) -> str:
        with lock:
            active_counter[0] += 1
            if active_counter[0] > peak_active[0]:
                peak_active[0] = active_counter[0]
        try:
            time.sleep(0.05)  # simulate network round-trip
            return f"OCR page {p} revenue 100"
        finally:
            with lock:
                active_counter[0] -= 1

    process_pages(
        num_pages, stub_render, concurrent_ocr, stub_translate,
        normalize=False,
        max_workers=num_pages,   # allow all pages to run at once
    )

    assert_true(
        "peak concurrency > 1",
        peak_active[0] > 1,
        f"peak_active={peak_active[0]} — threads did not overlap",
    )


# ---------------------------------------------------------------------------
# Test 9 — TRANSLATION_SYSTEM_PROMPT is present and complete
# ---------------------------------------------------------------------------
def test_translation_system_prompt() -> None:
    p = ocr_pipeline.TRANSLATION_SYSTEM_PROMPT
    assert_true("prompt is a str", isinstance(p, str))
    for fragment in [
        "financial document translator",
        "Preserve all numbers exactly",
        "Preserve all percentages exactly",
        "Preserve all tables exactly",
        "Preserve Markdown structure exactly",
        "Preserve headings hierarchy",
        "Preserve page references",
        "Do not summarize",
        "Do not explain",
        "Output translated Markdown only",
    ]:
        assert_true(
            f"prompt contains: {fragment!r}",
            fragment in p,
            f"missing from TRANSLATION_SYSTEM_PROMPT",
        )


# ---------------------------------------------------------------------------
# Test 10 — process_pages with 0 pages returns empty string
# ---------------------------------------------------------------------------
def test_zero_pages() -> None:
    result = process_pages(0, stub_render, stub_ocr, stub_translate)
    assert_eq("0 pages → empty string", result, "")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------
TESTS = [
    test_page_order_normalize_true,
    test_normalize_false_skips_translation,
    test_per_page_exception_captured,
    test_file_hash_deterministic,
    test_file_hash_nonexistent_fallback,
    test_auto_workers_bounds,
    test_auto_workers_env_override,
    test_concurrency,
    test_translation_system_prompt,
    test_zero_pages,
]

if __name__ == "__main__":
    failures = []
    for test_fn in TESTS:
        name = test_fn.__name__
        try:
            test_fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            print(f"  FAIL  {name}: {exc}")
            failures.append(name)
        except Exception as exc:
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
            failures.append(name)

    print()
    if failures:
        print(f"FAILED: {len(failures)} test(s): {failures}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")

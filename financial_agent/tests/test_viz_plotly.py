"""
tests/test_viz_plotly.py — stdlib-only self-test for viz_plotly.py
===================================================================
Run with:
    python3 tests/test_viz_plotly.py
or via py_compile first:
    python3 -m py_compile viz_plotly.py tests/test_viz_plotly.py
    python3 tests/test_viz_plotly.py
No third-party packages required.
"""

import json
import sys
import os

# ---------------------------------------------------------------------------
# Make sure the parent directory is on sys.path so we can import viz_plotly
# regardless of where this script is executed from.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from viz_plotly import (
    PLOTLY_CODE_SYSTEM_PROMPT,
    build_chart_prompt,
    extract_plotly_json,
    generate_plotly,
)

# ---------------------------------------------------------------------------
# Shared fixture: a minimal but valid Plotly figure JSON
# ---------------------------------------------------------------------------
_MINIMAL_FIGURE = {
    "data": [{"type": "bar", "x": ["A"], "y": [1]}],
    "layout": {"title": {"text": "t"}},
}
_MINIMAL_FIGURE_JSON = json.dumps(_MINIMAL_FIGURE)


# ===========================================================================
# Helper
# ===========================================================================

def _assert(condition: bool, message: str) -> None:
    """Raise AssertionError with *message* if *condition* is False."""
    if not condition:
        raise AssertionError(message)


# ===========================================================================
# Test 1 — PLOTLY_CODE_SYSTEM_PROMPT forbids matplotlib
# ===========================================================================

def test_system_prompt_forbids_matplotlib() -> None:
    lower = PLOTLY_CODE_SYSTEM_PROMPT.lower()
    _assert(
        "matplotlib" in lower,
        "PLOTLY_CODE_SYSTEM_PROMPT should mention 'matplotlib' (as a forbidden library)",
    )
    # The word "forbidden" (or an equivalent) must also appear in the prompt.
    _assert(
        "forbidden" in lower or "forbidden" in PLOTLY_CODE_SYSTEM_PROMPT,
        "PLOTLY_CODE_SYSTEM_PROMPT should contain the word 'FORBIDDEN'",
    )
    # It must name plotly as the required library.
    _assert(
        "plotly" in lower,
        "PLOTLY_CODE_SYSTEM_PROMPT should mention 'plotly'",
    )


# ===========================================================================
# Test 2 — build_chart_prompt embeds data and references fig.to_json()
# ===========================================================================

def test_build_chart_prompt_content() -> None:
    charts = [
        {
            "title": "Revenue Trend",
            "chart_type": "line",
            "description": "Annual revenue from 2020-2023",
            "data": [
                {"year": 2020, "revenue": 100},
                {"year": 2021, "revenue": 120},
            ],
        }
    ]
    prompt = build_chart_prompt(charts)

    _assert("Revenue Trend" in prompt, "Prompt must include chart title")
    _assert("line" in prompt, "Prompt must include chart type")
    _assert("Annual revenue from 2020-2023" in prompt, "Prompt must include description")
    # Data values must appear in the prompt
    _assert("2020" in prompt, "Prompt must include data value 2020")
    _assert("120" in prompt, "Prompt must include data value 120")
    # The critical instruction
    _assert(
        "fig.to_json()" in prompt,
        "Prompt must instruct the coder to end with print(fig.to_json())",
    )


def test_build_chart_prompt_multi_chart() -> None:
    charts = [
        {"title": "A", "chart_type": "bar", "description": "d1", "data": [{"x": 1}]},
        {"title": "B", "chart_type": "pie", "description": "d2", "data": [{"x": 2}]},
    ]
    prompt = build_chart_prompt(charts)
    _assert("make_subplots" in prompt or "subplot" in prompt.lower(),
            "Multi-chart prompt should mention subplots")
    _assert("Chart 1" in prompt, "Prompt should number the charts")
    _assert("Chart 2" in prompt, "Prompt should number the charts")


# ===========================================================================
# Test 3 — extract_plotly_json: valid Plotly JSON in noisy stdout
# ===========================================================================

def test_extract_plotly_json_with_noise() -> None:
    noisy_stdout = (
        "INFO: starting computation\n"
        "some debug output\n"
        + _MINIMAL_FIGURE_JSON
        + "\n"
        "INFO: done\n"
    )
    results = extract_plotly_json(noisy_stdout)
    _assert(len(results) == 1, f"Expected 1 figure, got {len(results)}")

    parsed = json.loads(results[0])
    _assert(parsed["data"][0]["type"] == "bar",
            "Extracted figure should have bar trace")
    _assert(parsed["layout"]["title"]["text"] == "t",
            "Extracted figure should preserve layout title")


def test_extract_plotly_json_empty_stdout() -> None:
    results = extract_plotly_json("")
    _assert(results == [], "Empty stdout should yield []")


def test_extract_plotly_json_non_figure_json() -> None:
    """JSON that is not a Plotly figure must be ignored."""
    stdout = (
        '{"status": "ok", "count": 42}\n'
        '{"error": "something went wrong"}\n'
        '["just", "an", "array"]\n'
        "plain text line\n"
    )
    results = extract_plotly_json(stdout)
    _assert(results == [],
            f"Non-figure JSON should yield [], got {results}")


def test_extract_plotly_json_data_must_be_list() -> None:
    """An object with 'data' and 'layout' where 'data' is not a list is NOT a figure."""
    not_a_figure = json.dumps({"data": "not-a-list", "layout": {}})
    results = extract_plotly_json(not_a_figure)
    _assert(results == [],
            f"Object with non-list 'data' should be ignored, got {results}")


def test_extract_plotly_json_multiple_figures() -> None:
    """When stdout contains two figure JSONs, both should be extracted."""
    figure2 = {
        "data": [{"type": "scatter", "x": [1, 2], "y": [3, 4]}],
        "layout": {"title": {"text": "scatter"}},
    }
    stdout = _MINIMAL_FIGURE_JSON + "\nsome noise\n" + json.dumps(figure2) + "\n"
    results = extract_plotly_json(stdout)
    _assert(len(results) == 2, f"Expected 2 figures, got {len(results)}")


# ===========================================================================
# Test 4 — generate_plotly: happy path with stub coder and stub runner
# ===========================================================================

def _make_stub_runner_success(figure_json: str):
    """Return a stub run_script_fn that always succeeds with *figure_json*."""
    def _runner(code: str):
        return (figure_json + "\n", "")
    return _runner


def _make_stub_runner_failing_then_succeeding(figure_json: str):
    """First call returns stderr; second call returns the valid figure JSON."""
    call_count = [0]

    def _runner(code: str):
        call_count[0] += 1
        if call_count[0] == 1:
            return ("", "NameError: name 'px' is not defined")
        return (figure_json + "\n", "")

    return _runner, call_count


def _make_stub_coder(code_to_return: str):
    """Return a stub coder_call that always returns *code_to_return*."""
    def _coder(messages, temperature=0.2):
        return code_to_return
    return _coder


def test_generate_plotly_happy_path() -> None:
    stub_code = "import plotly.express as px\nfig = px.bar(x=['A'], y=[1])\nprint(fig.to_json())"
    coder = _make_stub_coder(stub_code)
    runner = _make_stub_runner_success(_MINIMAL_FIGURE_JSON)

    charts = [{"title": "T", "chart_type": "bar", "description": "d",
               "data": [{"x": "A", "y": 1}]}]
    result = generate_plotly(charts, coder, runner, max_repair=1)

    _assert(len(result["plotly_jsons"]) == 1,
            f"Expected 1 figure JSON, got {len(result['plotly_jsons'])}")
    parsed = json.loads(result["plotly_jsons"][0])
    _assert(isinstance(parsed["data"], list), "Extracted figure data must be a list")
    _assert(result["code"] == stub_code, "Code in result must match stub code")
    _assert("layout" in result["stdout"] or result["stdout"] != "",
            "stdout must be non-empty")
    _assert(result["stderr"] == "", "stderr must be empty on success")


def test_generate_plotly_repair_path() -> None:
    """When first run fails with stderr, the repair path is taken."""
    stub_code = "import plotly.graph_objects as go\nfig = go.Figure()\nprint(fig.to_json())"
    coder = _make_stub_coder(stub_code)
    runner, call_count = _make_stub_runner_failing_then_succeeding(_MINIMAL_FIGURE_JSON)

    charts = [{"title": "T", "chart_type": "line", "description": "d", "data": []}]
    result = generate_plotly(charts, coder, runner, max_repair=1)

    _assert(call_count[0] == 2,
            f"run_script_fn should be called exactly twice (initial + repair), got {call_count[0]}")
    _assert(len(result["plotly_jsons"]) == 1,
            f"Repair path should yield 1 figure, got {len(result['plotly_jsons'])}")


def test_generate_plotly_no_repair_when_no_stderr() -> None:
    """If first run returns empty stdout with no stderr, no repair is attempted."""
    call_count = [0]

    def _runner(code: str):
        call_count[0] += 1
        return ("plain text, no figure JSON here\n", "")  # no figure, no error

    coder = _make_stub_coder("print('hello')")
    charts = [{"title": "X", "chart_type": "bar", "description": "", "data": []}]
    result = generate_plotly(charts, coder, _runner, max_repair=1)

    _assert(call_count[0] == 1,
            "No repair should be attempted if stderr is empty")
    _assert(result["plotly_jsons"] == [],
            "Result should have empty plotly_jsons when no figure found and no error")


def test_generate_plotly_strips_markdown_fences() -> None:
    """Code wrapped in ```python ... ``` fences is stripped before execution."""
    raw_code_in_fences = (
        "```python\n"
        "import plotly.express as px\n"
        "fig = px.bar(x=['A'], y=[1])\n"
        "print(fig.to_json())\n"
        "```"
    )
    expected_code = (
        "import plotly.express as px\n"
        "fig = px.bar(x=['A'], y=[1])\n"
        "print(fig.to_json())"
    )
    received_codes = []

    def _runner(code: str):
        received_codes.append(code)
        return (_MINIMAL_FIGURE_JSON + "\n", "")

    coder = _make_stub_coder(raw_code_in_fences)
    charts = [{"title": "T", "chart_type": "bar", "description": "", "data": []}]
    generate_plotly(charts, coder, _runner, max_repair=0)

    _assert(len(received_codes) == 1, "Runner should be called once")
    _assert(received_codes[0] == expected_code,
            f"Fences should be stripped.  Got:\n{received_codes[0]!r}")


def test_generate_plotly_returns_required_keys() -> None:
    coder = _make_stub_coder("pass")
    runner = _make_stub_runner_success(_MINIMAL_FIGURE_JSON)
    charts = [{"title": "T", "chart_type": "pie", "description": "", "data": []}]
    result = generate_plotly(charts, coder, runner)

    for key in ("plotly_jsons", "code", "stdout", "stderr"):
        _assert(key in result, f"Result must contain key '{key}'")


# ===========================================================================
# Run all tests
# ===========================================================================

def main() -> None:
    tests = [
        test_system_prompt_forbids_matplotlib,
        test_build_chart_prompt_content,
        test_build_chart_prompt_multi_chart,
        test_extract_plotly_json_with_noise,
        test_extract_plotly_json_empty_stdout,
        test_extract_plotly_json_non_figure_json,
        test_extract_plotly_json_data_must_be_list,
        test_extract_plotly_json_multiple_figures,
        test_generate_plotly_happy_path,
        test_generate_plotly_repair_path,
        test_generate_plotly_no_repair_when_no_stderr,
        test_generate_plotly_strips_markdown_fences,
        test_generate_plotly_returns_required_keys,
    ]

    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            print(f"  PASS  {test_fn.__name__}")
        except AssertionError as exc:
            print(f"  FAIL  {test_fn.__name__}: {exc}")
            failed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {test_fn.__name__}: {type(exc).__name__}: {exc}")
            failed += 1

    print()
    if failed:
        print(f"{failed}/{len(tests)} tests FAILED")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()

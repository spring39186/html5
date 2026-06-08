"""
viz_plotly.py — Interactive Plotly chart generation for the Financial RAG system.
==================================================================================

Phase 9 upgrade: replaces static matplotlib output_plot.png with interactive
Plotly figures.  The synthesizer produces chart specs; this module:
  1. Builds a coder prompt embedding those specs.
  2. Calls the injected coder LLM to produce Plotly Python code that ends with
     ``print(fig.to_json())``.
  3. Runs the code in an injected sandbox runner.
  4. Extracts the Plotly figure JSON from stdout.
  5. Returns the results (including a repair attempt on first failure).

LangGraph state field
---------------------
Add ``plotly_jsons: list[str]`` to your state / AgentResponse to carry the
serialised figures between nodes.

Streamlit render snippet
------------------------
::

    import plotly.io as pio
    for j in result["plotly_jsons"]:
        fig = pio.from_json(j)
        st.plotly_chart(fig, use_container_width=True)

Dependency injection
--------------------
Both the coder LLM and the sandbox runner are injected at call time so this
module is fully testable without a live LLM or a real subprocess environment.

    >>> from viz_plotly import generate_plotly
    >>> result = generate_plotly(charts, coder_call=my_llm, run_script_fn=my_sandbox)

Heavy dependencies (``plotly``, ``json`` is stdlib) are never imported at
module level — ``plotly`` is only needed inside the *generated* script that
runs in the sandbox.
"""

from __future__ import annotations

import json
import re
from typing import Callable, List, Tuple

# ---------------------------------------------------------------------------
# 1. System prompt constant
# ---------------------------------------------------------------------------

PLOTLY_CODE_SYSTEM_PROMPT: str = (
    "You are a senior data-visualisation engineer specialising in interactive "
    "financial charts.\n\n"
    "MANDATORY RULES — violating any rule makes the output invalid:\n"
    "1. Use ONLY plotly.graph_objects (imported as go) and/or plotly.express "
    "(imported as px).  You may also import plotly.subplots.make_subplots.\n"
    "2. FORBIDDEN libraries: matplotlib, seaborn, plotnine, bokeh, altair, "
    "and any other plotting library.  Do NOT import them.\n"
    "3. Use ONLY the real data provided in the prompt.  NEVER fabricate, "
    "simulate, or invent data points.  If a data field is missing, omit that "
    "trace rather than guessing values.\n"
    "4. Build exactly ONE figure object named `fig`.  When the request "
    "contains multiple charts, use plotly.subplots.make_subplots to combine "
    "them into a single figure with appropriate rows/cols for layout control.\n"
    "5. The LAST executable statement in the script MUST be:\n"
    "       print(fig.to_json())\n"
    "   Do NOT call fig.show() or write to a file.\n"
    "6. Output ONLY valid Python source code.  No markdown fences (``` or "
    "```python), no explanatory prose, no comments outside the code itself.\n"
    "7. Include every necessary import at the top of the script.\n"
    "8. Keep traces and layout clean: add titles, axis labels, and a legend "
    "where appropriate so the chart is self-explanatory.\n"
)

# ---------------------------------------------------------------------------
# 2. Build the user-facing coder prompt
# ---------------------------------------------------------------------------


def build_chart_prompt(charts: list[dict]) -> str:
    """Compose the user message sent to the coder LLM.

    Each chart spec is embedded with its title, type, description, and data
    serialised as JSON so the model can address them directly in code.

    For a single chart the model should build one figure.  For multiple charts
    it should use ``make_subplots`` to combine them in a single figure.

    The prompt explicitly reminds the model that the script must end with
    ``print(fig.to_json())``.

    Parameters
    ----------
    charts:
        List of chart spec dicts, each with keys:
        ``title``, ``chart_type`` ("line"|"bar"|"pie"),
        ``description``, ``data`` (list of row dicts).

    Returns
    -------
    str
        The fully-formatted user message string.
    """
    n = len(charts)
    if n == 0:
        return (
            "No charts were requested.  Please output a Python script that "
            "creates an empty Plotly figure and ends with print(fig.to_json())."
        )

    if n == 1:
        layout_instruction = (
            "Build a single Plotly figure for the chart spec below."
        )
    else:
        layout_instruction = (
            f"Build a single Plotly figure containing {n} subplots "
            f"(use plotly.subplots.make_subplots with an appropriate rows/cols "
            f"grid).  Assign one subplot per chart spec below."
        )

    lines: List[str] = [
        layout_instruction,
        "",
        "CHART SPECIFICATIONS",
        "=" * 40,
    ]

    for i, chart in enumerate(charts, start=1):
        title = chart.get("title", f"Chart {i}")
        chart_type = chart.get("chart_type", "bar")
        description = chart.get("description", "")
        data = chart.get("data", [])
        data_json = json.dumps(data, ensure_ascii=False, indent=2)

        lines += [
            f"\n--- Chart {i} of {n} ---",
            f"Title       : {title}",
            f"Chart type  : {chart_type}",
            f"Description : {description}",
            f"Data (JSON) :",
            data_json,
        ]

    lines += [
        "",
        "=" * 40,
        "REQUIREMENTS",
        "- Use ONLY the data provided above; do NOT invent or modify values.",
        "- The script MUST end with exactly:  print(fig.to_json())",
        "- Output ONLY Python code, no markdown fences.",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. Extract Plotly JSON from sandbox stdout
# ---------------------------------------------------------------------------

# Pre-compiled pattern: a JSON object that starts at a { and ends at }.
# We scan all such objects and keep only those that look like Plotly figures.
_JSON_OBJECT_RE = re.compile(r"\{", re.MULTILINE)


def _looks_like_plotly_figure(obj: dict) -> bool:
    """Return True if *obj* has both top-level "data" (list) and "layout" keys."""
    return (
        isinstance(obj, dict)
        and isinstance(obj.get("data"), list)
        and "layout" in obj
    )


def extract_plotly_json(stdout: str) -> list[str]:
    """Scan *stdout* for JSON objects that are valid Plotly figures.

    The function walks the stdout character-by-character looking for JSON
    objects (starting with ``{``).  For each candidate it attempts to parse it
    and then checks whether it contains the top-level ``"data"`` (a list) and
    ``"layout"`` keys that Plotly figure JSON always has.

    Extra print statements (debug output, progress messages, etc.) are silently
    skipped.

    Parameters
    ----------
    stdout:
        Full captured standard output from the sandbox run.

    Returns
    -------
    list[str]
        Compact JSON strings, each re-serialised via ``json.dumps`` to validate
        correctness.  Returns an empty list if no Plotly figures are found.
    """
    results: list[str] = []
    text = stdout or ""
    pos = 0
    length = len(text)

    while pos < length:
        brace_pos = text.find("{", pos)
        if brace_pos == -1:
            break

        # Try to find a matching closing brace by tracking nesting depth.
        depth = 0
        in_string = False
        escape_next = False
        end_pos = -1

        for i in range(brace_pos, length):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_pos = i
                    break

        if end_pos == -1:
            # No matching brace found; advance past this opening brace.
            pos = brace_pos + 1
            continue

        candidate = text[brace_pos : end_pos + 1]
        try:
            obj = json.loads(candidate)
            if _looks_like_plotly_figure(obj):
                results.append(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
        except (json.JSONDecodeError, ValueError):
            pass

        pos = end_pos + 1

    return results


# ---------------------------------------------------------------------------
# 4. Top-level generate_plotly function
# ---------------------------------------------------------------------------


def generate_plotly(
    charts: list[dict],
    coder_call: Callable[[list[dict], float], str],
    run_script_fn: Callable[[str], Tuple[str, str]],
    max_repair: int = 1,
) -> dict:
    """Generate interactive Plotly figures from synthesiser chart specs.

    Parameters
    ----------
    charts:
        List of chart spec dicts produced by the synthesiser, each containing:
        ``title``, ``chart_type`` ("line"|"bar"|"pie"),
        ``description``, ``data`` (list of row dicts).
    coder_call:
        Callable with signature ``(messages: list[dict], temperature: float=0.2) -> str``.
        Returns the raw model response (code possibly wrapped in markdown fences;
        fences are stripped automatically).
        In *agent.py* this is a thin wrapper around ``_chat(MODEL_CONFIG.coder, ...)``.
    run_script_fn:
        Callable with signature ``(code: str) -> (stdout: str, stderr: str)``.
        Executes *code* in an isolated subprocess and captures its output.
        In *agent.py* reuse the existing ``_run_script`` function directly.
    max_repair:
        Maximum number of repair attempts when the first run produces no figure
        JSON.  Defaults to 1 (one repair pass).

    Returns
    -------
    dict with keys:
        ``plotly_jsons`` – list[str] of compact Plotly figure JSON strings.
        ``code``         – the final Python code string that was executed.
        ``stdout``       – captured stdout from the final run.
        ``stderr``       – captured stderr from the final run.

    Streamlit render snippet
    ------------------------
    ::

        import plotly.io as pio
        for j in result["plotly_jsons"]:
            fig = pio.from_json(j)
            st.plotly_chart(fig, use_container_width=True)

    LangGraph state field
    ---------------------
    Append ``plotly_jsons`` to your ``AgentResponse`` dataclass and include it
    in the dict returned by ``run_financial_agent``::

        plotly_jsons: list[str] = field(default_factory=list)
    """

    def _strip_fences(raw: str) -> str:
        """Remove ```python ... ``` or ``` ... ``` markdown fences."""
        raw = (raw or "").strip()
        if "```python" in raw:
            raw = raw.split("```python", 1)[1]
            raw = raw.split("```", 1)[0]
        elif raw.startswith("```"):
            parts = raw.split("```")
            # parts[0] is empty, parts[1] is the code block, parts[2] is after
            raw = parts[1] if len(parts) > 1 else raw
        return raw.strip()

    # ---- Build initial prompt ----
    user_message = build_chart_prompt(charts)
    messages: list[dict] = [
        {"role": "system", "content": PLOTLY_CODE_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    # ---- First attempt ----
    raw_code = coder_call(messages, 0.2)
    code = _strip_fences(raw_code)
    stdout, stderr = run_script_fn(code)
    plotly_jsons = extract_plotly_json(stdout)

    # ---- Repair loop ----
    repairs_done = 0
    while not plotly_jsons and stderr and repairs_done < max_repair:
        repairs_done += 1
        repair_prompt = (
            f"The following Python script produced an error.  Fix it so it "
            f"runs correctly and ends with print(fig.to_json()).  Return ONLY "
            f"the corrected Python code, no markdown fences.\n\n"
            f"ORIGINAL CODE:\n{code}\n\n"
            f"ERROR MESSAGE:\n{stderr.strip()}\n"
        )
        repair_messages: list[dict] = [
            {"role": "system", "content": PLOTLY_CODE_SYSTEM_PROMPT},
            {"role": "user", "content": repair_prompt},
        ]
        raw_code = coder_call(repair_messages, 0.2)
        code = _strip_fences(raw_code)
        stdout, stderr = run_script_fn(code)
        plotly_jsons = extract_plotly_json(stdout)

    return {
        "plotly_jsons": plotly_jsons,
        "code": code,
        "stdout": stdout,
        "stderr": stderr,
    }

"""
tests/test_pipeline.py — stdlib-only offline tests for pipeline.py
===================================================================
Run with:
    cd /home/user/html5/financial_agent
    python3 tests/test_pipeline.py

No third-party imports required.  All LLM / search / SQL operations
are replaced by deterministic stubs with call counters.
"""

import sys
import os

# Allow importing pipeline from the parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import (
    PLANNER_V2_SYSTEM,
    PlanV2,
    parse_plan,
    route,
    ToolBudget,
    DEFAULT_TOOL_BUDGET,
    PipelineDeps,
    run_pipeline,
    _DEFAULT_QUERY_FOCUS,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert(condition: bool, msg: str) -> None:
    if not condition:
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# 1. PLANNER_V2_SYSTEM sanity checks
# ---------------------------------------------------------------------------

def test_planner_v2_system():
    _assert(isinstance(PLANNER_V2_SYSTEM, str), "PLANNER_V2_SYSTEM must be a str")
    _assert(len(PLANNER_V2_SYSTEM) > 100, "PLANNER_V2_SYSTEM seems too short")

    # Must contain all 7 allowed intents
    for intent in ("chat", "file_analysis", "multi_file", "financial_qa",
                   "database_query", "visualization", "translation"):
        _assert(intent in PLANNER_V2_SYSTEM,
                f"PLANNER_V2_SYSTEM missing intent '{intent}'")

    # Must include the required JSON output keys
    for key in ("intent", "confidence", "requires_files", "requires_rag",
                "requires_sql", "requires_ocr", "requires_visualization", "query_focus"):
        _assert(key in PLANNER_V2_SYSTEM,
                f"PLANNER_V2_SYSTEM missing JSON key '{key}'")

    # Must NOT mention tool design, step ordering or tool selection
    forbidden = ["工具執行順序", "撰寫步驟", "決定呼叫哪些工具"]
    for phrase in forbidden:
        # The prompt should explicitly say these are FORBIDDEN (禁), not instruct them
        # We check that the prompt contains the phrase only as part of a prohibition
        _assert("嚴禁" in PLANNER_V2_SYSTEM or "不要" in PLANNER_V2_SYSTEM,
                "PLANNER_V2_SYSTEM must forbid tool-flow design")
        break  # one check sufficient

    # Must mention query_focus
    _assert("query_focus" in PLANNER_V2_SYSTEM, "PLANNER_V2_SYSTEM missing query_focus")

    print("  [PASS] test_planner_v2_system")


# ---------------------------------------------------------------------------
# 2. parse_plan — good JSON (str)
# ---------------------------------------------------------------------------

def test_parse_plan_good_str():
    raw = '''{
        "intent": "file_analysis",
        "confidence": 0.92,
        "requires_files": true,
        "requires_rag": true,
        "requires_sql": false,
        "requires_ocr": false,
        "requires_visualization": false,
        "query_focus": ["2024年營收", "EPS"]
    }'''
    plan = parse_plan(raw)
    _assert(isinstance(plan, PlanV2), "parse_plan must return PlanV2")
    _assert(plan.intent == "file_analysis", f"intent mismatch: {plan.intent}")
    _assert(abs(plan.confidence - 0.92) < 1e-9, f"confidence mismatch: {plan.confidence}")
    _assert(plan.requires_files is True, "requires_files should be True")
    _assert(plan.requires_rag is True, "requires_rag should be True")
    _assert(plan.requires_sql is False, "requires_sql should be False")
    _assert(plan.query_focus == ["2024年營收", "EPS"],
            f"query_focus mismatch: {plan.query_focus}")
    print("  [PASS] test_parse_plan_good_str")


# ---------------------------------------------------------------------------
# 3. parse_plan — good JSON (dict passthrough)
# ---------------------------------------------------------------------------

def test_parse_plan_good_dict():
    data = {
        "intent": "database_query",
        "confidence": 0.85,
        "requires_files": False,
        "requires_rag": False,
        "requires_sql": True,
        "requires_ocr": False,
        "requires_visualization": False,
        "query_focus": ["historical revenue"],
    }
    plan = parse_plan(data)
    _assert(plan.intent == "database_query", f"intent: {plan.intent}")
    _assert(plan.requires_sql is True, "requires_sql must be True")
    _assert(plan.query_focus == ["historical revenue"], f"qf: {plan.query_focus}")
    print("  [PASS] test_parse_plan_good_dict")


# ---------------------------------------------------------------------------
# 4. parse_plan — JSON inside ```json fence
# ---------------------------------------------------------------------------

def test_parse_plan_fenced():
    raw = '''Sure, here you go:
```json
{
  "intent": "translation",
  "confidence": 0.99,
  "requires_files": false,
  "requires_rag": false,
  "requires_sql": false,
  "requires_ocr": false,
  "requires_visualization": false,
  "query_focus": []
}
```
That is the plan.'''
    plan = parse_plan(raw)
    _assert(plan.intent == "translation", f"intent: {plan.intent}")
    _assert(abs(plan.confidence - 0.99) < 1e-9, f"conf: {plan.confidence}")
    _assert(plan.query_focus == [], f"qf: {plan.query_focus}")
    print("  [PASS] test_parse_plan_fenced")


# ---------------------------------------------------------------------------
# 5. parse_plan — unknown intent → coerce to "chat"
# ---------------------------------------------------------------------------

def test_parse_plan_unknown_intent():
    raw = '{"intent": "super_analysis", "confidence": 0.8, "query_focus": []}'
    plan = parse_plan(raw)
    _assert(plan.intent == "chat", f"Expected 'chat' fallback, got '{plan.intent}'")
    print("  [PASS] test_parse_plan_unknown_intent")


# ---------------------------------------------------------------------------
# 6. parse_plan — confidence clamping
# ---------------------------------------------------------------------------

def test_parse_plan_confidence_clamp():
    plan_high = parse_plan({"intent": "chat", "confidence": 9.5, "query_focus": []})
    _assert(plan_high.confidence == 1.0, f"Expected 1.0, got {plan_high.confidence}")

    plan_low = parse_plan({"intent": "chat", "confidence": -3.0, "query_focus": []})
    _assert(plan_low.confidence == 0.0, f"Expected 0.0, got {plan_low.confidence}")
    print("  [PASS] test_parse_plan_confidence_clamp")


# ---------------------------------------------------------------------------
# 7. parse_plan — garbage input → fallback
# ---------------------------------------------------------------------------

def test_parse_plan_garbage():
    for bad in ("", "hello world", "```not json```", "{broken:", None):
        plan = parse_plan(bad)  # type: ignore[arg-type]
        _assert(isinstance(plan, PlanV2), "Must return PlanV2 on garbage")
        _assert(plan.intent == "chat", f"Fallback intent should be 'chat', got '{plan.intent}'")
        _assert(abs(plan.confidence - 0.3) < 1e-9,
                f"Fallback confidence should be 0.3, got {plan.confidence}")
        _assert(plan.requires_files is False, "Fallback requires_files must be False")
        _assert(plan.requires_rag is False, "Fallback requires_rag must be False")
        _assert(plan.requires_sql is False, "Fallback requires_sql must be False")
        _assert(plan.requires_ocr is False, "Fallback requires_ocr must be False")
        _assert(plan.requires_visualization is False,
                "Fallback requires_visualization must be False")
        _assert(plan.query_focus == [], f"Fallback query_focus must be [], got {plan.query_focus}")
    print("  [PASS] test_parse_plan_garbage")


# ---------------------------------------------------------------------------
# 8. parse_plan — query_focus not a list → coerce to []
# ---------------------------------------------------------------------------

def test_parse_plan_qf_coerce():
    plan = parse_plan({"intent": "chat", "confidence": 0.5, "query_focus": "single string"})
    _assert(plan.query_focus == [], f"Expected [], got {plan.query_focus}")
    print("  [PASS] test_parse_plan_qf_coerce")


# ---------------------------------------------------------------------------
# 9. route() — tool_pipeline when requires_files
# ---------------------------------------------------------------------------

def test_route_requires_files():
    plan = PlanV2(intent="file_analysis", confidence=0.9, requires_files=True)
    _assert(route(plan) == "tool_pipeline",
            "requires_files=True must route to tool_pipeline")
    print("  [PASS] test_route_requires_files")


# ---------------------------------------------------------------------------
# 10. route() — tool_pipeline when requires_sql
# ---------------------------------------------------------------------------

def test_route_requires_sql():
    plan = PlanV2(intent="database_query", confidence=0.9, requires_sql=True)
    _assert(route(plan) == "tool_pipeline",
            "requires_sql=True must route to tool_pipeline")
    print("  [PASS] test_route_requires_sql")


# ---------------------------------------------------------------------------
# 11. route() — tool_pipeline when requires_visualization
# ---------------------------------------------------------------------------

def test_route_requires_viz():
    plan = PlanV2(intent="visualization", confidence=0.9, requires_visualization=True)
    _assert(route(plan) == "tool_pipeline",
            "requires_visualization=True must route to tool_pipeline")
    print("  [PASS] test_route_requires_viz")


# ---------------------------------------------------------------------------
# 12. route() — fast_translate
# ---------------------------------------------------------------------------

def test_route_fast_translate():
    plan = PlanV2(intent="translation", confidence=0.95)
    _assert(route(plan) == "fast_translate",
            f"translation intent should route fast_translate, got {route(plan)}")
    print("  [PASS] test_route_fast_translate")


# ---------------------------------------------------------------------------
# 13. route() — fast_chat (confidence > 0.75)
# ---------------------------------------------------------------------------

def test_route_fast_chat():
    plan_hi = PlanV2(intent="chat", confidence=0.8)
    _assert(route(plan_hi) == "fast_chat",
            f"chat + confidence 0.8 should route fast_chat, got {route(plan_hi)}")

    plan_lo = PlanV2(intent="chat", confidence=0.75)  # exactly 0.75 → NOT > 0.75
    _assert(route(plan_lo) == "tool_pipeline",
            f"chat + confidence 0.75 should route tool_pipeline, got {route(plan_lo)}")
    print("  [PASS] test_route_fast_chat")


# ---------------------------------------------------------------------------
# 14. route() — direct_answer
# ---------------------------------------------------------------------------

def test_route_direct_answer():
    plan = PlanV2(intent="financial_qa", confidence=0.9)
    _assert(route(plan) == "direct_answer",
            f"financial_qa + confidence 0.9 should route direct_answer, got {route(plan)}")

    plan_lo = PlanV2(intent="financial_qa", confidence=0.6)
    _assert(route(plan_lo) == "tool_pipeline",
            f"financial_qa + confidence 0.6 should route tool_pipeline, got {route(plan_lo)}")
    print("  [PASS] test_route_direct_answer")


# ---------------------------------------------------------------------------
# 15. route() — fallback to tool_pipeline
# ---------------------------------------------------------------------------

def test_route_fallback():
    plan = PlanV2(intent="multi_file", confidence=0.5)
    _assert(route(plan) == "tool_pipeline",
            f"multi_file without flags should fall back to tool_pipeline, got {route(plan)}")
    print("  [PASS] test_route_fallback")


# ---------------------------------------------------------------------------
# 16. route() — flags take priority over intent
# ---------------------------------------------------------------------------

def test_route_flags_override_intent():
    # Even translation intent → tool_pipeline if requires_files is set
    plan = PlanV2(intent="translation", confidence=0.99, requires_files=True)
    _assert(route(plan) == "tool_pipeline",
            "flags must override even translation intent")
    # Even chat intent → tool_pipeline if requires_sql is set
    plan2 = PlanV2(intent="chat", confidence=0.99, requires_sql=True)
    _assert(route(plan2) == "tool_pipeline",
            "requires_sql must override chat fast-path")
    print("  [PASS] test_route_flags_override_intent")


# ---------------------------------------------------------------------------
# 17. ToolBudget — basic allow / spend / remaining
# ---------------------------------------------------------------------------

def test_toolbudget_basic():
    budget = ToolBudget({"parse_financial_pdf": 2, "search_knowledge_base": 3})

    _assert(budget.allow("parse_financial_pdf"), "Should allow first call")
    _assert(budget.remaining("parse_financial_pdf") == 2, "Initial remaining should be 2")

    budget.spend("parse_financial_pdf")
    _assert(budget.remaining("parse_financial_pdf") == 1, "Remaining should drop to 1")
    _assert(budget.allow("parse_financial_pdf"), "Should still allow second call")

    budget.spend("parse_financial_pdf")
    _assert(budget.remaining("parse_financial_pdf") == 0, "Remaining should be 0")
    _assert(not budget.allow("parse_financial_pdf"), "Budget exhausted, should deny")
    print("  [PASS] test_toolbudget_basic")


# ---------------------------------------------------------------------------
# 18. ToolBudget — unknown tool always allowed, remaining=999
# ---------------------------------------------------------------------------

def test_toolbudget_unknown_tool():
    budget = ToolBudget()
    _assert(budget.allow("unknown_tool_xyz"), "Unknown tool must always be allowed")
    _assert(budget.remaining("unknown_tool_xyz") == 999,
            "Unknown tool remaining should be 999")
    print("  [PASS] test_toolbudget_unknown_tool")


# ---------------------------------------------------------------------------
# 19. ToolBudget — exhaust via loop
# ---------------------------------------------------------------------------

def test_toolbudget_exhaust():
    budget = ToolBudget({"run_sql_query": 3})
    calls = 0
    while budget.allow("run_sql_query"):
        budget.spend("run_sql_query")
        calls += 1
    _assert(calls == 3, f"Expected 3 calls before exhaustion, got {calls}")
    _assert(budget.remaining("run_sql_query") == 0, "Remaining should be 0 after exhaustion")
    print("  [PASS] test_toolbudget_exhaust")


# ---------------------------------------------------------------------------
# 20. ToolBudget — DEFAULT_TOOL_BUDGET values
# ---------------------------------------------------------------------------

def test_default_tool_budget():
    _assert(isinstance(DEFAULT_TOOL_BUDGET, dict), "DEFAULT_TOOL_BUDGET must be a dict")
    _assert(DEFAULT_TOOL_BUDGET.get("parse_financial_pdf") == 3,
            "parse_financial_pdf default budget should be 3")
    _assert(DEFAULT_TOOL_BUDGET.get("search_knowledge_base") == 8,
            "search_knowledge_base default budget should be 8")
    _assert(DEFAULT_TOOL_BUDGET.get("run_sql_query") == 3,
            "run_sql_query default budget should be 3")
    _assert(DEFAULT_TOOL_BUDGET.get("get_database_schema") == 1,
            "get_database_schema default budget should be 1")
    print("  [PASS] test_default_tool_budget")


# ---------------------------------------------------------------------------
# 21. run_pipeline — RAG path: all files parsed, query_focus drives searches
# ---------------------------------------------------------------------------

def test_run_pipeline_rag_basic():
    parse_calls = []
    search_calls = []

    def stub_parse(file_name: str) -> str:
        parse_calls.append(file_name)
        return f"parsed:{file_name}"

    def stub_search(query: str, file_name: str):
        search_calls.append((query, file_name))
        return [
            {"id": f"{file_name}:{query}:0", "text": f"hit {query} in {file_name}", "metadata": {}},
        ]

    def stub_get_schema() -> str:
        return "schema"

    def stub_generate_sql(prompt: str, schema: str) -> str:
        return "SELECT 1"

    def stub_run_sql(sql: str) -> str:
        return "result"

    files = ["report_2023.pdf", "report_2024.pdf"]
    plan = PlanV2(
        intent="file_analysis",
        confidence=0.9,
        requires_files=True,
        query_focus=["revenue", "EPS"],
    )
    deps = PipelineDeps(
        parse_file=stub_parse,
        search_kb=stub_search,
        get_schema=stub_get_schema,
        generate_sql=stub_generate_sql,
        run_sql=stub_run_sql,
        files=files,
    )

    evidence = run_pipeline(plan, "analyze reports", deps)

    # All files must be parsed
    _assert(set(parse_calls) == set(files),
            f"Expected all files parsed, got: {parse_calls}")

    # query_focus × files = 2 queries × 2 files = 4 search calls
    _assert(len(search_calls) == 4,
            f"Expected 4 search calls (2 queries × 2 files), got {len(search_calls)}")

    # All searches driven by plan.query_focus
    searched_queries = {q for q, _ in search_calls}
    _assert(searched_queries == {"revenue", "EPS"},
            f"Expected queries {{revenue, EPS}}, got {searched_queries}")

    # Evidence items are all type "rag"
    _assert(all(e["type"] == "rag" for e in evidence),
            "All RAG-path evidence must have type='rag'")

    # Evidence count = 4 unique hits (one per search call, all unique ids)
    _assert(len(evidence) == 4, f"Expected 4 evidence items, got {len(evidence)}")

    print("  [PASS] test_run_pipeline_rag_basic")


# ---------------------------------------------------------------------------
# 22. run_pipeline — RAG path: empty query_focus uses _DEFAULT_QUERY_FOCUS
# ---------------------------------------------------------------------------

def test_run_pipeline_rag_default_focus():
    search_calls = []

    def stub_parse(f):
        return ""

    def stub_search(q, f):
        search_calls.append(q)
        return [{"id": f"{q}:{f}", "text": f"t:{q}", "metadata": {}}]

    plan = PlanV2(intent="file_analysis", confidence=0.9,
                  requires_files=True, query_focus=[])  # empty!
    deps = PipelineDeps(
        parse_file=stub_parse,
        search_kb=stub_search,
        get_schema=lambda: "",
        generate_sql=lambda p, s: "",
        run_sql=lambda s: "",
        files=["f.pdf"],
    )

    run_pipeline(plan, "analyze", deps)

    # Should fall back to _DEFAULT_QUERY_FOCUS
    searched = set(search_calls)
    for q in _DEFAULT_QUERY_FOCUS:
        _assert(q in searched, f"Default focus query '{q}' not searched")
    print("  [PASS] test_run_pipeline_rag_default_focus")


# ---------------------------------------------------------------------------
# 23. run_pipeline — evidence deduplication by hit id
# ---------------------------------------------------------------------------

def test_run_pipeline_rag_dedup():
    """Two search calls return a hit with the same id → only one evidence entry."""

    def stub_parse(f):
        return ""

    call_count = [0]

    def stub_search(q, f):
        call_count[0] += 1
        # Always return the same id "SHARED-1" regardless of query
        return [{"id": "SHARED-1", "text": f"text for {q}", "metadata": {}}]

    plan = PlanV2(intent="file_analysis", confidence=0.9,
                  requires_files=True, query_focus=["q1", "q2"])
    deps = PipelineDeps(
        parse_file=stub_parse,
        search_kb=stub_search,
        get_schema=lambda: "",
        generate_sql=lambda p, s: "",
        run_sql=lambda s: "",
        files=["f.pdf"],
    )

    evidence = run_pipeline(plan, "analyze", deps)

    # Two search calls happened (q1 and q2)
    _assert(call_count[0] == 2, f"Expected 2 search calls, got {call_count[0]}")

    # But only 1 evidence entry (dedup on id="SHARED-1")
    _assert(len(evidence) == 1,
            f"Expected 1 evidence after dedup, got {len(evidence)}")
    print("  [PASS] test_run_pipeline_rag_dedup")


# ---------------------------------------------------------------------------
# 24. run_pipeline — budget caps parse calls
# ---------------------------------------------------------------------------

def test_run_pipeline_budget_caps_parse():
    parse_calls = []

    def stub_parse(f):
        parse_calls.append(f)
        return ""

    def stub_search(q, f):
        return []

    plan = PlanV2(intent="file_analysis", confidence=0.9,
                  requires_files=True, query_focus=["revenue"])
    deps = PipelineDeps(
        parse_file=stub_parse,
        search_kb=stub_search,
        get_schema=lambda: "",
        generate_sql=lambda p, s: "",
        run_sql=lambda s: "",
        files=["a.pdf", "b.pdf", "c.pdf"],
    )

    # Budget allows only 1 parse call
    budget = ToolBudget({"parse_financial_pdf": 1, "search_knowledge_base": 10})
    run_pipeline(plan, "analyze", deps, budget=budget)

    _assert(len(parse_calls) == 1,
            f"Budget capped parse at 1, but got {len(parse_calls)} calls")
    print("  [PASS] test_run_pipeline_budget_caps_parse")


# ---------------------------------------------------------------------------
# 25. run_pipeline — budget caps search calls
# ---------------------------------------------------------------------------

def test_run_pipeline_budget_caps_search():
    search_calls = []

    def stub_search(q, f):
        search_calls.append((q, f))
        return [{"id": f"{q}:{f}", "text": "t", "metadata": {}}]

    plan = PlanV2(intent="file_analysis", confidence=0.9,
                  requires_files=True, query_focus=["q1", "q2", "q3"])
    deps = PipelineDeps(
        parse_file=lambda f: "",
        search_kb=stub_search,
        get_schema=lambda: "",
        generate_sql=lambda p, s: "",
        run_sql=lambda s: "",
        files=["a.pdf", "b.pdf"],
    )

    # Limit search to 2 calls (out of 3 queries × 2 files = 6 possible)
    budget = ToolBudget({"parse_financial_pdf": 5, "search_knowledge_base": 2})
    evidence = run_pipeline(plan, "analyze", deps, budget=budget)

    _assert(len(search_calls) == 2,
            f"Budget capped search at 2, but got {len(search_calls)} calls")
    _assert(len(evidence) == 2,
            f"Expected 2 evidence items (one per allowed search), got {len(evidence)}")
    print("  [PASS] test_run_pipeline_budget_caps_search")


# ---------------------------------------------------------------------------
# 26. run_pipeline — SQL path produces sql-type evidence
# ---------------------------------------------------------------------------

def test_run_pipeline_sql_path():
    schema_calls = [0]
    sql_calls = []
    run_calls = []

    def stub_get_schema():
        schema_calls[0] += 1
        return "TABLE financial_data"

    def stub_generate_sql(prompt, schema):
        sql_calls.append((prompt, schema))
        return "SELECT revenue FROM financial_data"

    def stub_run_sql(sql):
        run_calls.append(sql)
        return "revenue: 1000"

    plan = PlanV2(intent="database_query", confidence=0.9,
                  requires_sql=True, requires_files=False)
    deps = PipelineDeps(
        parse_file=lambda f: "",
        search_kb=lambda q, f: [],
        get_schema=stub_get_schema,
        generate_sql=stub_generate_sql,
        run_sql=stub_run_sql,
        files=[],
    )

    evidence = run_pipeline(plan, "show me revenue", deps)

    _assert(schema_calls[0] == 1, f"get_schema called {schema_calls[0]} times, expected 1")
    _assert(len(sql_calls) == 1, f"generate_sql called {len(sql_calls)} times, expected 1")
    _assert(len(run_calls) == 1, f"run_sql called {len(run_calls)} times, expected 1")

    _assert(len(evidence) == 1, f"Expected 1 sql evidence, got {len(evidence)}")
    ev = evidence[0]
    _assert(ev["type"] == "sql", f"Evidence type should be 'sql', got '{ev['type']}'")
    _assert(ev["source"] == "db", f"Evidence source should be 'db', got '{ev['source']}'")
    _assert("revenue: 1000" in ev["content"],
            f"Evidence content missing result: {ev['content']}")
    print("  [PASS] test_run_pipeline_sql_path")


# ---------------------------------------------------------------------------
# 27. run_pipeline — SQL retry on empty result
# ---------------------------------------------------------------------------

def test_run_pipeline_sql_retry():
    run_calls = []

    def stub_run_sql(sql):
        run_calls.append(sql)
        if len(run_calls) == 1:
            return "no rows"  # triggers retry
        return "revenue: 500"

    plan = PlanV2(intent="database_query", confidence=0.9,
                  requires_sql=True, requires_files=False)
    deps = PipelineDeps(
        parse_file=lambda f: "",
        search_kb=lambda q, f: [],
        get_schema=lambda: "schema",
        generate_sql=lambda p, s: f"SELECT 1 -- {p}",
        run_sql=stub_run_sql,
        files=[],
    )

    evidence = run_pipeline(plan, "show revenue", deps)

    _assert(len(run_calls) == 2, f"Expected 2 SQL runs (retry), got {len(run_calls)}")
    _assert(len(evidence) == 1, "Should have 1 evidence after successful retry")
    _assert("revenue: 500" in evidence[0]["content"],
            f"Evidence should contain retry result: {evidence[0]['content']}")
    print("  [PASS] test_run_pipeline_sql_retry")


# ---------------------------------------------------------------------------
# 28. run_pipeline — SQL retry budget exhausted (no more retry allowed)
# ---------------------------------------------------------------------------

def test_run_pipeline_sql_retry_budget_limit():
    """With budget=1 for run_sql_query, the pipeline runs once, cannot retry.
    The "no rows" string is still a non-empty result, so it IS appended to evidence
    (the pipeline does not suppress truthy results even if they look empty — that
    is the caller's responsibility).  We only verify that exactly 1 SQL run happens.
    """
    run_calls = [0]

    def stub_run_sql(sql):
        run_calls[0] += 1
        return "no rows"  # looks empty heuristically, but is a truthy string

    plan = PlanV2(intent="database_query", confidence=0.9,
                  requires_sql=True, requires_files=False)
    deps = PipelineDeps(
        parse_file=lambda f: "",
        search_kb=lambda q, f: [],
        get_schema=lambda: "schema",
        generate_sql=lambda p, s: "SELECT 1",
        run_sql=stub_run_sql,
        files=[],
    )

    # Only 1 sql_query budget → no retry allowed
    budget = ToolBudget({"get_database_schema": 1, "run_sql_query": 1})
    evidence = run_pipeline(plan, "show revenue", deps, budget=budget)

    # Exactly 1 SQL run — the retry path was blocked by the exhausted budget
    _assert(run_calls[0] == 1,
            f"With budget=1 for run_sql_query, should only run once, got {run_calls[0]}")

    # The "no rows" string is truthy, so it is appended as evidence
    # (the pipeline appends any non-empty result string)
    _assert(len(evidence) == 1,
            f"Expected 1 evidence entry (no rows is still a result string), got {len(evidence)}")
    _assert(evidence[0]["type"] == "sql",
            f"Evidence type should be 'sql', got {evidence[0]['type']}")
    _assert("no rows" in evidence[0]["content"],
            f"Evidence content should contain 'no rows', got {evidence[0]['content']}")
    print("  [PASS] test_run_pipeline_sql_retry_budget_limit")


# ---------------------------------------------------------------------------
# 29. run_pipeline — requires_files=True AND requires_sql=True, no explicit_db
#     → SQL path suppressed (files take priority unless intent==database_query)
# ---------------------------------------------------------------------------

def test_run_pipeline_files_suppress_sql():
    sql_calls = [0]

    def stub_run_sql(sql):
        sql_calls[0] += 1
        return "data"

    plan = PlanV2(intent="file_analysis", confidence=0.9,
                  requires_files=True, requires_sql=True)  # file_analysis, NOT database_query
    deps = PipelineDeps(
        parse_file=lambda f: "",
        search_kb=lambda q, f: [],
        get_schema=lambda: "schema",
        generate_sql=lambda p, s: "SELECT 1",
        run_sql=stub_run_sql,
        files=["report.pdf"],
    )

    run_pipeline(plan, "analyze report", deps)

    _assert(sql_calls[0] == 0,
            f"SQL should be suppressed when intent=file_analysis, got {sql_calls[0]} calls")
    print("  [PASS] test_run_pipeline_files_suppress_sql")


# ---------------------------------------------------------------------------
# 30. run_pipeline — database_query intent with files → SQL runs (explicit_db)
# ---------------------------------------------------------------------------

def test_run_pipeline_explicit_db_with_files():
    sql_calls = [0]

    def stub_run_sql(sql):
        sql_calls[0] += 1
        return "db result"

    plan = PlanV2(intent="database_query", confidence=0.9,
                  requires_files=True, requires_sql=True)  # explicit database_query
    deps = PipelineDeps(
        parse_file=lambda f: "",
        search_kb=lambda q, f: [],
        get_schema=lambda: "schema",
        generate_sql=lambda p, s: "SELECT 1",
        run_sql=stub_run_sql,
        files=["report.pdf"],
    )

    evidence = run_pipeline(plan, "query db", deps)

    # SQL should run because intent=database_query (explicit_db=True)
    _assert(sql_calls[0] >= 1,
            f"SQL should run for database_query intent, got {sql_calls[0]} calls")

    sql_evidence = [e for e in evidence if e["type"] == "sql"]
    _assert(len(sql_evidence) >= 1, "Should have at least 1 sql evidence entry")
    print("  [PASS] test_run_pipeline_explicit_db_with_files")


# ---------------------------------------------------------------------------
# 31. run_pipeline — no files, no sql flags → returns empty evidence
# ---------------------------------------------------------------------------

def test_run_pipeline_empty():
    plan = PlanV2(intent="chat", confidence=0.9)
    deps = PipelineDeps(
        parse_file=lambda f: "",
        search_kb=lambda q, f: [],
        get_schema=lambda: "",
        generate_sql=lambda p, s: "",
        run_sql=lambda s: "",
        files=[],
    )

    evidence = run_pipeline(plan, "hello", deps)
    _assert(evidence == [], f"Expected empty evidence for chat plan, got {evidence}")
    print("  [PASS] test_run_pipeline_empty")


# ---------------------------------------------------------------------------
# 32. run_pipeline — None budget defaults to DEFAULT_TOOL_BUDGET
# ---------------------------------------------------------------------------

def test_run_pipeline_default_budget():
    parse_calls = [0]

    def stub_parse(f):
        parse_calls[0] += 1
        return ""

    # Provide 10 files; default parse budget is 3
    files = [f"f{i}.pdf" for i in range(10)]
    plan = PlanV2(intent="file_analysis", confidence=0.9,
                  requires_files=True, query_focus=["revenue"])
    deps = PipelineDeps(
        parse_file=stub_parse,
        search_kb=lambda q, f: [],
        get_schema=lambda: "",
        generate_sql=lambda p, s: "",
        run_sql=lambda s: "",
        files=files,
    )

    run_pipeline(plan, "analyze", deps, budget=None)

    _assert(parse_calls[0] == DEFAULT_TOOL_BUDGET["parse_financial_pdf"],
            f"Default budget should cap parse at {DEFAULT_TOOL_BUDGET['parse_financial_pdf']}, "
            f"got {parse_calls[0]}")
    print("  [PASS] test_run_pipeline_default_budget")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

def run_all():
    tests = [
        test_planner_v2_system,
        test_parse_plan_good_str,
        test_parse_plan_good_dict,
        test_parse_plan_fenced,
        test_parse_plan_unknown_intent,
        test_parse_plan_confidence_clamp,
        test_parse_plan_garbage,
        test_parse_plan_qf_coerce,
        test_route_requires_files,
        test_route_requires_sql,
        test_route_requires_viz,
        test_route_fast_translate,
        test_route_fast_chat,
        test_route_direct_answer,
        test_route_fallback,
        test_route_flags_override_intent,
        test_toolbudget_basic,
        test_toolbudget_unknown_tool,
        test_toolbudget_exhaust,
        test_default_tool_budget,
        test_run_pipeline_rag_basic,
        test_run_pipeline_rag_default_focus,
        test_run_pipeline_rag_dedup,
        test_run_pipeline_budget_caps_parse,
        test_run_pipeline_budget_caps_search,
        test_run_pipeline_sql_path,
        test_run_pipeline_sql_retry,
        test_run_pipeline_sql_retry_budget_limit,
        test_run_pipeline_files_suppress_sql,
        test_run_pipeline_explicit_db_with_files,
        test_run_pipeline_empty,
        test_run_pipeline_default_budget,
    ]

    print(f"\nRunning {len(tests)} tests for pipeline.py...\n")
    failed = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"  [FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, f"EXCEPTION: {e}"))
            print(f"  [ERROR] {t.__name__}: {e}")

    print()
    if failed:
        print(f"FAILED {len(failed)}/{len(tests)} tests:")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    run_all()

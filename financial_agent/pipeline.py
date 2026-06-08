"""
pipeline.py — Deterministic financial-RAG pipeline layer
=========================================================
Replaces the LLM-driven "gather agent loop" with:

  1. PLANNER_V2_SYSTEM   — concise planner prompt (classify + decompose only)
  2. PlanV2 / parse_plan — robust plan dataclass + JSON extraction
  3. route()             — fully deterministic router
  4. ToolBudget          — per-tool call-count budget
  5. PipelineDeps        — injected callables (fully testable offline)
  6. run_pipeline()      — deterministic evidence-collection pipeline

No heavy imports at module level; everything that requires third-party
libraries is imported lazily inside the functions that need it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# 1.  PLANNER_V2_SYSTEM
# ---------------------------------------------------------------------------
PLANNER_V2_SYSTEM = """\
你是一位任務分類分析師。你的唯一職責是：
(1) 判斷使用者意圖並給出信心分數
(2) 列出具體的查詢焦點（query_focus）

【嚴禁】設計工具執行順序、撰寫步驟、或決定呼叫哪些工具——那是後續管線的工作。

【意圖類型（intent）】
- chat              : 打招呼、閒聊、不需資料的對話
- file_analysis     : 需分析使用者已上傳的檔案
- multi_file        : 需跨多份已上傳檔案比較
- financial_qa      : 財務概念問答，無需查閱任何檔案或資料庫（如「什麼是ROE」）
- database_query    : 使用者明確要求查歷史資料庫（說「查DB」「資料庫」「歷史數據」等）
- visualization     : 使用者提供了明確數字並要求繪圖（與 file_analysis 互斥）
- translation       : 翻譯需求

【判斷規則】
- 有上傳檔案且問題與內容相關 → file_analysis 或 multi_file
- 使用者提供明確數字且要求畫圖、且沒有上傳檔案 → visualization（獨立意圖）
- 若混合了「分析檔案＋畫圖」→ 標 file_analysis，requires_visualization=true，
  「不要」標成 visualization（visualization 是獨立意圖，不與 file_analysis 並存）
- 使用者明確說「查DB」「查資料庫」「歷史數據庫」→ database_query（優先於 file_analysis）
- 不確定時把 confidence 壓低（< 0.7），讓系統走完整工具管線
- 不要猜測資料庫或知識庫內是否已有資料

【query_focus】
列出「具體要查找的財務指標或問題點」，例如：
  ["2024年營收", "營業利益率趨勢", "EPS年增率"]
若問題是一般性閒聊或翻譯，query_focus 給空陣列 []。

【輸出】只輸出有效 JSON，不要任何其他文字：
{
  "intent": "chat|file_analysis|multi_file|financial_qa|database_query|visualization|translation",
  "confidence": 0.0-1.0,
  "requires_files": true/false,
  "requires_rag": true/false,
  "requires_sql": true/false,
  "requires_ocr": true/false,
  "requires_visualization": true/false,
  "query_focus": ["查詢點1", "查詢點2"]
}
"""

# ---------------------------------------------------------------------------
# 2.  PlanV2 dataclass + parse_plan()
# ---------------------------------------------------------------------------

@dataclass
class PlanV2:
    intent: str = "chat"
    confidence: float = 0.5
    requires_files: bool = False
    requires_rag: bool = False
    requires_sql: bool = False
    requires_ocr: bool = False
    requires_visualization: bool = False
    query_focus: List[str] = field(default_factory=list)


_SAFE_FALLBACK = PlanV2(
    intent="chat",
    confidence=0.3,
    requires_files=False,
    requires_rag=False,
    requires_sql=False,
    requires_ocr=False,
    requires_visualization=False,
    query_focus=[],
)

_ALLOWED_INTENTS = frozenset({
    "chat", "file_analysis", "multi_file", "financial_qa",
    "database_query", "visualization", "translation",
})


def _extract_json_obj(text: str) -> dict:
    """Strip markdown fences and extract the first {...} JSON object."""
    text = (text or "").strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


def parse_plan(raw: "str | dict") -> PlanV2:
    """
    Parse a planner response into a PlanV2.

    Accepts either:
    - a str (raw LLM output, possibly with markdown fences)
    - a dict (already-decoded JSON)

    On any failure returns the safe fallback PlanV2.
    """
    try:
        if isinstance(raw, dict):
            data = raw
        else:
            data = _extract_json_obj(str(raw))

        intent = str(data.get("intent", "chat")).strip()
        if intent not in _ALLOWED_INTENTS:
            intent = "chat"

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        query_focus = data.get("query_focus") or []
        if not isinstance(query_focus, list):
            query_focus = []
        query_focus = [str(q) for q in query_focus if q]

        return PlanV2(
            intent=intent,
            confidence=confidence,
            requires_files=bool(data.get("requires_files", False)),
            requires_rag=bool(data.get("requires_rag", False)),
            requires_sql=bool(data.get("requires_sql", False)),
            requires_ocr=bool(data.get("requires_ocr", False)),
            requires_visualization=bool(data.get("requires_visualization", False)),
            query_focus=query_focus,
        )
    except Exception:  # noqa: BLE001
        return PlanV2(
            intent="chat",
            confidence=0.3,
            requires_files=False,
            requires_rag=False,
            requires_sql=False,
            requires_ocr=False,
            requires_visualization=False,
            query_focus=[],
        )


# ---------------------------------------------------------------------------
# 3.  route() — fully deterministic
# ---------------------------------------------------------------------------

def route(plan: PlanV2) -> str:
    """
    Deterministic router.  Returns one of:
      "tool_pipeline" | "fast_translate" | "fast_chat" | "direct_answer"

    Priority:
      1. Any resource-heavy flag → tool_pipeline
      2. translation intent     → fast_translate
      3. chat + high confidence → fast_chat
      4. financial_qa + high confidence → direct_answer
      5. fallback               → tool_pipeline
    """
    # Resource-heavy flags always go to the full pipeline
    if plan.requires_files or plan.requires_sql or plan.requires_visualization:
        return "tool_pipeline"

    if plan.intent == "translation":
        return "fast_translate"

    if plan.intent == "chat" and plan.confidence > 0.75:
        return "fast_chat"

    if plan.intent == "financial_qa" and plan.confidence > 0.75:
        return "direct_answer"

    return "tool_pipeline"


# ---------------------------------------------------------------------------
# 4.  ToolBudget
# ---------------------------------------------------------------------------

DEFAULT_TOOL_BUDGET: dict[str, int] = {
    "parse_financial_pdf": 3,
    "search_knowledge_base": 8,
    "run_sql_query": 3,
    "get_database_schema": 1,
}


class ToolBudget:
    """
    Per-tool call-count budget.

    Usage:
        budget = ToolBudget({"parse_financial_pdf": 2, "search_knowledge_base": 5})
        if budget.allow("parse_financial_pdf"):
            budget.spend("parse_financial_pdf")
    """

    def __init__(self, limits: Optional[dict] = None) -> None:
        base = dict(DEFAULT_TOOL_BUDGET)
        if limits:
            base.update(limits)
        self._limits: dict[str, int] = {k: int(v) for k, v in base.items()}
        self._used: dict[str, int] = {k: 0 for k in self._limits}

    def allow(self, tool: str) -> bool:
        """Return True if the tool still has remaining budget."""
        limit = self._limits.get(tool)
        if limit is None:
            # Unknown tools are always allowed (no budget registered)
            return True
        return self._used.get(tool, 0) < limit

    def spend(self, tool: str) -> None:
        """Decrement the remaining budget for *tool*."""
        if tool in self._used:
            self._used[tool] += 1
        else:
            self._used[tool] = 1

    def remaining(self, tool: str) -> int:
        """Return how many calls remain for *tool*."""
        limit = self._limits.get(tool)
        if limit is None:
            return 999  # unlimited
        return max(0, limit - self._used.get(tool, 0))


# ---------------------------------------------------------------------------
# 5.  PipelineDeps — injected callables
# ---------------------------------------------------------------------------

@dataclass
class PipelineDeps:
    """
    All I/O callables injected into the pipeline so it is fully testable
    offline without any LLM, ChromaDB, or SQL server.

    Signatures:
        parse_file  : (file_name: str) -> str
                      Run OCR / parse on a file, return text summary.
        search_kb   : (query: str, file_name: str) -> list[dict]
                      Semantic search; each hit dict must have at least
                      {"id": str, "text": str, "metadata": dict}.
        get_schema  : () -> str
                      Return the database schema description.
        generate_sql: (user_prompt: str, schema: str) -> str
                      Generate a SELECT SQL for the given prompt + schema.
        run_sql     : (sql: str) -> str
                      Execute SQL and return result text.
        files       : list[str]
                      Names of uploaded files available for analysis.
    """
    parse_file: Callable[[str], str]
    search_kb: Callable[[str, str], list]
    get_schema: Callable[[], str]
    generate_sql: Callable[[str, str], str]
    run_sql: Callable[[str], str]
    files: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 6.  run_pipeline()
# ---------------------------------------------------------------------------

_DEFAULT_QUERY_FOCUS = ["revenue", "operating income", "net income", "EPS"]

_EMPTY_RESULT_PATTERNS = (
    "no rows",
    "0 rows",
    "empty",
    "no results",
    "no data",
    "查無",
    "0筆",
)


def _looks_empty(result_text: str) -> bool:
    """Heuristic: does a SQL result look like it returned nothing useful?"""
    low = (result_text or "").lower().strip()
    return not low or any(p in low for p in _EMPTY_RESULT_PATTERNS)


def run_pipeline(
    plan: PlanV2,
    user_prompt: str,
    deps: PipelineDeps,
    budget: Optional[ToolBudget] = None,
) -> List[dict]:
    """
    Deterministic evidence-collection pipeline.

    Returns a list of evidence dicts:
        {
            "source"  : str,   # file name or "db"
            "query"   : str,   # the query / SQL used
            "content" : str,   # retrieved text
            "type"    : str,   # "rag" | "sql"
        }

    Logic (NO LLM deciding order):
    ─────────────────────────────
    RAG path (plan.requires_files):
      For each file in deps.files (while parse budget allows):
        - deps.parse_file(file_name)
      For each file × each query in plan.query_focus
        (fallback: _DEFAULT_QUERY_FOCUS if query_focus is empty):
        - While search budget allows: deps.search_kb(query, file_name)
        - Append top hits; dedup by hit["id"] within the run.

    SQL path (plan.requires_sql and not plan.requires_files,
              OR user explicitly wants DB):
      - deps.get_schema()
      - sql = deps.generate_sql(user_prompt, schema)
      - result = deps.run_sql(sql)
      - If result looks empty: retry once with user_prompt + "retry"
      - Append evidence with type "sql"

    Budget is respected throughout; a tool stops when budget hits 0.
    """
    if budget is None:
        budget = ToolBudget()

    evidence: List[dict] = []
    seen_ids: set[str] = set()

    queries = plan.query_focus if plan.query_focus else _DEFAULT_QUERY_FOCUS

    # ── RAG path ──────────────────────────────────────────────────────────────
    if plan.requires_files and deps.files:
        # Step 1: parse each file
        for file_name in deps.files:
            if not budget.allow("parse_financial_pdf"):
                break
            budget.spend("parse_financial_pdf")
            try:
                deps.parse_file(file_name)
            except Exception:  # noqa: BLE001
                pass  # parse failure is non-fatal; search may still work

        # Step 2: search each file × each query
        for file_name in deps.files:
            for q in queries:
                if not budget.allow("search_knowledge_base"):
                    break
                budget.spend("search_knowledge_base")
                try:
                    hits = deps.search_kb(q, file_name) or []
                except Exception:  # noqa: BLE001
                    hits = []

                for hit in hits:
                    hit_id = hit.get("id") if isinstance(hit, dict) else None
                    if hit_id is not None:
                        if hit_id in seen_ids:
                            continue
                        seen_ids.add(hit_id)
                    content = (hit.get("text", "") if isinstance(hit, dict) else str(hit))
                    evidence.append({
                        "source": file_name,
                        "query": q,
                        "content": content,
                        "type": "rag",
                    })

    # ── SQL path ──────────────────────────────────────────────────────────────
    # Run SQL when:
    #   a) requires_sql is True AND we are NOT purely in file mode
    #      (i.e. no files uploaded, OR user explicitly asked for DB)
    # The explicit-DB check mirrors agent.py's dispatch_tool logic:
    #   "唯有 database_query 意圖才直接查 DB"
    explicit_db = (plan.intent == "database_query")
    run_sql_path = plan.requires_sql and (not plan.requires_files or explicit_db)

    if run_sql_path:
        if budget.allow("get_database_schema"):
            budget.spend("get_database_schema")
            try:
                schema = deps.get_schema()
            except Exception:  # noqa: BLE001
                schema = ""
        else:
            schema = ""

        if budget.allow("run_sql_query"):
            budget.spend("run_sql_query")
            try:
                sql = deps.generate_sql(user_prompt, schema)
                result = deps.run_sql(sql)
            except Exception:  # noqa: BLE001
                sql, result = "", ""

            # One retry if result looks empty
            if _looks_empty(result) and budget.allow("run_sql_query"):
                budget.spend("run_sql_query")
                try:
                    sql = deps.generate_sql(user_prompt + " (retry)", schema)
                    result = deps.run_sql(sql)
                except Exception:  # noqa: BLE001
                    pass

            if result:
                evidence.append({
                    "source": "db",
                    "query": sql,
                    "content": result,
                    "type": "sql",
                })

    return evidence

"""
LangGraph 工作流編排（Phase 6 / Phase 8）
========================================
FA_USE_GRAPH=1 才啟用，預設關閉。此模組「重用」agent.py 既有函式為節點，
不重寫任何業務邏輯，回傳與 run_financial_agent 完全相同的 dict。

工作流：
    User Input
        │
        ▼
    planner ──(route)──┬─ fast_chat ─────────────► END
                       ├─ fast_translate ────────► END
                       ├─ direct_answer ─────────► END
                       ├─ visualize ─────────────► END
                       └─ gather ─► synthesize ─► present ─► END
                          （gather 內部即完成 QueryTranslator/EntityExtractor/
                            HybridSearch/EvidenceGatherer —— 由 FA_HYBRID_RETRIEVAL 控制）

節點與既有函式對應：
    planner       → agent.planning_phase + agent.route_by_intent
    fast_chat     → agent.handle_fast_chat
    fast_translate→ agent.handle_fast_translate
    direct_answer → agent.handle_direct_answer
    visualize     → agent.handle_visualize
    gather        → agent._gather_evidence（含混合檢索/查詢翻譯/實體）
    synthesize    → agent._synthesize（英文證據 → 繁中報告）
    present       → agent._present_synthesis（表格 + Plotly/matplotlib 圖）

備註：本模組需要 `langgraph`（pip install langgraph）。未安裝或執行失敗時，
run_financial_agent 會自動回退到原生流程，不影響系統可用性。
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any, List, Optional, TypedDict

import agent  # 由 run_financial_agent 惰性載入；此時 agent 已完成初始化


class AgentState(TypedDict, total=False):
    """強型別工作流狀態（涵蓋 spec 要求欄位 + 內部攜帶物件）。"""
    # 公開欄位
    user_query: str
    file_registry: dict
    history: list
    translated_query: str
    expanded_terms: list
    intent: str
    entities: dict
    retrieved_chunks: list
    evidence: list
    answer: str
    plotly_jsons: list
    retrieval_debug: Optional[dict]
    execution_logs: list
    errors: list
    metadata: dict
    route: str
    # 內部攜帶（不序列化）
    _plan: Any
    _resp: Any
    _synth: dict
    _want_viz: bool


# ============================================================
# 節點（皆重用 agent.py 既有函式）
# ============================================================
def _planner(state: AgentState) -> dict:
    uq = state["user_query"]
    fr = state.get("file_registry") or {}
    resp = agent.AgentResponse()
    agent._trace(resp, "request", user_prompt=uq, files=list(fr.keys()),
                 history_turns=len(state.get("history") or []), engine="langgraph")
    plan = agent.planning_phase(uq, fr, state.get("history") or [])
    resp.planning_result = agent._build_planning_result(plan)
    route = agent.route_by_intent(plan, fr)
    resp.route = route
    agent._trace(resp, "planning", model=agent.MODEL_CONFIG.planner, **resp.planning_result)
    agent._trace(resp, "routing", route=route, intent=plan.intent.value,
                 confidence=plan.confidence)
    return {"_plan": plan, "_resp": resp, "intent": plan.intent.value, "route": route}


def _fast_chat(state: AgentState) -> dict:
    resp = state["_resp"]
    resp.report_text = agent.handle_fast_chat(state["user_query"], state.get("history"))
    agent._trace(resp, "fast_path", route="fast_chat", model=agent.MODEL_CONFIG.chat)
    return {"_resp": resp, "answer": resp.report_text}


def _fast_translate(state: AgentState) -> dict:
    resp = state["_resp"]
    resp.report_text = agent.handle_fast_translate(state["user_query"])
    agent._trace(resp, "fast_path", route="fast_translate", model=agent.MODEL_CONFIG.coder)
    return {"_resp": resp, "answer": resp.report_text}


def _direct_answer(state: AgentState) -> dict:
    resp = state["_resp"]
    resp.report_text = agent.handle_direct_answer(
        state["user_query"], state["_plan"], state.get("history"))
    agent._trace(resp, "fast_path", route="direct_answer", model=agent.MODEL_CONFIG.executor)
    return {"_resp": resp, "answer": resp.report_text}


def _visualize(state: AgentState) -> dict:
    resp = state["_resp"]
    result = agent.handle_visualize(state["user_query"], state["_plan"])
    if result.get("plot"):
        resp.images.append(result["plot"])
    resp.report_text = result.get("output", "圖表已生成。")
    agent._trace(resp, "visualize", model=agent.MODEL_CONFIG.coder)
    return {"_resp": resp, "answer": resp.report_text}


def _gather(state: AgentState) -> dict:
    resp = state["_resp"]
    # gather 內部即把 DB 大數據 CSV 快取路徑寫進 resp.csv_cache_path（前端解鎖樞紐/網格 Tab）
    evidence = agent.gather(state["_plan"], state["user_query"],
                            state.get("file_registry") or {}, resp)
    return {"_resp": resp, "evidence": evidence}


def _synthesize(state: AgentState) -> dict:
    resp = state["_resp"]
    want = agent._wants_visualization(state["user_query"], state["_plan"])
    synth = agent._synthesize(state["user_query"], state["_plan"],
                              state.get("evidence") or [], resp, want)
    return {"_resp": resp, "_synth": synth, "_want_viz": want,
            "answer": synth.get("report", "")}


def _present(state: AgentState) -> dict:
    resp = state["_resp"]
    agent._present_synthesis(state.get("_synth") or {}, resp,
                             state.get("file_registry") or {}, state.get("_want_viz", False))
    return {"_resp": resp, "plotly_jsons": resp.plotly_jsons}


# ============================================================
# 建圖（編譯一次、重用）
# ============================================================
@lru_cache(maxsize=1)
def _build():
    from langgraph.graph import StateGraph, END

    g = StateGraph(AgentState)
    g.add_node("planner", _planner)
    g.add_node("fast_chat", _fast_chat)
    g.add_node("fast_translate", _fast_translate)
    g.add_node("direct_answer", _direct_answer)
    g.add_node("visualize", _visualize)
    g.add_node("gather", _gather)
    g.add_node("synthesize", _synthesize)
    g.add_node("present", _present)

    g.set_entry_point("planner")
    g.add_conditional_edges("planner", lambda s: s.get("route", "execute_tools"), {
        "fast_chat": "fast_chat",
        "fast_translate": "fast_translate",
        "direct_answer": "direct_answer",
        "visualize": "visualize",
        "execute_tools": "gather",
    })
    for leaf in ("fast_chat", "fast_translate", "direct_answer", "visualize"):
        g.add_edge(leaf, END)
    g.add_edge("gather", "synthesize")
    g.add_edge("synthesize", "present")
    g.add_edge("present", END)

    _compiled = g.compile()
    return _compiled


# ============================================================
# 對外入口（與 run_financial_agent 回傳一致）
# ============================================================
def run(user_prompt: str, file_registry: dict = None, history: List[dict] = None) -> dict:
    print("\n" + "=" * 60 + "\n🕸️  [LangGraph] 工作流執行\n" + "=" * 60)
    app = _build()
    t_start = time.perf_counter()
    final = app.invoke({
        "user_query": user_prompt,
        "file_registry": file_registry or {},
        "history": history or [],
    })
    resp = final["_resp"]
    agent._trace(resp, "done", total_ms=round((time.perf_counter() - t_start) * 1000, 1),
                 image_count=len(resp.images), table_count=len(resp.tables))
    return agent._build_result(resp)  # 與 run_financial_agent 共用同一建構器

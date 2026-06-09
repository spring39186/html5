"""
多模型協作 Agentic 財務 AI 助手（合併優化版）
=============================================
整合並取代原本三個重複版本（financial_agent_v2 / multi_model_agent / LLMAgenticModelDM）。

架構（流程）：
    User Input
        │
        ▼
   [Phase 1] Qwen Planner ── 中文意圖分析 + 信心評分 + 產生計畫
        │
        ▼
   [Phase 2] Router ── 確定性路由（明確意圖直接拍板，不再讓執行者重複決策）
        │
   ┌────┼─────────────┬──────────────┬───────────────┐
   ▼    ▼             ▼              ▼               ▼
 chat  financial_qa  translation   visualization   file_analysis / 其他
 (chat) (executor)   (coder)       (coder→沙箱)         │
                                                        ▼
                              [Phase 3] 多 agent 協作管線：
                              收集 agent（OCR/檢索/SQL 撈證據）
                                 → 總結 agent（整合成連貫報告 + 指定圖表）
                                 → Coder agent（依指定畫圖）
                                 → 一起呈現（報告 + 表格 + 圖表，由總結者統籌）

關鍵優化（相對原版）：
1. 視覺化改為「確定性路徑」：intent=visualization 直接指定 Coder 生成繪圖碼，
   不再靠 len(code)<50 / 關鍵字「畫」這種脆弱啟發式。
2. Executor 預設改用 Qwen（function-calling 較穩、中文較好），可用環境變數切回 Gemma。
3. Router 對明確意圖直接拍板，避免 Planner 與 Executor 雙重決策打架。
4. 修正 PDF 頁數統計 bug（原版在 doc.close() 後讀 len(doc)）。
5. ChromaDB chunk 清理改用實際查詢，不再硬迴圈 range(500)。
6. 全頁 OCR 失敗時逐頁降級，不會因單頁壞掉整份失敗。
"""

import os
import io
import re
import json
import time
import base64
import subprocess
import tempfile
from enum import Enum
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field

from openai import OpenAI
import fitz  # PyMuPDF
import pandas as pd
from great_tables import GT
from chromadb.utils import embedding_functions 
from langchain_text_splitters import RecursiveCharacterTextSplitter
import chromadb

from config import MODEL_CONFIG, RUNTIME
import mock_db
import units

# ============================================================
# 初始化
# ============================================================
client = OpenAI(base_url=RUNTIME.base_url, api_key=RUNTIME.api_key)

# 告訴 ChromaDB 不要上網找，直接去我的 D 槽拿模型！
local_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name=r"D:\ASEHC\K26495\PythonTools\Codes\FinanceAI\paraphrase-multilingual-MiniLM-L12-v2"
)

chroma_client = chromadb.Client()
collection = chroma_client.get_or_create_collection(
    name="financial_docs",
    embedding_function=local_ef
)

os.makedirs(RUNTIME.cache_dir, exist_ok=True)

# MCP 橋接（agent 當 MCP client）。預設關閉，FA_USE_MCP=1 才啟用。
MCP_BRIDGE = None
MCP_TOOL_NAMES: set = set()


class IntentType(str, Enum):
    CHAT = "chat"
    FINANCIAL_QA = "financial_qa"
    FILE_ANALYSIS = "file_analysis"
    TRANSLATION = "translation"
    VISUALIZATION = "visualization"
    DATABASE_QUERY = "database_query"
    CODE_EXECUTION = "code_execution"
    MULTI_FILE_COMPARE = "multi_file"

    @classmethod
    def coerce(cls, value: str) -> "IntentType":
        try:
            return cls(value)
        except ValueError:
            return cls.CHAT


@dataclass
class PlanningResult:
    intent: IntentType
    confidence: float
    steps: List[str]
    first_tool: Optional[str]
    requires_files: bool
    target_files: List[str]
    reasoning: str
    is_multi_file: bool = False


@dataclass
class AgentResponse:
    report_text: str = ""
    tables: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    thought_logs: List[dict] = field(default_factory=list)
    planning_result: Optional[dict] = None
    route: str = ""
    executed_sql: List[str] = field(default_factory=list)  # 本輪實際執行的 SQL（供對話記憶）
    plotly_jsons: List[str] = field(default_factory=list)  # 互動式 Plotly 圖（FA_PLOTLY 開啟時）
    trace: List[dict] = field(default_factory=list)  # 完整執行軌跡（供下載/優化）


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ── 進度回呼：前端可註冊 hook，即時顯示「思考中…」狀態，避免使用者以為當機 ──
_PROGRESS_HOOK = None


def set_progress_hook(fn) -> None:
    """前端註冊一個 fn(message:str) 來即時接收進度；傳 None 取消。"""
    global _PROGRESS_HOOK
    _PROGRESS_HOOK = fn


def _progress(message: str) -> None:
    """回報一句進度給前端（hook 失敗不影響主流程）。"""
    if _PROGRESS_HOOK is not None:
        try:
            _PROGRESS_HOOK(message)
        except Exception:  # noqa: BLE001
            pass


# trace phase → 使用者看得懂的進度短語（其餘 phase 不顯示）
def _phase_message(phase: str, event: dict):
    if phase == "planning":
        return f"🧠 意圖分析完成：{event.get('intent', '')}（信心 {event.get('confidence', '')}）"
    if phase == "routing":
        return f"🔀 規劃路由：{event.get('route', '')}"
    if phase == "tool_call":
        tool = event.get("tool", "")
        args = event.get("args", {}) or {}
        detail = args.get("file_name") or args.get("search_query") or args.get("sql") or ""
        names = {"parse_financial_pdf": "📄 解析檔案", "search_knowledge_base": "🔎 檢索",
                 "get_database_schema": "🗂️ 讀取資料庫結構", "run_sql_query": "📊 查詢資料庫"}
        return f"{names.get(tool, '🔧 ' + tool)}：{str(detail)[:50]}"
    if phase == "gather_done":
        return f"📚 證據收集完成（{event.get('evidence_count', '')} 筆）"
    if phase == "metric_extraction":
        return "🔢 抽取各年度數據完成"
    if phase == "synthesis":
        return "🧩 報告整合完成"
    if phase == "charts":
        return "📈 圖表繪製完成"
    if phase == "present":
        return "🖼️ 整理輸出中…"
    return None


def _trace(resp: "AgentResponse", phase: str, **fields) -> None:
    """記錄一筆執行軌跡事件，含時間戳。"""
    event = {"phase": phase, "ts": _now_iso()}
    event.update({k: v for k, v in fields.items() if v is not None})
    resp.trace.append(event)
    msg = _phase_message(phase, event)
    if msg:
        _progress(msg)


def _preview(value: Any, limit: int = 800) -> str:
    """把工具結果裁成可讀的預覽（避免 trace 過肥）。"""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + f"…（已截斷，共 {len(text)} 字）"


# ============================================================
# 工具 Schema 載入
# ============================================================
def load_tools_schema(path: str = None) -> List[dict]:
    path = path or RUNTIME.tools_path
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    # 找不到外部檔時回傳內建預設（與 AgentTools.json 一致）
    return _DEFAULT_TOOLS


AGENT_TOOLS: List[dict] = []  # 於檔末載入（需先定義 _DEFAULT_TOOLS）


# ============================================================
# 小工具：穩健 JSON 解析
# ============================================================
def _extract_json(text: str) -> dict:
    """從可能夾雜 markdown / 多餘文字的回應中抽出第一個 JSON 物件。"""
    text = (text or "").strip()
    # 去掉 ```json ... ``` 圍欄
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


def _chat(model: str, messages: list, temperature: float = 0.2, **kw) -> str:
    """單輪文字呼叫，集中錯誤處理。預設帶請求逾時，避免卡死整個流程。"""
    kw.setdefault("timeout", RUNTIME.request_timeout)
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature, **kw
    )
    return resp.choices[0].message.content or ""


def _history_messages(history: List[dict], max_turns: int = 6,
                      max_chars: int = 1500) -> List[dict]:
    """把對話歷史轉成可丟給模型的 messages（取最近幾輪、各自截斷）。"""
    out = []
    for m in (history or [])[-max_turns:]:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content[:max_chars]})
    return out


def _history_text(history: List[dict], max_turns: int = 6, max_chars: int = 800) -> str:
    """把對話歷史壓成一段文字摘要（給 planner 參考脈絡用）。"""
    lines = []
    for m in (history or [])[-max_turns:]:
        role = "使用者" if m.get("role") == "user" else "助手"
        content = (m.get("content") or "").strip().replace("\n", " ")
        if content:
            lines.append(f"{role}：{content[:max_chars]}")
    return "\n".join(lines) if lines else "（無）"


# ============================================================
# Phase 1: Qwen 意圖分析與任務規劃
# ============================================================
PLANNING_SYSTEM_PROMPT = """你是一個專業的任務規劃分析師，專精於分析繁體中文使用者意圖。

【你的職責】
1. 精準分析使用者的真實意圖
2. 評估你的判斷信心（confidence）
3. 將任務拆解為可執行步驟，並決定第一個要呼叫的工具

【意圖類型】
- chat: 打招呼、閒聊、簡單問候
- financial_qa: 財務概念問答，不需查檔案（如「什麼是ROE」）
- file_analysis: 需要分析已上傳的檔案內容
- translation: 翻譯需求
- visualization: 需要繪製圖表
- database_query: 查詢歷史資料庫
- multi_file: 跨檔案比較

【判斷原則】
- 若使用者有上傳檔案且問題與內容相關 → 通常是 file_analysis（或 multi_file）。
- 一句話混合多個任務（如「分析＋統計＋畫圖」）時：
    * 只要其中需要讀取上傳檔案，就標 file_analysis 或 multi_file，
      把「畫圖」當成其中一個步驟列進 steps，**不要**標成 visualization。
    * 唯有「使用者已經把要畫的數字直接寫在訊息裡、且沒有上傳檔案」才標 visualization。
- 不確定時把 confidence 壓低（<0.7），讓系統走完整工具流程，不要硬猜。
- 【追問處理】若使用者是針對「先前的結果或動作」追問（例如：「把剛才的SQL show出來」、
  「解釋上一個答案」、「剛才那張圖改一下」），這通常是後續對話，多半標為 chat 或
  financial_qa、first_tool 設為 null，直接用對話脈絡回答，**不要重跑檔案解析或資料庫查詢**。

【輸出格式】只輸出有效 JSON，不要任何其他文字：
{
  "intent": "意圖類型",
  "confidence": 0.0-1.0,
  "steps": ["步驟1", "步驟2"],
  "first_tool": "第一個工具名稱或 null",
  "requires_files": true/false,
  "target_files": ["檔名"],
  "reasoning": "推理說明",
  "is_multi_file": true/false
}"""


def planning_phase(user_prompt: str, file_registry: Dict[str, str],
                   history: List[dict] = None) -> PlanningResult:
    """Phase 1：Qwen 做中文意圖分析。失敗時降級為一般對話。"""
    file_list = list(file_registry.keys()) if file_registry else []
    planning_prompt = (
        f"【最近對話脈絡】\n{_history_text(history)}\n\n"
        f"【使用者本次輸入】\n{user_prompt}\n\n"
        f"【目前已上傳檔案】\n"
        f"{json.dumps(file_list, ensure_ascii=False) if file_list else '無'}\n\n"
        "請分析意圖並規劃步驟。只輸出 JSON。"
    )

    try:
        raw = _chat(
            MODEL_CONFIG.planner,
            [
                {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
                {"role": "user", "content": planning_prompt},
            ],
            temperature=0.1,
        )
        r = _extract_json(raw)
        return PlanningResult(
            intent=IntentType.coerce(r.get("intent", "chat")),
            confidence=float(r.get("confidence", 0.5)),
            steps=r.get("steps", []) or [],
            first_tool=r.get("first_tool") or None,
            requires_files=bool(r.get("requires_files", False)),
            target_files=r.get("target_files", []) or [],
            reasoning=r.get("reasoning", ""),
            is_multi_file=bool(r.get("is_multi_file", False)),
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Planning phase error: {e}")
        # 有檔案時降級為 file_analysis 比較安全，避免漏掉檔案分析
        fallback_intent = IntentType.FILE_ANALYSIS if file_registry else IntentType.CHAT
        return PlanningResult(
            intent=fallback_intent,
            confidence=0.3,
            steps=["降級處理"],
            first_tool="parse_financial_pdf" if file_registry else None,
            requires_files=bool(file_registry),
            target_files=file_list,
            reasoning=f"規劃失敗，降級處理: {e}",
        )


# ============================================================
# Phase 2: 確定性路由器
# ============================================================
def route_by_intent(plan: PlanningResult, file_registry: Dict[str, str]) -> str:
    """
    回傳處理方式："fast_chat" | "direct_answer" | "fast_translate"
                  | "visualize" | "execute_tools"

    原則：
    - 任何需要「先讀檔案內容」的任務，一律走完整工具迴圈（parse → search → ...）。
    - visualize 捷徑只用於「使用者已在訊息裡給好數據、單純畫圖」的情境，
      絕不能用在需要從上傳檔案萃取數據的繪圖（否則 Coder 會憑空捏造數據）。
    """
    conf = plan.confidence
    intent = plan.intent
    th = RUNTIME.fastpath_confidence

    # 規劃器一旦判為 chat，就一律走 fast_chat（不再用 confidence 卡關）。
    # 不對稱原則：把任務誤當聊天只是「回一句、使用者再問」的小代價；
    # 把聊天誤當任務卻會觸發 9 分鐘 gather + 逾時，代價慘重。
    # 信心低代表「拿不準」——拿不準時當聊天，比硬塞進完整工具流程安全。
    if intent == IntentType.CHAT:
        return "fast_chat"

    # ── 有上傳檔案：除了純翻譯，全部走工具迴圈 ──
    # （含 visualization：要先解析檔案拿到真實數據，才能畫真實的圖）
    if file_registry:
        if intent == IntentType.TRANSLATION:
            return "fast_translate"
        return "execute_tools"

    # ── 無上傳檔案 ──
    if intent == IntentType.TRANSLATION:
        return "fast_translate"
    # 只有「不需檔案、非多檔、非多步」的純畫圖才走捷徑
    if (intent == IntentType.VISUALIZATION
            and not plan.requires_files
            and not plan.is_multi_file
            and len(plan.steps) <= 2):
        return "visualize"
    if intent == IntentType.FINANCIAL_QA and conf >= th and not plan.first_tool:
        return "direct_answer"
    if plan.first_tool or plan.requires_files or plan.is_multi_file:
        return "execute_tools"
    return "direct_answer"


# ============================================================
# 工具實作
# ============================================================
def _should_vision_ocr(text_layer: str, has_images: bool) -> bool:
    """懶人 OCR 決策：True=用 vision（掃描頁/影像化表格），False=直接用文字層。"""
    t = (text_layer or "").strip()
    if len(t) < 80:
        return True  # 幾乎沒文字 → 掃描頁，必須 vision
    # 文字少又有圖 → 才需要看數字密度（可能是影像化的表格）；長文頁直接用文字層
    if has_images and len(t) < 400:
        digit_ratio = sum(c.isdigit() for c in t) / len(t)
        if digit_ratio > 0.2:
            return True
    return False


def _cache_path(pdf_path: str) -> str:
    return os.path.join(RUNTIME.cache_dir, f"{os.path.basename(pdf_path)}.md")


def _normalize_to_english(full_text: str) -> str:
    """把多語言 OCR 內容逐頁翻成英文（保留表格/數字/結構），統一語言以利檢索與整合。"""
    from ocr_pipeline import TRANSLATION_SYSTEM_PROMPT  # 共用同一份翻譯指令，避免兩路徑不一致
    out = []
    for sec in full_text.split("\n\n---\n\n"):
        if len(sec.strip()) < 20:
            out.append(sec)
            continue
        try:
            out.append(_chat(MODEL_CONFIG.coder,
                             [{"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                              {"role": "user", "content": sec}],
                             temperature=0.1))
        except Exception:  # noqa: BLE001
            out.append(sec)  # 翻譯失敗保留原文
    return "\n\n---\n\n".join(out)


def parse_financial_pdf(args: dict, file_registry: dict) -> str:
    """PDF 全頁 OCR → Markdown → 向量化入庫。"""
    file_name = args.get("file_name")
    pdf_path = file_registry.get(file_name) if file_registry else None
    if not file_name or not pdf_path or not os.path.exists(pdf_path):
        available = ", ".join(file_registry.keys()) if file_registry else "無"
        return f"❌ 找不到檔案 '{file_name}'。可用檔案: {available}"

    # 冪等：本檔若已解析入庫（本 session），直接略過，避免模型重複呼叫造成重複 OCR/向量化
    try:
        existing = collection.get(where={"file_name": file_name})
        if existing and existing.get("ids"):
            return (f"✅ 檔案 {file_name} 先前已解析完成（{len(existing['ids'])} 個區塊），"
                    f"不需再次解析。請直接呼叫 search_knowledge_base 檢索數據。")
    except Exception:  # noqa: BLE001
        pass

    normalize = (RUNTIME.normalize_lang == "en")

    if RUNTIME.concurrent_ocr:
        # 並發 OCR + 逐頁翻譯（content-hash 快取由 ocr_pipeline 內部處理）
        from ocr_pipeline import process_pdf, TRANSLATION_SYSTEM_PROMPT

        def _ocr_call(p: int, img_bytes: bytes) -> str:
            b64 = base64.b64encode(img_bytes).decode()
            return _chat(MODEL_CONFIG.vision,
                [{"role": "user", "content": [
                    {"type": "text", "text": f"這是 PDF 第 {p + 1} 頁。請完整轉成 Markdown，"
                                             f"保留所有表格、數字與文字，不可遺漏任何數據。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]}], temperature=0.0, max_tokens=4096)

        def _translate_call(p: int, md: str) -> str:
            if not normalize or len(md.strip()) < 20:
                return md
            return _chat(MODEL_CONFIG.coder,
                [{"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                 {"role": "user", "content": md}], temperature=0.1)

        try:
            full_text = process_pdf(pdf_path, _ocr_call, _translate_call,
                                    normalize=normalize, cache_dir=RUNTIME.cache_dir)
        except Exception as e:  # noqa: BLE001
            return f"❌ OCR 解析失敗: {e}"
        total_pages = full_text.count("## 第") or 1
    else:
        cache_file = _cache_path(pdf_path)
        if os.path.exists(cache_file):
            print(f"🔄 [Cache Hit] {cache_file}")
            with open(cache_file, "r", encoding="utf-8") as f:
                full_text = f.read()
            total_pages = full_text.count("## 第") or 1
        else:
            print(f"🔍 [Cache Miss] 全頁 OCR: {pdf_path}")
            try:
                doc = fitz.open(pdf_path)
            except Exception as e:  # noqa: BLE001
                return f"❌ 無法開啟 PDF: {e}"

            total_pages = len(doc)
            pages_text = []
            vision_pages = 0
            for page_num in range(total_pages):
                try:
                    page = doc.load_page(page_num)
                    # 懶人 OCR：先看有沒有可用文字層
                    text_layer = page.get_text() if RUNTIME.lazy_ocr else ""
                    has_images = bool(page.get_images()) if RUNTIME.lazy_ocr else True
                    use_vision = (not RUNTIME.lazy_ocr) or _should_vision_ocr(text_layer, has_images)

                    if not use_vision:
                        # 直接用文字層，不呼叫 vision（省成本）
                        print(f"  📄 第 {page_num + 1}/{total_pages} 頁：文字層直抽")
                        content = text_layer
                    else:
                        vision_pages += 1
                        print(f"  🔍 第 {page_num + 1}/{total_pages} 頁：vision OCR")
                        pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
                        img_b64 = base64.b64encode(pix.tobytes("png")).decode()
                        content = _chat(
                            MODEL_CONFIG.vision,
                            [{"role": "user", "content": [
                                {"type": "text", "text":
                                    f"這是 PDF 第 {page_num + 1} 頁。請完整轉成 Markdown，"
                                    f"保留所有表格、數字與文字，不可遺漏任何數據。"},
                                {"type": "image_url",
                                 "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                            ]}],
                            temperature=0.0,
                            max_tokens=4096,
                        )
                except Exception as e:  # noqa: BLE001
                    content = f"_（第 {page_num + 1} 頁解析失敗：{e}）_"
                pages_text.append(f"## 第 {page_num + 1} 頁\n\n{content}")
            if RUNTIME.lazy_ocr:
                print(f"  💰 [Lazy OCR] {total_pages} 頁中只有 {vision_pages} 頁用了 vision")

            full_text = "\n\n---\n\n".join(pages_text)
            doc.close()
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(full_text)

        # 選用：統一翻成英文再入庫
        if normalize:
            norm_cache = cache_file + ".en.md"
            if os.path.exists(norm_cache):
                print(f"🔄 [Norm Cache] {norm_cache}")
                with open(norm_cache, "r", encoding="utf-8") as f:
                    full_text = f.read()
            else:
                print(f"🌐 [Normalize→EN] 正在統一語言: {file_name} ...")
                full_text = _normalize_to_english(full_text)
                with open(norm_cache, "w", encoding="utf-8") as f:
                    f.write(full_text)

    # 切塊
    print(f"📚 [Vectorizing] {file_name} ...")
    if RUNTIME.struct_chunk:
        # 結構化分塊（表格不跨塊、附豐富 metadata）；chunk_markdown 內部會自行做
        # infer_doc_metadata，這裡只需傳 file_name（供清理舊 chunk 的 where 查詢）
        from chunking import chunk_markdown
        chunk_objs = chunk_markdown(full_text, source_file=file_name,
                                    base_metadata={"file_name": file_name})
        chunk_ids = [c["id"] for c in chunk_objs]
        chunk_docs = [c["text"] for c in chunk_objs]
        chunk_metas = [c["metadata"] for c in chunk_objs]
    else:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n## ", "\n### ", "\n\n", "\n", " "],
        )
        chunks = splitter.split_text(full_text)
        chunk_ids = [f"{file_name}_chunk_{i}" for i in range(len(chunks))]
        chunk_docs = chunks
        chunk_metas = [{"file_name": file_name, "chunk_index": i, "total_chunks": len(chunks)}
                       for i in range(len(chunks))]

    # 清掉同檔舊 chunk
    try:
        existing = collection.get(where={"file_name": file_name})
        if existing and existing.get("ids"):
            collection.delete(ids=existing["ids"])
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 清理舊 chunk 失敗（可忽略）: {e}")

    # ChromaDB 不接受 None metadata 值 → 過濾掉值為 None 的鍵（結構化分塊常有 page/year=None）
    safe_metas = [{k: v for k, v in m.items() if v is not None} for m in chunk_metas]
    collection.add(ids=chunk_ids, documents=chunk_docs, metadatas=safe_metas)
    return (f"✅ 檔案 {file_name} 已完成全頁解析！共 {total_pages} 頁，"
            f"切割成 {len(chunk_docs)} 個知識區塊。請呼叫 search_knowledge_base 檢索數據。")


def search_knowledge_base(args: dict, file_registry: dict) -> str:
    """語意向量檢索，動態 Top-K + 來源/相關性標註。"""
    search_query = args.get("search_query", "")
    file_name = args.get("file_name")

    if file_name:
        n_results = 10
        where_filter = {"file_name": file_name}
    else:
        file_count = len(file_registry) if file_registry else 1
        n_results = min(8 * file_count, 25)
        where_filter = None

    if any(kw in search_query for kw in ("比較", "趨勢", "變化", "差異", "對比")):
        n_results = min(n_results + 10, 30)

    # 混合檢索路徑（FA_HYBRID_RETRIEVAL=1）：查詢翻英+擴展 → dense(Chroma)+BM25+RRF
    if RUNTIME.hybrid_retrieval:
        try:
            return _hybrid_search(search_query, file_name, where_filter, n_results)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 混合檢索失敗，回退純向量檢索: {e}")

    print(f"🔍 [Vector Search] '{search_query}' | {file_name or '全域'} | Top-K={n_results}")
    try:
        results = collection.query(
            query_texts=[search_query],
            n_results=n_results,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
        docs = results.get("documents", [[]])[0]
        if not docs:
            return f"⚠️ 知識庫中找不到與 '{search_query}' 相關的內容。"

        blocks = []
        for doc, meta, dist in zip(docs, results["metadatas"][0], results["distances"][0]):
            relevance = max(0.0, 1.0 - dist)
            blocks.append(
                f"【來源: {meta.get('file_name', '未知')} | 區塊 #{meta.get('chunk_index', '?')} "
                f"| 相關性: {relevance:.2f}】\n{doc}"
            )
        context = "\n\n---\n\n".join(blocks)
        return f"🔍 檢索成功，找到 {len(blocks)} 個片段：\n\n{context}\n\n請只根據以上原文回答，禁止捏造。"
    except Exception as e:  # noqa: BLE001
        return f"❌ 檢索失敗: {e}"


def _hybrid_search(search_query: str, file_name: Optional[str],
                   where_filter: Optional[dict], n_results: int) -> str:
    """混合檢索：查詢翻英+擴展 → Chroma dense + BM25 → RRF（+可選 rerank）。"""
    from query_processing import translate_and_expand_query
    from retrieval import HybridRetriever

    def _llm(messages, temperature=0.1):
        return _chat(MODEL_CONFIG.coder, messages, temperature=temperature)

    tx = translate_and_expand_query(search_query, _llm)
    eng_query = tx.get("translated_query", search_query)
    expanded = tx.get("expanded_terms", [])
    return _retrieve_and_format(eng_query, expanded, where_filter, file_name,
                                n_results, original=search_query)


def _retrieve_hits(eng_query: str, expanded: List[str],
                   where_filter: Optional[dict], n_results: int) -> List[dict]:
    """dense(Chroma) + BM25 + RRF（+可選 rerank），回傳結構化 hits（不格式化、不翻譯）。"""
    from retrieval import HybridRetriever
    effective = (eng_query + " " + " ".join(expanded)).strip()
    raw = collection.query(
        query_texts=[effective], n_results=max(n_results, 20),
        where=where_filter, include=["documents", "metadatas", "distances"],
    )
    ids0 = (raw.get("ids") or [[]])[0]
    docs0 = (raw.get("documents") or [[]])[0]
    metas0 = (raw.get("metadatas") or [[]])[0]
    dists0 = (raw.get("distances") or [[]])[0]
    if not docs0:
        return []
    dense_results = [{"id": i, "text": d, "metadata": m, "score": max(0.0, 1.0 - dist)}
                     for i, d, m, dist in zip(ids0, docs0, metas0, dists0)]
    retriever = HybridRetriever(cross_encoder_name=RUNTIME.rerank_model or None)
    retriever.index([{"id": r["id"], "text": r["text"], "metadata": r["metadata"]}
                     for r in dense_results])
    return retriever.search(eng_query, expanded_terms=expanded,
                            dense_results=dense_results, top_k=n_results).get("results", [])


def _retrieve_en(query_en: str, file_name: Optional[str], n_results: int = 8) -> str:
    """已是英文的查詢：直接檢索，跳過翻譯（給確定性管線用，省掉大量 LLM 呼叫）。"""
    where = {"file_name": file_name} if file_name else None
    return _retrieve_and_format(query_en, [], where, file_name, n_results, original=query_en)


def _retrieve_and_format(eng_query: str, expanded: List[str],
                         where_filter: Optional[dict], file_name: Optional[str],
                         n_results: int, original: str = "") -> str:
    """檢索並組成可讀片段。"""
    hits = _retrieve_hits(eng_query, expanded, where_filter, n_results)
    if not hits:
        return f"⚠️ 知識庫中找不到與 '{original or eng_query}' 相關的內容。"
    blocks = []
    for r in hits:
        m = r.get("metadata", {})
        blocks.append(f"【來源: {m.get('source_file', m.get('file_name', '未知'))} | "
                      f"區塊 #{m.get('chunk_index', '?')}】\n{r['text']}")
    context = "\n\n---\n\n".join(blocks)
    print(f"🔍 [Hybrid] '{original or eng_query}' | {file_name or '全域'} | {len(blocks)} 片段")
    return (f"🔍 檢索成功（{eng_query}），找到 {len(blocks)} 個片段：\n\n"
            f"{context}\n\n請只根據以上原文回答，禁止捏造。")


def get_database_schema(args: dict, file_registry: dict) -> str:
    """回傳 mock MSSQL 資料庫的結構說明，讓模型知道能查什麼、欄位怎麼拼。"""
    print("🗂️ [DB Schema] 提供資料庫結構說明")
    return mock_db.get_schema()


def run_sql_query(args: dict, file_registry: dict) -> str:
    """執行模型生成的 SQL（唯讀）於 mock MSSQL 模擬器，回傳假資料。"""
    sql = args.get("sql", "")
    print(f"📊 [SQL] {sql[:160]}")
    result = mock_db.execute_sql(sql)
    if result.get("ok"):
        print(f"   └─ {result['rowcount']} 筆 | 實際執行: {result.get('translated_sql', '')[:120]}")
    else:
        print(f"   └─ ❌ {result.get('error')}")
    return mock_db.format_result(result)


def generate_financial_table(args: dict, file_registry: dict) -> str:
    """把 JSON 數據渲染成 HTML 表格。"""
    print(f"📋 [Table] {args.get('title')}")
    try:
        df = pd.read_json(io.StringIO(args["data_json"]))
        return GT(df).tab_header(title=args["title"]).as_raw_html()
    except Exception as e:  # noqa: BLE001
        return f"❌ 製表失敗: {e}"


# ---- 程式碼生成：一律由 Coder（Qwen 27B）負責 ----
CODE_SYSTEM_PROMPT = """你是專業 Python 工程師，專責把任務轉成可獨立執行的繪圖/計算程式碼。

硬性規範：
1. 包含所有必要 import。
2. 繪圖一律用 matplotlib，並務必設定中文字體：
   import matplotlib
   matplotlib.use('Agg')
   import matplotlib.pyplot as plt
   plt.rcParams['font.sans-serif'] = ['Noto Sans CJK TC', 'SimHei', 'Arial Unicode MS', 'DejaVu Sans']
   plt.rcParams['axes.unicode_minus'] = False
3. 圖檔一律存成 'output_plot.png'（plt.savefig('output_plot.png', dpi=150, bbox_inches='tight')）。
4. 不要用 plt.show()。
5. 只輸出純 Python 程式碼，不要任何解說或 markdown 圍欄。
6. 【最重要】只能使用「任務描述／數據上下文」中明確提供的真實數據。
   嚴禁自己捏造、模擬、假設任何數字（不准出現「模擬數據」「示意」「sample」之類）。
   若沒有取得任何具體數據，不要硬畫圖，請改成：
       print("⚠️ 缺少實際數據，無法繪圖。請先從檔案萃取營收/EPS 等數值後再傳入。")
   且不要產生 output_plot.png。"""


def _strip_code_fence(code: str) -> str:
    code = code.strip()
    if "```python" in code:
        code = code.split("```python", 1)[1].split("```", 1)[0]
    elif "```" in code:
        parts = code.split("```")
        code = parts[1] if len(parts) > 1 else parts[0]
    return code.strip()


def generate_code_with_coder(task_description: str, data_context: str = "") -> str:
    """指定 Coder 模型生成程式碼（這就是「指定特定 model 畫圖」的落點）。"""
    prompt = (
        f"任務描述：{task_description}\n\n"
        f"數據上下文：\n{data_context or '無特定數據'}\n\n"
        "請生成 Python 程式碼。"
    )
    raw = _chat(
        MODEL_CONFIG.coder,
        [{"role": "system", "content": CODE_SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return _strip_code_fence(raw)


def _run_script(code: str) -> Tuple[str, str]:
    """在子行程沙箱執行 Python，回傳 (stdout, stderr)。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        script_path = f.name
    try:
        env = os.environ.copy()
        env["MPLBACKEND"] = "Agg"
        proc = subprocess.run(
            ["python", script_path],
            capture_output=True, text=True,
            timeout=RUNTIME.code_timeout, env=env,
        )
        return proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return "", f"執行逾時（超過 {RUNTIME.code_timeout} 秒）"
    finally:
        if os.path.exists(script_path):
            os.remove(script_path)


def run_python_code(args: dict, file_registry: dict) -> dict:
    """
    執行 Python（繪圖 / 計算）。
    若 code 為空或像是描述文字，交給 Coder 生成；執行失敗則自動修復重試一次。
    """
    thought = args.get("thought_process", "")
    code = (args.get("code") or "").strip()

    looks_like_description = (len(code) < 40) or not any(
        tok in code for tok in ("import", "=", "def ", "print(", "plt")
    )
    if not code or looks_like_description:
        print("🎨 [Code Gen] 由 Coder 生成程式碼...")
        code = generate_code_with_coder(thought, data_context=args.get("code", ""))

    plot_path = "output_plot.png"
    if os.path.exists(plot_path):
        os.remove(plot_path)  # 清掉上一輪殘留，避免誤判

    print("💻 [Code Interpreter] 執行中...")
    out, err = _run_script(code)
    if os.path.exists(plot_path):
        return {"status": "success", "output": out, "plot": plot_path, "code": code}

    if err:
        print("⚠️ 執行錯誤，嘗試自動修復一次...")
        fixed = generate_code_with_coder(
            f"修復以下程式碼的錯誤後重新輸出完整程式碼：\n{code}\n\n錯誤訊息：\n{err}"
        )
        out2, err2 = _run_script(fixed)
        if os.path.exists(plot_path):
            return {"status": "success", "output": out2, "plot": plot_path, "code": fixed}
        if err2:
            return {"status": "error", "output": f"⚠️ 執行錯誤:\n{err2}", "code": fixed}
        return {"status": "success", "output": f"✅ 執行成功:\n{out2}", "code": fixed}

    return {"status": "success", "output": f"✅ 執行成功:\n{out}", "code": code}


def translate_text(args: dict, file_registry: dict) -> str:
    """翻譯（由 Coder 模型處理）。"""
    text = args.get("text", "")
    target_lang = args.get("target_language", "繁體中文")
    print(f"🌐 [Translate] → {target_lang}")
    return _chat(
        MODEL_CONFIG.coder,
        [{"role": "system",
          "content": f"你是專業翻譯師。請將內容準確翻譯成{target_lang}，"
                     f"保持原意與專業術語準確。只輸出翻譯結果。"},
         {"role": "user", "content": text}],
        temperature=0.3,
    )


# ============================================================
# 工具派遣中心
# ============================================================
TOOL_DISPATCH = {
    "parse_financial_pdf": parse_financial_pdf,
    "search_knowledge_base": search_knowledge_base,
    "get_database_schema": get_database_schema,
    "run_sql_query": run_sql_query,
    "generate_financial_table": generate_financial_table,
    "run_python_code": run_python_code,
    "translate_text": translate_text,
}


def dispatch_tool(func_name: str, args: dict, file_registry: dict,
                  thought_logs: list, intent: str = "") -> Any:
    """
    工具派遣 + 防呆：
    - 只有在「意圖屬於檔案分析」且有上傳檔案、卻想跳過解析直接查 DB 時才攔截。
    - 若 Planner 已判定為 database_query（使用者明確要查資料庫），直接放行，
      不再逼它解析無關的上傳檔案（避免浪費大量時間）。
    """
    file_centric = intent in ("file_analysis", "multi_file", "")
    if func_name == "run_sql_query" and file_registry and file_centric:
        has_parsed = any(
            isinstance(log, dict) and log.get("tool") == "parse_financial_pdf"
            for log in thought_logs
        )
        if not has_parsed:
            return ("❌ 系統攔截：目前有上傳檔案，請先 parse_financial_pdf + "
                    "search_knowledge_base 從檔案找答案。確實查無時才查資料庫。")

    # MCP 工具：轉發給 MCP server（資料庫查詢走這條）
    if func_name in MCP_TOOL_NAMES and MCP_BRIDGE is not None:
        print(f"   └─ 經 MCP 轉發: {func_name}")
        return MCP_BRIDGE.call_tool(func_name, args)

    fn = TOOL_DISPATCH.get(func_name)
    if fn:
        return fn(args, file_registry)
    return f"❌ 未知工具: {func_name}"


# ============================================================
# 快速路徑
# ============================================================
def handle_fast_chat(user_prompt: str, history: List[dict] = None) -> str:
    messages = [{"role": "system", "content": "你是友善的 AI 助手，請用繁體中文回覆。"}]
    messages += _history_messages(history)
    messages.append({"role": "user", "content": user_prompt})
    return _chat(MODEL_CONFIG.chat, messages, temperature=0.7)


def handle_direct_answer(user_prompt: str, plan: PlanningResult,
                         history: List[dict] = None) -> str:
    messages = [{"role": "system",
                 "content": ("你是專業財務 AI 助手。請用繁體中文回答。"
                             "若使用者是追問先前的結果或你做過的動作（例如剛才執行的 SQL），"
                             "請根據對話脈絡直接回答。\n"
                             f"本次規劃分析：{plan.reasoning}")}]
    messages += _history_messages(history)
    messages.append({"role": "user", "content": user_prompt})
    return _chat(MODEL_CONFIG.executor, messages, temperature=0.3)


_LANG_HINTS = {
    "英文": "English", "english": "English",
    "日文": "日本語", "日語": "日本語",
    "韓文": "한국어", "韓語": "한국어",
    "中文": "繁體中文", "繁體": "繁體中文",
}


def handle_fast_translate(user_prompt: str) -> str:
    low = user_prompt.lower()
    target = "繁體中文"
    for kw, lang in _LANG_HINTS.items():
        if kw in user_prompt or kw in low:
            target = lang
            break
    return translate_text({"text": user_prompt, "target_language": target}, {})


def handle_visualize(user_prompt: str, plan: PlanningResult) -> dict:
    """確定性視覺化：直接指定 Coder 生成繪圖碼並執行。"""
    print("📈 [Visualize] 指定 Coder 生成繪圖程式碼...")
    task = f"{user_prompt}\n\n（規劃補充：{plan.reasoning}）"
    return run_python_code({"thought_process": task, "code": ""}, {})


# ============================================================
# Phase 3: Gather（收集證據）→ Synthesize（整合統籌）→ Visualize（畫圖）→ Present
# ============================================================
# 設計理念（回應「流程死版、缺乏一致性」）：
#   舊版：executor 邊查邊講，最後一句話當報告，圖表臨時起意 → 敘述與圖各做各的。
#   新版：先讓「收集 agent」把 OCR/檢索/SQL 的證據全部撈齊；
#         再由「總結 agent」統一整合成連貫報告，並明確指定要畫哪些圖（附真實數據）；
#         最後「Coder agent」依指定繪圖；報告與圖表由同一個總結者統籌，保證一致性。

# 收集階段只開放「取得數據」的工具，避免它自己亂畫圖或提早下結論
_GATHER_TOOL_NAMES = {
    "parse_financial_pdf", "search_knowledge_base",
    "get_database_schema", "run_sql_query",
}


def _gather_tools() -> List[dict]:
    names = _GATHER_TOOL_NAMES | MCP_TOOL_NAMES
    return [t for t in AGENT_TOOLS if t.get("function", {}).get("name") in names]


def build_gather_system_prompt(plan: PlanningResult, file_list: str) -> str:
    # 資料庫查詢意圖：直接查 DB，忽略無關的上傳檔案
    if plan.intent == IntentType.DATABASE_QUERY:
        return f"""【前置規劃（由 Planner 提供）】
- 意圖: database_query（使用者明確要查歷史資料庫）
- 推理: {plan.reasoning}

【你的角色：資料收集員】
使用者要查的是「歷史財務資料庫」，與目前上傳的檔案無關。
請「直接」查資料庫，**不要**去 parse_financial_pdf 或 search_knowledge_base 解析上傳檔案（浪費時間）。

【步驟】
1. 先 get_database_schema 了解資料表與欄位。
2. 再 run_sql_query 產生並執行 SELECT 取得數據。
3. SQL 失敗時，依錯誤訊息與 schema 修正後重試。
4. 取得數據後回覆「收集完成」即可。

【已上傳檔案（本次與其無關，請忽略）】
{file_list}

【絕對禁止】捏造資料庫中不存在的數據。"""

    # 知識庫已正規化成英文時，必須用英文術語查詢；否則用各檔原文語言
    if RUNTIME.normalize_lang == "en":
        search_lang_rule = (
            "【知識庫語言：英文】所有檔案內容已統一翻成英文入庫，因此 search_query "
            "「一律用英文財務術語」，不要用中文或日文關鍵字（否則會比對不到）。\n"
            "   而且每個 query 結尾都「加上 current fiscal year」，以鎖定『本期』數字、"
            "避免撈到去年比較數。範例：\n"
            "   - 營收：revenue current fiscal year\n"
            "   - 營業利益：operating income current fiscal year\n"
            "   - 淨利：profit attributable to owners of the parent current fiscal year\n"
            "   - 資本支出：capital expenditure CAPEX current fiscal year\n"
            "   - EPS：basic earnings per share current fiscal year")
    else:
        search_lang_rule = (
            "請用「該檔案原文語言」的正式用語（中文檔『營業收入』、英文檔『revenue net sales』、"
            "日文檔『売上収益』），並在結尾加上『當期/本期』以鎖定本期數字。")

    return f"""【前置規劃（由 Planner 提供）】
- 意圖: {plan.intent.value}
- 步驟: {' → '.join(plan.steps)}
- 推理: {plan.reasoning}
- 跨檔案: {'是' if plan.is_multi_file else '否'}

【你的角色：資料收集員】
你的「唯一任務」是把回答使用者所需的**原始證據/數據**用工具撈齊，
不需要做最終分析、不需要畫圖、不需要寫結論——那是下一棒（總結者）的工作。

【收集規則】
1. 有上傳檔案：先對每個檔案 parse_financial_pdf（若未解析）→ 再 search_knowledge_base 撈具體數值。
2. 跨檔案：對「每個檔案」分別 search_knowledge_base，指定 file_name。
3. 需要歷史資料庫：先 get_database_schema 看結構 → 再 run_sql_query 產生 SELECT 取數。
4. 【關鍵：精準檢索】不要把多個指標、多種語言塞進同一個 search_query（會讓檢索失焦）。
   請「一個指標一次查詢」。{search_lang_rule}
   - 也務必查「Consolidated Results / Financial Summary」這類彙總表，數字通常在那裡。
5. 每個檔案的每個關鍵指標都要確認有撈到「實際數值」；若某指標沒撈到，換用語再查一次。
6. 證據撈足後，直接回覆「收集完成」即可，不要長篇大論。

【已上傳檔案】
{file_list}

【絕對禁止】
- 捏造任何檔案/資料庫中不存在的數據。
- 有上傳檔案時跳過 parse_financial_pdf。"""


SYNTHESIS_SYSTEM_PROMPT = """你是首席財務分析師（總結整合 agent）。
你會收到使用者的原始需求，以及前一階段透過 OCR／向量檢索／SQL 收集到的所有「證據」。

【你的任務】
1. 整合所有證據，產出一份連貫、專業、結構清楚的繁體中文分析報告。
2. 依使用者提供的【視覺化規則】決定 charts：有要求才指定圖表（附實際數據），沒要求就給空陣列。
3. 需要結構化表格時，也一併指定。

【鐵則】
- 只能使用證據中實際出現的數據，嚴禁捏造、臆測或自行假設數字。
- 若證據不足以回答某部分，在報告中誠實說明「資料不足」，不要硬湊。
- 圖表/表格的 data 必須是從證據抽出的真實數值。
- 【年度判讀，最重要】財報幾乎都同時列「本期」與「去年同期比較數」。你必須從證據文字判讀
  每段內容的「本期所屬會計年度」，並以各份報告的『本期年度』作為分析年度。判讀線索例如：
  「For the fiscal year 2023」「year ended 31 August 2024」「FYE Aug 2025」「当期/通期…2025年8月期」
  → 該段本期年度分別為 2023、2024、2025。
  規則：
  (1) 先盤點證據中總共出現哪些『本期年度』，分析就涵蓋「全部這些本期年度」，一個都不能漏
      （特別是最新年度，它常只出現在最新那份報告裡）。
  (2) 「去年比較數」只用來對照或補洞，絕不可把比較年度當成獨立分析年度
      （例：2023 報告裡附的 2022 數字，不要把 2022 列成一個分析年度）。
  (3) 同一年度若多份報告都有，數字應一致；以該年度為『本期』的那份報告為準。
  (4) 【所有指標年度一致】營收、營業利益、淨利、EPS… 等「全部指標都必須使用同一組分析年度」，
      不可營收用 2023-2025、淨利卻用 2022-2024。先決定這組年度，再讓每個指標、每張圖都對齊它。
- 【單位一致】不同來源可能用不同單位（億日圓 / 百萬日圓 / 兆日圓；億元 等）。
  比較或畫圖前，務必先「換算成同一單位」，並在報告中標明所用單位；
  例：2,766,557 百萬日圓 = 約 2.77 兆日圓 = 27,665 億日圓。換算錯誤等同捏造，請特別小心。

【輸出格式】只輸出有效 JSON：
{
  "report": "完整 Markdown 分析報告（繁體中文）。可在文中提到下方圖表。",
  "charts": [
    {
      "title": "圖表標題",
      "chart_type": "line | bar | pie",
      "description": "這張圖要呈現什麼",
      "data": [ {"label/年度...": 值, ...}, ... ]
    }
  ],
  "tables": [
    { "title": "表格標題", "data": [ {"欄位": 值, ...}, ... ] }
  ]
}
- 不需要圖表時，charts 給空陣列 []；不需要表格時，tables 給空陣列 []。"""


# 確定性解析管線要撈的標準財務指標（英文查詢；KB 已英文）
_STD_METRIC_QUERIES = [
    ("營收 Revenue", "revenue net sales current fiscal year"),
    ("營業利益 Operating income", "operating income operating profit current fiscal year"),
    ("淨利 Net income", "profit attributable to owners of the parent net income current fiscal year"),
    ("EPS", "basic earnings per share current fiscal year"),
    ("毛利 Gross profit", "gross profit current fiscal year"),
    ("資本支出 CAPEX", "capital expenditure CAPEX current fiscal year"),
    ("資產/權益 Assets & Equity", "total assets total equity current fiscal year"),
]


def _gather_files_deterministic(user_prompt: str, file_registry: dict,
                                resp: AgentResponse) -> List[str]:
    """
    確定性解析管線（取代 LLM 逐步決策）：
      1) 解析所有上傳檔（OCR 內部已並行）
      2) 對每檔 × 標準財務指標做檢索（檔案層級多工）
    回傳證據清單，交給抽數 + 總結 agent。
    """
    from concurrent.futures import ThreadPoolExecutor
    files = list(file_registry.keys())

    # 1) 解析所有檔案（序列；單一 Ollama 下多檔並行助益有限，OCR 頁層級已並行）
    print(f"\n  📂 [Parse] 解析 {len(files)} 個檔案…")
    for fn in files:
        try:
            res = parse_financial_pdf({"file_name": fn}, file_registry)
        except Exception as e:  # noqa: BLE001
            res = f"⚠️ 解析失敗: {e}"
        resp.thought_logs.append({"tool": "parse_financial_pdf", "step": 1,
                                  "thought": f"解析 {fn}"})
        _trace(resp, "tool_call", step=1, tool="parse_financial_pdf",
               args={"file_name": fn}, result_preview=_preview(str(res)))

    # 2) 每檔的標準指標檢索（檔案層級多工）
    print(f"  🔎 [Retrieve] 每檔 {len(_STD_METRIC_QUERIES)} 項指標，{RUNTIME.gather_workers} 路並行…")

    def _search_one_file(fn: str):
        """對單一檔案跑所有指標查詢；round-robin 取片段，確保每個指標(尤其EPS)的最佳片段都進得來。"""
        metric_hits = []
        for label, q in _STD_METRIC_QUERIES:
            try:
                hits = _retrieve_hits(q, [], {"file_name": fn}, n_results=3)
            except Exception:  # noqa: BLE001
                hits = []
            metric_hits.append(hits)
            _trace(resp, "tool_call", step=2, tool="search_knowledge_base",
                   args={"file_name": fn, "search_query": q},
                   result_preview=_preview(str([h.get("id") for h in hits])))
        # round-robin：先收各指標的 rank0，再 rank1…，並依 chunk id 去重
        seen = {}
        for rank in range(max((len(h) for h in metric_hits), default=0)):
            for hits in metric_hits:
                if rank < len(hits):
                    h = hits[rank]
                    cid = h.get("id")
                    if cid and cid not in seen:
                        seen[cid] = h.get("text", "")
        return fn, seen

    workers = max(1, min(len(files), RUNTIME.gather_workers))
    seen_by_file = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fn, seen in ex.map(_search_one_file, files):
            seen_by_file[fn] = seen

    # 每檔均衡預算；只「整段取捨」（去重後的完整片段），不從中間硬切，避免切斷數字
    per_file_budget = max(2000, int(RUNTIME.max_evidence_chars * 0.9 / max(1, len(files))))
    evidence: List[str] = []
    for fn in files:  # 穩定輸出順序
        used = 0
        for cid, text in seen_by_file.get(fn, {}).items():  # 依相關性順序
            block = f"【{fn}｜{cid}】\n{text}"
            if used and used + len(block) > per_file_budget:
                break  # 預算用完就停；但第一段（最相關，含彙總表）一定保留完整
            used += len(block)
            evidence.append(block)

    _trace(resp, "gather_done", evidence_count=len(evidence), mode="deterministic")
    return evidence


def gather(plan: PlanningResult, user_prompt: str,
           file_registry: dict, resp: AgentResponse) -> List[str]:
    """收集階段分派：檔案分析任務 + 開啟旗標時走確定性多工管線，否則走 LLM 工具迴圈。
    （工具流程與 LangGraph 兩入口共用此分派。）"""
    if (RUNTIME.deterministic_gather and file_registry
            and plan.intent in (IntentType.FILE_ANALYSIS, IntentType.MULTI_FILE_COMPARE)):
        return _gather_files_deterministic(user_prompt, file_registry, resp)
    return _gather_evidence(plan, user_prompt, file_registry, resp)


def _gather_evidence(plan: PlanningResult, user_prompt: str,
                     file_registry: dict, resp: AgentResponse) -> List[str]:
    """收集階段：跑工具迴圈撈證據，回傳證據字串清單。"""
    file_list = "\n".join(f"- {n}" for n in file_registry) if file_registry else "無"
    messages = [
        {"role": "system", "content": build_gather_system_prompt(plan, file_list)},
        {"role": "user", "content": user_prompt},
    ]
    tools = _gather_tools()
    evidence: List[str] = []
    tool_used = False
    nudged = False

    for step in range(1, RUNTIME.max_steps + 1):
        print(f"\n  [Gather {step}/{RUNTIME.max_steps}]")
        try:
            params = {"model": MODEL_CONFIG.executor, "messages": messages,
                      "temperature": 0.1, "timeout": RUNTIME.request_timeout}
            if tools:
                params["tools"] = tools
                params["tool_choice"] = "auto"
            api_resp = client.chat.completions.create(**params)
        except Exception as e:  # noqa: BLE001
            _trace(resp, "gather_error", error=str(e))
            # 收集階段一開始就失敗（常見：執行器模型名稱打錯/未安裝）。
            # 把真正錯誤帶進證據，避免總結誤報成「資料不足」而掩蓋根因。
            if not tool_used:
                evidence.append(
                    f"⚠️ 系統錯誤：收集階段呼叫執行器模型（FA_EXECUTOR='{MODEL_CONFIG.executor}'）失敗：{e}\n"
                    f"請在報告中直接說明此錯誤，並提示使用者確認該模型名稱是否正確、是否已在 Ollama 安裝。")
            break

        msg = api_resp.choices[0].message
        if not msg.tool_calls:
            # 模型有時會直接以文字回答而不呼叫工具；若還沒撈到任何證據就先「催一次」
            if tools and not tool_used and not nudged:
                nudged = True
                print("  ↪︎ 模型未呼叫工具，催促改用工具…")
                messages.append({"role": "user", "content":
                    "你必須使用上面提供的工具來實際取得資料（例如 get_database_schema 後 "
                    "run_sql_query，或 search_knowledge_base），不可僅以文字回答或自行假設。請現在呼叫工具。"})
                continue
            print("  └─ 收集完成")
            break

        tool_used = True
        messages.append(msg)
        for tc in msg.tool_calls:
            func_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            print(f"  ├─ 收集工具: {func_name}")
            if "thought_process" in args:
                resp.thought_logs.append(
                    {"tool": func_name, "step": step, "thought": args["thought_process"]}
                )

            t0 = time.perf_counter()
            try:
                result = dispatch_tool(func_name, args, file_registry,
                                       resp.thought_logs, intent=plan.intent.value)
            except Exception as e:  # noqa: BLE001
                result = f"⚠️ 工具執行錯誤: {e}"
            duration_ms = round((time.perf_counter() - t0) * 1000, 1)

            # 記下實際執行的 SQL（供對話記憶／使用者追問「show 出 SQL」）
            if func_name == "run_sql_query" and args.get("sql"):
                resp.executed_sql.append(args["sql"])

            result_str = str(result)
            # 解析類訊息（如「已完成解析」）不算證據，檢索/SQL 結果才收進證據
            if func_name in ("search_knowledge_base", "run_sql_query") or "查詢成功" in result_str:
                evidence.append(f"【{func_name}｜{args.get('search_query') or args.get('sql') or ''}】\n"
                                f"{_preview(result_str, 2500)}")

            _trace(resp, "tool_call", step=step, tool=func_name,
                   thought=args.get("thought_process"),
                   args={k: v for k, v in args.items() if k != "thought_process"},
                   result_preview=_preview(result_str), duration_ms=duration_ms)

            messages.append({
                "role": "tool", "tool_call_id": tc.id,
                "name": func_name,
                # 回灌給執行器的內容截短：它只需知道「這次查到了」就能決定下一步；
                # 完整結果已另存進 evidence 供總結使用。避免 messages 累積爆量拖慢每一步。
                "content": _preview(result_str, 1200),
            })

    _trace(resp, "gather_done", evidence_count=len(evidence))
    return evidence


# 視覺化關鍵字：使用者有提到才畫圖
_VIZ_KEYWORDS = ("圖", "趨勢", "視覺化", "畫", "繪", "長條", "折線", "圓餅", "柱狀",
                 "chart", "plot", "graph", "bar", "line", "pie", "visuali", "trend")


def _wants_visualization(user_prompt: str, plan: PlanningResult) -> bool:
    """判斷使用者這次是否真的想要圖表（沒要求就不畫）。"""
    if plan.intent == IntentType.VISUALIZATION:
        return True
    low = (user_prompt or "").lower()
    return any(k in user_prompt or k in low for k in _VIZ_KEYWORDS)


# 量化任務關鍵字：要統計各年數字才需要「程式化抽數」這一步
_METRIC_KEYWORDS = ("統計", "各年", "逐年", "歷年", "趨勢", "比較", "年度", "指標", "成長",
                    "營收", "毛利", "淨利", "eps", "每股", "資本支出", "revenue", "margin")


def _needs_metric_extraction(user_prompt: str, plan: PlanningResult, want_viz: bool) -> bool:
    """是否需要先做『各年度×指標』結構化抽取（只在量化的檔案分析任務才需要）。"""
    if plan.intent not in (IntentType.FILE_ANALYSIS, IntentType.MULTI_FILE_COMPARE):
        return False
    low = (user_prompt or "").lower()
    return want_viz or any(k in user_prompt or k in low for k in _METRIC_KEYWORDS)


_METRIC_EXTRACT_SYSTEM = """你是嚴謹的財務數據抽取器。從證據中抽出使用者需要的「各年度 × 指標」數值。

【鐵則】
- 【年度歸屬，最重要】每個數字屬於哪一年，依「欄位/段落上的年度標籤」判讀
  （如 year ended August 31, 2023 / FYE Aug 2024 / 当期 2025年8月期）。
  * 某一年的數字，可能出現在某份報告的「本期欄」，也可能出現在另一份報告的「去年比較欄」——
    只要該欄『標籤是那一年』就可採用，並可跨報告交叉比對（同一年數字應一致）。
    例：2023 的營收若 2023 報告只有敘述、不清楚，可改用 2024 報告「Year ended Aug 2023」欄的數值。
  * 但「不要」捏造或多列出證據中根本沒有標籤的年度（例如不要因為某欄是 2022 就硬生出 2022 這一年，
    除非使用者要的年度範圍包含它）。
- 涵蓋證據中出現的所有目標年度，特別是「最新年度」一定要有，一年都不能漏。
- 【數字＋單位＋來源，絕不自己換算/過濾】每個值用 {"v": 數字, "u": 單位, "src": 來源標籤} 表示：
  * v = 原文數字「原樣照抄」（含千分位即照抄，如 "27,665" 或 "3,103,836" 或 "3兆4,005億"）。
  * u = 該數字旁邊/表頭的單位原文（如 "億円"、"百万円"、"兆円"、"million yen"、"円"）。
  * src = 該數字所在「欄位/段落的標籤原文」，原樣照抄（如 "Year ended August 31, 2025"、
    "当期実績"、"通期予想"、"見通し"、"Outlook"…）。這很重要：程式會用 src 判斷是實績還是預測。
  * 千萬「不要」自己換算單位、也「不要」自己過濾預測——你只負責「照抄」v/u/src，其餘交給程式。
  * 查無就填 null。
- 只輸出 JSON，不要任何說明：
  {
    "years": ["2023","2024","2025"],
    "metrics": {
      "營收": {"2023": {"v":"27,665","u":"億円","src":"年度実績"}, "2024": {"v":"3,103,836","u":"百万円","src":"Year ended Aug 2024"}, "2025": {"v":"3兆4,005億","u":"円","src":"当期実績"}},
      "營業利益": {...}, "淨利": {...}, "EPS": {"2023": {"v":"966.09","u":"円","src":"…"}, ...}
    }
  }"""


# 預測欄關鍵字（程式化過濾：src 命中即視為預測、丟棄，不靠模型判斷）
_FORECAST_RE = re.compile(
    r"forecast|outlook|guidance|projection|estimat|見通し|予想|予測|翌期|通期予想|来期|計画|目標",
    re.IGNORECASE)
# 每股/比率類指標：不做百萬換算，保留原值
_PER_SHARE_OR_RATIO = ("eps", "每股", "盈餘", "盈利", "per share", "margin", "率", "比率", "%", "ratio")


def _normalize_extracted(data: dict) -> dict:
    """把抽數結果程式化處理：① src 命中預測 → 丟棄；② 金額用 units 換算成百萬；③ EPS/比率保留原值。"""
    years = data.get("years") or []
    out = {"years": years, "unit": "百萬日圓（金額；EPS 為日圓，皆由程式換算）", "metrics": {}}
    for metric, yearmap in (data.get("metrics") or {}).items():
        is_amount = not any(k in metric.lower() for k in _PER_SHARE_OR_RATIO)
        clean = {}
        for yr, val in (yearmap or {}).items():
            if not isinstance(val, dict):
                # 容錯：舊格式 bare number
                clean[yr] = units.parse_amount(val) if val is not None else None
                continue
            v, u, src = val.get("v"), val.get("u", ""), val.get("src", "")
            if v is None or (src and _FORECAST_RE.search(str(src))):
                clean[yr] = None  # 查無 或 預測欄 → 丟棄
                continue
            num = units.normalize_million(v, u) if is_amount else units.parse_amount(v)
            clean[yr] = round(num, 2) if isinstance(num, float) else num
        out["metrics"][metric] = clean
    return out


def _extract_metrics(user_prompt: str, evidence_text: str, resp: AgentResponse) -> Optional[dict]:
    """量化任務專用：把證據抽成乾淨的『年度×指標』JSON（與敘述分離，降低弱模型漏年度/抄錯欄）。"""
    print("  🔢 [Extract] 抽取各年度×指標結構化數據…")
    t0 = time.perf_counter()
    try:
        raw = _chat(
            MODEL_CONFIG.synthesizer,
            [{"role": "system", "content": _METRIC_EXTRACT_SYSTEM},
             {"role": "user", "content": f"【使用者需求】\n{user_prompt}\n\n"
                                         f"【證據】\n{evidence_text}\n\n只輸出 JSON。"}],
            temperature=0.0,
        )
        data = _extract_json(raw)
        normalized = _normalize_extracted(data)  # 程式化：剔除預測欄 + 單位換算成百萬
        _trace(resp, "metric_extraction", years=normalized.get("years"),
               metric_count=len(normalized.get("metrics", {})),
               duration_ms=round((time.perf_counter() - t0) * 1000, 1))
        return normalized
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 抽數失敗，改由總結者直接讀證據: {e}")
        _trace(resp, "metric_extraction_failed", error=str(e))
        return None


def _synthesize(user_prompt: str, plan: PlanningResult,
                evidence: List[str], resp: AgentResponse,
                want_viz: bool = True) -> dict:
    """整合階段：總結 agent 把證據整合成報告 + 圖表/表格指定（JSON）。"""
    if evidence:
        evidence_text = "\n\n========\n\n".join(evidence)
    else:
        evidence_text = "（收集階段沒有取得任何證據，請在報告中說明資料不足。）"

    # 限制證據總長度，避免輸入過大讓總結模型超慢/逾時
    if len(evidence_text) > RUNTIME.max_evidence_chars:
        evidence_text = evidence_text[:RUNTIME.max_evidence_chars] + "\n…（證據過長已截斷）"

    # 量化任務：先做程式化抽數，得到權威的「年度×指標」表（非量化問題則跳過）
    extracted = None
    if evidence and _needs_metric_extraction(user_prompt, plan, want_viz):
        _progress("🔢 抽取各年度關鍵數據中…")
        extracted = _extract_metrics(user_prompt, evidence_text, resp)

    viz_rule = ("使用者本次「有要求」視覺化，charts 可填入需要的圖表規格。"
                if want_viz else
                "使用者本次「沒有要求」視覺化，charts 一律給空陣列 []，只輸出文字報告（必要時可用表格）。")

    extracted_block = ""
    if extracted:
        extracted_block = (
            "\n【已抽取的權威數據（最重要）】下面是從證據抽出、且「已由程式換算單位、已剔除預測欄」的"
            "『年度×指標』表（金額單位＝百萬日圓，EPS＝日圓）。報告逐年數字、所有圖表與表格 data「一律以此為準」，"
            "且「不要再自行換算單位、不要再判斷實績/預測」（程式都處理好了）；涵蓋年度就是這裡的 years，"
            "值為 null 代表查無或屬預測、該年該指標就寫『資料不足』，不可自己補：\n"
            f"{json.dumps(extracted, ensure_ascii=False)}\n")

    user_block = (f"【使用者需求】\n{user_prompt}\n\n"
                  f"【視覺化規則】{viz_rule}\n"
                  f"{extracted_block}\n"
                  f"【收集到的證據（原文，供補充敘述與佐證）】\n{evidence_text}\n\n"
                  "請整合以上資訊，輸出 JSON（report / charts / tables）。")

    t0 = time.perf_counter()
    used_model = MODEL_CONFIG.synthesizer
    raw = ""
    try:
        raw = _chat(
            used_model,
            [{"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
             {"role": "user", "content": user_block}],
            temperature=0.2,
        )
        data = _extract_json(raw)
        report = data.get("report", "").strip() or raw.strip()
        charts = data.get("charts", []) or []
        tables = data.get("tables", []) or []
    except Exception as e:  # noqa: BLE001
        # 逾時或其他錯誤：用 executor（通常已在 VRAM、較快）重試一次，產出純文字報告
        print(f"⚠️ 總結失敗（{e}），改用 executor 降級重試...")
        _trace(resp, "synthesis_fallback", error=str(e), from_model=used_model)
        used_model = MODEL_CONFIG.executor
        try:
            report = _chat(
                used_model,
                [{"role": "system", "content":
                  "你是財務分析師。根據以下證據，用繁體中文寫一份連貫的分析報告，只用證據中的真實數據，"
                  "不要捏造，不需輸出 JSON。"},
                 {"role": "user", "content": user_block}],
                temperature=0.2,
            )
        except Exception as e2:  # noqa: BLE001
            report = (f"⚠️ 整合階段逾時，無法完成報告（{e2}）。\n\n"
                      f"以下為收集到的原始證據摘要：\n\n{_preview(evidence_text, 3000)}")
        charts, tables = [], []

    _trace(resp, "synthesis", model=used_model,
           duration_ms=round((time.perf_counter() - t0) * 1000, 1),
           report_text=_preview(report, 2000),
           chart_count=len(charts), table_count=len(tables))
    return {"report": report, "charts": charts, "tables": tables}


def _render_charts(charts: List[dict], resp: AgentResponse) -> None:
    """
    視覺化階段：把總結者指定的「所有」圖表，一次交給 Coder 畫在「同一張圖」上
    （用 subplots 排版）。這樣圖會一起出現、可控 layout，也避免多張圖互相覆蓋同一檔案。
    """
    if not charts:
        return

    titles = "、".join(c.get("title", f"圖{i}") for i, c in enumerate(charts, 1))

    # 互動式 Plotly 路徑（FA_PLOTLY=1）：產生 plotly 程式碼 → 沙箱執行 → 取 fig.to_json()
    if RUNTIME.use_plotly:
        from viz_plotly import generate_plotly
        def _coder_call(messages, temperature=0.2):
            return _chat(MODEL_CONFIG.coder, messages, temperature=temperature)
        print(f"  📈 [Coder] 產生互動式 Plotly 圖 ({len(charts)}): {titles}")
        result = generate_plotly(charts, coder_call=_coder_call,
                                 run_script_fn=_run_script, max_repair=1)
        resp.plotly_jsons.extend(result.get("plotly_jsons", []))
        _trace(resp, "charts", chart_count=len(charts), titles=titles, engine="plotly",
               code=_preview(result.get("code", ""), 2500),
               output=_preview(result.get("stdout", "")))
        return

    # 預設：matplotlib 靜態圖（單一 figure、子圖排版）
    n = len(charts)
    lines = [
        f"請用 matplotlib 在「單一張圖（一個 figure）」中，以子圖 subplots 排版呈現以下 {n} 個圖表。",
        "排版要求：自動選擇適當網格（例如 2 欄；單張就 1 個圖），整體用 tight_layout 避免重疊，",
        "每個子圖都要有自己的標題、座標軸標籤與數值標註；最後只存成一個檔案 output_plot.png。",
        "鐵則：只能使用我提供的實際數據，不可更改、捏造或新增資料點。\n",
    ]
    for i, c in enumerate(charts, 1):
        lines.append(
            f"[子圖{i}] 標題：{c.get('title','')}｜類型：{c.get('chart_type','適當類型')}\n"
            f"說明：{c.get('description','')}\n"
            f"數據(JSON)：{json.dumps(c.get('data', []), ensure_ascii=False)}\n"
        )
    task = "\n".join(lines)

    print(f"  🎨 [Coder] 一次繪製 {n} 張圖於同一版面: {titles}")
    result = run_python_code({"thought_process": task, "code": ""}, {})
    _trace(resp, "charts", chart_count=n, titles=titles,
           code=_preview(result.get("code", ""), 2500),
           output=_preview(result.get("output", "")))
    if result.get("plot"):
        resp.images.append(result["plot"])


def _present_synthesis(synthesis: dict, resp: AgentResponse,
                       file_registry: dict, want_viz: bool) -> None:
    """呈現階段：把總結者指定的表格與圖表產出（供工具流程與 LangGraph 共用）。"""
    resp.report_text = synthesis.get("report", resp.report_text)

    # 表格（由總結者指定）
    for tbl in synthesis.get("tables", []):
        try:
            html = generate_financial_table(
                {"data_json": json.dumps(tbl.get("data", []), ensure_ascii=False),
                 "title": tbl.get("title", "表格")}, file_registry)
            if not str(html).startswith("❌"):
                resp.tables.append(html)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ 表格生成失敗: {e}")

    # 視覺化：只有使用者要求時才畫，且一次畫好（同一版面）
    if want_viz:
        try:
            _render_charts(synthesis.get("charts", []), resp)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ 圖表生成失敗: {e}")

    _trace(resp, "present", tables=len(resp.tables), images=len(resp.images))


def _execute_tool_loop(plan: PlanningResult, user_prompt: str,
                       file_registry: dict, resp: AgentResponse) -> None:
    """統籌三階段：收集 → 整合 → 視覺化/表格 → 一起呈現。"""
    _progress("📂 解析檔案、收集證據中…")
    evidence = gather(plan, user_prompt, file_registry, resp)
    print("\n  🧩 [Synthesize] 總結 agent 整合證據...")
    _progress("🧩 整合分析、撰寫報告中…（這步較久，請稍候）")
    want_viz = _wants_visualization(user_prompt, plan)
    synthesis = _synthesize(user_prompt, plan, evidence, resp, want_viz)
    if want_viz:
        _progress("📈 繪製圖表中…")
    _present_synthesis(synthesis, resp, file_registry, want_viz)


# ============================================================
# 共用建構器（run_financial_agent 與 graph.run 共用，避免兩處平行維護）
# ============================================================
def _build_planning_result(plan: PlanningResult) -> dict:
    return {
        "intent": plan.intent.value, "confidence": plan.confidence,
        "steps": plan.steps, "first_tool": plan.first_tool, "reasoning": plan.reasoning,
    }


def _build_result(resp: AgentResponse) -> dict:
    """把 AgentResponse 組成對前端的回傳 dict（單一來源，兩個入口共用）。"""
    return {
        "report_text": resp.report_text,
        "figures": [],          # 舊欄位保留相容
        "plotly_jsons": resp.plotly_jsons,
        "tables": resp.tables,
        "images": resp.images,
        "thought_logs": resp.thought_logs,
        "planning_result": resp.planning_result,
        "route": resp.route,
        "executed_sql": resp.executed_sql,
        "trace": resp.trace,
    }


# 純問候語白名單（去掉標點後「完全等於」才算，分析請求絕不會誤判）
_GREETINGS = {
    "你好", "妳好", "您好", "哈囉", "哈嘍", "嗨", "hi", "hello", "hey", "yo",
    "早安", "午安", "晚安", "在嗎", "在不在", "你在嗎", "謝謝", "謝啦", "感謝",
    "thanks", "thankyou", "thx", "掰掰", "bye", "ok", "好", "好的", "嗨嗨",
}


def _is_greeting(user_prompt: str) -> bool:
    """是否為純問候/閒聊（去標點後完全等於白名單詞）。保守判斷，避免把分析請求誤當招呼。"""
    cleaned = re.sub(r"[\s,，。!！?？~、.…\-—）（()]+", "", (user_prompt or "").lower())
    return cleaned in _GREETINGS


# ============================================================
# 主執行入口
# ============================================================
def run_financial_agent(user_prompt: str, file_registry: dict = None,
                        history: List[dict] = None) -> dict:
    """
    多模型協作主流程，回傳 dict（相容 Streamlit 前端）：
      { report_text, figures, tables, images, thought_logs, planning_result, route, executed_sql }

    history: 先前對話（list of {"role","content"}，純文字），用於理解追問與保持脈絡。
    """
    # LangGraph 編排（FA_USE_GRAPH=1）：交給 graph.py 跑同一套節點，回傳相同 dict
    if RUNTIME.use_graph:
        try:
            import graph
            return graph.run(user_prompt, file_registry or {}, history or [])
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ LangGraph 執行失敗，回退原生流程: {e}")

    file_registry = file_registry or {}
    history = history or []

    # 招呼語快速通道：純問候/閒聊（且無上傳檔案）直接回覆，跳過 planning 那次大模型呼叫，省一半時間
    if not file_registry and _is_greeting(user_prompt):
        resp = AgentResponse()
        _progress("💬 回覆中…")
        resp.route = "fast_chat"
        resp.planning_result = {"intent": "chat", "confidence": 1.0, "steps": [],
                                "first_tool": None, "reasoning": "招呼語快速通道（略過規劃）"}
        _trace(resp, "request", user_prompt=user_prompt, quick_chat=True)
        resp.report_text = handle_fast_chat(user_prompt, history)
        _trace(resp, "fast_path", route="fast_chat", model=MODEL_CONFIG.chat)
        return _build_result(resp)

    resp = AgentResponse()
    t_start = time.perf_counter()
    _trace(resp, "request", user_prompt=user_prompt,
           files=list(file_registry.keys()), history_turns=len(history),
           models={"planner": MODEL_CONFIG.planner, "executor": MODEL_CONFIG.executor,
                   "synthesizer": MODEL_CONFIG.synthesizer, "coder": MODEL_CONFIG.coder,
                   "vision": MODEL_CONFIG.vision, "chat": MODEL_CONFIG.chat})

    # Phase 1: 規劃
    print("\n" + "=" * 60 + "\n🧠 [Phase 1] Qwen 意圖分析\n" + "=" * 60)
    _progress("🧠 分析你的意圖中…")
    t0 = time.perf_counter()
    plan = planning_phase(user_prompt, file_registry, history)
    plan_ms = round((time.perf_counter() - t0) * 1000, 1)
    resp.planning_result = _build_planning_result(plan)
    print(f"  ├─ 意圖: {plan.intent.value} (信心 {plan.confidence})")
    print(f"  └─ 首要工具: {plan.first_tool or '無'}")
    _trace(resp, "planning", model=MODEL_CONFIG.planner,
           duration_ms=plan_ms, **resp.planning_result)

    # Phase 2: 路由
    route = route_by_intent(plan, file_registry)
    resp.route = route
    print(f"🔀 [Phase 2] 路由 → {route}")
    _trace(resp, "routing", route=route, intent=plan.intent.value,
           confidence=plan.confidence)

    if route == "fast_chat":
        resp.report_text = handle_fast_chat(user_prompt, history)
        _trace(resp, "fast_path", route=route, model=MODEL_CONFIG.chat,
               report_text=_preview(resp.report_text, 2000))
    elif route == "direct_answer":
        resp.report_text = handle_direct_answer(user_prompt, plan, history)
        _trace(resp, "fast_path", route=route, model=MODEL_CONFIG.executor,
               report_text=_preview(resp.report_text, 2000))
    elif route == "fast_translate":
        resp.report_text = handle_fast_translate(user_prompt)
        _trace(resp, "fast_path", route=route, model=MODEL_CONFIG.coder,
               report_text=_preview(resp.report_text, 2000))
    elif route == "visualize":
        result = handle_visualize(user_prompt, plan)
        if result.get("plot"):
            resp.images.append(result["plot"])
        resp.report_text = result.get("output", "圖表已生成。")
        _trace(resp, "visualize", model=MODEL_CONFIG.coder,
               code=_preview(result.get("code", ""), 2000),
               output=_preview(result.get("output", "")))
    else:  # execute_tools
        print("\n" + "=" * 60 +
              "\n⚙️ [Phase 3] 收集 → 整合 → 視覺化 → 呈現\n" + "=" * 60)
        _execute_tool_loop(plan, user_prompt, file_registry, resp)

    _trace(resp, "done", total_ms=round((time.perf_counter() - t_start) * 1000, 1),
           image_count=len(resp.images), table_count=len(resp.tables))

    return _build_result(resp)


# 便利別名
def chat(message: str, files: dict = None, history: List[dict] = None) -> dict:
    return run_financial_agent(message, files, history)


# ============================================================
# 內建預設工具 Schema（與 AgentTools.json 同步）
# ============================================================
_DEFAULT_TOOLS = [
    {"type": "function", "function": {
        "name": "parse_financial_pdf",
        "description": "【檔案分析第一步】通用 PDF 視覺解析，全頁 OCR 後向量化入庫。使用者要求閱讀/統整/分析上傳檔案時必須優先呼叫。",
        "parameters": {"type": "object", "properties": {
            "thought_process": {"type": "string", "description": "用繁體中文寫下為何要解析此檔、預期取得什麼。"},
            "file_name": {"type": "string", "description": "要解析的檔名（需與上傳檔名完全一致）。"},
        }, "required": ["thought_process", "file_name"]}}},
    {"type": "function", "function": {
        "name": "search_knowledge_base",
        "description": "【檔案分析第二步】語意檢索已解析文件。跨檔案分析時必須對每個檔案分別呼叫並指定 file_name。",
        "parameters": {"type": "object", "properties": {
            "thought_process": {"type": "string", "description": "說明要搜尋什麼、為何需要。"},
            "search_query": {"type": "string", "description": "具體問題或關鍵字。"},
            "file_name": {"type": "string", "description": "（選填）限定檔案。跨檔案分析時必填。"},
        }, "required": ["thought_process", "search_query"]}}},
    {"type": "function", "function": {
        "name": "get_database_schema",
        "description": "【查資料庫第一步】取得歷史財務資料庫(MSSQL)的結構說明（有哪些表、欄位、範例查詢）。要查資料庫前，必須先呼叫此工具了解能查什麼，再產生 SQL。",
        "parameters": {"type": "object", "properties": {
            "thought_process": {"type": "string", "description": "說明你想從資料庫查什麼、為何需要。"},
        }, "required": ["thought_process"]}}},
    {"type": "function", "function": {
        "name": "run_sql_query",
        "description": "【查資料庫第二步】在歷史財務資料庫執行你產生的 SQL（唯讀，只允許 SELECT）。支援 T-SQL 語法。【最後手段】有上傳檔案時，應先用 parse_financial_pdf + search_knowledge_base 找答案，確實查無才查資料庫。",
        "parameters": {"type": "object", "properties": {
            "thought_process": {"type": "string", "description": "說明這個查詢的目的，以及為何已確認需要查資料庫。"},
            "sql": {"type": "string", "description": "要執行的 SELECT 查詢。欄位/表名請依 get_database_schema 提供的結構。"},
        }, "required": ["thought_process", "sql"]}}},
    {"type": "function", "function": {
        "name": "generate_financial_table",
        "description": "把 JSON 數據渲染成專業 HTML 財務表格。",
        "parameters": {"type": "object", "properties": {
            "thought_process": {"type": "string", "description": "說明表格用途。"},
            "data_json": {"type": "string", "description": "JSON 陣列字串。"},
            "title": {"type": "string", "description": "表格標題。"},
        }, "required": ["thought_process", "data_json", "title"]}}},
    {"type": "function", "function": {
        "name": "run_python_code",
        "description": "【程式碼執行與視覺化】繪圖/計算。圖存成 output_plot.png。程式碼會由 Coder 模型優化生成。",
        "parameters": {"type": "object", "properties": {
            "thought_process": {"type": "string", "description": "詳述要畫什麼圖/算什麼、用什麼數據。會傳給程式碼生成模型。"},
            "code": {"type": "string", "description": "Python 程式碼；留空或為描述時系統自動生成。"},
        }, "required": ["thought_process"]}}},
    {"type": "function", "function": {
        "name": "translate_text",
        "description": "專業翻譯，支援中英日韓互譯，擅長財務/技術術語。",
        "parameters": {"type": "object", "properties": {
            "thought_process": {"type": "string", "description": "說明翻譯需求。"},
            "text": {"type": "string", "description": "原文。"},
            "target_language": {"type": "string", "description": "目標語言。", "default": "繁體中文"},
        }, "required": ["thought_process", "text"]}}},
]

AGENT_TOOLS = load_tools_schema()


# ============================================================
# MCP 初始化：把 MCP server 的工具併入 AGENT_TOOLS（同名則由 MCP 接管）
# ============================================================
def init_mcp() -> None:
    """若 FA_USE_MCP=1，連線 MCP server 並把其工具併入 agent 可用工具。"""
    global AGENT_TOOLS, MCP_BRIDGE, MCP_TOOL_NAMES
    if not RUNTIME.use_mcp:
        return
    try:
        import mcp_client
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 無法載入 mcp_client（請 pip install \"mcp[cli]\"）：{e}")
        return

    args = RUNTIME.mcp_args.split()
    print(f"🔌 [MCP] 連線中: {RUNTIME.mcp_command} {' '.join(args)}")
    bridge = mcp_client.get_bridge(RUNTIME.mcp_command, args)
    if bridge.error or not bridge.tool_names():
        print(f"⚠️ [MCP] 連線失敗，改用本地工具：{bridge.error}")
        return

    MCP_BRIDGE = bridge
    MCP_TOOL_NAMES = bridge.tool_names()
    # 同名工具由 MCP 取代（例如 get_database_schema / run_sql_query）
    base = [t for t in AGENT_TOOLS
            if t.get("function", {}).get("name") not in MCP_TOOL_NAMES]
    AGENT_TOOLS = base + bridge.list_openai_tools()
    print(f"✅ [MCP] 已併入工具: {sorted(MCP_TOOL_NAMES)}")


init_mcp()


# ============================================================
# 測試入口
# ============================================================
if __name__ == "__main__":
    print("=" * 60 + "\n🚀 多模型財務 AI 助手（合併優化版）測試\n" + "=" * 60)
    out = run_financial_agent("你好，請問什麼是 ROE？")
    print(f"\n回覆: {out['report_text'][:300]}")

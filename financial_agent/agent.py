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
    trace: List[dict] = field(default_factory=list)  # 完整執行軌跡（供下載/優化）


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _trace(resp: "AgentResponse", phase: str, **fields) -> None:
    """記錄一筆執行軌跡事件，含時間戳。"""
    event = {"phase": phase, "ts": _now_iso()}
    event.update({k: v for k, v in fields.items() if v is not None})
    resp.trace.append(event)


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

    # ── 有上傳檔案：除了純閒聊/純翻譯，全部走工具迴圈 ──
    # （含 visualization：要先解析檔案拿到真實數據，才能畫真實的圖）
    if file_registry:
        if intent == IntentType.CHAT and conf >= th:
            return "fast_chat"
        if intent == IntentType.TRANSLATION:
            return "fast_translate"
        return "execute_tools"

    # ── 無上傳檔案 ──
    if intent == IntentType.CHAT and conf >= th:
        return "fast_chat"
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
def _cache_path(pdf_path: str) -> str:
    return os.path.join(RUNTIME.cache_dir, f"{os.path.basename(pdf_path)}.md")


def _normalize_to_english(full_text: str) -> str:
    """把多語言 OCR 內容逐頁翻成英文（保留表格/數字/結構），統一語言以利檢索與整合。"""
    sys = ("You are a professional financial translator. Translate the following report page "
           "into English. Keep ALL numbers, dates and markdown tables EXACTLY as-is; only "
           "translate surrounding text and labels. Output only the translation, no commentary.")
    out = []
    for sec in full_text.split("\n\n---\n\n"):
        if len(sec.strip()) < 20:
            out.append(sec)
            continue
        try:
            out.append(_chat(MODEL_CONFIG.coder,
                             [{"role": "system", "content": sys},
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

        total_pages = len(doc)  # 先存起來，避免 close 後再讀
        pages_text = []
        for page_num in range(total_pages):
            print(f"  📄 解析第 {page_num + 1}/{total_pages} 頁...")
            try:
                page = doc.load_page(page_num)
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
                # 單頁失敗不拖垮整份，標記後繼續
                content = f"_（第 {page_num + 1} 頁解析失敗：{e}）_"
            pages_text.append(f"## 第 {page_num + 1} 頁\n\n{content}")

        full_text = "\n\n---\n\n".join(pages_text)
        doc.close()
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(full_text)

    # 選用：統一翻成英文再入庫（跨多國語言文件，提升檢索與整合一致性）
    if RUNTIME.normalize_lang == "en":
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
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        separators=["\n## ", "\n### ", "\n\n", "\n", " "],
    )
    chunks = splitter.split_text(full_text)

    # 清掉同檔舊 chunk（用實際查詢，不再硬迴圈 range(500)）
    try:
        existing = collection.get(where={"file_name": file_name})
        if existing and existing.get("ids"):
            collection.delete(ids=existing["ids"])
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 清理舊 chunk 失敗（可忽略）: {e}")

    collection.add(
        ids=[f"{file_name}_chunk_{i}" for i in range(len(chunks))],
        documents=chunks,
        metadatas=[
            {"file_name": file_name, "chunk_index": i, "total_chunks": len(chunks)}
            for i in range(len(chunks))
        ],
    )
    return (f"✅ 檔案 {file_name} 已完成全頁解析！共 {total_pages} 頁，"
            f"切割成 {len(chunks)} 個知識區塊。請呼叫 search_knowledge_base 檢索數據。")


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
   請「一個指標一次查詢」，並用「該檔案原文語言」的正式用語：
   - 營收：中文檔用「營業收入 綜合收益總額」、英文檔用「revenue net sales」、日文檔用「売上収益」
   - 淨利：中文「母公司擁有人應佔溢利」、英文「profit attributable to owners」、日文「親会社の所有者に帰属する当期利益」
   - 也務必查「合併財務摘要 / Consolidated Results / 連結業績」這類彙總表，數字通常在那裡。
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
2. 自行判斷哪些結果適合視覺化，並把要畫的圖「明確指定」出來（附上實際數據）。
3. 需要結構化表格時，也一併指定。

【鐵則】
- 只能使用證據中實際出現的數據，嚴禁捏造、臆測或自行假設數字。
- 若證據不足以回答某部分，在報告中誠實說明「資料不足」，不要硬湊。
- 圖表/表格的 data 必須是從證據抽出的真實數值。

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
            break

        msg = api_resp.choices[0].message
        if not msg.tool_calls:
            print("  └─ 收集完成")
            break

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
                "name": func_name, "content": result_str,
            })

    _trace(resp, "gather_done", evidence_count=len(evidence))
    return evidence


def _synthesize(user_prompt: str, plan: PlanningResult,
                evidence: List[str], resp: AgentResponse) -> dict:
    """整合階段：總結 agent 把證據整合成報告 + 圖表/表格指定（JSON）。"""
    if evidence:
        evidence_text = "\n\n========\n\n".join(evidence)
    else:
        evidence_text = "（收集階段沒有取得任何證據，請在報告中說明資料不足。）"

    # 限制證據總長度，避免輸入過大讓總結模型超慢/逾時
    if len(evidence_text) > RUNTIME.max_evidence_chars:
        evidence_text = evidence_text[:RUNTIME.max_evidence_chars] + "\n…（證據過長已截斷）"

    user_block = (f"【使用者需求】\n{user_prompt}\n\n"
                  f"【收集到的證據】\n{evidence_text}\n\n"
                  "請整合以上證據，輸出 JSON（report / charts / tables）。")

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


def _render_chart(spec: dict, resp: AgentResponse) -> None:
    """視覺化階段：把總結者指定的單張圖交給 Coder 生成並執行。"""
    title = spec.get("title", "圖表")
    chart_type = spec.get("chart_type", "")
    description = spec.get("description", "")
    data = spec.get("data", [])
    data_json = json.dumps(data, ensure_ascii=False)

    task = (f"請畫一張「{chart_type or '適當類型'}」圖：{title}。\n"
            f"需求說明：{description}\n"
            f"務必只用以下實際數據（不可更改、不可捏造、不可新增資料點）：\n{data_json}")

    print(f"  🎨 [Coder] 繪製: {title}")
    result = run_python_code({"thought_process": task, "code": ""}, {})
    _trace(resp, "chart", title=title, chart_type=chart_type,
           code=_preview(result.get("code", ""), 2000),
           output=_preview(result.get("output", "")))
    if result.get("plot"):
        resp.images.append(result["plot"])


def _execute_tool_loop(plan: PlanningResult, user_prompt: str,
                       file_registry: dict, resp: AgentResponse) -> None:
    """統籌三階段：收集 → 整合 → 視覺化/表格 → 一起呈現。"""
    # 1) 收集證據
    evidence = _gather_evidence(plan, user_prompt, file_registry, resp)

    # 2) 總結 agent 整合
    print("\n  🧩 [Synthesize] 總結 agent 整合證據...")
    synthesis = _synthesize(user_prompt, plan, evidence, resp)
    resp.report_text = synthesis["report"]

    # 3) 表格（由總結者指定）
    for tbl in synthesis.get("tables", []):
        try:
            html = generate_financial_table(
                {"data_json": json.dumps(tbl.get("data", []), ensure_ascii=False),
                 "title": tbl.get("title", "表格")}, file_registry)
            if not str(html).startswith("❌"):
                resp.tables.append(html)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ 表格生成失敗: {e}")

    # 4) 視覺化（總結者指派給 Coder）
    for chart in synthesis.get("charts", []):
        try:
            _render_chart(chart, resp)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ 圖表生成失敗: {e}")

    _trace(resp, "present", tables=len(resp.tables), images=len(resp.images))


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
    file_registry = file_registry or {}
    history = history or []
    resp = AgentResponse()
    t_start = time.perf_counter()
    _trace(resp, "request", user_prompt=user_prompt,
           files=list(file_registry.keys()), history_turns=len(history),
           models={"planner": MODEL_CONFIG.planner, "executor": MODEL_CONFIG.executor,
                   "synthesizer": MODEL_CONFIG.synthesizer, "coder": MODEL_CONFIG.coder,
                   "vision": MODEL_CONFIG.vision, "chat": MODEL_CONFIG.chat})

    # Phase 1: 規劃
    print("\n" + "=" * 60 + "\n🧠 [Phase 1] Qwen 意圖分析\n" + "=" * 60)
    t0 = time.perf_counter()
    plan = planning_phase(user_prompt, file_registry, history)
    plan_ms = round((time.perf_counter() - t0) * 1000, 1)
    resp.planning_result = {
        "intent": plan.intent.value, "confidence": plan.confidence,
        "steps": plan.steps, "first_tool": plan.first_tool, "reasoning": plan.reasoning,
    }
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

    return {
        "report_text": resp.report_text,
        "figures": [],          # 預留給互動式 Plotly 圖（目前用 images 靜態圖）
        "tables": resp.tables,
        "images": resp.images,
        "thought_logs": resp.thought_logs,
        "planning_result": resp.planning_result,
        "route": resp.route,
        "executed_sql": resp.executed_sql,
        "trace": resp.trace,
    }


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

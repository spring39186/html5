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
 (chat) (executor)   (coder)       (coder→沙箱)     (Phase 3 tool loop)
                                                        │
                                                        ▼
                                            [Phase 3] Executor tool loop
                                            （PDF 解析 → 向量檢索 → 回答）

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
import base64
import subprocess
import tempfile
from enum import Enum
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
    """單輪文字呼叫，集中錯誤處理。"""
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature, **kw
    )
    return resp.choices[0].message.content or ""


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
- 只有在「明確要畫圖/長條圖/趨勢圖」時才標 visualization。
- 不確定時把 confidence 壓低（<0.7），讓系統走完整工具流程，不要硬猜。

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


def planning_phase(user_prompt: str, file_registry: Dict[str, str]) -> PlanningResult:
    """Phase 1：Qwen 做中文意圖分析。失敗時降級為一般對話。"""
    file_list = list(file_registry.keys()) if file_registry else []
    planning_prompt = (
        f"【使用者輸入】\n{user_prompt}\n\n"
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

    原則：意圖明確就直接拍板，避免 Planner / Executor 雙重決策打架。
    有上傳檔案時，一律進工具流程（避免憑空回答）。
    """
    conf = plan.confidence
    intent = plan.intent
    th = RUNTIME.fastpath_confidence

    # 有檔案 + 與檔案相關 → 一定走工具流程
    if file_registry and intent in (
        IntentType.FILE_ANALYSIS, IntentType.MULTI_FILE_COMPARE, IntentType.DATABASE_QUERY
    ):
        return "execute_tools"

    if intent == IntentType.CHAT and conf >= th:
        return "fast_chat"
    if intent == IntentType.TRANSLATION:
        return "fast_translate"
    if intent == IntentType.VISUALIZATION:
        return "visualize"
    if intent == IntentType.FINANCIAL_QA and conf >= th and not plan.first_tool:
        return "direct_answer"
    if plan.first_tool or plan.requires_files:
        return "execute_tools"
    return "direct_answer"


# ============================================================
# 工具實作
# ============================================================
def _cache_path(pdf_path: str) -> str:
    return os.path.join(RUNTIME.cache_dir, f"{os.path.basename(pdf_path)}.md")


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


def query_financial_data(args: dict, file_registry: dict) -> str:
    """查詢（模擬）歷史資料庫。實務上換成真實 DB 連線即可。"""
    query_target = args.get("query_target", "2023-2025 整體營收")
    print(f"📊 [Database] {query_target}")
    df = pd.DataFrame([
        {"Year": "2023", "Revenue": 1500, "Profit": 250, "GrowthRate": "N/A"},
        {"Year": "2024", "Revenue": 1800, "Profit": 320, "GrowthRate": "20%"},
        {"Year": "2025", "Revenue": 2100, "Profit": 400, "GrowthRate": "16.7%"},
    ])
    return df.to_json(orient="records", force_ascii=False)


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
5. 只輸出純 Python 程式碼，不要任何解說或 markdown 圍欄。"""


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
    "query_financial_data": query_financial_data,
    "generate_financial_table": generate_financial_table,
    "run_python_code": run_python_code,
    "translate_text": translate_text,
}


def dispatch_tool(func_name: str, args: dict, file_registry: dict, thought_logs: list) -> Any:
    """工具派遣 + 防呆：有檔案卻想跳過解析直查 DB → 攔截。"""
    if func_name == "query_financial_data" and file_registry:
        has_parsed = any(
            isinstance(log, dict) and log.get("tool") == "parse_financial_pdf"
            for log in thought_logs
        )
        if not has_parsed:
            return "❌ 系統攔截：請先呼叫 parse_financial_pdf 解析檔案，禁止直接查資料庫。"
    fn = TOOL_DISPATCH.get(func_name)
    if fn:
        return fn(args, file_registry)
    return f"❌ 未知工具: {func_name}"


# ============================================================
# 快速路徑
# ============================================================
def handle_fast_chat(user_prompt: str) -> str:
    return _chat(
        MODEL_CONFIG.chat,
        [{"role": "system", "content": "你是友善的 AI 助手，請用繁體中文回覆。"},
         {"role": "user", "content": user_prompt}],
        temperature=0.7,
    )


def handle_direct_answer(user_prompt: str, plan: PlanningResult) -> str:
    return _chat(
        MODEL_CONFIG.executor,
        [{"role": "system",
          "content": f"你是專業財務 AI 助手。規劃分析：{plan.reasoning}\n請用繁體中文回答。"},
         {"role": "user", "content": user_prompt}],
        temperature=0.3,
    )


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
# Phase 3: Executor 工具迴圈
# ============================================================
def build_execution_system_prompt(plan: PlanningResult, file_list: str) -> str:
    return f"""【前置規劃（由 Qwen Planner 提供）】
- 意圖: {plan.intent.value}
- 步驟: {' → '.join(plan.steps)}
- 首要工具: {plan.first_tool or '無'}
- 推理: {plan.reasoning}
- 信心: {plan.confidence}
- 跨檔案: {'是' if plan.is_multi_file else '否'}

【你的角色】執行協調者。根據上述計畫呼叫工具完成任務。

【決策規則】
1. file_analysis：先 parse_financial_pdf（若尚未解析）→ 再 search_knowledge_base 檢索。
2. 跨檔案分析：對「每個檔案」分別呼叫 search_knowledge_base，並指定 file_name。
3. 需要圖表：呼叫 run_python_code，在 thought_process 詳述要畫什麼、用什麼數據。
4. query_financial_data 是最後手段，只在上傳檔案查無數據時使用。

【已上傳檔案】
{file_list}

【絕對禁止】
- 有上傳檔案時跳過 parse_financial_pdf 直接查資料庫。
- 捏造檔案中不存在的數據。
- 回覆務必使用繁體中文。"""


def _execute_tool_loop(plan: PlanningResult, user_prompt: str,
                       file_registry: dict, resp: AgentResponse) -> None:
    file_list = "\n".join(f"- {n}" for n in file_registry) if file_registry else "無"
    messages = [
        {"role": "system", "content": build_execution_system_prompt(plan, file_list)},
        {"role": "user", "content": user_prompt},
    ]

    for step in range(1, RUNTIME.max_steps + 1):
        print(f"\n  [Step {step}/{RUNTIME.max_steps}]")
        try:
            params = {"model": MODEL_CONFIG.executor, "messages": messages, "temperature": 0.1}
            if AGENT_TOOLS:
                params["tools"] = AGENT_TOOLS
                params["tool_choice"] = "auto"
            api_resp = client.chat.completions.create(**params)
        except Exception as e:  # noqa: BLE001
            resp.report_text = f"❌ API 呼叫失敗: {e}"
            return

        msg = api_resp.choices[0].message
        if msg.content:
            resp.report_text = msg.content
        if not msg.tool_calls:
            print("  └─ 完成（無更多工具呼叫）")
            return

        messages.append(msg)
        for tc in msg.tool_calls:
            func_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            print(f"  ├─ 呼叫工具: {func_name}")

            if "thought_process" in args:
                resp.thought_logs.append(
                    {"tool": func_name, "step": step, "thought": args["thought_process"]}
                )

            try:
                result = dispatch_tool(func_name, args, file_registry, resp.thought_logs)
                if func_name == "run_python_code" and isinstance(result, dict):
                    if result.get("plot"):
                        resp.images.append(result["plot"])
                    result = result.get("output", "")
                elif func_name == "generate_financial_table" and not str(result).startswith("❌"):
                    resp.tables.append(result)
                    result = f"✅ 表格已生成：{args.get('title')}"
            except Exception as e:  # noqa: BLE001
                result = f"⚠️ 工具執行錯誤: {e}"

            messages.append({
                "role": "tool", "tool_call_id": tc.id,
                "name": func_name, "content": str(result),
            })

    resp.report_text += "\n\n⚠️ 已達最大執行步驟，任務中斷。"


# ============================================================
# 主執行入口
# ============================================================
def run_financial_agent(user_prompt: str, file_registry: dict = None) -> dict:
    """
    多模型協作主流程，回傳 dict（相容 Streamlit 前端）：
      { report_text, figures, tables, images, thought_logs, planning_result, route }
    """
    file_registry = file_registry or {}
    resp = AgentResponse()

    # Phase 1: 規劃
    print("\n" + "=" * 60 + "\n🧠 [Phase 1] Qwen 意圖分析\n" + "=" * 60)
    plan = planning_phase(user_prompt, file_registry)
    resp.planning_result = {
        "intent": plan.intent.value, "confidence": plan.confidence,
        "steps": plan.steps, "first_tool": plan.first_tool, "reasoning": plan.reasoning,
    }
    print(f"  ├─ 意圖: {plan.intent.value} (信心 {plan.confidence})")
    print(f"  └─ 首要工具: {plan.first_tool or '無'}")

    # Phase 2: 路由
    route = route_by_intent(plan, file_registry)
    resp.route = route
    print(f"🔀 [Phase 2] 路由 → {route}")

    if route == "fast_chat":
        resp.report_text = handle_fast_chat(user_prompt)
    elif route == "direct_answer":
        resp.report_text = handle_direct_answer(user_prompt, plan)
    elif route == "fast_translate":
        resp.report_text = handle_fast_translate(user_prompt)
    elif route == "visualize":
        result = handle_visualize(user_prompt, plan)
        if result.get("plot"):
            resp.images.append(result["plot"])
        resp.report_text = result.get("output", "圖表已生成。")
    else:  # execute_tools
        print("\n" + "=" * 60 + "\n⚙️ [Phase 3] Executor 工具執行\n" + "=" * 60)
        _execute_tool_loop(plan, user_prompt, file_registry, resp)

    return {
        "report_text": resp.report_text,
        "figures": [],          # 預留給互動式 Plotly 圖（目前用 images 靜態圖）
        "tables": resp.tables,
        "images": resp.images,
        "thought_logs": resp.thought_logs,
        "planning_result": resp.planning_result,
        "route": resp.route,
    }


# 便利別名
def chat(message: str, files: dict = None) -> dict:
    return run_financial_agent(message, files)


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
        "name": "query_financial_data",
        "description": "【最後手段】查歷史財務資料庫。僅在已搜尋上傳檔案仍查無數據、或使用者明確要求時呼叫。",
        "parameters": {"type": "object", "properties": {
            "thought_process": {"type": "string", "description": "說明為何需查 DB、是否已確認檔案查無。"},
            "query_target": {"type": "string", "description": "查詢目標，預設 2023-2025。"},
        }, "required": ["thought_process"]}}},
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
# 測試入口
# ============================================================
if __name__ == "__main__":
    print("=" * 60 + "\n🚀 多模型財務 AI 助手（合併優化版）測試\n" + "=" * 60)
    out = run_financial_agent("你好，請問什麼是 ROE？")
    print(f"\n回覆: {out['report_text'][:300]}")

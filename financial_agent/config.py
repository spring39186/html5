"""
模型與系統配置中心
==================
所有模型角色、連線參數都集中在這裡，並支援用環境變數覆寫，
方便在不同機器 / Ollama 部署之間切換，而不必改程式碼。

設計重點（回應「為什麼這樣分工」）：
- Planner 用 Qwen：中文意圖理解最強，負責「看懂使用者要什麼」+ 路由。
- Executor 預設也用 Qwen：Qwen3 的 function-calling 比 Gemma 穩定，
  且工具參數常含中文，交給中文最強的模型最不容易出錯。
  （若你想沿用 Gemma 當 executor，設環境變數 FA_EXECUTOR=gemma4:31b 即可。）
- Coder 用 Qwen 27B：專責「寫程式碼 / 翻譯」，繪圖一律走它，不靠關鍵字猜。
- Vision 用 GLM-OCR：PDF 逐頁轉 Markdown。
- Chat 用 mesllm：純閒聊的輕量備援。
"""

import os
from dataclasses import dataclass


def _load_dotenv(filename: str = ".env") -> None:
    """極簡 .env 載入：把 KEY=VALUE 寫進環境變數（已存在的真實環境變數優先）。
    設定寫在 .env 檔即可，不必每次在 PowerShell key 變數。"""
    for path in (filename, os.path.join(os.path.dirname(__file__), filename)):
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip()
                        if val[:1] not in ('"', "'") and " #" in val:
                            val = val.split(" #", 1)[0].strip()  # 去行內註解
                        val = val.strip('"').strip("'")
                        if key:
                            os.environ.setdefault(key, val)  # 真實環境變數優先
            except Exception:  # noqa: BLE001
                pass
            break


_load_dotenv()  # 必須在讀取 os.getenv 之前


def _bool_env(name: str, default: str = "0") -> bool:
    """統一的環境變數布林解析（避免各處複製 in ("1","true","yes","on")）。"""
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# 一鍵把主要文字角色都設成同一顆模型，徹底消除「換模型載入」抖動。
# 例如：set FA_MODEL=qwen3.6:27b  → planner/executor/synthesizer/coder/chat 全用它。
# （vision 仍維持 glm-ocr，因為 OCR 需要視覺模型。）
_ALL = os.getenv("FA_MODEL", "").strip() or None


@dataclass(frozen=True)
class ModelConfig:
    """各角色使用的模型（可用環境變數覆寫）"""
    planner: str = _ALL or os.getenv("FA_PLANNER", "qwen3.6:35b-a3b")   # 中文意圖分析 + 路由
    executor: str = _ALL or os.getenv("FA_EXECUTOR", "qwen3.6:35b-a3b")  # 工具決策（收集）
    synthesizer: str = _ALL or os.getenv("FA_SYNTHESIZER", "gemma4:31b")  # 證據整合 + 統籌呈現（總結 agent）
    coder: str = _ALL or os.getenv("FA_CODER", "qwen3.6:27b")           # 程式碼生成 / 翻譯
    vision: str = os.getenv("FA_VISION", "glm-ocr")             # PDF / 圖片 OCR（需視覺模型，不受 FA_MODEL 影響）
    chat: str = _ALL or os.getenv("FA_CHAT", "gemma4:31b")              # 一般聊天
    fallback: str = _ALL or os.getenv("FA_FALLBACK", "qwen3.6:27b")     # 主模型失敗時的備援


@dataclass(frozen=True)
class RuntimeConfig:
    """執行期參數"""
    base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    api_key: str = os.getenv("OLLAMA_API_KEY", "ollama")
    chroma_path: str = os.getenv("FA_CHROMA_PATH", "./chroma_db")
    cache_dir: str = os.getenv("FA_CACHE_DIR", "./ocr_cache")
    tools_path: str = os.getenv("FA_TOOLS_PATH", "AgentTools.json")
    max_steps: int = int(os.getenv("FA_MAX_STEPS", "12"))
    code_timeout: int = int(os.getenv("FA_CODE_TIMEOUT", "60"))
    # 單次 LLM 請求逾時（秒）。超過就放棄該次呼叫並走降級，避免整個 app 卡死。
    request_timeout: int = int(os.getenv("FA_REQUEST_TIMEOUT", "240"))
    # 餵給總結 agent 的證據總長度上限（字元），避免輸入過大導致超慢。
    max_evidence_chars: int = int(os.getenv("FA_MAX_EVIDENCE_CHARS", "16000"))
    # OCR 正規化語言：預設 "en" → 解析後把每頁統一翻成英文再入庫（跨多國語言文件，
    # 大幅減少跨語言檢索與整合漏資料的問題）。設成 "" 可關閉。
    normalize_lang: str = os.getenv("FA_NORMALIZE_LANG", "en")
    # confidence 門檻：高於此值才允許走 fast-path 直接回答
    fastpath_confidence: float = float(os.getenv("FA_FASTPATH_CONF", "0.7"))

    # ── MCP（讓 agent 當 MCP client 查資料庫）──
    use_mcp: bool = _bool_env("FA_USE_MCP")
    mcp_command: str = os.getenv("FA_MCP_COMMAND", "python")
    mcp_args: str = os.getenv("FA_MCP_ARGS", "mcp_server_mssql.py")  # 以空白分隔

    # ── Production refactor 功能開關（全部預設關閉，開啟才啟用新模組，確保零破壞）──
    hybrid_retrieval: bool = _bool_env("FA_HYBRID_RETRIEVAL")  # retrieval.py + query_processing.py
    use_plotly: bool = _bool_env("FA_PLOTLY")                  # viz_plotly.py（互動圖）
    concurrent_ocr: bool = _bool_env("FA_CONCURRENT_OCR")      # ocr_pipeline.py（並發OCR）
    struct_chunk: bool = _bool_env("FA_STRUCT_CHUNK")          # chunking.py（結構化分塊）
    use_graph: bool = _bool_env("FA_USE_GRAPH")                # graph.py（LangGraph 編排）
    rerank_model: str = os.getenv("FA_RERANK_MODEL", "")  # 設 cross-encoder 名稱才啟用 rerank（如 BAAI/bge-reranker-base）
    # 確定性解析管線：file_analysis/multi_file 改用「解析所有檔→標準指標檢索（檔案層級多工）」，
    # 不再讓執行器逐步 LLM 決策，更快更穩。預設關閉。
    deterministic_gather: bool = _bool_env("FA_DETERMINISTIC_GATHER")
    gather_workers: int = int(os.getenv("FA_GATHER_WORKERS", "3"))  # 解析管線檢索階段的並行數

    # 懶人 OCR：PDF 有可用文字層就直接抽取，不呼叫 vision（glm-ocr）；只有掃描頁/影像化表格才用 vision。
    # 大幅省 OCR 成本與時間。預設開啟；若發現抓不到表格數字，設 FA_LAZY_OCR=0 改回全頁 vision。
    lazy_ocr: bool = os.getenv("FA_LAZY_OCR", "1").strip().lower() in ("1", "true", "yes", "on")


MODEL_CONFIG = ModelConfig()
RUNTIME = RuntimeConfig()

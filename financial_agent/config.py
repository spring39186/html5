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


@dataclass(frozen=True)
class ModelConfig:
    """各角色使用的模型（可用環境變數覆寫）"""
    planner: str = os.getenv("FA_PLANNER", "qwen3.6:35b-a3b")   # 中文意圖分析 + 路由
    executor: str = os.getenv("FA_EXECUTOR", "qwen3.6:35b-a3b")  # 工具決策（預設 Qwen，較穩；可改 gemma4:31b）
    synthesizer: str = os.getenv("FA_SYNTHESIZER", "gemma4:31b")  # 證據整合 + 統籌呈現（總結 agent）
    # 註：曾試 mesllm（疑似 gpt-oss-120b），但 120B 反覆載入造成總結逾時，故不用。
    #     改用 gemma4:31b（密集、載入快）。若覺得繁中略弱，可 FA_SYNTHESIZER=qwen3.6:27b。
    coder: str = os.getenv("FA_CODER", "qwen3.6:27b")           # 程式碼生成 / 翻譯
    vision: str = os.getenv("FA_VISION", "glm-ocr")             # PDF / 圖片 OCR
    chat: str = os.getenv("FA_CHAT", "mesllm")                  # 一般聊天
    fallback: str = os.getenv("FA_FALLBACK", "qwen3.6:27b")     # 主模型失敗時的備援


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
    max_evidence_chars: int = int(os.getenv("FA_MAX_EVIDENCE_CHARS", "12000"))
    # OCR 正規化語言："" 關閉；"en" 解析後把每頁譯成英文再入庫（跨多國語言文件用）。
    normalize_lang: str = os.getenv("FA_NORMALIZE_LANG", "")
    # confidence 門檻：高於此值才允許走 fast-path 直接回答
    fastpath_confidence: float = float(os.getenv("FA_FASTPATH_CONF", "0.7"))

    # ── MCP（讓 agent 當 MCP client 查資料庫）──
    # FA_USE_MCP=1 開啟後，agent 會連到下列 MCP server，把它的工具併入可用工具。
    # 預設指向本地 mock server；要接真實 MSSQL MCP server 時改這兩個環境變數即可。
    use_mcp: bool = os.getenv("FA_USE_MCP", "0").lower() in ("1", "true", "yes", "on")
    mcp_command: str = os.getenv("FA_MCP_COMMAND", "python")
    mcp_args: str = os.getenv("FA_MCP_ARGS", "mcp_server_mssql.py")  # 以空白分隔


MODEL_CONFIG = ModelConfig()
RUNTIME = RuntimeConfig()

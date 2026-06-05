# 多模型協作 Agentic 財務 AI 助手（合併優化版）

把原本三個重複的後端（`financial_agent_v2.py` / `multi_model_agent.py` /
`LLMAgenticModelDM.py`）合併成單一、可維護的模組，並修掉實際 bug、強化路由與視覺化。

## 檔案結構

| 檔案 | 說明 |
|------|------|
| `config.py` | 模型角色與執行參數（全部可用環境變數覆寫） |
| `agent.py` | 後端主程式：規劃 → 路由 → 工具執行 |
| `AgentTools.json` | 工具 schema（找不到時 `agent.py` 內建同款預設） |
| `app.py` | Streamlit 前端 |
| `requirements.txt` | 相依套件 |

## 模型分工與設計理由

```
User → [Planner] → [Router] → fast-path 或 [Executor 工具迴圈] → 輸出
```

| 角色 | 預設模型 | 為什麼 |
|------|----------|--------|
| Planner | `qwen3.6:35b-a3b` | **中文意圖理解最強**，負責看懂需求 + 信心評分 + 路由 |
| Executor | `qwen3.6:35b-a3b` | 收集階段的工具決策；Qwen3 **function-calling 比 Gemma 穩** |
| Synthesizer | `gemma4:31b` | **總結整合 agent**：把證據整合成連貫報告並統籌圖表（密集、載入快；可改 `qwen3.6:27b`）|
| Coder | `qwen3.6:27b` | 專責寫程式碼／翻譯；**所有繪圖一律走它** |
| Vision | `glm-ocr` | PDF 逐頁轉 Markdown |
| Chat | `mesllm` | 純閒聊輕量備援 |

### Phase 3：多 agent 協作管線（收集 → 整合 → 視覺化 → 呈現）

複雜任務（檔案分析／資料庫查詢／多工）走這條，解決「流程死版、敘述與圖表各做各的」：

```
收集 agent（executor）  撈齊所有證據：parse PDF / search 向量庫 / get_schema + run_sql
        │  （只開放取數工具，不讓它提早下結論或亂畫圖）
        ▼
總結 agent（synthesizer）整合證據 → 一份連貫報告 + 明確指定要畫哪些圖（附真實數據）
        │  （輸出 JSON：report / charts / tables，嚴禁捏造）
        ▼
Coder agent（coder）   依指定逐張繪圖（只用總結者給的真實數據）
        ▼
一起呈現  報告 + 表格 + 圖表，全部由「同一個總結者」統籌 → 一致性
```

好處：報告與圖表同源、可一致；收集與表達分離；圖表資料由總結者明確指派，杜絕 Coder 捏造。

> 想沿用 Gemma 當 executor：`export FA_EXECUTOR=gemma4:31b` 即可，不必改程式碼。

### 回應幾個關鍵設計問題

- **Qwen 處理中文較好嗎？** 是。所以意圖分析交給 Qwen。
- **為什麼先讓 Qwen 處理？** 主要為了「**便宜的路由**」——閒聊／翻譯／純問答不必進昂貴的
  tool loop；其次是產生結構化計畫約束執行者。
- **直接回答 vs 調用工具誰決定？** 由 **Router 確定性拍板**（明確意圖直接走 fast-path），
  不再讓 Planner 與 Executor 重複決策互相打架。有上傳檔案時一律進工具流程，避免憑空作答。
- **畫圖要不要指定模型？** 要。視覺化是**一級確定性路徑**：
  `intent=visualization → 指定 Coder 生成繪圖碼 → 沙箱執行 → 失敗自動修復一次`，
  不再靠 `len(code)<50`／關鍵字「畫」這種脆弱啟發式。

## 相對原版的修正

1. PDF 頁數統計 bug（原版於 `doc.close()` 後讀 `len(doc)`）。
2. ChromaDB 舊 chunk 清理改用實際查詢，取代硬迴圈 `range(500)`。
3. 單頁 OCR 失敗只標記該頁、不再整份失敗。
4. Planner JSON 解析更穩健；失敗時依「是否有檔案」智慧降級。
5. 翻譯語言偵測涵蓋中英日韓。
6. 三版合一，消除重複維護成本。

## 執行

```bash
pip install -r requirements.txt
# 確保 Ollama 已跑起來並有對應模型；如需自訂：
# export OLLAMA_BASE_URL=http://localhost:11434/v1
# export FA_EXECUTOR=gemma4:31b
streamlit run app.py
```

## Mock MSSQL 資料庫（讓模型產生 SQL 查詢）

用 SQLite in-memory 模擬一台 Microsoft SQL Server，灌入 MSSQL 風格的財務 schema 與假資料。
模型透過兩個工具操作，不再是寫死的單一 DataFrame：

| 工具 | 作用 |
|------|------|
| `get_database_schema` | 回傳資料表/欄位/範例查詢，讓模型「知道能查什麼」 |
| `run_sql_query` | 執行模型生成的 SQL（唯讀，只允許 SELECT/WITH）並回傳假資料 |

- 引擎在 `mock_db.py`，**純標準函式庫**，可獨立執行測試：`python mock_db.py`。
- 含安全防護：擋掉 INSERT/UPDATE/DELETE/DROP… 與多語句（stacked queries）。
- 含 **T-SQL → SQLite 方言轉換**：`SELECT TOP n`、`dbo.` 前綴、`ISNULL/LEN/GETDATE` 等都能跑。
- 假資料：3 家公司 × 2023–2025 年財務（營收/毛利/淨利/EPS…）＋ 事業部營收。
- 要換成「真實 MSSQL」時，只要改寫 `mock_db.execute_sql()` 接真實連線即可，agent 不必動。

### （選用）改用 MCP 查詢

`mcp_server_mssql.py` 把同一個引擎包成標準 MCP server，可掛到 Claude Desktop / Cursor：

```bash
pip install "mcp[cli]"
python mcp_server_mssql.py        # stdio 啟動
```

> 你現有的 Ollama + Streamlit agent **不需要**這支也能查（它直接呼叫 `mock_db`）。
> 這支是為「透過 MCP host 查詢」這條路準備，與 function-calling 共用同一份假資料。

## 效能 / 穩定性調校（環境變數）

| 變數 | 預設 | 說明 |
|------|------|------|
| `FA_MODEL` | `（空）` | **一鍵把 planner/executor/synthesizer/coder/chat 全設成同一顆模型**，徹底消除換模型載入抖動（強烈建議在單 GPU 機器上設定，例如 `qwen3.6:27b`）。vision 仍用 glm-ocr |
| `FA_REQUEST_TIMEOUT` | `240` | 單次 LLM 請求逾時（秒）。超過就放棄並走降級，不會卡死整個 app |
| `FA_MAX_EVIDENCE_CHARS` | `12000` | 餵給總結 agent 的證據總長度上限，避免輸入過大導致超慢 |
| `FA_NORMALIZE_LANG` | `（空）` | 設 `en` 時，OCR 後把每頁統一翻成英文再入庫（跨多國語言文件用）|
| `FA_SYNTHESIZER` | `gemma4:31b` | 總結 agent 模型。**避免用超大模型（如 120B）**，反覆載入會逾時 |

> **避免 model-swap 抖動**：同一任務若用到多顆差異很大的模型（35B/27B/120B），
> Ollama 會反覆載入/卸載，造成單步暴慢甚至逾時。建議讓各角色模型盡量集中、
> 並在 Ollama 設定 `keep_alive` 讓常用模型常駐。

## 後續可加強

- 視覺化可改回傳互動式 Plotly 圖（前端 `figures` 欄位已預留）。
- 多使用者情境下，ChromaDB collection 建議依 session 隔離。
- mock DB 之後可換真實 MSSQL 或現成的 MSSQL MCP server（如 microsoft/mcp）。

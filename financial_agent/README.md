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
| Executor | `qwen3.6:35b-a3b` | Qwen3 的 **function-calling 比 Gemma 穩**，工具參數常含中文 |
| Coder | `qwen3.6:27b` | 專責寫程式碼／翻譯；**所有繪圖一律走它** |
| Vision | `glm-ocr` | PDF 逐頁轉 Markdown |
| Chat | `mesllm` | 純閒聊輕量備援 |

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

## 後續可加強

- `query_financial_data` 目前是模擬資料，接真實 DB 即可。
- 視覺化可改回傳互動式 Plotly 圖（前端 `figures` 欄位已預留）。
- 多使用者情境下，ChromaDB collection 建議依 session 隔離。

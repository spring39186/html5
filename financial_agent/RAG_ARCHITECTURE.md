# Production-Grade Multilingual Financial RAG — 架構

把零散的模組收斂成一套「可上線」的多語言財務 RAG。下面每一塊都對應到實際檔案，
並標明哪些已完成、哪些是這次新增。

```
                ┌─────────────────────────────────────────────┐
   PDF（多語）  │  Ingestion：OCR + 多語言融合                 │
  ───────────►  │  ocr_router.py（分層 OCR）                   │
                │   ├─ 有文字層 → 直接抽取（0 成本）           │
                │   ├─ 掃描/表格密集頁 → vision OCR            │
                │   └─ 通用轉寫 prompt（不做財報重構）         │
                │  多語言融合：原文 chunk + 英文正規化 chunk   │
                │   （metadata.lang），跨語言都檢索得到        │
                └───────────────────┬─────────────────────────┘
                                    ▼  ChromaDB（dense 向量）
   使用者查詢   ┌─────────────────────────────────────────────┐
  （任意語言） │  Retrieval：rag.py（統一入口 rag_search）    │
  ───────────► │  1. Query Rewrite（QUERY_REWRITE_SYSTEM）    │
                │     → 2-4 條英文子查詢 + HyDE 假設答案        │
                │  2. Hybrid Search（retrieval.py）           │
                │     dense(Chroma) + BM25 → 每條子查詢候選    │
                │  3. RRF Fusion 跨子查詢融合                  │
                │  4. Cross-Encoder Rerank（可選，FA_RERANK） │
                │  5. Top-K（cost knob）                       │
                └───────────────────┬─────────────────────────┘
                                    ▼  Evidence（evidence.py）
                ┌─────────────────────────────────────────────┐
                │  Context 控制：dedup → rank → token budget   │
                │  （不再 over-context；top-k 8~12、非 char-cut）│
                └───────────────────┬─────────────────────────┘
                                    ▼
                ┌─────────────────────────────────────────────┐
                │  Synthesizer（SYNTHESIS_V2，證據整合器）     │
                │  每個結論引用 [E#]、禁外部知識、charts 驗證  │
                └─────────────────────────────────────────────┘
```

## 1. Query Rewrite（新增，`rag.py`）

`QUERY_REWRITE_SYSTEM` 把任意語言的財務問題改寫成：
- `rewrites`: 2–4 條**聚焦的英文子查詢**（標準財務術語、保留公司/年度/季別/股票代號），
  把多重問題拆解開來；
- `hyde`: 一段**假設性英文答案**，用於 embedding 召回（HyDE）。

比舊的「翻譯+同義擴展」（`query_processing.translate_and_expand_query`）更強：它做**改寫與拆解**，
對「2024 營業利益率與資本支出」這種複合問題會拆成多條精準子查詢。

## 2. Hybrid Search（已完成，`retrieval.py`）

`HybridRetriever` = dense(Chroma 向量) + BM25(rank_bm25，CJK-aware tokenizer) + **RRF 融合**。
`rag_search` 對「每條子查詢」各跑一次 hybrid，再用 `reciprocal_rank_fusion` 把多條子查詢的
結果融合，召回率明顯優於單一查詢。

## 3. Reranker Pipeline（已完成＋串接，`retrieval.py` / `rag.py`）

`Vector + BM25 → RRF → Cross-Encoder → Final Top-K`。
Cross-encoder（如 `BAAI/bge-reranker-base`）以 `FA_RERANK_MODEL` 啟用；缺套件自動跳過、退回 RRF 順序。

## 4. OCR + Multilingual Fusion（分層已完成 / 融合為設計）

- **分層 OCR（`ocr_router.py`）**：有文字層直接抽取（0 vision 成本），只對掃描頁/表格密集頁打 vision。
  通用轉寫 prompt（`GENERIC_OCR_PROMPT`）忠實轉寫、不分類、不重構、不補數據。
- **多語言融合（`FA_KB_FUSION`）**：入庫時同時保留「原文 chunk」與「英文正規化 chunk」
  （`metadata.lang = "orig"|"en"`）。好處：英文查詢命中英文 chunk、原文關鍵字也命中原文 chunk，
  跨語言召回不漏；總結時以英文 chunk 為主、原文 chunk 佐證。

## 5. Cost Control（多層，已完成＋`RagConfig`）

| 層級 | 機制 | 檔案 |
|---|---|---|
| OCR | 分層路由：能用文字層就不打 vision（最大成本點）| `ocr_router.py` |
| 重解析 | 冪等 + `ConversationMemory.is_parsed` 跳過已解析檔 | `memory.py` |
| 檢索 | `RagConfig`：max_subqueries / dense_candidates / top_k / rerank_top_n | `rag.py` |
| Context | `evidence.select_within_budget`：token 預算 + top-k（非 char-cut）| `evidence.py` |
| 工具迴圈 | `ToolBudget`：parse/search/sql 次數上限，杜絕 over-call/retry loop | `pipeline.py` |
| 查詢翻譯 | 英文 KB + 英文查詢時跳過翻譯（`_retrieve_en`）| `agent.py` |

## 模組對照

| 功能 | 模組 | 狀態 |
|---|---|---|
| Query rewrite + HyDE | `rag.py` | 新增 |
| 統一 RAG 入口（rewrite→hybrid→fuse→rerank→topk）| `rag.py` | 新增 |
| Hybrid BM25+vector+RRF | `retrieval.py` | 已完成 |
| Cross-encoder rerank | `retrieval.py` | 已完成（可選）|
| 分層 OCR + 通用 prompt | `ocr_router.py` | 已完成 |
| 多語言融合入庫 | `FA_KB_FUSION`（整合階段接線）| 設計 |
| Evidence 結構化/去重/預算 | `evidence.py` | 已完成 |
| Synthesizer 證據整合器 | `chart_schema.SYNTHESIS_V2_SYSTEM` | 已完成 |
| Cost control 多層 | `RagConfig`/`ToolBudget`/memory/ocr_router | 已完成 |

## 接線（整合階段，behind flag）

`agent.py` 的 `_retrieve_hits` / `_hybrid_search` 委派給 `rag.rag_search`：
- `dense_search` ← `collection.query`
- `cross_encode` ← sentence-transformers `CrossEncoder`（`FA_RERANK_MODEL` 設定時）
- `llm_call` ← `_chat(MODEL_CONFIG.coder, …)`（query rewrite）
全部以 `FA_RAG_V2` 旗標控制，預設關閉，不影響現有行為。

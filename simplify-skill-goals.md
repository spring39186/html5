# Claude Code `/simplify` Skill — 目標與重點（供 GitHub Copilot 實作參考）

> 來源：Claude Code 內建 `/simplify` skill 的原始提示（prompt）內容整理。
> 一句話摘要：`/simplify → 4 個清理（cleanup）代理並行審查 → 直接套用修正`。

---

## 1. 目標（Goal)

**提升「已變更程式碼」的品質，而不是找 bug。**

- 針對本次 diff（變更範圍）審查四個面向：**重用（Reuse）、簡化（Simplification）、效率（Efficiency）、高度（Altitude）**，找到問題後**直接修正**。
- **明確排除正確性 bug**：不去尋找 correctness bug，那是 `/code-review` 的職責。
- 修正**不得改變原本的預期行為**（behavior-preserving cleanup）。

## 2. 核心原則

1. **Quality only**：只做品質清理，不做除錯。
2. **範圍限定在 diff**：審查對象是這次變更的程式碼，修正也不應大幅超出被審查的 diff 範圍。
3. **每個發現（finding）都要具體**：必須含 `file`、`line`、一行 `summary`，以及**具體代價**（什麼被重複了、什麼被浪費了、什麼變得難維護）。
4. **不確定就跳過**：若修正會改變預期行為、需要大幅改動 diff 以外的程式碼、或判斷是誤報（false positive），就跳過並**記錄跳過原因**，不要硬修。

## 3. 流程（三個階段）

### Phase 0 — 取得 diff（審查範圍）

- 執行 `git diff @{upstream}...HEAD`（沒有 upstream 時退回 `git diff main...HEAD` 或 `git diff HEAD~1`）取得待審查的 unified diff。
- 若有未提交的變更、或上述範圍 diff 為空，另外執行 `git diff HEAD` 並把 working-tree 變更納入範圍（審查常在 commit 之前進行）。
- 若使用者指定了 PR 編號、分支名稱或檔案路徑，改以該目標為審查範圍。
- **這份 diff 就是審查範圍（review scope）。**

### Phase 1 — 審查（4 個獨立審查代理並行）

同時（單一訊息、並行）啟動 **4 個獨立的審查代理**，每個代理拿到同一份 diff，但只負責下列四個角度之一。每個代理回報自己的 findings（`file` / `line` / 一行 `summary` / 具體代價）。

#### 角度 1：Reuse（重用）

> 標記「重新實作了 codebase 已有功能」的新程式碼 —— 用 Grep 搜尋共用/工具模組以及變更附近的檔案，並**指名應改用的既有 helper**。

#### 角度 2：Simplification（簡化）

> 標記 diff 新增的不必要複雜度：
> - 冗餘或可推導（derivable）的狀態
> - 複製貼上後略作變化的程式碼
> - 過深的巢狀（deep nesting）
> - 遺留的死程式碼（dead code）
>
> 並**指名能完成同樣工作的更簡單寫法**。

#### 角度 3：Efficiency（效率）

> 標記 diff 引入的浪費工作：
> - 重複計算或重複 I/O
> - 彼此獨立卻被依序執行的操作（可平行化）
> - 加到啟動流程或熱路徑（hot path）上的阻塞工作
> - 由 closure／捕獲環境建構的長生命週期物件 —— 它們會讓整個外層 scope 在物件存活期間無法釋放（當該 scope 持有大型值時就是記憶體洩漏）；優先改用只複製所需欄位的 class/struct
>
> 並**指名更便宜的替代方案**。

#### 角度 4：Altitude（高度／實作深度）

> 檢查每個變更是否實作在**正確的深度**，而不是脆弱的 OK繃（bandaid）式修補。
> 在共用基礎設施上層層堆疊特例（special case），是修法不夠深的訊號 ——
> **優先把底層機制一般化（generalize），而非不斷加特例。**

### Phase 2 — 套用修正

1. 等所有 4 個代理完成。
2. **去重**：合併指向同一行或同一機制的 findings。
3. 對剩下的每個 finding **直接修正**。
4. **跳過**符合以下任一條件的 finding（並註記跳過，不與其爭辯）：
   - 修正會改變預期行為
   - 需要大幅改動被審查 diff 以外的程式碼
   - 判斷為誤報（false positive）
5. 最後輸出簡短總結：**修了什麼、跳過了什麼**（或確認程式碼本來就乾淨）。

## 4. Findings 的統一格式

| 欄位 | 說明 |
|------|------|
| `file` | 檔案路徑 |
| `line` | 行號 |
| `summary` | 一行問題摘要 |
| 具體代價 | 不是描述 crash，而是描述**成本**：什麼被重複、什麼被浪費、什麼變得更難維護 |

> 補充：在 Claude Code 的 code-review 體系中，cleanup／altitude 類 finding 的優先級**永遠低於 correctness bug** —— 當輸出額度不夠時先砍 cleanup 類。

---

## 5. GitHub Copilot 實作建議

Copilot 沒有「並行子代理」機制，建議把四個角度**合併為單一 prompt、依序執行**。可做成 prompt file（`.github/prompts/simplify.prompt.md`，VS Code 中以 `/simplify` 呼叫）：

```markdown
---
mode: agent
description: Clean up the changed code without changing behavior (reuse / simplification / efficiency / altitude)
---

You are improving the quality of the changed code, NOT hunting for bugs.
Do not look for correctness bugs.

## Step 1 — Gather the diff
Run `git diff main...HEAD`; if empty or there are uncommitted changes, also
run `git diff HEAD`. Treat this diff as the review scope.

## Step 2 — Review the diff from 4 angles
For each angle, report findings as: file, line, one-line summary, and the
concrete cost (what is duplicated, wasted, or harder to maintain).

1. **Reuse** — flag new code that re-implements something the codebase
   already has; search shared/utility modules and files adjacent to the
   change, and name the existing helper to call instead.
2. **Simplification** — flag unnecessary complexity the diff adds:
   redundant or derivable state, copy-paste with slight variation, deep
   nesting, dead code left behind. Name the simpler form that does the
   same job.
3. **Efficiency** — flag wasted work: redundant computation or repeated
   I/O, independent operations run sequentially, blocking work on startup
   or hot paths, long-lived objects built from closures that keep the
   enclosing scope alive. Name the cheaper alternative.
4. **Altitude** — check each change is implemented at the right depth,
   not as a fragile bandaid; special cases layered on shared
   infrastructure mean the fix isn't deep enough — prefer generalizing
   the underlying mechanism over adding special cases.

## Step 3 — Apply the fixes
Dedup findings that point at the same line or mechanism, then fix each
remaining one directly. Skip (and note) any finding whose fix would change
intended behavior, require changes well outside the reviewed diff, or that
you judge to be a false positive. Finish with a brief summary of what was
fixed and what was skipped (or confirm the code was already clean).
```

### 實作時的對應要點

| Claude Code 原設計 | Copilot 對應做法 |
|--------------------|------------------|
| 4 個代理並行審查 | 單一 agent 依序跑 4 個角度（或分 4 次對話各跑一個角度） |
| Skill 引數指定審查目標（PR/分支/路徑） | prompt file 的輸入變數，或在對話中直接指定 |
| findings 去重後直接套用修正 | 用 agent mode 讓 Copilot 直接編輯檔案；或先只產報告、人工確認後再修 |
| 「不改變行為」的保險 | 修完後跑既有測試驗證行為不變 |

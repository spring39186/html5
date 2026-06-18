# Essbase MDX 查詢指南（AI 必讀）

> 這份檔案是 financial agent 在「產生 MDX 查詢前」的必讀參考。
> `get_database_schema` 會把本檔內容原樣提供給模型。
> **維護方式：直接編輯本檔即可，不需要改程式碼。**

---

## 0. 一眼摘要（硬規則）

- 這顆資料庫是 **Essbase 多維 cube**，用 **MDX** 查詢（**不是 SQL**）。
- 目標 cube（App.Db）見下方「§1 資料庫說明」；`get_database_schema` 也會在最上方帶入實際 cube 名。
- 基本骨架：
  ```mdx
  SELECT { 欄成員集合 } ON COLUMNS,
         { 列成員集合 } ON ROWS
  FROM   App.Db
  WHERE  ( 切片成員 )
  ```
- 成員一律寫 `[維度].[成員]`；集合用 `{ }`、成員間用逗號分隔。
- 一個維度只能出現在「一個軸」**或** `WHERE` 其中一處，不可重複。
- **要「全部／合計」就用維度的「頂層成員」本身**（如 `[Sector Total]`、`[Currency]`、`[Site Org]`），或 `[維度].Members`／`Descendants(頂層, 1, SELF_AND_BEFORE)`。Essbase **沒有** SSAS 的 `[All]` 成員——**嚴禁** `[X].[All]`、`[維度].[All Departments]`、`[Sector Total].[All Sectors]`（一律報 `Unknown Member`）。每個維度的頂層成員名就印在 outline 各維度的「頂層 `[…]`」欄。各維度只用 outline 列出的名字，**不要自創** `[Department]`、`[Sector]` 等不存在的維度。
- 產好 MDX 後，呼叫 `run_sql_query` 工具，把查詢字串放進 **`mdx`** 參數。

---

## 1. 資料庫說明

<!-- 這顆 cube 的業務意義、單位、涵蓋範圍、注意事項；模型會據此決定該不該查、怎麼解讀。 -->

- **目標 cube（App.Db）**：`VSalRPTH.SaleRPTA`
- **這顆 cube 是什麼 / 業務範圍**：Sales 報告（銷售）
- **金額單位 / 幣別**：`NTD K` = 新台幣仟元、`USD K` = 美元仟元
- **資料涵蓋範圍**：2012–2018
- **重要提醒 / 限制**：目前**只有 2018 年**的資料可查；篩 2018 可用 `[Time].[2018]`（Time 維成員）或 `[Year].[2018]`（Year 屬性維）。
- **時間／月份怎麼查（最常踩雷，務必照做）**：
  - 月份在 **`Time` 維底下**，命名為 **`<年>/<月>`、月份補零兩位**：`Time → 2018 → 2018/H1、2018/H2 → 2018/Q1…2018/Q4 → 2018/01…2018/12`。季是 `2018/Q1…Q4`、半年是 `2018/H1`／`2018/H2`。
  - ⚠️ **嚴禁自創英文月名**：`[Time].[Jan]`、`[Period].[Jan]`、`[Time].[Feb]`…**都不存在**。月份要寫 `[Time].[2018/01]`、`[Time].[2018/12]` 這種格式（**補零**）。
  - 要「某年各月」最穩用 **`Descendants([Time].[2018], LEAVES)`**（取最底葉＝12 個月）或 `Descendants([Time].[2018], 3, SELF)`；要單月就 `[Time].[2018/03]`。不要逐月用英文名列舉。
  - **`Period`、`Year` 是屬性維**（Period＝H1/H2/Q…/`01`–`12`；Year＝2007–2025），主要用於**篩選/切片**（如 `WHERE ([Year].[2018])`）；要當欄軸放月份，優先用 `Time` 的 `2018/01…12`。
  - ⚠️ **要「每個月都列出來」（含沒資料的月顯示 0）時，不要加 `NON EMPTY`**：Essbase 會在回傳前就把「整月皆 #Missing」的月份（如某年 1~4 月）整欄藏掉，後端就補不回來了。把 12 個月（`Descendants([Time].[2018], LEAVES)`）放上軸、且**不要 NON EMPTY**，後端會自動把 #Missing 補成 0。

### 1.1 中文業務詞 → 維度對照（**使用者用中文時必看**）

使用者多半用中文（如「部門」「幣別」「情境」）。本 cube 維度名是英文，請**先用下表把中文對應到正確維度**，再產 MDX；**絕不自創維度（如 `Department`/`Sector`）或 `[All]` 成員**。

| 使用者常說的中文 | 對應維度（MDX 用這名字） | 備註／常用成員 |
|---|---|---|
| **部門**、事業群、事業部、產業別、Sector | **`Sector Total`** | ⚠️ 沒有 `Department`／`Sector` 維！「各部門」＝ `Descendants([Sector Total], 1, SELF_AND_BEFORE)` |
| **幣別**、貨幣、台幣、美元 | **`Currency`** | 台幣＝`[Currency].[NTD K]`、美元＝`[Currency].[USD K]` |
| **指標**、科目、度量、金額類型 | **`Measure`** | `Current`(本期)、各種 `… Variance`／`… %`／`… YTD` |
| **情境**、版本、實際、草稿、預算、預測 | **`Scenario`** | `Actual`／`Draft`／`ForecastV1…4` 等 |
| **時間**、年、季、月 | **`Time`** | 月＝`[Time].[2018/01]`…；各月＝`Descendants([Time].[2018], LEAVES)` |
| 年度、哪一年（純篩選） | `Year`（屬性）或 `Time` | `WHERE ([Year].[2018])` |
| 期間、上下半年、季（純篩選） | `Period`（屬性）或 `Time` | H1/H2、Q1–Q4 |
| 廠區群組、據點群組 | **`Site Group`** | |
| 廠區、據點、組織、公司別 | **`Site Org`** | |
| 申報、報備 | **`Filings`** | `TW_Filing`／`US_Filing` |

> 表上對不到時：看文末 outline 的維度清單做語意對應；仍不確定就 `Children`/`Descendants` 展開該維頂層查看成員——**不要硬猜、不要自創維度/成員**。
> （此表是業務知識，可直接編輯；上面非英文直觀的對應（如廠區/據點）請依貴公司實際定義調整。）

---

## 2. MDX 基本教學

> 本節一律以 **Oracle Essbase MDX** 為準（Essbase 是 Oracle 產品）。它與微軟 SSAS 的 MDX 有差異：
> Essbase **不用** SSAS 的 `&[key]` 成員鍵語法；成員前綴只能用「單一」維度或單一祖先
> （**不可串多個祖先**，否則 Essbase 報錯）。

### 2.1 MDX 是什麼？跟 SQL 差在哪
MDX（MultiDimensional eXpressions）是查 OLAP cube 的語言。
- **SQL** 面對「表 / 列 / 欄」，靠 `JOIN`、`GROUP BY`。
- **MDX** 面對「cube / 維度 / 成員」：你不是 join 表，而是「從每個維度挑成員、擺到軸上」，彙總（如部門小計）由 cube 自己算好。

### 2.2 四個核心概念
- **維度 (Dimension)**：分析角度（Time、Currency、Sector Total…）。
- **成員 (Member)**：維度裡的值，寫成 `[維度].[成員]`，如 `[Currency].[NTD K]`。
- **tuple（座標）**：跨多個維度「鎖定一格」，用 `( )`，如 `([Currency].[NTD K], [Scenario].[Actual])`。
- **set（集合）**：一組成員或 tuple，用 `{ }`、逗號分隔，如 `{ [Currency].[NTD K], [Currency].[USD K] }`。

### 2.3 查詢骨架
```mdx
SELECT
  { ...欄... } ON COLUMNS,   -- 也可寫 ON AXIS(0)
  { ...列... } ON ROWS       -- 也可寫 ON AXIS(1)
FROM App.Db
WHERE ( ...切片... )         -- 選用
```
- `ON COLUMNS` / `ON ROWS`：把集合擺到欄 / 列軸。
- `FROM`：哪顆 cube，寫 `App.Db` 或 `[App].[Db]`（如 `FROM VSalRPTH.SaleRPTA`）。
- `WHERE`（slicer）：把「沒放上軸」的維度固定成某成員，縮小範圍。

### 2.4 WHERE（切片 / slicer）
- 放沒上 ROWS/COLUMNS 的維度，固定成單一成員或 tuple。
- 例：`WHERE ([Currency].[NTD K])` → 整份結果都只看 NTD K。
- 多維切片：`WHERE ([Currency].[NTD K], [Scenario].[Actual])`。
- **規則**：出現在 `WHERE` 的維度，不可同時出現在 ROWS/COLUMNS。

### 2.5 最常用的集合函式（重點）
| 函式 | 作用 | 範例 |
|---|---|---|
| `[成員].Children` | 直接子成員集合 | `[Sector Total].Children` |
| `Descendants(成員, 層數, 旗標)` | 後代（可控層數、含不含自己） | `Descendants([Sector Total], 1, SELF_AND_BEFORE)` |
| `[維度].Members` | 該維度全部成員 | `[Scenario].Members` |
| `[維度].Levels(0).Members` | 某一層全部成員（0 = 最底葉層） | `[Time].Levels(0).Members` |
| `Crossjoin(集合A, 集合B)` | 兩維度交叉（笛卡兒積） | `Crossjoin([Time].Children, [Scenario].Children)` |
| `Filter(集合, 條件)` | 依條件篩選 | `Filter([Sector Total].Children, [Measure].[Current] > 0)` |
| `Order(集合, 值, DESC)` | 排序 | `Order([Sector Total].Children, [Currency].[NTD K], BDESC)` |
| `TopCount(集合, n, 值)` | 取前 n 大 | `TopCount([Sector Total].Children, 3, [Currency].[NTD K])` |
| `Hierarchize(集合)` | 依階層順序排好 | `Hierarchize({ Descendants([Sector Total]) })` |

#### Descendants 的旗標（flag）
`Descendants(member, layer, flag)`，`layer` 是往下幾層，常用 `flag`：
- `SELF`：只取「距離 = layer」那一層。
- `SELF_AND_BEFORE`：自己 + 到該層之間的所有層（**最常用**，父與子一起帶出）。
- `BEFORE`：到該層之前（不含該層）。
- `LEAVES`：只取葉節點（最底層）。

例：`Descendants([Time].[2018], 3, SELF)` → 2018 往下第 3 層（依本 cube 約為各月）。

### 2.6 計算成員 WITH MEMBER（進階，選用）
臨時算一個新指標：
```mdx
WITH MEMBER [Currency].[USD-NTD 差] AS
  '[Currency].[USD K] - [Currency].[NTD K]'
SELECT { [Currency].[NTD K], [Currency].[USD K], [Currency].[USD-NTD 差] } ON COLUMNS,
       { [Sector Total].Children } ON ROWS
FROM App.Db
```

### 2.7 註解
- 區塊註解：`/* ... */`（Essbase MDX 通用）。

### 2.8 常見錯誤（請避免）
1. **維度重複上軸**：同一維度同時放 ROWS 和 WHERE → 報錯。
2. **忘了中括號**：成員名有空白或以數字開頭，一定要包 `[ ]`（如 `[2018]`、`[NTD K]`）。
3. **set 與 tuple 搞混**：多成員用 `{ }`，單一座標用 `( )`。
4. **空軸**：每個軸至少要有成員。
5. **成員名亂猜**：只用 §3 Outline 列出的成員；不確定就用 `Children`/`Descendants` 動態展開。
   - **最常見：亂加 `[All]`**。Essbase 沒有 `[All]`／`[All Departments]`／`[All Sectors]`。要全部/合計→直接用頂層成員（`[Sector Total]`）或 `Descendants([Sector Total], 1, SELF_AND_BEFORE)`。
   - **自創維度**：本 cube 沒有 `Department`/`Sector`；部門請用 `Sector Total` 維。
6. **成員前綴只能一層**（Essbase 專屬）：寫 `[維度].[成員]` 或 `[單一祖先].[成員]`，
   **不要串多個祖先**（如 `[A].[B].[C].[成員]`）——Essbase 會報錯。
7. **別用 SSAS 語法**（Essbase 專屬）：沒有 `&[key]`、沒有 `.&[2018]` 這種鍵參照；
   成員直接用名稱，如 `[2018]`、`[Actual]`。
8. **維度名照 outline 原樣、不要自創**：本 cube 的部門維度就叫 **`Sector Total`**（不是 `Sector`），
   也**沒有** `Department` 這種維度。先看文末「本 cube 實際維度與成員」確認維度/成員名再寫。

---

## 3. Cube Outline（維度與成員）

> **本 cube 的實際維度/成員清單，由系統在文末「本 cube 實際維度與成員」自動帶入**——
> 因為**每顆 cube 的 outline 不同**，這裡不寫死，而是依「目前設定的 cube（App.Db）」抓對應的 outline 檔。
>
> **每顆 cube 各維護一份 outline 檔**（檔名帶 cube）：
> - `essbase_outline_parent_child.<App.Db>.csv` — 程式讀這份產生上面的摘要。
> - `essbase_outline.<App.Db>.md` — 人看的完整縮排階層 + 成員公式。
> - 目前已備妥 **`VSalRPTH.SaleRPTA`** 的檔：`essbase_outline_parent_child.VSalRPTH.SaleRPTA.csv`
>   與 `essbase_outline.VSalRPTH.SaleRPTA.md`。（找不到 cube 專屬檔時，才退回無 cube 後綴的舊檔名。）
>
> ⚠ outline 檔是「對某一顆 cube」抽取的快照；**換 cube 一定要抽該 cube 的 outline 檔**
> （抽法記在 `CLAUDE.md`）。部分分支抽取時為「收合」狀態（標 `collapsed:N`），深層葉成員可能未完整列出。
> **不確定某成員是否存在時，用 `Descendants`/`Children` 動態展開，不要硬猜成員名。**

---

## 4. 可直接抄改的 MDX 範例（對本 cube 實測可跑）

> 把 `App.Db` 換成 §1 裡的實際 cube 名（`get_database_schema` 最上方也會給）。

**A. 各部門 × 幣別**
```mdx
SELECT { [Currency].[NTD K], [Currency].[USD K] } ON COLUMNS,
       { Descendants([Sector Total], 1, SELF_AND_BEFORE) } ON ROWS
FROM App.Db
```

**B. 部門 × 各年度（幣別固定 NTD K）**
```mdx
SELECT { Descendants([Time], 1, SELF_AND_BEFORE) } ON COLUMNS,
       { Descendants([Sector Total], 1, SELF_AND_BEFORE) } ON ROWS
FROM App.Db
WHERE ([Currency].[NTD K])
```

**C. 部門 ×（2018 各月 × 各情境）— Crossjoin 多維欄**
```mdx
SELECT { Crossjoin(Descendants([Time].[2018], 3, SELF), [Scenario].Children) } ON COLUMNS,
       { Descendants([Sector Total], 1, SELF_AND_BEFORE) } ON ROWS
FROM App.Db
WHERE ([Currency].[NTD K])
```

**D. 只挑特定幾個成員**
```mdx
SELECT { [Scenario].[Actual], [Scenario].[Draft] } ON COLUMNS,
       { [Sector Total].Children } ON ROWS
FROM App.Db
WHERE ([Currency].[NTD K])
```

---

### 給模型的最終提醒
1. 一定先看 **§1 資料庫說明** 確認 cube 名與業務範圍。
2. 成員只用 **§3 Outline** 列出的；不確定就用 `Children`/`Descendants` 展開。
3. 產好 MDX → 呼叫 `run_sql_query`，放進 **`mdx`** 參數。
4. 結果會自動落地 CSV 交給前端樞紐；**不需要**再寫 Python。

<!--
參考來源（一律以 Oracle Essbase 官方文件為準；Essbase 是 Oracle 產品，MDX 為 Essbase 方言）：
- Oracle Essbase 21c《Calculation and Query Reference》— MDX Member Specification、Descendants、MDX Function List
- Oracle Essbase 21c《Database Administrator's Guide》— Write MDX Queries
- Oracle Essbase —《MDX Grammar Rules》
全部位於 docs.oracle.com/en/database/other-databases/essbase/
-->

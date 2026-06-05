"""
Mock MSSQL 財務資料庫模擬器
===========================
用 SQLite in-memory 模擬一台 Microsoft SQL Server，灌入 MSSQL 風格的
財務／審計 schema 與「假資料」。提供給 Agent 兩個能力：

1. get_schema()        → 回傳完整結構說明，讓模型知道「能查什麼」。
2. execute_sql(sql)    → 執行模型生成的 SQL（唯讀，只允許 SELECT/WITH），
                         回傳假資料。內含簡易 MSSQL→SQLite 方言轉換，
                         讓模型用熟悉的 T-SQL 語法也能跑。

設計理念：
- 純 Python 標準函式庫（sqlite3），無外部相依，可獨立測試。
- 與 agent.py 解耦：agent 只呼叫 get_schema()/execute_sql()，
  之後要換成真實 MSSQL 或真實 MCP server，只要替換這兩個函式即可。
"""

import re
import sqlite3
import threading
from typing import Any, Dict, List

# ============================================================
# Schema 說明（給模型看的「資料字典」）
# ============================================================
SCHEMA_DESCRIPTION = """\
【模擬資料庫：FinanceAuditDB（Microsoft SQL Server）】
這是一個財務審計資料庫，內含上市公司歷年財務數據。可用 T-SQL 語法查詢（唯讀，只允許 SELECT）。

─── 資料表 ───

▸ dbo.dim_company  （公司主檔）
    company_id      INT          公司代號（主鍵）
    company_name    NVARCHAR     公司名稱（如：宏遠科技）
    ticker          NVARCHAR     股票代號（如：2330）
    industry        NVARCHAR     產業別（半導體 / 電子零組件 / 金融）
    listed_market   NVARCHAR     上市市場（TWSE / TPEx）

▸ dbo.fact_financials  （年度財務數據，事實表）
    record_id           INT       流水號（主鍵）
    company_id          INT       對應 dim_company.company_id
    fiscal_year         INT       會計年度（2023 / 2024 / 2025）
    revenue             DECIMAL   營收（億元）
    gross_profit        DECIMAL   毛利（億元）
    operating_income    DECIMAL   營業利益（億元）
    net_income          DECIMAL   稅後淨利（億元）
    eps                 DECIMAL   每股盈餘（元）
    total_assets        DECIMAL   總資產（億元）
    total_equity        DECIMAL   股東權益（億元）
    operating_cashflow  DECIMAL   營業活動現金流（億元）

▸ dbo.fact_segment_revenue  （各事業部營收，事實表）
    company_id      INT       對應 dim_company.company_id
    fiscal_year     INT       會計年度
    segment_name    NVARCHAR   事業部名稱（晶圓代工 / 封裝測試 / IC設計 ...）
    revenue         DECIMAL   該事業部營收（億元）

─── 範例查詢 ───
1. 查某公司歷年營收與EPS：
   SELECT c.company_name, f.fiscal_year, f.revenue, f.eps
   FROM fact_financials f JOIN dim_company c ON f.company_id = c.company_id
   WHERE c.ticker = '2330' ORDER BY f.fiscal_year;

2. 查 2025 年營收前三高的公司：
   SELECT TOP 3 c.company_name, f.revenue
   FROM fact_financials f JOIN dim_company c ON f.company_id = c.company_id
   WHERE f.fiscal_year = 2025 ORDER BY f.revenue DESC;

3. 計算某公司毛利率：
   SELECT fiscal_year, gross_profit * 100.0 / revenue AS gross_margin_pct
   FROM fact_financials WHERE company_id = 1 ORDER BY fiscal_year;
"""

# ============================================================
# 假資料種子
# ============================================================
_COMPANIES = [
    # company_id, name, ticker, industry, market
    (1, "宏遠半導體", "2330", "半導體", "TWSE"),
    (2, "聯昇電子", "2454", "電子零組件", "TWSE"),
    (3, "富鼎金控", "2881", "金融", "TWSE"),
]

# company_id, year, revenue, gross_profit, op_income, net_income, eps, assets, equity, ocf
_FINANCIALS = [
    # 宏遠半導體（穩定成長）
    (1, 2023, 1500.0, 750.0, 520.0, 410.0, 3.20, 4200.0, 2600.0, 680.0),
    (1, 2024, 1800.0, 930.0, 650.0, 512.0, 3.95, 4800.0, 2950.0, 820.0),
    (1, 2025, 2100.0, 1110.0, 790.0, 624.0, 4.78, 5500.0, 3380.0, 970.0),
    # 聯昇電子（中速成長）
    (2, 2023, 880.0, 264.0, 150.0, 112.0, 2.10, 1900.0, 1050.0, 175.0),
    (2, 2024, 960.0, 297.0, 172.0, 130.0, 2.42, 2100.0, 1180.0, 205.0),
    (2, 2025, 1085.0, 347.0, 205.0, 158.0, 2.88, 2380.0, 1340.0, 240.0),
    # 富鼎金控（金融，毛利概念不同但仍填值）
    (3, 2023, 1200.0, 600.0, 430.0, 360.0, 1.85, 38000.0, 4200.0, 520.0),
    (3, 2024, 1320.0, 660.0, 470.0, 395.0, 2.02, 41000.0, 4500.0, 560.0),
    (3, 2025, 1410.0, 705.0, 505.0, 421.0, 2.16, 43500.0, 4780.0, 600.0),
]

# company_id, year, segment_name, revenue
_SEGMENTS = [
    (1, 2025, "晶圓代工", 1400.0), (1, 2025, "封裝測試", 480.0), (1, 2025, "IC設計", 220.0),
    (1, 2024, "晶圓代工", 1180.0), (1, 2024, "封裝測試", 430.0), (1, 2024, "IC設計", 190.0),
    (1, 2023, "晶圓代工", 980.0),  (1, 2023, "封裝測試", 360.0), (1, 2023, "IC設計", 160.0),
    (2, 2025, "被動元件", 720.0),  (2, 2025, "連接器", 365.0),
    (2, 2024, "被動元件", 640.0),  (2, 2024, "連接器", 320.0),
    (2, 2023, "被動元件", 590.0),  (2, 2023, "連接器", 290.0),
]

_DDL = """
CREATE TABLE dim_company (
    company_id    INTEGER PRIMARY KEY,
    company_name  TEXT,
    ticker        TEXT,
    industry      TEXT,
    listed_market TEXT
);
CREATE TABLE fact_financials (
    record_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id         INTEGER,
    fiscal_year        INTEGER,
    revenue            REAL,
    gross_profit       REAL,
    operating_income   REAL,
    net_income         REAL,
    eps                REAL,
    total_assets       REAL,
    total_equity       REAL,
    operating_cashflow REAL
);
CREATE TABLE fact_segment_revenue (
    company_id   INTEGER,
    fiscal_year  INTEGER,
    segment_name TEXT,
    revenue      REAL
);
"""

# ============================================================
# 建庫（單例、執行緒安全）
# ============================================================
_conn: sqlite3.Connection = None
_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.executescript(_DDL)
        conn.executemany(
            "INSERT INTO dim_company VALUES (?,?,?,?,?)", _COMPANIES
        )
        conn.executemany(
            "INSERT INTO fact_financials "
            "(company_id,fiscal_year,revenue,gross_profit,operating_income,"
            "net_income,eps,total_assets,total_equity,operating_cashflow) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", _FINANCIALS
        )
        conn.executemany(
            "INSERT INTO fact_segment_revenue VALUES (?,?,?,?)", _SEGMENTS
        )
        conn.commit()
        _conn = conn
        return _conn


# ============================================================
# 安全檢查 + MSSQL→SQLite 方言轉換
# ============================================================
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|"
    r"MERGE|GRANT|REVOKE|EXEC|EXECUTE|ATTACH|PRAGMA)\b",
    re.IGNORECASE,
)


def _is_read_only(sql: str) -> bool:
    s = sql.strip().lstrip("(").lstrip().upper()
    if not (s.startswith("SELECT") or s.startswith("WITH")):
        return False
    if _FORBIDDEN.search(sql):
        return False
    # 不允許多語句（避免 stacked queries）
    if ";" in sql.strip().rstrip(";"):
        return False
    return True


def _translate_mssql(sql: str) -> str:
    """把常見 T-SQL 語法轉成 SQLite 等價寫法，提升相容性。"""
    s = sql.strip().rstrip(";")
    # 移除 dbo. / 中括號識別字
    s = re.sub(r"\bdbo\.", "", s, flags=re.IGNORECASE)
    s = s.replace("[", "").replace("]", "")
    # SELECT TOP n ...  →  ... LIMIT n
    m = re.search(r"select\s+top\s+(\d+)\s+", s, flags=re.IGNORECASE)
    if m:
        n = m.group(1)
        s = re.sub(r"select\s+top\s+\d+\s+", "SELECT ", s, count=1, flags=re.IGNORECASE)
        if re.search(r"\blimit\b", s, flags=re.IGNORECASE) is None:
            s = f"{s} LIMIT {n}"
    # 常見函式對應
    s = re.sub(r"\bISNULL\s*\(", "IFNULL(", s, flags=re.IGNORECASE)
    s = re.sub(r"\bLEN\s*\(", "LENGTH(", s, flags=re.IGNORECASE)
    s = re.sub(r"\bGETDATE\s*\(\s*\)", "date('now')", s, flags=re.IGNORECASE)
    s = re.sub(r"\bNVARCHAR\b", "TEXT", s, flags=re.IGNORECASE)
    return s


# ============================================================
# 對外 API
# ============================================================
def get_schema() -> str:
    """回傳資料庫結構說明（給模型決定怎麼查）。"""
    return SCHEMA_DESCRIPTION


def execute_sql(sql: str, max_rows: int = 200) -> Dict[str, Any]:
    """
    執行唯讀 SQL，回傳：
      成功 → {"ok": True, "columns": [...], "rows": [[...]], "rowcount": n,
              "translated_sql": "..."}
      失敗 → {"ok": False, "error": "..."}
    """
    if not sql or not sql.strip():
        return {"ok": False, "error": "SQL 為空。"}

    if not _is_read_only(sql):
        return {"ok": False,
                "error": "僅允許單一 SELECT/WITH 唯讀查詢，且不得包含資料異動或多語句。"}

    translated = _translate_mssql(sql)
    try:
        conn = _get_conn()
        cur = conn.execute(translated)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows)
        return {
            "ok": True,
            "columns": columns,
            "rows": [list(r) for r in rows],
            "rowcount": len(rows),
            "translated_sql": translated,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "translated_sql": translated}


def format_result(result: Dict[str, Any]) -> str:
    """把 execute_sql 的結果格式化成易讀字串（Markdown 表格 + JSON）。"""
    if not result.get("ok"):
        return (f"❌ SQL 執行失敗：{result.get('error')}\n"
                f"提示：請先呼叫 get_database_schema 確認表格與欄位名稱，再修正 SQL。")

    columns: List[str] = result["columns"]
    rows: List[list] = result["rows"]
    if not rows:
        return "✅ 查詢成功，但沒有符合條件的資料（0 筆）。"

    # Markdown 表格
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = "\n".join(
        "| " + " | ".join("" if v is None else str(v) for v in row) + " |"
        for row in rows
    )
    md = f"{header}\n{sep}\n{body}"

    # 同時附 JSON，方便後續傳給 run_python_code 畫圖
    import json
    records = [dict(zip(columns, row)) for row in rows]
    json_block = json.dumps(records, ensure_ascii=False)

    return (f"✅ 查詢成功，共 {result['rowcount']} 筆：\n\n{md}\n\n"
            f"（JSON 供繪圖／後續處理使用）\n```json\n{json_block}\n```")


# ============================================================
# 自我測試
# ============================================================
if __name__ == "__main__":
    print("=== Schema ===")
    print(get_schema()[:300], "...\n")

    tests = [
        "SELECT c.company_name, f.fiscal_year, f.revenue, f.eps "
        "FROM dbo.fact_financials f JOIN dbo.dim_company c "
        "ON f.company_id=c.company_id WHERE c.ticker='2330' ORDER BY f.fiscal_year;",
        "SELECT TOP 2 company_name, ticker FROM dim_company ORDER BY company_id;",
        "DELETE FROM dim_company;",  # 應被擋
        "SELECT * FROM not_a_table;",  # 應報錯
    ]
    for q in tests:
        print(f"\n>>> {q}")
        print(format_result(execute_sql(q)))

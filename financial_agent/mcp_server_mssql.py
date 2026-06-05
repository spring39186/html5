"""
（選用）Mock MSSQL 的 MCP Server 包裝
=====================================
把 mock_db.py 的同一套假資料庫，包成標準 MCP（Model Context Protocol）server，
讓 Claude Desktop / Cursor / VS Code 等 MCP host，或任何 MCP client 都能連上查詢。

→ 你現有的 Streamlit + Ollama agent「不需要」這支也能運作（它直接呼叫 mock_db）。
   這支是為了「透過 MCP 查詢」這條路而備，與 function-calling 共用同一個引擎。

安裝與啟動：
    pip install "mcp[cli]"
    python mcp_server_mssql.py          # 以 stdio 傳輸啟動

在 Claude Desktop 設定檔（claude_desktop_config.json）註冊：
    {
      "mcpServers": {
        "mock-mssql-finance": {
          "command": "python",
          "args": ["/絕對路徑/financial_agent/mcp_server_mssql.py"]
        }
      }
    }

提供的 MCP 工具：
    - get_database_schema()      取得資料庫結構說明
    - run_sql_query(sql)         執行唯讀 SELECT，回傳假資料
"""

import mock_db

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:  # noqa: BLE001
    raise SystemExit(
        "需要先安裝 MCP SDK：pip install \"mcp[cli]\"\n"
        f"（原始錯誤：{e}）"
    )

mcp = FastMCP("mock-mssql-finance")


@mcp.tool()
def get_database_schema() -> str:
    """取得歷史財務資料庫(Microsoft SQL Server)的結構說明：表、欄位、範例查詢。
    要查資料庫前，先呼叫此工具了解可查詢的內容，再產生 SQL。"""
    return mock_db.get_schema()


@mcp.tool()
def run_sql_query(sql: str) -> str:
    """在歷史財務資料庫執行唯讀 SQL（只允許單一 SELECT/WITH，支援 T-SQL 語法），
    回傳查詢結果（Markdown 表格 + JSON）。

    Args:
        sql: 要執行的 SELECT 查詢。表名/欄位請依 get_database_schema。
    """
    return mock_db.format_result(mock_db.execute_sql(sql))


if __name__ == "__main__":
    mcp.run()  # 預設 stdio 傳輸

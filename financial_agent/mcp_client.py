"""
MCP Client Bridge（讓 agent 當 MCP client）
===========================================
把一個 MCP server（stdio 傳輸）連起來，並提供「同步」介面給 agent 使用：

- list_openai_tools() → 把 MCP 工具轉成 OpenAI function-calling 的 tools 格式，
                         可直接併入 agent 的 AGENT_TOOLS 丟給 Ollama 模型。
- tool_names()        → 這個 server 提供哪些工具名稱（用來判斷該不該走 MCP）。
- call_tool(name,args)→ 呼叫 MCP 工具，回傳純文字結果。

實作重點：
- MCP Python SDK 是 async，agent 迴圈是 sync。這裡在背景執行緒跑一個常駐
  event loop，連線與 session 保持開啟，呼叫時用 run_coroutine_threadsafe 橋接，
  避免每次查詢都重啟 server 程序。
- 連線參數由 config 提供（command + args），所以可指向本地 mock server，
  也可指向任何真實 MSSQL MCP server，不改 agent 程式碼。
"""

import asyncio
import threading
from typing import Any, Dict, List, Optional


class MCPClientBridge:
    def __init__(self, command: str, args: List[str]):
        self.command = command
        self.args = args
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session = None
        self._stack = None
        self._tools: list = []
        self._ready = threading.Event()
        self._error: Optional[str] = None

    # ---------- 啟動 / 連線 ----------
    def start(self, timeout: float = 30.0) -> bool:
        """啟動背景 loop 並連線。成功回傳 True，失敗回傳 False（self.error 有原因）。"""
        if self._thread is not None:
            return self._session is not None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            self._error = self._error or "連線逾時"
            return False
        return self._session is not None

    @property
    def error(self) -> Optional[str]:
        return self._error

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect())
        except Exception as e:  # noqa: BLE001
            self._error = f"{type(e).__name__}: {e}"
            self._ready.set()
            return
        # 連線成功後維持 loop 運轉，等待後續 call_tool
        self._loop.run_forever()

    async def _connect(self) -> None:
        from contextlib import AsyncExitStack
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        params = StdioServerParameters(command=self.command, args=self.args)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        resp = await self._session.list_tools()
        self._tools = list(resp.tools)
        self._ready.set()

    # ---------- 查詢工具清單 ----------
    def tool_names(self) -> set:
        return {t.name for t in self._tools}

    def list_openai_tools(self) -> List[dict]:
        tools = []
        for t in self._tools:
            schema = getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
            tools.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": (t.description or "").strip(),
                    "parameters": schema,
                },
            })
        return tools

    def _allowed_keys(self, name: str) -> Optional[set]:
        for t in self._tools:
            if t.name == name:
                schema = getattr(t, "inputSchema", None) or {}
                props = schema.get("properties") or {}
                return set(props.keys())
        return None

    # ---------- 呼叫工具 ----------
    def call_tool(self, name: str, args: Dict[str, Any], timeout: float = 60.0) -> str:
        if self._session is None or self._loop is None:
            return f"❌ MCP 尚未連線（{self._error or '未啟動'}）"

        # 只保留該工具 schema 接受的參數（例如濾掉 agent 自加的 thought_process）
        allowed = self._allowed_keys(name)
        call_args = {k: v for k, v in args.items() if allowed is None or k in allowed}

        fut = asyncio.run_coroutine_threadsafe(
            self._call(name, call_args), self._loop
        )
        try:
            return fut.result(timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return f"❌ MCP 工具呼叫失敗（{name}）：{type(e).__name__}: {e}"

    async def _call(self, name: str, args: Dict[str, Any]) -> str:
        result = await self._session.call_tool(name, args)
        parts = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            parts.append(text if text is not None else str(block))
        return "\n".join(parts) if parts else "（MCP 工具無回傳內容）"

    # ---------- 關閉 ----------
    def close(self) -> None:
        if self._loop and self._stack:
            async def _shutdown():
                await self._stack.aclose()
            try:
                asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)


# ---------- 模組級單例（跨 Streamlit rerun 重用同一條連線）----------
_bridge: Optional[MCPClientBridge] = None


def get_bridge(command: str, args: List[str]) -> MCPClientBridge:
    global _bridge
    if _bridge is None:
        _bridge = MCPClientBridge(command, args)
        _bridge.start()
    return _bridge

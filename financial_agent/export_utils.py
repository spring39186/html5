"""
對話記錄與 Agent 思考流程匯出工具
==================================
把整段對話（含每一輪的規劃、路由、工具呼叫、思考過程、計時）
匯出成兩種格式：

- JSON：完整結構化資料，適合程式化分析 / 比較不同版本表現。
- Markdown：人類可讀的逐輪報告，適合直接閱讀、找出可優化點。

每則 assistant 訊息預期帶有：
    content, route, planning_result, thought_logs, trace, tables(數量), images
"""

import json
from datetime import datetime, timezone
from typing import List, Dict, Any


def build_export_payload(messages: List[Dict[str, Any]], meta: Dict[str, Any] = None) -> dict:
    """組裝可序列化的完整對話 payload（過濾掉無法 JSON 化的物件）。"""
    clean_messages = []
    for m in messages:
        entry = {"role": m.get("role"), "content": m.get("content", "")}
        if m.get("role") == "assistant":
            entry.update({
                "route": m.get("route", ""),
                "planning_result": m.get("planning_result"),
                "thought_logs": m.get("thought_logs", []),
                "trace": m.get("trace", []),
                "table_count": len(m.get("tables", []) or []),
                "images": m.get("images", []),
            })
        clean_messages.append(entry)
    return {
        "exported_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "meta": meta or {},
        "turns": _count_turns(messages),
        "messages": clean_messages,
    }


def to_json(messages: List[Dict[str, Any]], meta: Dict[str, Any] = None) -> str:
    # default=str 保證任何非標準型別都能序列化，匯出永不因單筆資料而失敗
    return json.dumps(build_export_payload(messages, meta),
                      ensure_ascii=False, indent=2, default=str)


def _count_turns(messages: List[Dict[str, Any]]) -> int:
    return sum(1 for m in messages if m.get("role") == "user")


def _json_block(value) -> str:
    """把任意值轉成可讀 JSON 字串（失敗回退 str），給 trace 區塊顯示用。"""
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:  # noqa: BLE001
        return str(value)


def _fmt_trace_event(ev: dict) -> str:
    """把單一 trace 事件格式化成 markdown 一段。"""
    phase = ev.get("phase", "?")
    ts = ev.get("ts", "")
    head = f"- **[{phase}]** `{ts}`"
    parts = [head]

    def _sec(ms):
        try:
            return f"{float(ms) / 1000:.2f} s"
        except Exception:  # noqa: BLE001
            return f"{ms} ms"

    if "duration_ms" in ev:
        parts[0] += f" — ⏱ {_sec(ev['duration_ms'])}"
    if "total_ms" in ev:
        parts[0] += f" — ⏱ 總計 {_sec(ev['total_ms'])}"

    for key in ("route", "intent", "confidence", "model", "first_tool", "tool", "step"):
        if key in ev:
            parts.append(f"  - {key}: `{ev[key]}`")
    if ev.get("reasoning"):
        parts.append(f"  - 推理: {ev['reasoning']}")
    if ev.get("steps"):
        parts.append(f"  - 步驟: {' → '.join(str(s) for s in ev['steps'])}")
    if ev.get("thought"):
        parts.append(f"  - 思考: {ev['thought']}")
    if ev.get("args"):
        try:
            parts.append(f"  - 參數: `{json.dumps(ev['args'], ensure_ascii=False, default=str)}`")
        except Exception:  # noqa: BLE001
            parts.append(f"  - 參數: `{ev['args']}`")
    if ev.get("result_preview"):
        parts.append(f"  - 結果: {ev['result_preview']}")
    if ev.get("extracted"):
        parts.append(f"  - 抽數結果（原始 → 換算後）:\n\n```json\n{_json_block(ev['extracted'])}\n```")
    if ev.get("corrections"):
        parts.append("  - ⚠️ 健全性校正（利益 > 營收，多為 10x 單位錯）:"
                     f"\n\n```json\n{_json_block(ev['corrections'])}\n```")
    if ev.get("evidence_preview"):
        parts.append(f"  - 證據預覽（實際餵給抽數的內容）:\n\n```\n{ev['evidence_preview']}\n```")
    if ev.get("code"):
        parts.append(f"  - 生成程式碼:\n\n```python\n{ev['code']}\n```")
    if ev.get("output"):
        parts.append(f"  - 輸出: {ev['output']}")
    if ev.get("error"):
        et = ev.get("error_type", "")
        parts.append(f"  - ❌ 錯誤{f'（{et}）' if et else ''}: {ev['error']}")
    if ev.get("traceback"):
        parts.append(f"  - 例外堆疊（定位來源）:\n\n```\n{ev['traceback']}\n```")
    return "\n".join(parts)


def to_markdown(messages: List[Dict[str, Any]], meta: Dict[str, Any] = None) -> str:
    """人類可讀的逐輪報告。"""
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    lines = [
        "# AI 財務助手 對話記錄與思考流程",
        f"\n匯出時間：{now}  ｜  對話輪數：{_count_turns(messages)}",
    ]
    if meta:
        lines.append(f"\n模型配置：`{json.dumps(meta, ensure_ascii=False)}`")
    lines.append("\n---\n")

    turn = 0
    for m in messages:
        role = m.get("role")
        if role == "user":
            turn += 1
            lines.append(f"## 第 {turn} 輪")
            lines.append(f"\n**🧑 使用者：**\n\n{m.get('content', '')}\n")
        elif role == "assistant":
            lines.append(f"**🤖 助手回覆：**\n\n{m.get('content', '')}\n")

            if m.get("route"):
                lines.append(f"> 路由：`{m['route']}`")

            plan = m.get("planning_result")
            if plan:
                lines.append("\n**📋 規劃結果：**")
                lines.append(f"- 意圖：`{plan.get('intent')}` (信心 {plan.get('confidence')})")
                if plan.get("steps"):
                    lines.append(f"- 步驟：{' → '.join(plan['steps'])}")
                if plan.get("reasoning"):
                    lines.append(f"- 推理：{plan['reasoning']}")

            trace = m.get("trace", [])
            if trace:
                lines.append("\n<details><summary><b>🔍 完整執行軌跡（點開）</b></summary>\n")
                for ev in trace:
                    lines.append(_fmt_trace_event(ev))
                lines.append("\n</details>")

            lines.append("\n---\n")

    return "\n".join(lines)

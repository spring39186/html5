"""essbase_grid.py — Essbase /mdx JSON grid → tidy 長表 + 階層工具
================================================================================
把 Essbase「Run MDX Query」回傳的 JSON grid 攤平成一筆一格的長表（long table），
並提供用 essbase_outline_parent_child.csv 建階層（treemap / 樹狀表）的小工具。

**純 Python、不需 pandas**（`to_long_df()` 才會用到 pandas，且為選用）。
`essbase_pivot_app.py`（Streamlit）與之後要接的 agent 都可共用這支。

真實 /mdx 回應形狀（實測，與 Oracle 文件一致）：
    {
      "metadata": {"page":[dim,…], "column":[dim,…], "row":[dim,…]},
      "data": [ ["", …, <欄成員…>],          # data[0] = 欄標頭列（前面留空×列維數）
                [<列成員×列維數>, <值…>], … ] # data[1:] = 每一列：列成員 + 各欄的值
    }
例（單列維 Sector Total、單欄維 Currency）：
    data[0] = ["", "NTD K", "USD K"]
    data[1] = ["Sector Total", 100, 200]
"""

from __future__ import annotations

import csv as _csv
import os
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_CSV_NAME = "essbase_outline_parent_child.csv"
# parent/child 建檔預設在 repo 根（financial_agent 的上一層）；找不到再搜其他常見位置。
DEFAULT_PARENT_CHILD_CSV = os.path.normpath(os.path.join(_HERE, "..", _CSV_NAME))


def parent_child_csv_path(csv_path: str | None = None) -> str | None:
    """定位 parent/child 建檔：給定路徑優先，否則搜常見位置；找不到回 None。"""
    cands = [csv_path] if csv_path else []
    cands += [
        os.path.join(_HERE, "..", _CSV_NAME),        # repo 根（預設位置）
        os.path.join(_HERE, _CSV_NAME),              # 與本模組同層（financial_agent/）
        os.path.join(os.getcwd(), _CSV_NAME),        # 目前工作目錄
        os.path.join(os.getcwd(), "..", _CSV_NAME),  # 工作目錄上一層
    ]
    for p in cands:
        if p and os.path.exists(p):
            return os.path.normpath(p)
    return None


def _to_num(v: Any) -> float | None:
    """儲存格值正規化：#Missing/#NoAccess/空 → None；其餘轉 float（含 '1.14E8'）。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s == "" or s.startswith("#"):       # #Missing, #NoAccess, #Invalid…
        return None
    try:
        return float(s)
    except ValueError:
        return None                         # 純文字（value 欄通常不會出現）


def parse_mdx_grid(payload: dict) -> tuple[list[dict], dict]:
    """JSON grid → (records, meta)。

    records：長表，每筆 = {各列維名: 列成員, 欄維名: 欄成員, 'value': float|None}
    meta   ：{'row':[…], 'column':[…], 'page':[…]}（維度名稱）
    """
    meta_in = payload.get("metadata", {}) or {}
    row_dims = [str(d) for d in (meta_in.get("row") or [])]
    col_dims = [str(d) for d in (meta_in.get("column") or [])]
    page_dims = [str(d) for d in (meta_in.get("page") or [])]
    meta = {"row": row_dims, "column": col_dims, "page": page_dims}

    data = payload.get("data") or []
    n_row = len(row_dims)
    records: list[dict] = []
    if not data:
        return records, meta

    header = data[0]
    col_members = [str(x) for x in header[n_row:]]   # 去掉前面 n_row 個空格
    # 欄維名：單欄維就用該維名當欄位名，多欄維則統一叫 'Column'
    col_key = col_dims[0] if len(col_dims) == 1 else "Column"

    for drow in data[1:]:
        row_members = [str(x) for x in drow[:n_row]]
        values = drow[n_row:]
        base = {row_dims[i]: row_members[i] for i in range(n_row)}
        for j, cm in enumerate(col_members):
            rec = dict(base)
            rec[col_key] = cm
            rec["value"] = _to_num(values[j]) if j < len(values) else None
            records.append(rec)
    return records, meta


# ── 階層（parent/child）工具 ────────────────────────────────────────────────
def load_parent_map(csv_path: str | None = None,
                    dimension: str | None = None) -> dict[str, str]:
    """讀 parent_child 建檔 → {child: parent}（parent 為 '' 代表該維頂層）。

    dimension 有給就只取該維度（避免不同維度同名成員互相汙染）。
    檔案不存在則回空 dict（呼叫端自行降級成平面）。
    """
    path = parent_child_csv_path(csv_path)
    parent: dict[str, str] = {}
    if not path:
        return parent
    with open(path, encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            if dimension and (r.get("Dimension") or "").strip() != dimension:
                continue
            child = (r.get("Child") or "").strip()
            par = (r.get("Parent") or "").strip()
            if child and child not in parent:      # 先到先得，避免同名覆寫
                parent[child] = par
    return parent


def member_path(member: str, parent_map: dict[str, str]) -> list[str]:
    """回傳 root…member 的祖先路徑（含自己）。防環。"""
    chain = [member]
    seen = {member}
    cur = member
    while True:
        p = parent_map.get(cur, "")
        if not p or p in seen:
            break
        chain.append(p)
        seen.add(p)
        cur = p
    return list(reversed(chain))


def aggregate_values(values_by_member: dict[str, float | None],
                     parent_map: dict[str, str]) -> dict[str, float]:
    """把「在結果集裡」的成員，依 parent/child 由下往上加總。

    葉節點（在結果集中沒有任何子成員）用自己的值；內部節點 = 其子成員加總。
    這樣 treemap 用 branchvalues='total' 一定一致、必能畫出來（不依賴伺服器回的
    彙總值是否剛好等於子項和）。
    """
    present = set(values_by_member)
    children: dict[str, list[str]] = {}
    for m in present:
        p = parent_map.get(m, "")
        if p in present and p != m:
            children.setdefault(p, []).append(m)

    memo: dict[str, float] = {}

    def _val(m: str) -> float:
        if m in memo:
            return memo[m]
        memo[m] = 0.0                       # 佔位防環
        kids = children.get(m)
        if not kids:
            v = values_by_member.get(m) or 0.0
        else:
            v = sum(_val(k) for k in kids)
        memo[m] = float(v)
        return memo[m]

    return {m: _val(m) for m in present}


def to_long_df(payload: dict):
    """長表 DataFrame（需要 pandas）。df.attrs 帶 row/column/page 維度名。"""
    import pandas as pd  # 延後匯入：純解析不需 pandas

    records, meta = parse_mdx_grid(payload)
    df = pd.DataFrame.from_records(records)
    df.attrs.update(meta)
    return df

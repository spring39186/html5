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
import functools as _functools
import glob as _glob
import os
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_CSV_NAME = "essbase_outline_parent_child.csv"
# parent/child 建檔預設在 repo 根（financial_agent 的上一層）；找不到再搜其他常見位置。
DEFAULT_PARENT_CHILD_CSV = os.path.normpath(os.path.join(_HERE, "..", _CSV_NAME))


@_functools.lru_cache(maxsize=32)
def parent_child_csv_path(csv_path: str | None = None,
                          cube: str | None = None) -> str | None:
    """定位 parent/child 建檔（**每顆 cube 一份，檔名帶 cube**）。

    順序：明確 `csv_path` ＞ 該 cube 專屬檔 `essbase_outline_parent_child.<App.Db>.csv`
    ＞ 通用 `essbase_outline_parent_child.csv`（向下相容）。
    沒指定 cube 又找不到上述檔時，最後 glob 任一「顯名」檔（給 pivot demo / 測試用）。
    指定了 cube 卻沒有對應檔 → 回 None（不亂抓別顆 cube 的 outline）。
    """
    dirs = (_HERE, os.path.join(_HERE, ".."),          # financial_agent/、repo 根
            os.getcwd(), os.path.join(os.getcwd(), ".."))
    names: list[str] = []
    if cube and cube.strip():
        names.append(f"essbase_outline_parent_child.{cube.strip()}.csv")  # cube 專屬優先
    names.append(_CSV_NAME)                                                # 通用（向下相容）
    cands = [csv_path] if csv_path else []
    cands += [os.path.join(d, n) for n in names for d in dirs]
    for p in cands:
        if p and os.path.exists(p):
            return os.path.normpath(p)
    # 沒給 cube（pivot demo / 測試）→ 撿任一「顯名」outline 檔
    if not (cube and cube.strip()):
        for d in dirs:
            hits = sorted(_glob.glob(os.path.join(d, "essbase_outline_parent_child.*.csv")))
            if hits:
                return os.path.normpath(hits[0])
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

    # 欄標頭列數 = 欄維數（至少 1）。Crossjoin 多欄維時，data 前面數列都是標頭，
    # 每一列對應一個欄維、給出該欄維在每個輸出欄的成員。
    n_head = min(len(col_dims) or 1, len(data))
    col_headers = [row[n_row:] for row in data[:n_head]]      # 每欄維一列標頭（去掉前 n_row 空格）
    num_cols = min((len(h) for h in col_headers), default=0)
    # 每個輸出欄 = 跨各欄維的成員 tuple，例如 (2018Q1, Actual)
    col_tuples = [tuple(h[j] for h in col_headers) for j in range(num_cols)]

    for drow in data[n_head:]:
        row_members = [str(x) for x in drow[:n_row]]
        if any(not m.strip() for m in row_members):       # 空成員列＝結構雜訊，跳過
            continue
        values = drow[n_row:]
        base = {row_dims[i]: row_members[i] for i in range(n_row)}
        for j, ctup in enumerate(col_tuples):
            if any(not str(c).strip() for c in ctup):      # 空成員欄（屬性維常見）→ 前端的 'null'，跳過
                continue
            rec = dict(base)
            if col_dims:
                for d, dim in enumerate(col_dims):
                    rec[dim] = str(ctup[d]) if d < len(ctup) else ""
            else:
                rec["Column"] = str(ctup[0]) if ctup else ""
            rec["value"] = _to_num(values[j]) if j < len(values) else None
            records.append(rec)
    return records, meta


# ── 階層（parent/child）工具 ────────────────────────────────────────────────
def load_parent_map(csv_path: str | None = None,
                    dimension: str | None = None,
                    cube: str | None = None) -> dict[str, str]:
    """讀 parent_child 建檔 → {child: parent}（parent 為 '' 代表該維頂層）。

    dimension 有給就只取該維度（避免不同維度同名成員互相汙染）。
    檔案不存在則回空 dict（呼叫端自行降級成平面）。
    """
    path = parent_child_csv_path(csv_path, cube=cube)
    parent: dict[str, str] = {}
    if not path:
        return parent
    for dim, child, par in _read_parent_child_rows(path):
        if dimension and dim != dimension:
            continue
        if child and child not in parent:          # 先到先得，避免同名覆寫
            parent[child] = par
    return parent


@_functools.lru_cache(maxsize=16)
def _read_parent_child_rows(path: str) -> tuple[tuple[str, str, str], ...]:
    """讀 parent_child CSV → ((Dimension, Child, Parent), …)，以 path 快取避免每次查詢重讀檔。
    （outline 在單次行程內視為固定；換 cube／重抽後重啟即可。）"""
    with open(path, encoding="utf-8") as f:
        return tuple(((r.get("Dimension") or "").strip(),
                      (r.get("Child") or "").strip(),
                      (r.get("Parent") or "").strip())
                     for r in _csv.DictReader(f))


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


def measure_is_additive(name) -> bool:
    """Measure（Accounts）維成員可否跨成員加總（Sum）。

    依 essbase_outline.*.md 的 Measure 成員與 formula 歸納：
    - 名稱含 ``%`` → 比率／百分比（如 ``Current %``、``C/L %``）→ **不可加總**。
    - 以 ``_D`` 結尾 → 分母／總計參照（如 ``Current_D = (Sector Total, Current)``，
      每列都等於總計）→ **不可加總**。
    - 其餘（``Current``/``OP``/金額、``Current YTD``、``C/L Variance`` 等差額）→ 可加總。

    給前端決定父層／樞紐預設要 Sum 還是 Average，避免把 % 亂加成垃圾數字。
    純名稱判斷，換 cube 時若新增比率成員，照同規則命名即可自動涵蓋。"""
    n = str(name).strip()
    if not n:
        return True
    return ("%" not in n) and (not n.endswith("_D"))


def outline_summary(csv_path: str | None = None, max_children: int = 12,
                    cube: str | None = None) -> str:
    """從 parent/child 建檔產生「維度 → 頂層成員 + 直接子成員 + 成員數」的精簡 markdown 摘要。

    給 agent 的 get_database_schema 動態帶入：**outline 每顆 cube 不同**，所以即時讀檔、
    不寫死在指南裡。換 cube 時重抽 essbase_outline_parent_child.csv 即可自動跟著變。
    找不到檔回空字串。
    """
    import collections

    path = parent_child_csv_path(csv_path, cube=cube)
    if not path:
        return ""
    dims: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
    with open(path, encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            dim = (r.get("Dimension") or "").strip()
            child = (r.get("Child") or "").strip()
            parent = (r.get("Parent") or "").strip()
            if not dim or not child:
                continue
            d = dims.setdefault(dim, {"members": set(),
                                      "kids": collections.defaultdict(list),
                                      "roots": []})
            d["members"].add(child)
            if parent:
                d["kids"][parent].append(child)
            else:
                d["roots"].append(child)

    lines = []
    for dim, d in dims.items():
        top = d["roots"][0] if d["roots"] else dim
        children = d["kids"].get(top, [])
        shown = ", ".join(children[:max_children])
        more = f" …(+{len(children) - max_children})" if len(children) > max_children else ""
        kids = f"：{shown}{more}" if shown else ""
        lines.append(f"- **{dim}**（{len(d['members'])} 個成員，頂層 `[{top}]`）{kids}")
    return "\n".join(lines)


def outline_dimensions(csv_path: str | None = None, cube: str | None = None) -> list[str]:
    """回傳該 cube 的維度名稱清單（依 parent/child 檔出現順序）。找不到回 []。
    給 agent 在 MDX 失敗時回提示「可用維度」，幫模型自我修正、別自創維度名。"""
    path = parent_child_csv_path(csv_path, cube=cube)
    if not path:
        return []
    seen: list[str] = []
    with open(path, encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            dim = (r.get("Dimension") or "").strip()
            if dim and dim not in seen:
                seen.append(dim)
    return seen


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

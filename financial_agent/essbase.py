"""
essbase.py — 把 Essbase 多維長表轉成「樞紐分析友善」結構
=======================================================
Teradata 撈出的明細是 Essbase OLAP 長表，維度被塞在複合/階層字串裡：

    CHILD_SITE_ORG = "[Site Org].[OtherH_Group].[IC_ATM_T].[Oth_Manufactors-Consol]"
    YEAR_MON       = "2014_Feb"
    CURC           = "USD K"

前端 PivotTableJS / AgGrid 直接吃這種字串，只會把整串當「單一類別」，
無法逐層下鑽，月份還會以字串排序（Apr < Feb < Jan…）。

本模組把這些維度「拆解成正規欄位」（只新增衍生欄、保留原始欄不破壞）：
  - CHILD_SITE_ORG 階層 [A].[B].[C] → ORG_L1 / ORG_L2 / …（逐層下鑽、群組）
  - PARENT_SITE_ORG → PARENT（去括號、取最末層，當乾淨列標籤）
  - YEAR_MON 'YYYY_Mon' → YEAR / MONTH / MONTH_NO（MONTH_NO 讓月份正確排序）
  - CURC 'USD K' → CURRENCY / UNIT（避免 USD/NTD 混加）

只依賴 pandas（專案既有相依），方便單獨測試與重用。
"""

from __future__ import annotations

import re
from typing import List

import pandas as pd

_MONTH_NO = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}

_MEMBER_RE = re.compile(r"\[([^\[\]]+)\]")


def split_members(value: object) -> List[str]:
    """把 Essbase 成員字串 '[A].[B].[C]' 拆成 ['A','B','C']；
    非中括號格式則回傳 [去空白後的原值]，空值回傳 []。"""
    if not isinstance(value, str) or not value.strip():
        return []
    found = _MEMBER_RE.findall(value)
    return found if found else [value.strip()]


def to_pivot_ready(df: "pd.DataFrame") -> "pd.DataFrame":
    """回傳「新增衍生維度欄」後的 DataFrame（原始欄一律保留，不破壞）。
    欄位不存在就略過該段轉換，對任意查詢結果都安全。"""
    out = df.copy()

    # 0) AMT 是金額「度量」，務必轉成數值。從 Teradata 來常是 Decimal/字串，
    #    一旦以文字落地，PivotTableJS / AgGrid 會把它當成「維度」而非可加總的值
    #    → 拖過去只變成欄位、只能算件數(Count)、不能 Sum。強制轉 numeric 根治。
    if "AMT" in out.columns:
        out["AMT"] = pd.to_numeric(out["AMT"], errors="coerce")

    # 1) 組織階層展開：CHILD_SITE_ORG → ORG_L1..ORG_Ln（前端逐層下鑽/群組的關鍵）
    if "CHILD_SITE_ORG" in out.columns:
        levels = out["CHILD_SITE_ORG"].map(split_members)
        depth = int(levels.map(len).max()) if len(levels) else 0
        for i in range(depth):
            out[f"ORG_L{i + 1}"] = levels.map(
                lambda parts, _i=i: parts[_i] if _i < len(parts) else None)

    # 2) 父組織：去括號取最末層，當乾淨的列標籤
    if "PARENT_SITE_ORG" in out.columns:
        out["PARENT"] = out["PARENT_SITE_ORG"].map(
            lambda v: (split_members(v) or [None])[-1])

    # 3) 時間維度：'YYYY_Mon' → YEAR / MONTH / MONTH_NO（MONTH_NO 供正確排序）
    if "YEAR_MON" in out.columns:
        ym = out["YEAR_MON"].astype(str).str.split("_", n=1, expand=True)
        out["YEAR"] = ym[0]
        if ym.shape[1] > 1:
            out["MONTH"] = ym[1]
            out["MONTH_NO"] = out["MONTH"].map(_MONTH_NO).astype("Int64")

    # 4) 幣別/單位：'USD K' → CURRENCY / UNIT（避免不同幣別被加總在一起）
    if "CURC" in out.columns:
        cu = out["CURC"].astype(str).str.split(n=1, expand=True)
        out["CURRENCY"] = cu[0]
        if cu.shape[1] > 1:
            out["UNIT"] = cu[1]

    # 5) 清 null：階層較淺的列在較深的 ORG_Ln（及其他衍生維度欄）會留下 None
    #    （ragged hierarchy）。前端／CSV 顯示一堆 'null'/'None' 很雜，
    #    一律以空字串取代——維度欄空白即可，不影響加總，也不誤刪任何列。
    derived_dims = [c for c in out.columns if c.startswith("ORG_L")]
    derived_dims += [c for c in ("PARENT", "MONTH", "CURRENCY", "UNIT") if c in out.columns]
    for c in derived_dims:
        out[c] = out[c].fillna("")

    return out

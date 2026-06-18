"""tests/test_essbase_grid.py
================================
Offline 測 essbase_grid（純 Python，不需 pandas / 不連網）：
  1. parse_mdx_grid：真實 grid 形狀 → 長表 records（含值正規化）
  2. load_parent_map：從 parent_child.csv 取 {child:parent}（限定維度）
  3. member_path：祖先路徑
  4. aggregate_values：由下往上加總（treemap branchvalues='total' 用）

執行：
    python3 tests/test_essbase_grid.py        # 自帶 runner
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from essbase_grid import (  # noqa: E402
    aggregate_values,
    load_parent_map,
    measure_is_additive,
    member_path,
    parse_mdx_grid,
    _to_num,
)

# 真實回應的縮小版（結構 1:1，數值改成乾淨好驗的）
SAMPLE = {
    "metadata": {
        "page": ["Time", "Measure", "Site Group", "Site Org", "Scenario", "Filings"],
        "column": ["Currency"],
        "row": ["Sector Total"],
    },
    "data": [
        ["", "NTD K", "USD K"],
        ["Sector Total", "100", "200"],
        ["Assy", "40", "#Missing"],
        ["Test", "60", "120"],
    ],
}


def test_parse_basic() -> None:
    recs, meta = parse_mdx_grid(SAMPLE)
    assert meta["row"] == ["Sector Total"]
    assert meta["column"] == ["Currency"]
    assert len(meta["page"]) == 6
    # 3 列成員 × 2 欄成員 = 6 筆
    assert len(recs) == 6
    assert set(recs[0].keys()) == {"Sector Total", "Currency", "value"}
    by = {(r["Sector Total"], r["Currency"]): r["value"] for r in recs}
    assert by[("Sector Total", "NTD K")] == 100.0
    assert by[("Sector Total", "USD K")] == 200.0
    assert by[("Assy", "NTD K")] == 40.0
    assert by[("Assy", "USD K")] is None          # #Missing → None
    assert by[("Test", "USD K")] == 120.0


def test_parse_empty_and_num() -> None:
    assert parse_mdx_grid({"metadata": {"row": ["X"]}, "data": []})[0] == []
    assert _to_num("#Missing") is None
    assert _to_num("") is None
    assert _to_num("1.1440808254845E8") == 114408082.54845
    assert _to_num("22181126201E9") == 22181126201e9
    assert _to_num(7) == 7.0


def test_parse_crossjoin_two_col_dims() -> None:
    # Crossjoin 讓欄軸有兩個維度 → data 前兩列都是標頭
    sim = {
        "metadata": {"page": ["Measure"], "column": ["Time", "Scenario"],
                     "row": ["Sector Total"]},
        "data": [
            ["", "2018Q1", "2018Q1", "2018Q2"],     # Time 標頭
            ["", "Actual", "Draft", "Actual"],       # Scenario 標頭
            ["Sector Total", "10", "11", "12"],
            ["Assy", "4", "5", "6"],
        ],
    }
    recs, meta = parse_mdx_grid(sim)
    assert meta["column"] == ["Time", "Scenario"]
    assert len(recs) == 6                            # 2 列成員 × 3 欄
    assert set(recs[0].keys()) == {"Sector Total", "Time", "Scenario", "value"}
    by = {(r["Sector Total"], r["Time"], r["Scenario"]): r["value"] for r in recs}
    assert by[("Sector Total", "2018Q1", "Actual")] == 10.0
    assert by[("Sector Total", "2018Q1", "Draft")] == 11.0
    assert by[("Assy", "2018Q2", "Actual")] == 6.0


def test_parent_map_and_path() -> None:
    pm = load_parent_map(dimension="Sector Total")
    # 至少要有頂層與第一層
    assert pm.get("Sector Total") == ""            # 頂層 → 空（root）
    assert pm.get("Assy") == "Sector Total"
    assert pm.get("AsLogic") == "Assy"             # 第二層也在
    assert member_path("Assy", pm) == ["Sector Total", "Assy"]
    assert member_path("AsLogic", pm) == ["Sector Total", "Assy", "AsLogic"]
    assert member_path("Sector Total", pm) == ["Sector Total"]


def test_parent_map_dimension_filter() -> None:
    # 限定維度後，不應撈到其他維度的成員
    pm = load_parent_map(dimension="Sector Total")
    assert "NTD K" not in pm                        # Currency 維的成員不該出現


def test_aggregate_values() -> None:
    pm = load_parent_map(dimension="Sector Total")
    # 只放頂層 + 兩個葉（NTD K 那欄）
    vbm = {"Sector Total": 999.0, "Assy": 40.0, "Test": 60.0}
    agg = aggregate_values(vbm, pm)
    # 頂層應 = 子項加總（40+60），不採用伺服器回的 999
    assert agg["Assy"] == 40.0
    assert agg["Test"] == 60.0
    assert agg["Sector Total"] == 100.0
    # 葉節點若沒有子成員在集合內，用自己的值
    assert aggregate_values({"Assy": 40.0}, pm)["Assy"] == 40.0


def test_aggregate_handles_missing_as_zero() -> None:
    pm = load_parent_map(dimension="Sector Total")
    agg = aggregate_values({"Sector Total": None, "Assy": None, "Test": 60.0}, pm)
    assert agg["Sector Total"] == 60.0              # None 視為 0 加總


def test_measure_is_additive() -> None:
    # 非加總：含 % 的比率、以 _D 結尾的分母參照
    for m in ("Current %", "C/L %", "C/S %", "CY/LY %", "C/D%", "C/F2 %",
              "Current_D", "CS_D", "CYLY_D", "CL_D"):
        assert measure_is_additive(m) is False, m
    # 可加總：金額、YTD、Variance 差額
    for m in ("Current", "OP", "Current YTD", "Last Period", "Same Period LY",
              "Last Year YTD", "C/L Variance", "Draft Variance", "FcstV2"):
        assert measure_is_additive(m) is True, m
    assert measure_is_additive("") is True            # 空名（未知）保守視為可加總
    # 與 outline 對齊：剛好 10 個非加總成員
    pm = load_parent_map(dimension="Measure")
    members = {m for m in (set(pm) | set(pm.values())) if m and m != "Measure"}
    assert sum(1 for m in members if not measure_is_additive(m)) == 10


# ── 自帶 runner ───────────────────────────────────────────────────────────
def _run() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())

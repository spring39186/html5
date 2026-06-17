"""tests/test_essbase_client.py
================================
Offline 測試 essbase_client（不連網、不需 requests）：
  1. URL 組裝（mdx_url / db_url / from_clause）
  2. 設定缺漏 → EssbaseError
  3. 成員 / tuple 名稱萃取的容錯
  4. 儲存格值正規化（#Missing→None、字串數字→float、物件取 value）
  5. mdx_to_long_df：grid → 長表（含「值前夾帶列標頭」與單一 page 兩種變體）
  6. MdxPreferences 預設值

執行：
    python3 tests/test_essbase_client.py      # 自帶 runner
    # 或 pytest tests/test_essbase_client.py
"""

from __future__ import annotations

import os
import sys

# 讓任意工作目錄都能 import 專案模組
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from essbase_client import (  # noqa: E402
    EssbaseClient,
    EssbaseError,
    MdxPreferences,
    _coerce_num,
    _member_name,
    _tuple_names,
    mdx_to_long_df,
    mdx_to_records,
)

try:
    import pandas as _pd  # noqa: F401
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False


def _client() -> EssbaseClient:
    """用顯式參數建 client（不依賴 .env / 環境變數）。"""
    return EssbaseClient(
        base_uri="https://host:9001/essbase/rest/v1/",  # 尾斜線應被去除
        app="FinApp", db="FinDb", user="u", password="p",
    )


def test_url_building() -> None:
    c = _client()
    assert c.base_uri == "https://host:9001/essbase/rest/v1"  # 去尾斜線
    assert c.mdx_url == "https://host:9001/essbase/rest/v1/applications/FinApp/databases/FinDb/mdx"
    assert c.db_url == "https://host:9001/essbase/rest/v1/applications/FinApp/databases/FinDb"
    assert c.from_clause == "FROM [FinApp].[FinDb]"


def test_missing_config_raises() -> None:
    try:
        EssbaseClient(base_uri="", app="", db="", user="", password="")
    except EssbaseError as e:
        msg = str(e)
        assert "FA_ESB_URI" in msg and "FA_ESB_PWD" in msg
    else:
        raise AssertionError("缺設定時應丟 EssbaseError")


def test_member_and_tuple_helpers() -> None:
    assert _member_name("Current") == "Current"
    assert _member_name({"memberName": "Current"}) == "Current"
    assert _member_name({"name": "OP"}) == "OP"
    assert _member_name({"uniqueName": "[Measure].[Current]"}) == "[Measure].[Current]"
    assert _tuple_names([{"memberName": "2024"}, {"memberName": "NTD K"}]) == ["2024", "NTD K"]
    assert _tuple_names("Current") == ["Current"]


def test_coerce_num() -> None:
    assert _coerce_num("#Missing") is None
    assert _coerce_num("") is None
    assert _coerce_num(None) is None
    assert _coerce_num("12.5") == 12.5
    assert _coerce_num(7) == 7.0
    assert _coerce_num({"value": 3}) == 3.0
    assert _coerce_num("text") == "text"


def test_mdx_to_records_basic() -> None:
    payload = {
        "metadata": {
            "page": [],
            "column": [[{"memberName": "Current"}], [{"memberName": "OP"}]],
            "row": [
                [{"memberName": "2024"}, {"memberName": "NTD K"}],
                [{"memberName": "2025"}, {"memberName": "NTD K"}],
            ],
        },
        "data": [
            [100.0, 110.0],
            [200.0, "#Missing"],
        ],
    }
    recs = mdx_to_records(payload)
    assert len(recs) == 4  # 2 row tuples × 2 col tuples
    assert list(recs[0].keys()) == ["Row1", "Row2", "Col1", "value"]
    assert {r["Col1"] for r in recs} == {"Current", "OP"}
    by = {(r["Row1"], r["Col1"]): r["value"] for r in recs}
    assert by[("2024", "Current")] == 100.0
    assert by[("2024", "OP")] == 110.0
    assert by[("2025", "Current")] == 200.0
    assert by[("2025", "OP")] is None       # #Missing → None
    assert all(r["Row2"] == "NTD K" for r in recs)


def test_mdx_to_records_row_header_and_page() -> None:
    # data 列在值前夾帶列標頭字串；且有單一 page tuple → 應出現 Page1 常數欄
    payload = {
        "metadata": {
            "page": [[{"memberName": "Actual"}]],
            "column": [[{"memberName": "Current"}]],
            "row": [[{"memberName": "2024"}]],
        },
        "data": [["2024", 999.0]],  # 前面夾帶 "2024" 標頭，值取最後 1 個
    }
    recs = mdx_to_records(payload)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["Row1"] == "2024"
    assert rec["Col1"] == "Current"
    assert rec["Page1"] == "Actual"
    assert rec["value"] == 999.0


def test_mdx_to_long_df_dataframe() -> None:
    if not _HAS_PANDAS:
        print("  (skip: pandas 未安裝)")
        return
    payload = {
        "metadata": {
            "column": [[{"memberName": "Current"}]],
            "row": [[{"memberName": "2024"}]],
        },
        "data": [[100.0]],
    }
    df = mdx_to_long_df(payload)
    assert list(df.columns) == ["Row1", "Col1", "value"]
    assert len(df) == 1
    assert df["value"].iloc[0] == 100.0


def test_preferences_default() -> None:
    d = MdxPreferences().to_dict()
    assert d["memberIdentifierType"] == "NAME"
    assert d["aliasTableName"] == "Default"
    assert d["dataless"] is False
    assert "query" not in d  # preferences 不含 query


# ── 自帶 runner（無 pytest 也能跑）────────────────────────────────────────
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

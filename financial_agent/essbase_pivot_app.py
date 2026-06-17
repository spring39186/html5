"""essbase_pivot_app.py — Essbase 多維度樞紐 / 階層報表（Streamlit）
================================================================================
把 Essbase「Run MDX Query」的回應，畫成主管看得懂的階層報表：
  • 🌳 Treemap / Sunburst / Icicle（Plotly，開源免授權，用 parent/child 建階層）
  • 📋 MultiIndex 樞紐表（pandas + st.dataframe，內建縮排階層）
  • 🌲（選用）AgGrid 可展開/收合樹狀表（treeData 為 AG Grid Enterprise 功能）

執行：
    streamlit run essbase_pivot_app.py

資料來源（側邊欄可切）：
    內建範例（離線即可看效果）  ←預設
    Essbase live（讀 financial_agent/.env 直接查；建議勾「原始數值」）

相依：streamlit、pandas、plotly（皆已列在 requirements.txt）；
     AgGrid 樹狀表額外需要 streamlit-aggrid（缺了會自動略過）。

備註：撈數/解析共用 essbase_grid.py 與 essbase_smoketest.py；之後接 agent 時，
      fetch_live() 可換成 EssbaseClient，解析仍走 essbase_grid.parse_mdx_grid()。
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

import pandas as pd
import plotly.express as px
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import essbase_grid as eg          # noqa: E402  解析 + 階層
import essbase_smoketest as smoke  # noqa: E402  重用 .env 載入 / build_body / raw fallback

try:
    from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
    _HAS_AGGRID = True
except Exception:                  # noqa: BLE001  缺套件就降級
    _HAS_AGGRID = False

# ── 內建範例：你實測抓到的真實回應（離線即可看 UI 效果）────────────────────
SAMPLE_RESPONSE = {
    "metadata": {
        "page": ["Time", "Measure", "Site Group", "Site Org", "Scenario", "Filings"],
        "column": ["Currency"],
        "row": ["Sector Total"],
    },
    "data": [
        ["", "NTD K", "USD K"],
        ["Sector Total", "22181126201E9", "2224319499238439E8"],
        ["Assy", "2267999997E9", "1.1440808254845E8"],
        ["Test", "222163213E8", "2228615009997E7"],
        ["Material", "2202636995998E7", "21997227620001"],
        ["EMS", "222248292662E9", "22260071671002E8"],
        ["Estate", "27135819E7", "55922233799999"],
        ["Other", "222039299997E7", "55922322"],
    ],
}

DEFAULT_MDX = (
    "SELECT { [Currency].[NTD K], [Currency].[USD K] } ON COLUMNS, "
    "{ Descendants([Sector Total], 1, SELF_AND_BEFORE) } ON ROWS "
    "FROM __APP__.__DB__"
)


@st.cache_data(show_spinner="向 Essbase 查詢中…")
def fetch_live(mdx: str, app: str, db: str, raw_values: bool = True) -> dict:
    """直接查 Essbase（重用 essbase_smoketest 的 .env 載入與 raw-socket 後援）。"""
    smoke.load_dotenv(os.path.join(os.getcwd(), ".env"), os.path.join(HERE, ".env"))
    base = smoke._cfg("FA_ESB_URI", "").rstrip("/")
    user = smoke._cfg("FA_ESB_USER", "")
    pwd = smoke._cfg("FA_ESB_PWD", "")
    verify = smoke._cfg("FA_ESB_VERIFY_TLS", "1").lower() not in ("0", "false", "no", "off")
    cafile = smoke._cfg("FA_ESB_CAFILE", "") or None
    if not (base and user and pwd):
        raise RuntimeError("尚未設定 FA_ESB_URI / FA_ESB_USER / FA_ESB_PWD（financial_agent/.env）")

    url = f"{base}/applications/{app}/databases/{db}/mdx?format=JSON"
    body = smoke.build_body(mdx, format_values=not raw_values)
    token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/octet-stream",
        "Authorization": f"Basic {token}",
    }
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif cafile:
        ctx.load_verify_locations(cafile=cafile)

    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
            text = r.read().decode("utf-8", "replace")
    except http.client.HTTPException:                  # 萬一伺服器回 HTTP/0.9 串流
        _s, _c, text = smoke.raw_http_request(url, headers, body,
                                              verify=verify, cafile=cafile)
    return json.loads(text)


def hierarchy_frame(sub: pd.DataFrame, row_dim: str, pmap: dict) -> pd.DataFrame:
    """給定已篩到「單一欄組合」的 sub，組出 treemap/sunburst 用的 (成員, 上層, 值)。"""
    vbm = dict(zip(sub[row_dim], sub["value"]))
    agg = eg.aggregate_values(vbm, pmap)             # 由下往上加總，保證一致可畫
    members = list(agg)
    return pd.DataFrame({
        "成員": members,
        "上層": [pmap.get(m, "") if pmap.get(m, "") in agg else "" for m in members],
        "值": [agg[m] for m in members],
    })


# ════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="Essbase 樞紐分析", layout="wide")
st.title("📊 Essbase 多維度樞紐分析")

# ── 側邊欄：資料來源 ────────────────────────────────────────────────────────
st.sidebar.header("資料來源")
mode = st.sidebar.radio("來源", ["內建範例（離線）", "Essbase live"], index=0)
app = st.sidebar.text_input("Application", value=os.environ.get("FA_ESB_APP", "VSalRPTH"))
db = st.sidebar.text_input("Database", value=os.environ.get("FA_ESB_DB", "SaleRPTA"))
mdx = st.sidebar.text_area(
    "MDX", height=150,
    value=DEFAULT_MDX.replace("__APP__", app).replace("__DB__", db))
raw_values = st.sidebar.checkbox("原始數值（formatValues=off，建議）", value=True)

if mode == "Essbase live":
    try:
        payload = fetch_live(mdx, app, db, raw_values)
        st.sidebar.success("已從 Essbase 取得資料")
    except Exception as e:                              # noqa: BLE001
        st.sidebar.error(f"查詢失敗：{e}")
        st.stop()
else:
    payload = SAMPLE_RESPONSE
    st.sidebar.info("目前用內建範例；要連線請切到「Essbase live」。")

# ── 解析成長表 ──────────────────────────────────────────────────────────────
records, meta = eg.parse_mdx_grid(payload)
if not records or not meta["row"]:
    st.warning("這個回應沒有列維度/資料列，無法畫階層報表。")
    st.json(payload)
    st.stop()

df = pd.DataFrame.from_records(records)
row_dim = meta["row"][0]
col_dims = list(meta["column"]) or ["Column"]   # 一或多個欄維（空則用解析器給的 "Column"）
_missing = [d for d in col_dims if d not in df.columns]
if _missing:
    st.error(
        f"解析結果缺欄位 {_missing} —— 多半是 **`essbase_grid.py` 版本過舊**"
        f"（多欄維 Crossjoin 沒被拆成各欄，被塞成單一 'Column'）。\n\n"
        f"請更新 `financial_agent/essbase_grid.py` 到最新版，並**完整重啟** `streamlit run`"
        f"（Ctrl+C 後重跑，不是只按 Rerun——Streamlit 不會自動重載已 import 的模組）。\n\n"
        f"目前 df 實際欄位：`{list(df.columns)}`")
    st.stop()
if len(meta["row"]) > 1:
    st.info(f"偵測到多個列維度 {meta['row']}；本頁階層先以第一個「{row_dim}」呈現。")
if len(col_dims) > 1:
    st.info(f"欄為多維 Crossjoin {col_dims}；下面每個欄維各有獨立篩選器。")

if meta["page"]:
    st.caption("🔒 固定維度（slicer，取頂層成員）： " + " · ".join(meta["page"]))

# ── 側邊欄：每個欄維各一個篩選器（多維 Crossjoin 也好操作）──
st.sidebar.header("欄維篩選")
view = df
for d in col_dims:
    opts = sorted(df[d].dropna().astype(str).unique().tolist())
    chosen = st.sidebar.multiselect(d, opts, default=opts, key=f"filt_{d}")
    if chosen:
        view = view[view[d].isin(chosen)]

pmap = eg.load_parent_map(dimension=row_dim)
if not pmap:
    st.warning(
        f"找不到階層建檔 `{eg._CSV_NAME}`，階層圖/表會退成平面。\n\n"
        f"它在 **repo 根目錄**（`financial_agent` 的上一層）。請確認有 `git pull` 整個 repo，"
        f"或直接把該檔放到 `{HERE}` 也可以。")

tab_viz, tab_tbl = st.tabs(["🌳 階層視覺化", "📋 樞紐表"])

# ── 🌳 視覺化 ───────────────────────────────────────────────────────────────
with tab_viz:
    st.markdown("**選每個欄維各一個成員，畫該「格」的部門階層：**")
    pick_cols = st.columns(len(col_dims) + 1)
    sub = view
    for i, d in enumerate(col_dims):
        opts = sorted(view[d].dropna().astype(str).unique().tolist())
        if opts:
            m = pick_cols[i].selectbox(d, opts, key=f"pick_{d}")
            sub = sub[sub[d] == m]
    chart = pick_cols[-1].radio("圖型", ["Treemap", "Sunburst", "Icicle"], horizontal=True)
    tdf = hierarchy_frame(sub, row_dim, pmap)
    if float(tdf["值"].fillna(0).abs().sum()) == 0:
        st.info("這個組合全是 #Missing／0，沒有可畫的值——換一個 Time／Scenario 組合試試。")
    else:
        plotter = {"Treemap": px.treemap, "Sunburst": px.sunburst, "Icicle": px.icicle}[chart]
        fig = plotter(tdf, names="成員", parents="上層", values="值", branchvalues="total")
        fig.update_traces(textinfo="label+value+percent parent")
        fig.update_layout(margin=dict(t=30, l=0, r=0, b=0), height=620)
        st.plotly_chart(fig, use_container_width=True)
    st.caption("數值由下往上加總（葉→父），階層比例一定一致。")

# ── 📋 樞紐表 ───────────────────────────────────────────────────────────────
with tab_tbl:
    # 用每個成員的祖先路徑做 MultiIndex → 內建縮排階層
    members = view[row_dim].unique().tolist()
    paths = {m: eg.member_path(m, pmap) for m in members}
    maxd = max((len(p) for p in paths.values()), default=1)
    rows_out = []
    for _, r in view.iterrows():
        levels = paths[r[row_dim]] + [""] * (maxd - len(paths[r[row_dim]]))
        rec = {f"L{i}": levels[i] for i in range(maxd)}
        for d in col_dims:
            rec[d] = r[d]
        rec["value"] = r["value"]
        rows_out.append(rec)
    pivot = (pd.DataFrame(rows_out)
             .pivot_table(index=[f"L{i}" for i in range(maxd)],
                          columns=col_dims, values="value", aggfunc="sum"))
    st.dataframe(pivot.style.format("{:,.0f}", na_rep="—"), use_container_width=True)

    if _HAS_AGGRID:
        with st.expander("🌲 AgGrid 可展開樹狀表（treeData 為 AG Grid Enterprise 功能）"):
            try:
                wide = view.pivot_table(index=row_dim, columns=col_dims,
                                        values="value", aggfunc="sum")
                wide.columns = ["｜".join(map(str, c)) if isinstance(c, tuple) else str(c)
                                for c in wide.columns]
                wide = wide.reset_index()
                # path 存成「分隔字串」(用 US 控制字元 \x1f)，JS 端再 split 回陣列；
                # 直接塞 list 會被序列化成字串，AgGrid 對它呼叫 .join() → "i.join is not a function"
                wide["path"] = wide[row_dim].map(
                    lambda m: chr(31).join(eg.member_path(m, pmap)))
                gob = GridOptionsBuilder.from_dataframe(wide.drop(columns=[row_dim, "path"]))
                opts = gob.build()
                opts["treeData"] = True
                opts["getDataPath"] = JsCode(
                    "function(d){return String(d.path).split(String.fromCharCode(31));}")
                opts["autoGroupColumnDef"] = {
                    "headerName": row_dim,
                    "cellRendererParams": {"suppressCount": True}}
                AgGrid(wide, gridOptions=opts, allow_unsafe_jscode=True,
                       enable_enterprise_modules=True, height=420)
            except Exception as e:                     # noqa: BLE001
                st.info(f"AgGrid 樹狀表略過：{e}")
    else:
        st.caption("想要可展開/收合的樹狀表：`pip install streamlit-aggrid`")

with st.expander("🔎 原始長表 / JSON（debug）"):
    st.dataframe(view, use_container_width=True)
    st.json(payload)

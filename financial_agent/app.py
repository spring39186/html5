"""
AI 財務智能審計與知識庫系統 - Streamlit 前端
=============================================
搭配合併優化版後端 agent.run_financial_agent。
"""

import os
from datetime import datetime

import streamlit as st

import pandas as pd
import streamlit.components.v1 as components

import agent as agent_mod
from agent import run_financial_agent
from config import MODEL_CONFIG
import export_utils

# 🚀 高階互動數據組件（樹狀網格 + 自由樞紐）；沒裝就安全降級成一般表格
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, ColumnsAutoSizeMode
    from pivottablejs import pivot_ui
    HAS_ADVANCED_GRID = True
except ImportError:
    HAS_ADVANCED_GRID = False


def _silence_windows_proactor_reset() -> None:
    """Windows 的 asyncio Proactor 在連線被對方強制關閉時，清理階段會噴
    ConnectionResetError（WinError 10054）的假錯誤堆疊——純連線收尾噪音，請求其實已完成、
    不影響功能。這裡只在 Windows 包一層把這個特定例外吞掉，讓 console 乾淨。"""
    import sys
    if not sys.platform.startswith("win"):
        return
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _orig = _ProactorBasePipeTransport._call_connection_lost

        def _quiet(self, exc):
            try:
                _orig(self, exc)
            except ConnectionResetError:
                pass  # 連線已被對方關閉，收尾噪音，忽略

        _ProactorBasePipeTransport._call_connection_lost = _quiet
    except Exception:  # noqa: BLE001  內部 API 變動就放著，純噪音、不影響功能
        pass


_silence_windows_proactor_reset()

st.set_page_config(page_title="AI 財務智能審計系統", layout="wide")
st.title("📊 AI 財務智能審計與知識庫系統")
st.markdown("結合 Vision 模型與多模型協作架構的動態財務分析平台")

# ------------------------------------------------------------
# 1. Session State
# ------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant",
         "content": "您好！我是您的 AI 財務審計助理。請上傳文件，或直接告訴我您想分析什麼？"}
    ]
if "file_registry" not in st.session_state:
    st.session_state.file_registry = {}
if "uploader_version" not in st.session_state:
    st.session_state.uploader_version = 0  # 清空知識庫時 +1，換 uploader 的 key 強制清空它

# ------------------------------------------------------------
# 2. 側邊欄：知識庫管理
# ------------------------------------------------------------
with st.sidebar:
    st.header("📂 知識庫管理")
    # uploader 帶版本 key：清空知識庫時換 key，Streamlit 會把它重置成空，
    # 否則 rerun 時殘留的上傳檔會把 file_registry 又塞回來（清不掉的主因）。
    uploaded_files = st.file_uploader(
        "上傳文件 (財報、合約、技術報告...)",
        type=["pdf", "txt", "docx"],
        accept_multiple_files=True,
        key=f"kb_uploader_{st.session_state.uploader_version}",
    )
    if uploaded_files:
        os.makedirs("temp_dir", exist_ok=True)
        new_registry = {}
        for uf in uploaded_files:
            temp_path = os.path.join("temp_dir", uf.name)
            with open(temp_path, "wb") as f:
                f.write(uf.getbuffer())
            new_registry[uf.name] = temp_path
        st.session_state.file_registry = new_registry
        st.success(f"✅ 已載入 {len(uploaded_files)} 個檔案")

    if st.session_state.file_registry:
        with st.expander("目前知識庫內容", expanded=True):
            for name in st.session_state.file_registry:
                st.write(f"📄 {name}")
        if st.button("🗑️ 清空知識庫", use_container_width=True,
                     help="移除所有已上傳檔案與其向量，徹底清空知識庫。"):
            import shutil
            removed = agent_mod.clear_knowledge_base()  # 清向量
            st.session_state.file_registry = {}
            shutil.rmtree("temp_dir", ignore_errors=True)  # 清暫存上傳檔
            st.session_state.uploader_version += 1  # 換 key → uploader 清空，rerun 不再塞回
            st.success(f"✅ 已清空知識庫（移除 {removed} 個向量區塊）")
            st.rerun()

    st.divider()
    st.markdown("### 💾 匯出對話與思考流程")
    # 用容器先佔位，實際按鈕在腳本最後才渲染——確保包含「本輪」最新對話
    # （否則 sidebar 先於底部聊天處理執行，下載內容會永遠少一輪）
    _export_slot = st.container()

    st.divider()
    st.markdown("### 🔧 模型配置")
    st.markdown(
        f"""
- **Planner**: `{MODEL_CONFIG.planner}` — 中文意圖分析 + 路由
- **Executor**: `{MODEL_CONFIG.executor}` — 工具決策
- **Coder**: `{MODEL_CONFIG.coder}` — 程式碼/翻譯
- **Vision**: `{MODEL_CONFIG.vision}` — PDF 解析
- **Chat**: `{MODEL_CONFIG.chat}` — 一般對話
"""
    )


def _render_plotly_jsons(jsons):
    """把 plotly JSON 字串還原成互動圖渲染。"""
    if not jsons:
        return
    try:
        import plotly.io as pio
    except Exception as e:  # noqa: BLE001  plotly 沒裝就明說，別讓整個區塊靜默掛掉
        st.warning(f"⚠️ 需要安裝 plotly 才能顯示互動圖（pip install plotly）：{e}")
        return
    for j in jsons:
        try:
            st.plotly_chart(pio.from_json(j), use_container_width=True)
        except Exception as e:  # noqa: BLE001
            st.warning(f"⚠️ Plotly 圖渲染失敗：{e}")


@st.cache_data(show_spinner=False)
def _load_db_csv(csv_path: str, mtime: float) -> pd.DataFrame:
    """讀 DB 快取 CSV，以 (路徑, mtime) 快取——st.tabs 每次 rerun 都會重跑所有頁籤、
    歷史每則 DB 訊息也會重渲染，沒快取會重複讀同一個檔。mtime 變動才重讀。"""
    return pd.read_csv(csv_path)


@st.cache_data(show_spinner=False)
def _pivot_html(csv_path: str, mtime: float) -> str:
    """產生 PivotTableJS HTML，以 (路徑, mtime) 快取——pivot_ui 建表很貴，
    避免每次 rerun 都為每則 DB 訊息重建一次。"""
    df = _load_db_csv(csv_path, mtime)
    tmp = os.path.join("temp_dir", f"_pivot_{abs(hash((csv_path, mtime)))}.html")
    os.makedirs("temp_dir", exist_ok=True)
    # 注意：pivottablejs.pivot_ui 的輸出參數叫 outfile_path（不是 outfile）。
    # 傳錯名會被 **kwargs 吃掉、實際寫到預設的 pivottablejs.html，導致下方 open(tmp) 找不到檔。
    pivot_ui(df, outfile_path=tmp)
    try:
        with open(tmp, "r", encoding="utf-8") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _render_essbase_aggrid(df: pd.DataFrame):
    """🚀 把 Essbase 格式資料用 AgGrid 做企業級樹狀群組展示（沒裝就退回一般表格）。"""
    st.markdown(f"📊 **資料庫完整數據明細（共 {len(df)} 筆紀錄）**")
    if not HAS_ADVANCED_GRID:
        st.dataframe(df, use_container_width=True)
        st.caption("💡 安裝 `streamlit-aggrid` 可解鎖多層級群組展開功能。")
        return

    gob = GridOptionsBuilder.from_dataframe(df)
    gob.configure_pagination(paginationAutoPageSize=False, paginationPageSize=15)
    gob.configure_side_bar()
    gob.configure_default_column(groupable=True, value=True, enableRowGroup=True,
                                 filter=True, sortable=True)
    # ⚡ 針對 Essbase 組織層級，預設啟用群組樹狀
    if "PARENT_SITE_ORG" in df.columns and "CHILD_SITE_ORG" in df.columns:
        gob.configure_column("PARENT_SITE_ORG", rowGroup=True, hide=True)
        gob.configure_column("CHILD_SITE_ORG", rowGroup=True, hide=True)
    AgGrid(df, gridOptions=gob.build(),
           columns_auto_size_mode=ColumnsAutoSizeMode.FIT_CONTENTS,
           theme="alpine", height=450)


def _render_essbase_pivotjs(csv_path: str, mtime: float):
    """🚀 把 Essbase 資料嵌入 PivotTableJS，還原 Excel 自由拖拉拽樞紐體驗。"""
    if not HAS_ADVANCED_GRID:
        st.warning("請先安裝 `streamlit-pivottablejs`（pip install streamlit-pivottablejs）。")
        return
    components.html(_pivot_html(csv_path, mtime), height=600, scrolling=True)


def _render_static_payload(payload: dict):
    """文字報告 + Plotly/figures/HTML 表格/圖片（不含 DB 樞紐 Tabs）。"""
    if payload.get("content"):
        # 清掉可能殘留的後端隱藏路徑標記，維持畫面純淨
        st.markdown(payload["content"].split(agent_mod._CSV_CACHE_MARKER)[0].rstrip())
    _render_plotly_jsons(payload.get("plotly_jsons"))
    for fig in payload.get("figures", []) or []:
        st.plotly_chart(fig, use_container_width=True)
    for html in payload.get("tables", []) or []:
        st.html(html)
    for img in payload.get("images", []) or []:
        if os.path.exists(img):
            st.image(img)


def _render_payload_with_tabs(payload: dict, msg_idx: int):
    """有 DB 大數據快取時，用 Tabs 把『AI 報告 / 企業級網格 / 自由樞紐』空間隔離；
    否則維持單一文字流。CSV 只讀一次，傳給兩個頁籤共用。"""
    csv_path = payload.get("csv_cache_path", "")
    if csv_path and os.path.exists(csv_path):
        mtime = os.path.getmtime(csv_path)
        df = _load_db_csv(csv_path, mtime)
        tab1, tab2, tab3 = st.tabs(
            ["🤖 AI 智能解讀報告", "🗂️ 企業級數據網格 (AgGrid)", "🔀 自由拖拉樞紐分析 (Excel UI)"])
        with tab1:
            _render_static_payload(payload)
        with tab2:
            _render_essbase_aggrid(df)
        with tab3:
            _render_essbase_pivotjs(csv_path, mtime)
    else:
        _render_static_payload(payload)


# ------------------------------------------------------------
# 3. 歷史對話
# ------------------------------------------------------------
for _idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        _render_payload_with_tabs(msg, _idx)

# ------------------------------------------------------------
# 4. 對話框 + Agent 觸發
# ------------------------------------------------------------
if prompt := st.chat_input("請輸入指令（分析財報、查詢趨勢、翻譯、畫圖或隨意聊聊）..."):
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        with st.status("🧠 Agent 思考中…", expanded=True) as status:
            steps_box = st.container()
            _steps: list[str] = []

            def _on_progress(msg: str):
                # 後端每個階段即時回報；更新狀態標題 + 累積步驟清單（讓使用者看到「在動」）
                _steps.append(msg)
                status.update(label=msg)
                steps_box.markdown("\n".join(f"- {s}" for s in _steps[-10:]))

            agent_mod.set_progress_hook(_on_progress)
            file_registry = st.session_state.get("file_registry", {})

            # 組裝對話歷史傳給後端（assistant 內容附上該輪實際執行的 SQL，
            # 這樣「把剛才的 SQL show 出來」之類的追問才答得出來）
            history = []
            for m in st.session_state.messages[:-1]:  # 不含本次 user 輸入
                content = m.get("content", "")
                if m.get("role") == "assistant" and m.get("executed_sql"):
                    content += "\n（本輪實際執行的 SQL：\n" + "\n".join(m["executed_sql"]) + "）"
                if content:
                    history.append({"role": m["role"], "content": content})

            try:
                result = run_financial_agent(
                    user_prompt=prompt, file_registry=file_registry, history=history)
            finally:
                agent_mod.set_progress_hook(None)  # 用完一定要解除，避免殘留 hook
            route = result.get("route", "")
            status.update(label=f"✅ 完成（路由: {route}）", state="complete", expanded=False)

        # 本輪產出（含 DB 大數據快取時自動解鎖 Tabs：報告 / AgGrid / 樞紐分析）
        _payload = {
            "content": result.get("report_text", ""),
            "plotly_jsons": result.get("plotly_jsons", []),
            "figures": result.get("figures", []),
            "tables": result.get("tables", []),
            "images": [img for img in result.get("images", []) if os.path.exists(img)],
            "csv_cache_path": result.get("csv_cache_path", ""),
        }
        _render_payload_with_tabs(_payload, len(st.session_state.messages))

        if result.get("thought_logs"):
            with st.expander("🔍 AI 推論與決策軌跡"):
                for log in result["thought_logs"]:
                    if isinstance(log, dict):
                        st.markdown(f"**步驟 {log['step']} ➜ `{log['tool']}`**")
                        st.info(log["thought"])
                    else:
                        st.write(log)

        st.session_state.messages.append({
            "role": "assistant",
            "content": result.get("report_text", ""),
            "figures": result.get("figures", []),
            "tables": result.get("tables", []),
            "images": result.get("images", []),
            # 以下供「下載對話與思考流程」使用
            "route": result.get("route", ""),
            "planning_result": result.get("planning_result"),
            "thought_logs": result.get("thought_logs", []),
            "executed_sql": result.get("executed_sql", []),
            "plotly_jsons": result.get("plotly_jsons", []),
            "trace": result.get("trace", []),
            "csv_cache_path": result.get("csv_cache_path", ""),
        })


# ------------------------------------------------------------
# 5. 匯出按鈕（在腳本最後才渲染到先前保留的 sidebar 容器，
#    確保下載內容包含本輪最新對話，不會落後一輪）
# ------------------------------------------------------------
with _export_slot:
    _meta = {
        "planner": MODEL_CONFIG.planner, "executor": MODEL_CONFIG.executor,
        "coder": MODEL_CONFIG.coder, "vision": MODEL_CONFIG.vision,
        "chat": MODEL_CONFIG.chat,
    }
    _has_history = any(m["role"] == "user" for m in st.session_state.messages)
    _stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        _json_data = export_utils.to_json(st.session_state.messages, _meta)
        _md_data = export_utils.to_markdown(st.session_state.messages, _meta)
    except Exception as e:  # noqa: BLE001
        _json_data = _md_data = f"匯出失敗：{e}"

    _cj, _cm = st.columns(2)
    with _cj:
        st.download_button("⬇️ JSON", data=_json_data,
                           file_name=f"agent_log_{_stamp}.json", mime="application/json",
                           use_container_width=True, disabled=not _has_history,
                           help="完整結構化資料：規劃、路由、工具呼叫、計時。")
    with _cm:
        st.download_button("⬇️ Markdown", data=_md_data,
                           file_name=f"agent_log_{_stamp}.md", mime="text/markdown",
                           use_container_width=True, disabled=not _has_history,
                           help="人類可讀的逐輪報告，含完整思考軌跡。")
    if st.button("🗑️ 清空對話", use_container_width=True, disabled=not _has_history):
        st.session_state.messages = st.session_state.messages[:1]
        st.rerun()

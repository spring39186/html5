"""
AI 財務智能審計與知識庫系統 - Streamlit 前端
=============================================
搭配合併優化版後端 agent.run_financial_agent。
"""

import os
from datetime import datetime

import streamlit as st

import agent as agent_mod
from agent import run_financial_agent
from config import MODEL_CONFIG
import export_utils


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


def _render_payload(payload: dict):
    """渲染一則 assistant 訊息的所有產出。"""
    if payload.get("content"):
        st.markdown(payload["content"])
    _render_plotly_jsons(payload.get("plotly_jsons"))
    for fig in payload.get("figures", []) or []:
        st.plotly_chart(fig, use_container_width=True)
    for html in payload.get("tables", []) or []:
        st.html(html)
    for img in payload.get("images", []) or []:
        if os.path.exists(img):
            st.image(img)


# ------------------------------------------------------------
# 3. 歷史對話
# ------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        _render_payload(msg)

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

        if result.get("report_text"):
            st.markdown(result["report_text"])

        if result.get("plotly_jsons"):
            st.divider()
            st.subheader("📈 互動式圖表")
            _render_plotly_jsons(result["plotly_jsons"])

        if result.get("figures"):
            st.divider()
            for fig in result["figures"]:
                st.plotly_chart(fig, use_container_width=True)

        if result.get("tables"):
            st.divider()
            for html in result["tables"]:
                st.html(html)

        valid_images = [img for img in result.get("images", []) if os.path.exists(img)]
        if valid_images:
            st.divider()
            st.subheader("🎨 AI 生成的圖表")
            st.image(valid_images[-1], caption="AI 最終版圖表")
            if len(valid_images) > 1:
                with st.expander("查看修正過程"):
                    for img in valid_images[:-1]:
                        st.image(img, caption="修正過程")

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

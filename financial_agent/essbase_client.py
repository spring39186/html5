"""essbase_client.py — Essbase REST API 用戶端（在資料庫上執行 MDX）
====================================================================
封裝 Oracle Essbase 21c REST 端點「Run MDX Query」：

    POST {base_uri}/applications/{app}/databases/{db}/mdx?format=JSON

連線參數一律從 .env / 環境變數讀（見 config.RuntimeConfig.esb_*）：

    FA_ESB_URI   REST 基底，例：https://host:9001/essbase/rest/v1
    FA_ESB_APP   application name
    FA_ESB_DB    database (cube) name
    FA_ESB_USER  user name
    FA_ESB_PWD   user password
    FA_ESB_VERIFY_TLS  自簽憑證可設 0（預設 1）
    FA_ESB_TIMEOUT     單次請求逾時秒數（預設 120）

設計：
- 認證走 HTTP Basic（帳密）。requests 採「延遲匯入」，所以只解析回應、
  或只組 URL / 檢查設定時，不需要安裝 requests。
- execute_mdx() 一律回傳原始 JSON dict（零失真）；query_to_dataframe() 再把
  grid 攤成長表，方便餵給既有的 essbase.to_pivot_ready() / 前端樞紐。

CLI 煙霧測試（填好 .env 後）：
    python essbase_client.py                       # 只做連線健檢 ping
    python essbase_client.py "SELECT {[Measure].[Current]} ON COLUMNS"
    python essbase_client.py "<MDX>" --raw         # 印原始 JSON
    python essbase_client.py "<MDX>" --csv out.csv # 長表存 CSV

Request / Response 形狀依 Oracle 文件：
- body = {"query": "<MDX>", "preferences": {...}}（FROM 子句此端點可省略）
- 回應(JSON) = {"metadata": {"page":[…],"column":[…],"row":[…]}, "data": [[…],…]}
  ⚠ 不同版本欄位名稱可能略異；mdx_to_long_df() 已做容錯，必要時據 raw JSON 微調。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

try:  # 允許在沒有專案設定（或單獨測試 parser）時匯入
    from config import RUNTIME as _RUNTIME
except Exception:  # noqa: BLE001
    _RUNTIME = None


class EssbaseError(RuntimeError):
    """Essbase REST 呼叫失敗（設定缺漏、連線、認證、HTTP 4xx/5xx、回應非預期）。"""


@dataclass
class MdxPreferences:
    """REST body 的 preferences 物件（預設取 Oracle 文件常見組合）。"""
    dataless: bool = False
    hideRestrictedData: bool = True
    cellAttributes: bool = False
    formatString: bool = True
    formatValues: bool = True
    meaninglessCells: bool = False
    textList: bool = True
    urlDrillThrough: bool = False
    memberIdentifierType: str = "NAME"   # NAME → 回成員名（非別名）
    aliasTableName: str = "Default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _cfg(attr: str, override: Optional[Any]) -> Any:
    """參數優先；否則回 RUNTIME.<attr>；都沒有回空字串。"""
    if override is not None:
        return override
    return getattr(_RUNTIME, attr, "") if _RUNTIME is not None else ""


class EssbaseClient:
    """Essbase 資料庫的 MDX 查詢用戶端。

    參數全部可省略 → 從 .env / 環境變數（config.RUNTIME）讀取。
    """

    def __init__(
        self,
        *,
        base_uri: Optional[str] = None,
        app: Optional[str] = None,
        db: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        verify_tls: Optional[bool] = None,
        timeout: Optional[int] = None,
        session: Any = None,
    ) -> None:
        self.base_uri = str(_cfg("esb_uri", base_uri) or "").rstrip("/")
        self.app = str(_cfg("esb_app", app) or "")
        self.db = str(_cfg("esb_db", db) or "")
        self._user = str(_cfg("esb_user", user) or "")
        self._password = str(_cfg("esb_pwd", password) or "")
        self.verify_tls = bool(_cfg("esb_verify_tls", verify_tls) if verify_tls is not None
                               else getattr(_RUNTIME, "esb_verify_tls", True))
        self.timeout = int(_cfg("esb_timeout", timeout) if timeout is not None
                           else getattr(_RUNTIME, "esb_timeout", 120))

        missing = [k for k, v in (
            ("FA_ESB_URI", self.base_uri), ("FA_ESB_APP", self.app),
            ("FA_ESB_DB", self.db), ("FA_ESB_USER", self._user),
            ("FA_ESB_PWD", self._password)) if not v]
        if missing:
            raise EssbaseError(
                "Essbase 連線未設定，缺少：" + ", ".join(missing)
                + "（請填入 .env 或環境變數）")

        self._session = session  # 延遲建立（見 _get_session）

    # ── URLs ────────────────────────────────────────────────────────────────
    @property
    def mdx_url(self) -> str:
        return f"{self.base_uri}/applications/{self.app}/databases/{self.db}/mdx"

    @property
    def db_url(self) -> str:
        return f"{self.base_uri}/applications/{self.app}/databases/{self.db}"

    @property
    def from_clause(self) -> str:
        """MDX FROM 子句；此端點可省略，保留給需明寫 cube 的查詢。"""
        return f"FROM [{self.app}].[{self.db}]"

    # ── session（延遲匯入 requests）────────────────────────────────────────
    def _get_session(self) -> Any:
        if self._session is None:
            try:
                import requests
            except ImportError as e:  # pragma: no cover
                raise EssbaseError("需要 requests 套件：pip install requests") from e
            s = requests.Session()
            s.auth = (self._user, self._password)  # HTTP Basic
            s.headers.update({"Content-Type": "application/json",
                              "Accept": "application/json"})
            self._session = s
        return self._session

    # ── 核心呼叫 ──────────────────────────────────────────────────────────
    def execute_mdx(
        self,
        query: str,
        *,
        preferences: Optional[MdxPreferences] = None,
        params: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        """POST 一段 MDX，回傳解析後的 JSON dict。任何失敗丟 EssbaseError。"""
        if not query or not query.strip():
            raise EssbaseError("MDX query 不可為空")
        sess = self._get_session()
        import requests  # 此時必可匯入（session 已建立）

        body = {"query": query.strip(),
                "preferences": (preferences or MdxPreferences()).to_dict()}
        q: Dict[str, str] = {"format": "JSON"}
        if params:
            q.update(params)
        try:
            resp = sess.post(self.mdx_url, params=q, data=json.dumps(body),
                             timeout=self.timeout, verify=self.verify_tls)
        except requests.RequestException as e:
            raise EssbaseError(f"連線 Essbase 失敗：{e}") from e

        if resp.status_code in (401, 403):
            raise EssbaseError(
                f"認證/授權失敗(HTTP {resp.status_code})：請確認 FA_ESB_USER / "
                f"FA_ESB_PWD 及該帳號對 {self.app}.{self.db} 的權限")
        if not resp.ok:
            raise EssbaseError(f"Essbase 回應 HTTP {resp.status_code}：{_short(resp.text)}")
        try:
            return resp.json()
        except ValueError as e:
            raise EssbaseError(f"Essbase 回應非 JSON：{_short(resp.text)}") from e

    def query_to_dataframe(self, query: str, **kw: Any):
        """執行 MDX 並把 grid 回應攤成長表 DataFrame。"""
        return mdx_to_long_df(self.execute_mdx(query, **kw))

    def ping(self) -> bool:
        """連線/認證健檢：GET app/db 端點，2xx 視為可用。"""
        sess = self._get_session()
        import requests
        try:
            r = sess.get(self.db_url, timeout=self.timeout, verify=self.verify_tls)
        except requests.RequestException as e:
            raise EssbaseError(f"連線 Essbase 失敗：{e}") from e
        if r.status_code in (401, 403):
            raise EssbaseError(f"認證/授權失敗(HTTP {r.status_code})：帳密或權限不正確")
        return r.ok


# ── 回應解析（grid → 長表）────────────────────────────────────────────────

def _short(text: str, n: int = 400) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:n] + ("…" if len(text) > n else "")


def _member_name(m: Any) -> str:
    """從成員（str 或 dict）取出名稱；容忍多種鍵名。"""
    if isinstance(m, str):
        return m
    if isinstance(m, Mapping):
        for k in ("memberName", "name", "member", "uniqueName", "value"):
            v = m.get(k)
            if v:
                return str(v)
    return str(m)


def _tuple_names(tpl: Any) -> List[str]:
    """把一個 axis tuple 轉成成員名稱清單。"""
    if isinstance(tpl, list):
        return [_member_name(m) for m in tpl]
    return [_member_name(tpl)]


def _coerce_num(v: Any) -> Any:
    """儲存格值正規化：#Missing→None、數字字串→float、cellAttributes 物件→取 value。"""
    if isinstance(v, Mapping):
        v = v.get("value", v.get("formattedValue"))
    if v in ("", None, "#Missing", "#MISSING", "#Mi"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def mdx_to_records(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """把 Essbase MDX(JSON) 回應攤成「長表的 records」（list[dict]，純 Python、不依賴 pandas）。

    每一筆 = 一個 row tuple × column tuple 交叉的儲存格：
        Row1..RowN  (列軸各維度成員) | Col1..ColM (欄軸各維度成員) |
        Page1..PageK (若有單一 page tuple) | value

    參考形狀：{"metadata": {"page":[…],"column":[…],"row":[…]}, "data": [[…],…]}
    對欄位別名（axes/columns/rows、metaData）與成員物件鍵名做了容錯。
    若你的回應形狀不同，execute_mdx() 已回傳原始 dict，可據以調整本函式。
    """
    md = payload.get("metadata") or payload.get("metaData") or {}
    columns = [_tuple_names(t) for t in (md.get("column") or md.get("columns") or [])]
    rows = [_tuple_names(t) for t in (md.get("row") or md.get("rows") or [])]
    pages = [_tuple_names(t) for t in (md.get("page") or md.get("pages") or [])]
    data = payload.get("data") or []

    n_col = max((len(c) for c in columns), default=0)
    n_row = max((len(r) for r in rows), default=0)
    page_members = pages[0] if len(pages) == 1 else []  # 單一 page → 當常數欄

    records: List[Dict[str, Any]] = []
    for ri, raw_row in enumerate(data):
        row_members = rows[ri] if ri < len(rows) else []
        values = list(raw_row) if isinstance(raw_row, (list, tuple)) else [raw_row]
        # data 列可能在值前夾帶列標頭；取「最後 len(columns) 個」當值最穩。
        if columns and len(values) > len(columns):
            values = values[-len(columns):]
        for ci, val in enumerate(values):
            col_members = columns[ci] if ci < len(columns) else []
            rec: Dict[str, Any] = {}
            for d in range(n_row):
                rec[f"Row{d + 1}"] = row_members[d] if d < len(row_members) else ""
            for d in range(n_col):
                rec[f"Col{d + 1}"] = col_members[d] if d < len(col_members) else ""
            for d, pm in enumerate(page_members):
                rec[f"Page{d + 1}"] = pm
            rec["value"] = _coerce_num(val)
            records.append(rec)

    return records


def mdx_to_long_df(payload: Mapping[str, Any]):
    """mdx_to_records() 的 pandas 包裝：回傳長表 DataFrame（欄序穩定）。"""
    import pandas as pd  # 延遲匯入：純解析(records)不需 pandas

    records = mdx_to_records(payload)
    columns = list(records[0].keys()) if records else ["value"]
    return pd.DataFrame.from_records(records, columns=columns)


# ── CLI 煙霧測試 ───────────────────────────────────────────────────────────

def _main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Essbase REST：在資料庫上執行 MDX")
    p.add_argument("mdx", nargs="?", help="MDX 查詢；省略則只做連線健檢(ping)")
    p.add_argument("--raw", action="store_true", help="印出原始 JSON 回應")
    p.add_argument("--csv", metavar="PATH", help="把長表結果存成 CSV")
    args = p.parse_args(argv)

    try:
        client = EssbaseClient()
    except EssbaseError as e:
        print(f"[設定錯誤] {e}", file=sys.stderr)
        return 2

    try:
        if not args.mdx:
            ok = client.ping()
            print(f"連線{'成功' if ok else '失敗'}：{client.db_url}")
            return 0 if ok else 1
        payload = client.execute_mdx(args.mdx)
        if args.raw:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        df = mdx_to_long_df(payload)
        if args.csv:
            df.to_csv(args.csv, index=False, encoding="utf-8-sig")
            print(f"已輸出 {len(df)} 列 → {args.csv}")
        else:
            print(df.to_string(index=False) if not df.empty else "(空結果)")
        return 0
    except EssbaseError as e:
        print(f"[Essbase 錯誤] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())

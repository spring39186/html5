#!/usr/bin/env python3
"""essbase_smoketest.py — 單獨測 Essbase REST 連線（對應你的 VBA testExeMDXquery）
===============================================================================
只用 Python 標準函式庫（urllib），**不需安裝任何套件、也不依賴本專案其他程式**，
方便隔離測「連得到 / 認證過 / 有回應」這三件事。等於把你那段 VBA 改寫成 Python：

    POST {URI}/applications/{APP}/databases/{DB}/mdx?format=JSON
    Headers: Content-Type: application/json
             Accept:       application/octet-stream   ← 與你 VBA 一致
             Authorization: Basic base64(user:pwd)    ← 帳密(HTTP Basic)
    Body:    {"query":"<MDX>","preferences":{"dataless":false,
              "formatValues":true,"memberIdentifierType":"NAME"}}

設定來源（優先序高→低）：命令列參數 > 環境變數 / .env > 本檔頂端 DEFAULT_* 常數
    FA_ESB_URI   REST 基底，例：https://host:9001/essbase/rest/v1
    FA_ESB_APP   application name（預設 VEMIS2T）
    FA_ESB_DB    database name （預設 IEMISA）
    FA_ESB_USER  帳號
    FA_ESB_PWD   密碼
    FA_ESB_VERIFY_TLS  自簽憑證設 0 可跳過 TLS 驗證（預設 1）

用法：
    python essbase_smoketest.py                                  # 用 DEFAULT_MDX
    python essbase_smoketest.py "SELECT {} ON COLUMNS FROM VEMIS2T.IEMISA"
    python essbase_smoketest.py --file my.mdx                    # MDX 讀檔
    python essbase_smoketest.py --out D:/MDX_Result.txt          # 指定輸出檔
    python essbase_smoketest.py --insecure                       # 跳過 TLS 驗證

⚠ 同你 VBA 的註解：MDX 內 FROM ApplicationName.DatabaseName 要與 URL 的 app/db 一致。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# ── 想直接寫死也行（留空就走環境變數 / .env）──────────────────────────────
DEFAULT_URI = ""              # 例：https://your-host:9001/essbase/rest/v1
DEFAULT_APP = "VEMIS2T"
DEFAULT_DB = "IEMISA"
DEFAULT_USER = ""
DEFAULT_PWD = ""

# 換成你「已知可以跑」的 MDX；FROM 要對到 APP.DB。
DEFAULT_MDX = "SELECT {} ON COLUMNS FROM VEMIS2T.IEMISA"


def load_dotenv(*paths: str) -> None:
    """極簡 .env 載入：KEY=VALUE → 環境變數（真實環境變數優先；# 開頭略過）。"""
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if v[:1] not in ('"', "'") and " #" in v:
                        v = v.split(" #", 1)[0].strip()      # 去行內註解
                    os.environ.setdefault(k, v.strip('"').strip("'"))
        except OSError:
            pass


def _cfg(name: str, default: str) -> str:
    return (os.environ.get(name) or default or "").strip()


def build_body(mdx: str) -> bytes:
    """用 json.dumps 組 body（自動跳脫引號/換行，比 VBA 字串相接更安全）。"""
    payload = {
        "query": mdx.strip(),
        "preferences": {
            "dataless": False,
            "formatValues": True,
            "memberIdentifierType": "NAME",
        },
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="單獨測 Essbase REST MDX 連線")
    ap.add_argument("mdx", nargs="?", help="MDX 查詢字串（省略則用 --file 或 DEFAULT_MDX）")
    ap.add_argument("--file", metavar="PATH", help="從檔案讀 MDX")
    ap.add_argument("--out", metavar="PATH", default="MDX_Result.txt", help="回應輸出檔（預設 MDX_Result.txt）")
    ap.add_argument("--timeout", type=int, default=120, help="逾時秒數（預設 120）")
    ap.add_argument("--insecure", action="store_true", help="跳過 TLS 憑證驗證（自簽用）")
    args = ap.parse_args(argv)

    # .env：先載 cwd/.env，再載本檔同層（financial_agent/.env）
    load_dotenv(os.path.join(os.getcwd(), ".env"), os.path.join(HERE, ".env"))

    base = _cfg("FA_ESB_URI", DEFAULT_URI).rstrip("/")
    app = _cfg("FA_ESB_APP", DEFAULT_APP)
    db = _cfg("FA_ESB_DB", DEFAULT_DB)
    user = _cfg("FA_ESB_USER", DEFAULT_USER)
    pwd = _cfg("FA_ESB_PWD", DEFAULT_PWD)
    verify = _cfg("FA_ESB_VERIFY_TLS", "1").lower() not in ("0", "false", "no", "off")
    if args.insecure:
        verify = False

    missing = [n for n, v in (("FA_ESB_URI", base), ("FA_ESB_USER", user),
                              ("FA_ESB_PWD", pwd)) if not v]
    if missing:
        print("[設定錯誤] 缺少：" + ", ".join(missing)
              + "\n  → 填到 .env / 環境變數，或改本檔頂端 DEFAULT_* 常數。", file=sys.stderr)
        return 2

    # 取得 MDX：--file > 位置參數 > DEFAULT_MDX
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as f:
                mdx = f.read()
        except OSError as e:
            print(f"[讀檔失敗] {e}", file=sys.stderr)
            return 2
    else:
        mdx = args.mdx or DEFAULT_MDX

    url = f"{base}/applications/{app}/databases/{db}/mdx?format=JSON"
    body = build_body(mdx)
    token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")

    print("== Essbase 連線測試 ==")
    print(f"  URL : {url}")
    print(f"  User: {user}   TLS驗證: {'on' if verify else 'OFF(insecure)'}")
    print(f"  MDX : {mdx.strip()[:120]}{'…' if len(mdx.strip()) > 120 else ''}")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/octet-stream")  # 與你 VBA 一致
    req.add_header("Authorization", f"Basic {token}")

    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=args.timeout, context=ctx) as r:
            status = r.status
            ctype = r.headers.get("Content-Type", "")
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:                     # 4xx/5xx：仍讀出錯誤內容
        status = e.code
        ctype = e.headers.get("Content-Type", "") if e.headers else ""
        text = e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:                      # 連不到 / DNS / TLS
        print(f"\n[連線失敗] {e.reason}"
              "\n  檢查：URL 是否正確、VPN/防火牆、port、TLS（自簽可加 --insecure）。",
              file=sys.stderr)
        return 1
    except (TimeoutError, OSError) as e:
        print(f"\n[連線失敗] {e}", file=sys.stderr)
        return 1

    print(f"\nHTTP {status}   Content-Type: {ctype}   回應長度: {len(text)} bytes")

    # 與你 VBA 一樣：把 '],[' 換行，存檔後較好讀
    pretty = text.replace("],[", "]," + "\n" + "[")
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(pretty)
        print(f"完整回應已存：{os.path.abspath(args.out)}")
    except OSError as e:
        print(f"[寫檔失敗] {e}（仍印出片段於下）", file=sys.stderr)

    snippet = pretty[:800]
    print("\n--- 回應前 800 字 ---")
    print(snippet + ("…" if len(pretty) > 800 else ""))

    if status == 200:
        print("\n✅ 連線 / 認證 OK，且伺服器有回應。")
        return 0
    if status in (401, 403):
        print("\n❌ 認證/授權失敗：請確認帳號密碼與該帳號對 cube 的權限。", file=sys.stderr)
    elif status == 400:
        print("\n⚠ 連線/認證 OK，但 MDX 被拒(400)：通常是 MDX 語法或 FROM App.Db 不符。",
              file=sys.stderr)
    else:
        print(f"\n❌ 伺服器回 HTTP {status}（細節見上方回應）。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

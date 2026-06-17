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
    FA_ESB_URI   REST 基底，例：https://host:9001/essbase/rest/v1（Essbase 9001 多為 HTTPS；自簽憑證加 --insecure）
    FA_ESB_APP   application name（預設 VEMIS2T）
    FA_ESB_DB    database name （預設 IEMISA）
    FA_ESB_USER  帳號
    FA_ESB_PWD   密碼
    FA_ESB_VERIFY_TLS  自簽憑證設 0 可跳過 TLS 驗證（預設 1）

用法：
    python essbase_smoketest.py                                  # 用 DEFAULT_MDX
    python essbase_smoketest.py "SELECT {} ON COLUMNS FROM VEMIS2T.IEMISA"
    python essbase_smoketest.py --file my.mdx                    # MDX 讀檔
    python essbase_smoketest.py --insecure                       # 跳過 TLS 驗證
    python essbase_smoketest.py --accept text/html               # 回應改 HTML 串流（octet-stream/text/html 為官方支援格式）

⚠ 同你 VBA 的註解：MDX 內 FROM ApplicationName.DatabaseName 要與 URL 的 app/db 一致。
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# ── 想直接寫死也行（留空就走環境變數 / .env）──────────────────────────────
DEFAULT_URI = ""              # 例：https://your-host:9001/essbase/rest/v1（Essbase 9001 多為 HTTPS；自簽憑證加 --insecure）
DEFAULT_APP = "VEMIS2T"
DEFAULT_DB = "IEMISA"
DEFAULT_USER = ""
DEFAULT_PWD = ""

# 你實測可跑的 MDX。FROM 的 __APP__/__DB__ 會在執行時自動換成設定的 app/db，
# 所以只要改 .env 的 FA_ESB_APP/FA_ESB_DB 即可，這行不用動。
DEFAULT_MDX = (
    "SELECT { [Currency].[NTD K], [Currency].[USD K] } ON COLUMNS, "
    "{ Descendants([Sector Total], 1, SELF_AND_BEFORE) } ON ROWS "
    "FROM __APP__.__DB__"
)


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


def build_body(mdx: str, format_values: bool = True) -> bytes:
    """用 json.dumps 組 body（自動跳脫引號/換行，比 VBA 字串相接更安全）。

    format_values=False 會關掉 formatValues/formatString，取「原始數值」
    （避免格式字串造成的科學記號/千分位等怪字串，較好直接給 pandas 用）。
    """
    payload = {
        "query": mdx.strip(),
        "preferences": {
            "dataless": False,
            "formatValues": format_values,
            "formatString": format_values,
            "memberIdentifierType": "NAME",
        },
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def raw_http_request(url: str, headers: dict[str, str], body: bytes,
                     timeout: int = 120, verify: bool = True,
                     legacy_tls: bool = False) -> tuple[int, str, str]:
    """不透過 http.client，自己用 socket 送 POST 並讀「整包」回應。

    用來應付伺服器回 HTTP/0.9 風格（沒有狀態列/標頭，常見於 Essbase
    Accept: application/octet-stream 串流）導致標準函式庫 BadStatusLine 的情況——
    就是 curl 要加 --http0.9 才讀得到的那種回應。
    回傳 (status, content_type, text)；若回應完全沒有 HTTP 標頭，status 視為 200。
    註：此路徑為「直連」，不套用 --proxy（你實測的 curl 也是直連）。
    """
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme or "http"
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    host_hdr = f"{host}:{port}" if parts.port else host

    lines = [f"POST {path} HTTP/1.1", f"Host: {host_hdr}", "Connection: close"]
    lines += [f"{k}: {v}" for k, v in headers.items()]
    lines.append(f"Content-Length: {len(body)}")
    raw_req = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body

    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        if scheme == "https":
            ctx = ssl.create_default_context()
            if not verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            if legacy_tls:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    try:
                        ctx.minimum_version = ssl.TLSVersion.TLSv1
                    except (AttributeError, ValueError):
                        pass
                try:
                    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
                except ssl.SSLError:
                    pass
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.sendall(raw_req)
        sock.settimeout(timeout)
        chunks: list[bytes] = []
        while True:
            try:
                buf = sock.recv(65536)
            except TimeoutError:
                break
            if not buf:                                     # 對方關閉連線 → 讀完
                break
            chunks.append(buf)
    finally:
        try:
            sock.close()
        except OSError:
            pass

    raw = b"".join(chunks)
    # 偵測：對 TLS port 送了明文 → 伺服器回的是 TLS record（Alert/Handshake），不是 HTTP
    if raw[:1] in (b"\x14", b"\x15", b"\x16", b"\x17") and raw[1:2] == b"\x03":
        rec = {0x14: "ChangeCipherSpec", 0x15: "Alert",
               0x16: "Handshake", 0x17: "ApplicationData"}.get(raw[0], "?")
        raise ValueError(
            f"收到 TLS {rec} record（前幾位元組 {raw[:8].hex()}）而不是 HTTP —— 這個 port 走 HTTPS。"
            "\n  → 把 FA_ESB_URI 改成 https:// 開頭再試；自簽憑證請加 --insecure（或設 FA_ESB_VERIFY_TLS=0）。")
    if raw[:5] == b"HTTP/":                                  # 正常 HTTP：切出狀態列/標頭/內容
        head, _, body_bytes = raw.partition(b"\r\n\r\n")
        head_lines = head.split(b"\r\n")
        bits = head_lines[0].decode("latin-1", "replace").split(None, 2)
        status = int(bits[1]) if len(bits) >= 2 and bits[1].isdigit() else 0
        ctype = ""
        for hl in head_lines[1:]:
            if hl.lower().startswith(b"content-type:"):
                ctype = hl.split(b":", 1)[1].strip().decode("latin-1", "replace")
                break
        text = body_bytes.decode("utf-8", "replace")
    else:                                                   # HTTP/0.9：整包都是 body
        status = 200
        ctype = "(無 HTTP 標頭 / HTTP-0.9 串流)"
        text = raw.decode("utf-8", "replace")
    return status, ctype, text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="單獨測 Essbase REST MDX 連線")
    ap.add_argument("mdx", nargs="?", help="MDX 查詢字串（省略則用 --file 或 DEFAULT_MDX）")
    ap.add_argument("--file", metavar="PATH", help="從檔案讀 MDX")
    ap.add_argument("--timeout", type=int, default=120, help="逾時秒數（預設 120）")
    ap.add_argument("--insecure", action="store_true", help="跳過 TLS 憑證驗證（自簽用）")
    ap.add_argument("--proxy", metavar="URL", help="指定 HTTP(S) Proxy，例：http://proxy:8080")
    ap.add_argument("--proxy-user", metavar="USER", help="Proxy 帳號（Proxy 需認證時；免去把密碼塞進 URL/編碼）")
    ap.add_argument("--proxy-pass", metavar="PWD", help="Proxy 密碼（搭配 --proxy-user）")
    ap.add_argument("--legacy-tls", action="store_true",
                    help="放寬 TLS（容忍舊版伺服器 / 弱 cipher；解 OpenSSL 比 Windows 嚴的連線中止）")
    ap.add_argument("--accept", metavar="MIME", default="application/octet-stream",
                    help="Accept 標頭（預設 application/octet-stream，與 VBA 一致；另支援 text/html）")
    ap.add_argument("--raw-values", action="store_true",
                    help="關閉 formatValues/formatString 取原始數值（避免格式化造成的科學記號/怪字串）")
    ap.add_argument("--debug", action="store_true", help="失敗時印完整 traceback")
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
        mdx = args.mdx or DEFAULT_MDX.replace("__APP__", app).replace("__DB__", db)

    url = f"{base}/applications/{app}/databases/{db}/mdx?format=JSON"
    body = build_body(mdx, format_values=not args.raw_values)
    token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")

    print("== Essbase 連線測試 ==")
    print(f"  URL : {url}")
    print(f"  User: {user}   TLS驗證: {'on' if verify else 'OFF(insecure)'}")
    print(f"  MDX : {mdx.strip()[:120]}{'…' if len(mdx.strip()) > 120 else ''}")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", args.accept)  # 預設與你 VBA 一致（application/octet-stream）
    req.add_header("Authorization", f"Basic {token}")

    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if args.legacy_tls:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # TLSv1 已棄用但這裡刻意允許
            try:
                ctx.minimum_version = ssl.TLSVersion.TLSv1       # 容忍 TLS 1.0+
            except (AttributeError, ValueError):
                pass
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")                # 放寬 OpenSSL3 安全等級
        except ssl.SSLError:
            pass

    # 自訂 opener：HTTPSHandler 套我們的 ctx；--proxy 覆寫系統/環境代理。
    handlers: list = [urllib.request.HTTPSHandler(context=ctx)]
    if args.proxy:
        handlers.append(urllib.request.ProxyHandler({"http": args.proxy, "https": args.proxy}))
        if args.proxy_user:
            # Proxy 需認證：用帳密管理器，不必把密碼編碼塞進 URL（僅支援 Basic proxy auth）
            pmgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            pmgr.add_password(None, args.proxy, args.proxy_user, args.proxy_pass or "")
            handlers.append(urllib.request.ProxyBasicAuthHandler(pmgr))
    opener = urllib.request.build_opener(*handlers)

    try:
        with opener.open(req, timeout=args.timeout) as r:
            status = r.status
            ctype = r.headers.get("Content-Type", "")
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:                     # 4xx/5xx：仍讀出錯誤內容
        status = e.code
        ctype = e.headers.get("Content-Type", "") if e.headers else ""
        text = e.read().decode("utf-8", "replace")
    except http.client.HTTPException as e:                  # 回應無標準 HTTP 標頭(BadStatusLine/HTTP-0.9)
        print(f"\n[note] 標準 HTTP 解析失敗（{type(e).__name__}: {e!r}）"
              "\n  → 伺服器回應沒有 HTTP 狀態列/標頭（HTTP/0.9 風格，就是 curl 要加 --http0.9 的原因），"
              "\n    改用 raw socket 直接讀整包回應…", file=sys.stderr)
        try:
            status, ctype, text = raw_http_request(
                url,
                {"Authorization": f"Basic {token}",
                 "Content-Type": "application/json",
                 "Accept": args.accept},
                body, timeout=args.timeout, verify=verify, legacy_tls=args.legacy_tls)
        except (OSError, ValueError) as e2:
            if args.debug:
                import traceback
                traceback.print_exc()
            print(f"\n[連線失敗-raw] {e2}", file=sys.stderr)
            return 1
    except (urllib.error.URLError, TimeoutError, OSError) as e:   # 連不到 / DNS / TLS / 連線中止
        if args.debug:
            import traceback
            traceback.print_exc()
        reason = getattr(e, "reason", e)
        print(f"\n[連線失敗] {reason}", file=sys.stderr)
        print(
            "  你的 VBA 能通、Python 不行 → 多半是 Python 的 TLS/代理跟 Windows(SChannel) 不同：\n"
            "  1) 公司 Proxy：PowerShell 先 $env:HTTPS_PROXY=\"http://proxy:port\" 再跑，或加 --proxy http://proxy:port\n"
            "  2) 舊版伺服器/弱加密被 OpenSSL 擋：加 --legacy-tls（必要時再加 --insecure）\n"
            "  3) 防毒/HTTPS 檢查軟體中止 python.exe：先用 Windows 內建 curl（走 SChannel，同你 VBA）比對：\n"
            f"       curl.exe -v -k -u 帳號:密碼 \"{base}/applications/{app}/databases/{db}\"\n"
            "     curl 會通但 Python 不通 → 屬 1)或2)；curl 也被中止 → 屬 3)（找 IT 放行或走 Proxy）。",
            file=sys.stderr)
        return 1

    print(f"\nHTTP {status}   Content-Type: {ctype}   回應長度: {len(text)} bytes")

    # 完整接收後，把 '],[' 換行讓表格較好讀，直接整包印出（不另存檔）
    pretty = text.replace("],[", "]," + "\n" + "[")
    print("\n--- 完整回應 ---")
    print(pretty)

    if status == 200:
        # 官方文件：這是 streaming API，即使 200 也可能在內容夾帶 errorMessage
        if "errorMessage" in text:
            print("\n⚠ HTTP 200，但回應含 errorMessage —— 串流 API 即使 200 也可能失敗。"
                  "\n  請看上方完整回應的 errorMessage（常見：MDX 語法、成員名稱、FROM App.Db、權限）。",
                  file=sys.stderr)
            return 1
        print("\n✅ 連線 / 認證 OK，且伺服器有回應（未見 errorMessage）。")
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

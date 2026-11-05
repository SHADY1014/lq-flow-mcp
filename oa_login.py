#!/usr/bin/env python3
"""
OA 登录助手：弹出浏览器，用户手动登录一次，脚本保存会话 token。

OA（oa.lqp9.cn）的服务接口 `/exoa/services` 只认 HEADER 里的 token
（localStorage.UserInfo.SESS_GUID），不需要 Cookie。
token 保存到 ~/.lq-oa.cookie（600 权限），供 MCP 的 oa_* 工具使用。

用法：
  python3 oa_login.py
  python3 oa_login.py --timeout 600
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://oa.lqp9.cn/modules/home/index.html"
SESSION_OUT = Path.home() / ".lq-oa.cookie"
PROFILE_DIR = Path.home() / ".lq-oa-chrome"
HOST_HINT = "oa.lqp9.cn"


def find_browser() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/usr/bin/google-chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/chromium",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return (shutil.which("google-chrome") or shutil.which("chrome")
            or shutil.which("msedge") or shutil.which("microsoft-edge"))


def free_port(start: int = 9222) -> int:
    for p in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return 9400


def targets(port: int) -> list[dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="OA 手动登录并保存会话")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--out", default=str(SESSION_OUT))
    args = ap.parse_args()

    browser_bin = find_browser()
    if not browser_bin:
        print("✗ 没找到 Chrome / Edge，安装其一后重试")
        return 2

    port = free_port()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [browser_bin, f"--remote-debugging-port={port}", f"--user-data-dir={PROFILE_DIR}",
         "--no-first-run", "--no-default-browser-check", "--new-window", args.url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"已打开浏览器（调试端口 {port}）")
    print("请登录 OA；登录成功会自动保存会话 token。")
    print(f"最长等待 {args.timeout} 秒 ...")

    try:
        import websocket
    except ImportError:
        print("缺少 websocket-client，请 pip install websocket-client")
        return 3

    deadline = time.time() + args.timeout
    page = None
    while time.time() < deadline:
        pages = [t for t in targets(port) if t.get("type") == "page"
                 and "oa.lqp9.cn" in (t.get("url") or "") and "/modules/home" in (t.get("url") or "")]
        if pages:
            page = pages[0]
            break
        time.sleep(2)

    if not page:
        print("✗ 等待超时，未检测到登录完成")
        if not args.keep_open:
            proc.terminate()
        return 4

    try:
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=6)
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 连不上调试端口：{exc}")
        return 5

    def evaluate(expr: str):
        ws.send(json.dumps({"id": 31, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True}}))
        for _ in range(30):
            msg = json.loads(ws.recv())
            if msg.get("id") == 31:
                return (msg.get("result", {}).get("result", {}) or {}).get("value")
        return None

    time.sleep(1.2)
    info_raw = evaluate("localStorage.getItem('UserInfo')||''") or ""
    ws.close()
    try:
        info = json.loads(info_raw)
    except Exception:
        info = {}

    token = (info.get("SESS_GUID") or "").strip()
    if not token:
        print("✗ 没读到会话 token，可能未真正登录")
        if not args.keep_open:
            proc.terminate()
        return 6

    out = Path(args.out).expanduser()
    out.write_text(json.dumps({
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": HOST_HINT,
        "user": info.get("USER_NAME", ""),
        "user_code": info.get("USER_CODE", ""),
        "token": token,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(out, 0o600)
    print(f"✓ 已保存会话 token → {out}")
    print(f"  登录用户：{info.get('USER_NAME') or '(未知)'}")
    print("  说明：OA 服务接口只认 token，不需要 Cookie")

    if not args.keep_open:
        proc.terminate()
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

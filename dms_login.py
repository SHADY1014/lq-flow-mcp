#!/usr/bin/env python3
"""
DMS 网页版登录助手：弹出浏览器，用户手动登录一次，脚本自动保存会话 Cookie。

原理：用独立临时配置启动 Chrome 并开启调试端口，轮询页面地址判断登录完成，
再通过 CDP 的 Network.getAllCookies 读取 Cookie（HttpOnly 也能拿到），
写入 ~/.lq-dms.cookie（600 权限）。

用法：
  python3 dms_login.py                  # 默认打开 DMS 首页
  python3 dms_login.py --keep-open      # 抓完不关浏览器
  python3 dms_login.py --timeout 600
"""
import argparse
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://192.168.199.26:9090/hjerp/indexServlet"
COOKIE_OUT = Path.home() / ".lq-dms.cookie"
PROFILE_DIR = Path.home() / ".lq-dms-chrome"
HOST_HINT = "192.168.199.26"


def find_chrome() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return shutil.which("google-chrome") or shutil.which("chrome")


def free_port(start: int = 9222) -> int:
    for p in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return random.randint(9300, 9400)


def cdp_targets(port: int) -> list[dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="DMS 手动登录并保存 Cookie")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--out", default=str(COOKIE_OUT))
    args = ap.parse_args()

    chrome = find_chrome()
    if not chrome:
        print("✗ 没找到 Chrome，请安装或手动登录后复制 Cookie")
        return 2

    port = args.port or free_port()
    profile = PROFILE_DIR
    profile.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [chrome, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", "--new-window", args.url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"已打开浏览器（调试端口 {port}）")
    print("请在窗口里用你的经销商系统账号登录；登录成功后会自动保存 Cookie。")
    print(f"最长等待 {args.timeout} 秒 ...")

    try:
        import websocket
    except ImportError:
        print("缺少 websocket-client，请 pip install websocket-client")
        return 3

    deadline = time.time() + args.timeout
    ws = None
    logged_in = False
    while time.time() < deadline:
        targets = [t for t in cdp_targets(port) if t.get("type") == "page"]
        page = next((t for t in targets if HOST_HINT in (t.get("url") or "")), None)
        if page and ws is None:
            try:
                ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=5)
                ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
                ws.recv()
            except Exception:
                ws = None
        url = (page or {}).get("url", "")
        if page and "/login" not in url and HOST_HINT in url:
            logged_in = True
            break
        time.sleep(2)

    if not logged_in or ws is None:
        print("✗ 等待超时，未检测到登录完成")
        if not args.keep_open:
            proc.terminate()
        return 4

    print("检测到登录成功，正在读取 Cookie ...")
    time.sleep(1.5)

    def ask(conn, mid, method, params=None):
        payload = {"id": mid, "method": method}
        if params:
            payload["params"] = params
        conn.send(json.dumps(payload))
        for _ in range(60):
            try:
                msg = json.loads(conn.recv())
            except Exception:
                return []
            if msg.get("id") == mid:
                if msg.get("error"):
                    print(f"  {method}: {str(msg['error'])[:90]}")
                    return []
                return msg.get("result", {}).get("cookies", []) or []
        return []

    cookies = []
    # ① 按 URL 取（最常用）
    cookies = ask(ws, 11, "Network.getCookies", {"urls": [args.url, f"http://{HOST_HINT}:9090/"]})
    print(f"  Network.getCookies → {len(cookies)} 条")
    # ② 页面会话里取全部
    if not cookies:
        cookies = ask(ws, 12, "Storage.getCookies")
        print(f"  Storage.getCookies(page) → {len(cookies)} 条")
    ws.close()

    # ③ 浏览器级 target 再试一次
    if not cookies:
        try:
            ver = json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=3).read().decode())
            bws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=5)
            cookies = ask(bws, 13, "Storage.getCookies")
            print(f"  Storage.getCookies(browser) → {len(cookies)} 条")
            bws.close()
        except Exception as exc:
            print(f"  浏览器级读取失败：{str(exc)[:80]}")

    picked = [c for c in cookies if HOST_HINT in (c.get("domain") or "")]
    if not picked:
        print("✗ 没有拿到该站点的 Cookie")
        if not args.keep_open:
            proc.terminate()
        return 5

    # 顺带记录登录用户 ID（报表权限过滤需要）
    user_id = ""
    try:
        ws2 = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=5)
        ws2.send(json.dumps({"id": 21, "method": "Runtime.evaluate",
                             "params": {"expression": "localStorage.getItem('sid')||''", "returnByValue": True}}))
        for _ in range(10):
            msg = json.loads(ws2.recv())
            if msg.get("id") == 21:
                sid = str((msg.get("result", {}).get("result", {}) or {}).get("value") or "")
                if sid:
                    user_id = sid.split("-")[-1][-8:]
                break
        ws2.close()
    except Exception:
        pass

    out = Path(args.out).expanduser()
    out.write_text(json.dumps({"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "host": HOST_HINT, "user_id": user_id, "cookies": picked},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(out, 0o600)
    print(f"✓ 已保存 {len(picked)} 条 Cookie → {out}")
    print("  Cookie 含 HttpOnly，仅供本机 MCP 使用，勿外传")

    if not args.keep_open:
        proc.terminate()
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

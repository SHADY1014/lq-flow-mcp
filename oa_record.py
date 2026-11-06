#!/usr/bin/env python3
"""
OA 请求录制器：启动带调试端口的 Chrome，你在里面正常操作，
脚本通过 CDP 记录所有 /exoa/services 请求（含 POST body），用于固化接口参数。

用法：
  python3 oa_record.py                 # 录制 10 分钟，命中 sendInst/newSaveMissive 后继续
  python3 oa_record.py --timeout 600
  python3 oa_record.py --out ~/oa-capture.json

产物：~/.lq-oa-capture.json（600 权限），含每次调用的 SERVICE 与参数原文。
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
PROFILE_DIR = Path.home() / ".lq-oa-chrome"
OUT = Path.home() / ".lq-oa-capture.json"


def find_browser() -> str | None:
    cands = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in cands:
        if c and Path(c).exists():
            return c
    return shutil.which("google-chrome") or shutil.which("msedge")


def free_port(start=9222):
    for p in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return 9410


def targets(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()

    bin_ = find_browser()
    if not bin_:
        print("✗ 没找到 Chrome/Edge")
        return 2
    port = free_port()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    # 关键：先杀掉复用同一 profile 的 Chrome，否则新进程会挂到旧实例上，调试端口不生效
    subprocess.run(["pkill", "-f", f"--user-data-dir={PROFILE_DIR}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    proc = subprocess.Popen(
        [bin_, f"--remote-debugging-port={port}", f"--user-data-dir={PROFILE_DIR}",
         "--no-first-run", "--no-default-browser-check", "--new-window", args.url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"已启动浏览器（端口 {port}）。请登录 OA，然后正常发起一次「2026年自由流程」并发给杨银文。", flush=True)

    import websocket
    page = None
    for _ in range(60):
        pages = [t for t in targets(port) if t.get("type") == "page" and "oa.lqp9.cn" in (t.get("url") or "")]
        if pages:
            page = pages[0]
            break
        time.sleep(1)
    if not page:
        print("✗ 未找到 OA 页面（Chrome 可能复用了已有实例，调试端口未生效）")
        return 3

    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=10)
    ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
    captured, seen = [], set()
    deadline = time.time() + args.timeout
    print("录制中...（命中 sendInst / newSaveMissive 后会提示）", flush=True)

    while time.time() < deadline:
        try:
            ws.settimeout(2)
            msg = json.loads(ws.recv())
        except Exception:
            continue
        if msg.get("method") != "Network.requestWillBeSent":
            continue
        req = (msg.get("params") or {}).get("request") or {}
        url = req.get("url") or ""
        body = req.get("postData") or ""
        if "/exoa/services" not in url:
            continue
        svc = ""
        try:
            import urllib.parse
            qs = urllib.parse.urlparse(url).query
            svc = urllib.parse.parse_qs(qs).get("SERVICE", [""])[0]
            if not svc and body:
                svc = urllib.parse.parse_qs(body).get("SERVICE", [""])[0]
        except Exception:
            pass
        key = (svc, len(body))
        if key in seen:
            continue
        seen.add(key)
        captured.append({"svc": svc, "url": url[:600], "body": body[:20000], "t": time.strftime("%H:%M:%S")})
        # 每捕获一条就落盘，避免进程中断丢数据
        try:
            Path(args.out).expanduser().write_text(json.dumps(
                {"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "count": len(captured), "calls": captured},
                ensure_ascii=False, indent=1), encoding="utf-8")
            os.chmod(Path(args.out).expanduser(), 0o600)
        except Exception:
            pass
        print(f"  + {svc} ({len(body)} bytes)", flush=True)
        if svc in ("OA.MissiveService.sendInst", "OA.MissiveService.newSaveMissive"):
            print("  ✓ 已捕获关键提交接口", flush=True)

    ws.close()
    out = Path(args.out).expanduser()
    out.write_text(json.dumps({"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "count": len(captured), "calls": captured},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(out, 0o600)
    print(f"✓ 已保存 {len(captured)} 条 → {out}")
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())

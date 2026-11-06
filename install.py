#!/usr/bin/env python3
"""
lq-flow 懒人包安装器（无交互）

做四件事：
  1. 安装 Python 依赖
  2. 把包内的 .lq-flow.env 复制到用户目录（600 权限）
  3. 自动检测并注册到已安装的 Agent 客户端（ZCode / Claude Desktop / Cursor）
  4. 做一次连通性检查并打印结果

用法（双击 安装.bat / 安装.command 即可，或手动）：
  python3 install.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_SRC = HERE / ".lq-flow.env"
ENV_DST = Path.home() / ".lq-flow.env"
SERVER = HERE / "server.py"


def line(msg=""):
    print(msg, flush=True)


def ensure_deps() -> bool:
    req = HERE / "requirements.txt"
    if not req.exists():
        return True
    line("[1/4] 安装依赖（首次可能需要 1-2 分钟）...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        line("  依赖安装失败，可手动执行：")
        line(f"  {sys.executable} -m pip install -r \"{req}\"")
        line((r.stderr or "")[-400:])
        return False
    line("  ✓ 依赖就绪")
    return True


def ensure_env() -> bool:
    line("[2/4] 写入数据库配置")
    if ENV_DST.exists():
        line(f"  · 已存在 {ENV_DST}，保留现有配置")
        return True

    if not ENV_SRC.exists():
        line("  · 未发现预置配置（GitHub 安装模式），进入配置向导")
        line("    会先检查 VPN，再识别客户端账号或提示输入数据库账号\n")
        rc = subprocess.call([sys.executable, str(HERE / "setup.py")])
        return rc == 0 and ENV_DST.exists()
    text = ENV_SRC.read_text(encoding="utf-8")
    out_dir = Path.home() / "Desktop" / "货物流向导出"
    text = "\n".join(
        f"LQ_OUT_DIR={out_dir}" if ln.startswith("LQ_OUT_DIR=") else ln
        for ln in text.splitlines()) + "\n"
    ENV_DST.write_text(text, encoding="utf-8")
    os.chmod(ENV_DST, 0o600)
    line(f"  ✓ 已写入 {ENV_DST}（权限 600）")
    return True


def _entry():
    return {"command": sys.executable, "args": [str(SERVER)]}


def _register_one(path: Path, container, extra=None) -> str:
    """在指定配置文件的 container 路径下写入 lq-flow。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return f"跳过（{path.name} 不是合法 JSON）"
    node = data
    for key in container:
        node = node.setdefault(key, {})
    if "lq-flow" in node:
        return "已存在"
    entry = dict(_entry())
    if extra:
        entry.update(extra)
    node["lq-flow"] = entry
    if path.exists():
        shutil.copy(path, path.with_suffix(path.suffix + ".lq-flow.bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return "已写入"


def register_clients() -> None:
    line("[3/4] 注册到 Agent 客户端")
    home = Path.home()
    found = False

    zcode = home / ".zcode" / "cli" / "config.json"
    if zcode.parent.exists():
        line(f"  · ZCode: {_register_one(zcode, ('mcp', 'servers'), {'type': 'stdio', 'timeoutMs': 600000})}")
        found = True

    if sys.platform == "darwin":
        claude = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        cursor = home / ".cursor" / "mcp.json"
    else:
        claude = Path(os.getenv("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
        cursor = home / ".cursor" / "mcp.json"

    if claude.parent.exists():
        line(f"  · Claude Desktop: {_register_one(claude, ('mcpServers',))}")
        found = True
    if cursor.parent.exists():
        line(f"  · Cursor: {_register_one(cursor, ('mcpServers',))}")
        found = True

    if not found:
        line("  · 未检测到已知客户端，请按下面配置手动添加：")
        line(f'    "lq-flow": {{"command": "{sys.executable}", "args": ["{SERVER}"]}}')


def refresh_catalog() -> None:
    """首次安装后自动拉一次报表目录（失败不阻塞）。"""
    line("[4/5] 拉取报表目录（首次需要，约 2 秒）")
    code = (
        "import server,json;"
        "print(json.dumps(server.refresh_catalog('db'),ensure_ascii=False))"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(HERE),
                       capture_output=True, text=True)
    if r.returncode == 0 and '"db_count"' in (r.stdout or ""):
        line("  ✓ " + (r.stdout or "").strip()[:160])
    else:
        line("  · 目录稍后可手动刷新：在 agent 里说「跑一下 refresh_catalog」")
        line("    " + ((r.stderr or r.stdout or "").strip()[-200:]))


def check_conn() -> None:
    line("[5/5] 连通性检查")
    host = "192.168.199.19"
    if ENV_DST.exists():
        for raw in ENV_DST.read_text(encoding="utf-8").splitlines():
            if raw.startswith("LQ_DB_HOST="):
                host = raw.split("=", 1)[1].strip()
    try:
        with socket.create_connection((host, 1433), timeout=5):
            line(f"  ✓ {host}:1433 可达，安装完成")
    except Exception as exc:  # noqa: BLE001
        line(f"  ✗ {host}:1433 不可达（{exc}）")
        line("    请先连接公司 VPN（TopSAP / EasyConnect），再重启客户端。")
    line("\n下一步：重启客户端，对 agent 说「跑一下 preflight」验证。")


def main() -> int:
    line("=" * 52)
    line(" 漓泉 cERP MCP 懒人安装")
    line("=" * 52)
    if not ensure_deps():
        return 1
    if not ensure_env():
        return 2
    register_clients()
    refresh_catalog()
    check_conn()
    return 0


if __name__ == "__main__":
    sys.exit(main())

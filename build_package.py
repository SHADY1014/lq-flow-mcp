#!/usr/bin/env python3
"""
打「懒人包」：把代码 + 预配置的数据库连接 + 一键安装脚本打成一个 zip。

⚠️ 产物内含数据库账号密码，只用于内部发放，切勿提交到仓库或公开分享。

用法：
  python3 build_package.py                      # 密码从 ~/.lq-flow.env 读
  python3 build_package.py --user erp --password xxx
  python3 build_package.py --out ~/Desktop/lq-flow-懒人包.zip
"""
import argparse
import getpass
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = ["server.py", "setup.py", "install.py", "requirements.txt",
         "regions.json",
         "dms_login.py", "oa_login.py", "install.bat", "install.command"]
PKG_DIR = "lq-flow-mcp"



README = """漓泉 cERP MCP 懒人包
========================

安装（2 步）：
  1. 先连公司 VPN（TopSAP 或 EasyConnect，任选其一）
  2. Windows 双击「安装.bat」；macOS 双击「安装.command」

装完重启你的 Agent 客户端（ZCode / Claude Desktop / Cursor 等），
对 agent 说「跑一下 preflight」，返回 db_reachable: true 即可。

说明：
- 账号密码已预置在本包的 .lq-flow.env 中，安装时会复制到你的用户目录（600 权限）。
- 如果提示 1433 不可达，先确认 VPN 已连接。
- 想换账号，删除 ~/.lq-flow.env 后运行 python3 setup.py 重新配置。
"""


def read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            if "=" in raw and not raw.strip().startswith("#"):
                k, v = raw.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="打 lq-flow 懒人包")
    ap.add_argument("--user", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--host", default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    env = read_env(Path.home() / ".lq-flow.env")
    user = args.user or env.get("LQ_DB_USER") or "erp"
    pwd = args.password or env.get("LQ_DB_PASSWORD") or getpass.getpass("数据库密码: ")
    host = args.host or env.get("LQ_DB_HOST") or "192.168.199.19"
    db = args.db or env.get("LQ_DB_NAME") or "lqerp"

    missing = [f for f in FILES if not (HERE / f).exists()]
    if missing:
        print("缺少文件：", ", ".join(missing))
        return 1

    out = Path(args.out or (Path.home() / "Desktop" / "lq-flow-懒人包.zip")).expanduser()
    stage = HERE / ".build" / PKG_DIR
    if stage.parent.exists():
        shutil.rmtree(stage.parent)
    stage.mkdir(parents=True)

    for f in FILES:
        shutil.copy(HERE / f, stage / f)
    (stage / ".lq-flow.env").write_text(
        "# 漓泉 cERP MCP 配置（内部发放，勿外传）\n"
        f"LQ_DB_HOST={host}\nLQ_DB_PORT=1433\nLQ_DB_USER={user}\n"
        f"LQ_DB_PASSWORD={pwd}\nLQ_DB_NAME={db}\n"
        "LQ_LINK=lqcerp_lnk\n"
        "LQ_OUT_DIR=\n", encoding="utf-8")
    os.chmod(stage / ".lq-flow.env", 0o600)

    sh = stage / "install.command"
    os.chmod(sh, os.stat(sh).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (stage / "README.txt").write_text(README, encoding="utf-8")

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in stage.rglob("*"):
            z.write(p, Path(PKG_DIR) / p.relative_to(stage))

    print(f"✓ 已生成：{out}")
    print(f"  内含账号：{user}（密码已写入包内 .lq-flow.env）")
    print("  ⚠️ 该文件含凭据，请勿上传仓库或公开分享")
    return 0


if __name__ == "__main__":
    sys.exit(main())

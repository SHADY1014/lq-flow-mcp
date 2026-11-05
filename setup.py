#!/usr/bin/env python3
"""
漓泉 cERP MCP —— 首次安装配置向导

流程：
  1. 检查 VPN 连通性（连不上就提示先连公司 VPN）
  2. 提示输入经销商系统数据库账号 / 密码
  3. 验证登录，成功后写入 ~/.lq-flow.env（权限 600）
  4. 打印 MCP 注册方法

用法：
  python3 setup.py            # 完整配置
  python3 setup.py --check    # 只做连通性自检，不改配置
"""
import getpass
import os
import socket
import sys
from pathlib import Path

DEFAULT_HOST = os.getenv("LQ_DB_HOST", "192.168.199.19")
DEFAULT_PORT = int(os.getenv("LQ_DB_PORT", "1433"))
DEFAULT_DB = os.getenv("LQ_DB_NAME", "lqerp")
ENV_FILE = Path.home() / ".lq-flow.env"


def tcp_ok(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def check_vpn(host: str, port: int) -> bool:
    print(f"\n[1/3] 检查 VPN 连通性：{host}:{port}")
    ok, err = tcp_ok(host, port)
    if ok:
        print("  ✓ 内网可达")
        return True
    print(f"  ✗ 不可达（{err}）")
    print("    请先连接公司 VPN（TopSAP 天融信 或 EasyConnect 深信服），")
    print("    确认能访问公司内网后重新运行本向导。")
    return False


def test_login(host: str, port: int, database: str, user: str, password: str) -> bool:
    try:
        import pymssql
    except ImportError:
        print("\n缺少依赖 pymssql，请先运行：pip3 install -r requirements.txt")
        return False
    try:
        conn = pymssql.connect(server=host, port=port, user=user, password=password,
                               database=database, tds_version="7.0",
                               login_timeout=8, timeout=10)
        cur = conn.cursor()
        cur.execute("select db_name(), suser_sname()")
        db, login = cur.fetchone()
        conn.close()
        print(f"  ✓ 登录成功：db={db} login={login}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 登录失败：{str(exc)[:200]}")
        return False


def write_env(host: str, port: int, database: str, user: str, password: str) -> None:
    out = Path.home() / "Desktop" / "货物流向导出"
    content = (
        "# 漓泉 cERP MCP 配置（本机私有，勿提交到仓库）\n"
        f"LQ_DB_HOST={host}\n"
        f"LQ_DB_PORT={port}\n"
        f"LQ_DB_USER={user}\n"
        f"LQ_DB_PASSWORD={password}\n"
        f"LQ_DB_NAME={database}\n"
        "LQ_LINK=lqcerp_lnk\n"
        f"LQ_OUT_DIR={out}\n"
    )
    ENV_FILE.write_text(content, encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)
    print(f"\n[3/3] ✓ 配置已保存：{ENV_FILE}（权限 600）")


def print_register() -> None:
    here = Path(__file__).resolve().with_name("server.py")
    print("\n下一步：把 MCP 注册到 ZCode")
    print("编辑 ~/.zcode/cli/config.json，在 mcp.servers 下加入：\n")
    print(f'''  "lq-flow": {{
    "type": "stdio",
    "command": "python3",
    "args": ["{here}"],
    "timeoutMs": 600000
  }}''')
    print("\n保存后重启 ZCode，对 agent 说「跑一下 preflight」即可验证。")


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="漓泉 cERP MCP 安装向导")
    ap.add_argument("--check", action="store_true", help="只做连通性自检")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--database", default=DEFAULT_DB)
    args = ap.parse_args()

    print("=" * 56)
    print(" 漓泉 cERP MCP —— 安装配置向导")
    print("=" * 56)

    if args.check:
        check_vpn(args.host, args.port)
        return 0

    if not check_vpn(args.host, args.port):
        return 2

    print("\n[2/3] 输入经销商系统数据库账号")
    print("  说明：这里要的是数据库连接账号（和佳客户端 hjapp.ini 里的")
    print("        LogId / LogPass），不是软件登录账号。")
    user = input("  用户名: ").strip()
    if not user:
        print("  用户名为空，已退出")
        return 3
    password = getpass.getpass("  密码: ")
    if not test_login(args.host, args.port, args.database, user, password):
        print("\n  请检查账号密码，或确认该账号有 lqerp 库的读取权限。")
        return 3

    write_env(args.host, args.port, args.database, user, password)
    print_register()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
漓泉 cERP MCP —— 首次安装配置向导

流程：
  1. 检查 VPN 连通性（连不上直接提示先连公司 VPN）
  2. 自动扫描本机和佳客户端目录（hjapp.ini / hjapp1.ini / hjsoft.ini），
     提取数据库连接并逐个试连；自动区分明文密码与密文
  3. 没装客户端 / 扫描不到，自动转手工输入数据库账号
  4. 登录失败时给出针对性诊断（客户端账号误用、账号锁定、VPN 断开等）
  5. 成功后写入 ~/.lq-flow.env（权限 600）

用法：
  python3 setup.py                  # 自动扫描 + 试连 + 保存
  python3 setup.py --ini <路径>      # 指定某个 ini
  python3 setup.py --manual         # 跳过扫描，直接手工输入
  python3 setup.py --check          # 只做连通性自检
"""
import getpass
import os
import re
import socket
import sys
from pathlib import Path

DEFAULT_HOST = os.getenv("LQ_DB_HOST", "192.168.199.19")
DEFAULT_PORT = int(os.getenv("LQ_DB_PORT", "1433"))
DEFAULT_DB = os.getenv("LQ_DB_NAME", "lqerp")
ENV_FILE = Path.home() / ".lq-flow.env"

# 常见安装目录（Windows 为主；其它平台用 --ini 指定）
SEARCH_DIRS = [
    r"D:\经销商应用系统", r"D:\ERP应用系统", r"D:\hjerp",
    r"C:\经销商应用系统", r"C:\ERP应用系统", r"C:\hjerp",
    r"C:\Program Files\经销商应用系统", r"C:\Program Files (x86)\经销商应用系统",
]
# hjapp1.ini 常存明文，优先读取
INI_NAMES = ["hjapp1.ini", "hjapp.ini", "hjsoft.ini"]


# ------------------------------------------------------------------ 基础
def tcp_ok(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def check_vpn(host: str, port: int) -> bool:
    print(f"\n[1/4] 检查 VPN 连通性：{host}:{port}")
    ok, err = tcp_ok(host, port)
    if ok:
        print("  ✓ 内网可达")
        return True
    print(f"  ✗ 不可达（{err}）")
    print("    请先连接公司 VPN（TopSAP 天融信 或 EasyConnect 深信服），")
    print("    确认能访问公司内网后重新运行。")
    return False


# ------------------------------------------------------------------ ini 解析
def parse_ini(path: Path) -> list[dict]:
    """解析和佳 ini，返回 [{host, db, user, passwd, plain, section, file}]。"""
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    text = None
    for enc in ("gb18030", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return []

    items, section, cur = [], "", {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("//", ";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            if cur.get("user") and cur.get("passwd"):
                items.append({**cur, "section": section, "file": str(path)})
            section, cur = line[1:-1].strip(), {}
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().lower(), v.strip().strip('"').strip("'")
        if k == "logid":
            cur["user"] = v
        elif k == "logpass":
            cur["passwd"] = v
        elif k == "dbparm":
            m = re.search(r"DATASOURCE\s*=\s*'([^']+)'", v, re.I)
            if m:
                cur["host"] = m.group(1)
            m = re.search(r"database\s*=\s*([^',]+)", v, re.I)
            if m:
                cur["db"] = m.group(1).strip()
    if cur.get("user") and cur.get("passwd"):
        items.append({**cur, "section": section, "file": str(path)})

    out = []
    for it in items:
        if not it.get("host") or not it.get("db"):
            continue
        pwd = it.get("passwd", "")
        out.append({
            "host": it["host"], "db": it["db"], "user": it.get("user", ""), "passwd": pwd,
            "plain": bool(re.search(r"[A-Za-z]", pwd)),  # 纯长数字串视为密文
            "section": it.get("section", ""), "file": it.get("file", ""),
        })
    return out


def collect_candidates(explicit: str | None) -> list[dict]:
    files: list[Path] = []
    if explicit:
        files.append(Path(explicit).expanduser())
    else:
        for d in SEARCH_DIRS:
            base = Path(d)
            if not base.exists():
                continue
            for name in INI_NAMES:
                p = base / name
                if p.exists():
                    files.append(p)
            for p in base.glob("*/hjapp.ini"):
                files.append(p)
    seen, out = set(), []
    for f in files:
        for it in parse_ini(f):
            key = (it["host"], it["db"], it["user"], it["passwd"])
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
    return out


# ------------------------------------------------------------------ 连接与诊断
def try_login(host: str, port: int, db: str, user: str, pwd: str) -> tuple[bool, str]:
    try:
        import pymssql
    except ImportError:
        return False, "缺少依赖 pymssql，请先 pip install -r requirements.txt"
    try:
        conn = pymssql.connect(server=host, port=port, user=user, password=pwd,
                               database=db, tds_version="7.0",
                               login_timeout=8, timeout=12)
        cur = conn.cursor()
        cur.execute("select db_name(), suser_sname()")
        d, u = cur.fetchone()
        conn.close()
        return True, f"db={d} login={u}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def explain_failure(user: str, host: str, db: str, err: str) -> list[str]:
    tips, e = [], err.lower()
    if "18456" in e or "登录失败" in err or "login failed" in e:
        if re.fullmatch(r"\d{6,12}", user or ""):
            tips.append(f"账号 '{user}' 是纯数字，很像【客户端软件登录账号】（如 00010013 / 22020005），"
                        "不是数据库账号。数据库账号通常是 erp 或 sa。")
            tips.append("客户端登录账号只用于打开软件，不能直连数据库。")
        tips.append("账号或密码不正确。数据库密码与软件登录密码通常不是同一个。")
        tips.append("若已连续失败多次，SQL Server 可能临时锁定该账号：等几分钟，或联系 DBA 解锁。")
    elif "timeout" in e or "timed out" in e or "20009" in e:
        tips.append("连接超时：VPN 可能断了，或主机/端口不通。重连 VPN 后再试。")
    elif "4060" in e or "cannot open database" in e:
        tips.append(f"账号能登录但打不开库 '{db}'：库名不对或没有该库权限。")
    elif "20002" in e or "tds" in e:
        tips.append("TDS 握手失败：可能连到了非 SQL Server 端口，确认主机和端口。")
    return tips


# ------------------------------------------------------------------ 保存
def write_env(host: str, port: int, db: str, user: str, pwd: str) -> None:
    out = Path.home() / "Desktop" / "货物流向导出"
    ENV_FILE.write_text(
        "# 漓泉 cERP MCP 配置（本机私有，勿提交到仓库）\n"
        f"LQ_DB_HOST={host}\nLQ_DB_PORT={port}\nLQ_DB_USER={user}\n"
        f"LQ_DB_PASSWORD={pwd}\nLQ_DB_NAME={db}\n"
        "LQ_LINK=lqcerp_lnk\n"
        f"LQ_OUT_DIR={out}\n", encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)
    print(f"\n[4/4] ✓ 配置已保存：{ENV_FILE}（权限 600）")


def print_register() -> None:
    here = Path(__file__).resolve().with_name("server.py")
    print("\n下一步：把 MCP 注册到你的 Agent 客户端（Claude Desktop / WorkBuddy / Cursor 等）")
    print("在客户端的 MCP 配置里加入：\n")
    print(f'  "lq-flow": {{\n    "command": "python3",\n    "args": ["{here}"]\n  }}')
    print('\n如果客户端要求，再补 "type": "stdio" 和 "timeoutMs": 600000。')
    print("保存后重启客户端，对 agent 说「跑一下 preflight」即可验证。")


# ------------------------------------------------------------------ 主流程
def manual_input(host_hint: str, port: int) -> int:
    print("\n[4/4] 手工输入数据库账号")
    print("  说明：这里要【数据库账号】（和佳 ini 里的 LogId / LogPass），")
    print("        不是经销商系统的软件登录账号；两者不是一回事。")
    host = input(f"  主机 [{host_hint}]: ").strip() or host_hint
    db = input(f"  数据库 [{DEFAULT_DB}]: ").strip() or DEFAULT_DB
    user = input("  用户名: ").strip()
    pwd = getpass.getpass("  密码: ")
    ok, msg = try_login(host, port, db, user, pwd)
    if not ok:
        print(f"  ✗ 登录失败：{msg[:200]}")
        for tip in explain_failure(user, host, db, msg):
            print(f"    · {tip}")
        print("\n  如果拿不到数据库账号，向 IT / 系统管理员申请：")
        print("    - lqerp 库的只读账号")
        print("    - 链接服务器 lqcerp_lnk 的访问权限")
        return 3
    print(f"  ✓ 成功：{msg}")
    write_env(host, port, db, user, pwd)
    print_register()
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="漓泉 cERP MCP 安装向导")
    ap.add_argument("--check", action="store_true", help="只做连通性自检")
    ap.add_argument("--ini", help="指定和佳 ini 文件路径")
    ap.add_argument("--manual", action="store_true", help="跳过扫描，手工输入")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    print("=" * 60)
    print(" 漓泉 cERP MCP —— 安装配置向导")
    print("=" * 60)

    if args.check:
        check_vpn(DEFAULT_HOST, args.port)
        return 0

    # 1) VPN
    cands = [] if args.manual else collect_candidates(args.ini)
    host_hint = cands[0]["host"] if cands else DEFAULT_HOST
    if not check_vpn(host_hint, args.port):
        return 2

    # 2) 扫描客户端配置
    if args.manual:
        print("\n[2/4] 已跳过客户端配置扫描（--manual）")
    elif not cands:
        print("\n[2/4] 未找到和佳客户端配置")
        print("  没装经销商系统也可以使用，只是需要手工输入数据库账号；")
        print("  如果本机装过客户端但不在默认目录，可用 --ini 指定 hjapp.ini 路径。")
    else:
        print(f"\n[2/4] 扫描到 {len(cands)} 组连接信息")
        for it in cands:
            tag = "明文" if it["plain"] else "密文"
            print(f"  - {it['host']}/{it['db']} user={it['user']} 密码({tag}) "
                  f"来源={Path(it['file']).name}[{it['section']}]")

    # 3) 逐个试连
    tried = set()
    if cands:
        print("\n[3/4] 尝试连接数据库")
        for it in cands:
            if not it["plain"]:
                print(f"  · {it['host']}/{it['db']}: 密码是密文（客户端启动时才解密），跳过")
                continue
            key = (it["host"], it["db"], it["user"], it["passwd"])
            if key in tried:
                continue
            tried.add(key)
            ok, msg = try_login(it["host"], args.port, it["db"], it["user"], it["passwd"])
            if ok:
                print(f"  ✓ 成功：{it['host']}/{it['db']} user={it['user']}（{msg}）")
                write_env(it["host"], args.port, it["db"], it["user"], it["passwd"])
                print_register()
                return 0
            print(f"  ✗ 失败：{it['host']}/{it['db']} user={it['user']} → {msg[:120]}")
            for tip in explain_failure(it["user"], it["host"], it["db"], msg):
                print(f"      · {tip}")

    return manual_input(host_hint, args.port)


if __name__ == "__main__":
    sys.exit(main())

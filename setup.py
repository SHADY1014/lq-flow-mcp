#!/usr/bin/env python3
"""
漓泉 cERP MCP —— 首次安装配置向导

流程：
  1. 检查 VPN 连通性
  2. 扫描和佳客户端 ini（hjapp1.ini 明文优先），收集凭据与目标库
  3. 交叉试连（明文密码 × 主机/库），并**限制尝试次数**，避免把账号试锁
  4. 失败时按 SQL Server 错误码给结论（18456 密码错 / 18486 账号被锁 / 超时 VPN）
  5. 成功后写入 ~/.lq-flow.env（权限 600）

用法：
  python3 setup.py                  # 自动扫描 + 试连 + 保存
  python3 setup.py --ini <路径>      # 指定某个 ini
  python3 setup.py --manual         # 跳过扫描，手工输入
  python3 setup.py --user erp --password ***   # 直接给凭据，一行装完（推荐发给新同事）
  python3 setup.py --check          # 只做连通性自检
  python3 setup.py --diag           # 只诊断，不写配置
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

SEARCH_DIRS = [
    r"D:\经销商应用系统", r"D:\ERP应用系统", r"D:\hjerp",
    r"C:\经销商应用系统", r"C:\ERP应用系统", r"C:\hjerp",
    r"C:\Program Files\经销商应用系统", r"C:\Program Files (x86)\经销商应用系统",
]
INI_NAMES = ["hjapp1.ini", "hjapp.ini", "hjsoft.ini"]

# 连续失败上限：超过就停，避免把共享账号试锁（这是 18456 扩散的常见原因）
MAX_ATTEMPTS = 4


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
    print("    请先连接公司 VPN（TopSAP / EasyConnect），确认能访问内网后重试。")
    return False


def is_client_account(user: str) -> bool:
    """纯数字 6-12 位 → 大概率是经销商系统软件登录账号，不是 SQL 登录。"""
    return bool(re.fullmatch(r"\d{6,12}", (user or "").strip()))


# ------------------------------------------------------------------ ini 解析
def parse_ini(path: Path) -> list[dict]:
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
            "plain": bool(re.search(r"[A-Za-z]", pwd)),
            "section": it.get("section", ""), "file": it.get("file", ""),
        })
    return out


def scan_ini(explicit: str | None) -> list[dict]:
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
            files.extend(base.glob("*/hjapp.ini"))
    seen, out = set(), []
    for f in files:
        for it in parse_ini(f):
            key = (it["host"], it["db"], it["user"], it["passwd"])
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
    return out


def deep_scan_ini(timeout: int = 25, max_depth: int = 4) -> list[Path]:
    """默认目录找不到时，浅层扫 D:/C: 等盘找 hjapp1.ini（明文版）。

    限制递归深度与耗时，避免卡住；跳过系统目录。
    """
    import time

    skip = ("windows", "program files", "program files (x86)", "programdata",
            "users", "$recycle.bin", "system volume information", "appdata")
    found: list[Path] = []
    if os.name != "nt":
        return found
    roots = [Path(f"{d}:\\") for d in "CDEF" if Path(f"{d}:\\").exists()]
    t0 = time.time()
    for root in roots:
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() - t0 > timeout:
                return found
            cur = Path(dirpath)
            if len(cur.parts) - base_depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d.lower() not in skip][:80]
            for fn in filenames:
                if fn.lower() in ("hjapp1.ini", "hjapp.ini", "hjsoft.ini"):
                    found.append(cur / fn)
    return found


def build_plan(items: list[dict]) -> tuple[list[tuple], list[tuple]]:
    """返回 (凭据集合, 目标集合)，目标按 .19/lqerp 优先排序。"""
    creds = []
    for it in items:
        if it["plain"]:
            key = (it["user"], it["passwd"])
            if key not in creds:
                creds.append(key)
    targets = []
    for it in items:
        key = (it["host"], it["db"])
        if key not in targets:
            targets.append(key)
    default = (DEFAULT_HOST, DEFAULT_DB)
    targets = [default] + [t for t in targets if t != default]
    return creds, targets


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


def classify(err: str) -> tuple[str, str]:
    """返回 (类型, 结论提示)。"""
    e = (err or "").lower()
    if "18486" in e or "锁定" in err or "locked" in e:
        return "locked", "账号已被锁定（连续失败过多）。等 15 分钟或让 DBA 执行 ALTER LOGIN ... WITH PASSWORD 解锁后重试。"
    if "18456" in e or "登录失败" in err or "login failed" in e:
        return "badcred", "账号或密码不对；或账号被锁（锁定时也常回 18456）。别再连续重试。"
    if "timeout" in e or "timed out" in e or "20009" in e:
        return "network", "连接超时：VPN 断了或主机/端口不通。"
    if "4060" in e or "cannot open database" in e:
        return "nodatabase", "账号能登录但库打不开：库名不对或没有该库权限。"
    if "20002" in e or "tds" in e:
        return "tds", "TDS 握手失败：可能连到了非 SQL Server 端口。"
    return "other", "未知错误，见上方原文。"


def write_env(host: str, port: int, db: str, user: str, pwd: str) -> None:
    ENV_FILE.write_text(
        "# 漓泉 cERP MCP 配置（本机私有，勿提交到仓库）\n"
        f"LQ_DB_HOST={host}\nLQ_DB_PORT={port}\nLQ_DB_USER={user}\n"
        f"LQ_DB_PASSWORD={pwd}\nLQ_DB_NAME={db}\n"
        "LQ_LINK=lqcerp_lnk\n"
        f"LQ_OUT_DIR={Path.home() / 'Desktop' / '货物流向导出'}\n", encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)
    print(f"\n[4/4] ✓ 配置已保存：{ENV_FILE}（权限 600）")


def print_register() -> None:
    here = Path(__file__).resolve().with_name("server.py")
    print("\n下一步：把 MCP 注册到你的 Agent 客户端（Claude Desktop / WorkBuddy / Cursor 等）")
    print("在客户端的 MCP 配置里加入：\n")
    print(f'  "lq-flow": {{\n    "command": "python3",\n    "args": ["{here}"]\n  }}')
    print('\n如果客户端要求，再补 "type": "stdio" 和 "timeoutMs": 600000。')
    print("保存后重启客户端，对 agent 说「跑一下 preflight」即可验证。")


def print_lockout_help() -> None:
    print("\n结论：这个共享账号很可能已经被多次失败试锁了（18456 常见于此）。")
    print("  1. 停止继续尝试，等 15 分钟再试；")
    print("  2. 或让 DBA 解锁：ALTER LOGIN [erp] WITH PASSWORD = '<原密码>'；")
    print("  3. 最省事的办法：找发放人拿「懒人包」，里面已带可用账号，安装时零输入。")


# ------------------------------------------------------------------ 主流程
def manual_input(host_hint: str, port: int) -> int:
    print("\n[4/4] 手工输入数据库账号")
    print("  要的是【数据库账号】（和佳 ini 里的 LogId / LogPass），不是软件登录账号。")
    host = input(f"  主机 [{host_hint}]: ").strip() or host_hint
    db = input(f"  数据库 [{DEFAULT_DB}]: ").strip() or DEFAULT_DB
    user = input("  用户名: ").strip()
    if is_client_account(user):
        print(f"  ✗ '{user}' 是纯数字，属于经销商系统软件登录账号，SQL Server 不认。")
        print("    数据库账号通常是 erp（或 sa），请查 hjapp1.ini 的 LogId。")
        return 3
    pwd = getpass.getpass("  密码: ")
    ok, msg = try_login(host, port, db, user, pwd)
    if ok:
        print(f"  ✓ 成功：{msg}")
        write_env(host, port, db, user, pwd)
        print_register()
        return 0
    kind, hint = classify(msg)
    print(f"  ✗ 登录失败：{msg[:200]}")
    print(f"    → {hint}")
    if kind in ("badcred", "locked"):
        print_lockout_help()
    return 3


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="漓泉 cERP MCP 安装向导")
    ap.add_argument("--check", action="store_true", help="只做连通性自检")
    ap.add_argument("--diag", action="store_true", help="只诊断，不写配置")
    ap.add_argument("--ini", help="指定和佳 ini 文件路径")
    ap.add_argument("--manual", action="store_true", help="跳过扫描，手工输入")
    ap.add_argument("--user", help="直接指定数据库用户名（如 erp），跳过 ini 扫描")
    ap.add_argument("--password", help="直接指定数据库密码；也可用环境变量 LQ_DB_PASSWORD")
    ap.add_argument("--host", help=f"数据库主机，默认 {DEFAULT_HOST}")
    ap.add_argument("--db", help=f"数据库名，默认 {DEFAULT_DB}")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    user = (args.user or "").strip()
    pwd = args.password if args.password is not None else os.getenv("LQ_DB_PASSWORD", "")
    host = (args.host or DEFAULT_HOST).strip()
    db = (args.db or DEFAULT_DB).strip()

    print("=" * 60)
    print(" 漓泉 cERP MCP —— 安装配置向导")
    print("=" * 60)

    if args.check:
        check_vpn(host, args.port)
        return 0

    # 直接给凭据：不扫盘、不试错，只连一次
    if user and pwd:
        if is_client_account(user):
            print(f"\n✗ '{user}' 是纯数字，属于经销商系统软件登录账号，SQL Server 不认。")
            print("  要的是 hjapp1.ini 里的 LogId / LogPass（通常是 erp）。")
            return 3
        if not check_vpn(host, args.port):
            return 2
        print(f"\n[2/4] 跳过扫描（已指定 --user）")
        print(f"\n[3/4] 试连 {host}:{args.port}/{db} user={user}")
        ok, msg = try_login(host, args.port, db, user, pwd)
        if ok:
            print(f"  ✓ 成功：{msg}")
            if args.diag:
                print("\n[done] 诊断结束（--diag，未写配置）")
                return 0
            write_env(host, args.port, db, user, pwd)
            print_register()
            return 0
        kind, hint = classify(msg)
        print(f"  ✗ 登录失败：{msg[:200]}")
        print(f"    → {hint}")
        if kind in ("badcred", "locked"):
            print_lockout_help()
        return 3

    items = [] if args.manual else scan_ini(args.ini)
    host_hint = items[0]["host"] if items else host
    if not check_vpn(host_hint, args.port):
        return 2

    if args.manual:
        print("\n[2/4] 已跳过客户端配置扫描（--manual）")
    elif not items:
        print("\n[2/4] 默认目录未找到和佳客户端配置，正在浅层扫描磁盘（最多 25 秒）...")
        found = deep_scan_ini()
        if found:
            print(f"  找到 {len(found)} 个候选配置文件：")
            for f in found[:6]:
                print(f"    - {f}")
            items = []
            for f in found:
                items.extend(parse_ini(f))
            seen = set()
            uniq = []
            for it in items:
                k = (it["host"], it["db"], it["user"], it["passwd"])
                if k not in seen:
                    seen.add(k)
                    uniq.append(it)
            items = uniq
        if not items:
            print("  仍未找到可用的明文配置。下一步二选一：")
            print("    1) 向发放人要「懒人包」（包内已带账号，零输入）")
            print("    2) 找 IT/DBA 要 lqerp 的只读数据库账号")
            print("  也可用 --ini 指定某个 hjapp1.ini 路径。")
    else:
        print(f"\n[2/4] 扫描到 {len(items)} 条连接信息")
        for it in items:
            tag = "明文" if it["plain"] else "密文"
            print(f"  - {it['host']}/{it['db']} user={it['user']} 密码({tag}) "
                  f"来源={Path(it['file']).name}[{it['section']}]")

    # 3) 交叉试连（有次数上限）
    creds, targets = build_plan(items)
    attempts = 0
    badcred_by_user: dict[str, int] = {}
    first_failure: tuple[str, str] | None = None
    if creds and targets:
        print("\n[3/4] 交叉试连（每次失败都会累计到账号锁定阈值，已限制次数）")
        for host, db in targets:
            for user, pwd in creds:
                if is_client_account(user):
                    print(f"  · 跳过 {user}（纯数字=客户端账号，SQL 不认）")
                    continue
                if attempts >= MAX_ATTEMPTS:
                    print(f"  · 已达尝试上限 {MAX_ATTEMPTS} 次，停止，避免把账号试锁")
                    break
                attempts += 1
                ok, msg = try_login(host, args.port, db, user, pwd)
                if ok:
                    print(f"  ✓ 成功：{host}/{db} user={user}（{msg}）")
                    if not args.diag:
                        write_env(host, args.port, db, user, pwd)
                        print_register()
                    return 0
                kind, hint = classify(msg)
                print(f"  ✗ {host}/{db} user={user} → {msg[:90]}")
                print(f"      → {hint}")
                if first_failure is None:
                    first_failure = (kind, msg)
                if kind in ("badcred", "locked"):
                    badcred_by_user[user] = badcred_by_user.get(user, 0) + 1
                    # 同一账号连续 2 次密码错误就停，避免把共享账号试锁
                    if badcred_by_user[user] >= 2 or kind == "locked":
                        print(f"  · 账号 {user} 已连续失败 {badcred_by_user[user]} 次，停止尝试")
                        print_lockout_help()
                        return 3
            if attempts >= MAX_ATTEMPTS:
                break

    if args.diag:
        print("\n[done] 诊断结束（--diag，未写配置）")
        return 0 if not attempts else 3

    # 4) 手工
    rc = manual_input(host_hint, args.port)
    if rc != 0 and first_failure and first_failure[0] in ("badcred", "locked"):
        print_lockout_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
lq-flow-mcp 自检脚本（回归用）

覆盖：工具注册完整性、网页版目录扫描不再抛 NameError、字符集解码、数据库连通。

用法：
  python3 selftest.py
退出码 0 = 全部通过；非 0 = 有失败项。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

failures: list[str] = []
passes: list[str] = []


def check(name: str, fn):
    try:
        fn()
        passes.append(name)
        print(f"  ✓ {name}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{name}: {exc}")
        print(f"  ✗ {name} → {exc}")


def main() -> int:
    import server

    print("[1] 工具注册")

    def tools_ok():
        names = {t.name for t in server.mcp._tool_manager.list_tools()}
        required = {
            "preflight", "flow_query", "flow_export", "flow_export_async",
            "refresh_catalog", "report_catalog", "report_detail", "export_catalog_table",
            "check_office_changes", "refresh_regions",
            "dms_status", "dms_fetch", "dms_flow_report",
            "oa_status", "oa_menu", "oa_services", "oa_user", "oa_send_message", "oa_flow_list",
        }
        missing = required - names
        if missing:
            raise AssertionError(f"缺少工具: {sorted(missing)}")
        if len(names) < 27:
            raise AssertionError(f"工具数偏少: {len(names)}")

    check("工具齐全（>=27 且关键工具在册）", tools_ok)

    print("[2] 网页版目录扫描不得抛 NameError")

    def web_no_nameerror():
        r = server.refresh_catalog("web")
        if not isinstance(r, dict):
            raise AssertionError("refresh_catalog 返回非字典")
        # 无会话时应是友好错误，而不是 NameError 文本
        err = str(r.get("web_error") or "")
        if "NameError" in err or "not defined" in err:
            raise AssertionError(f"仍是代码层异常: {err}")
        if r.get("web_count") is None and not err:
            raise AssertionError("既没数量也没错误说明")

    check("refresh_catalog('web') 优雅降级", web_no_nameerror)

    print("[3] 一期 ERP 菜单目录")

    def erp1_ok():
        items = server._scan_erp1_catalog()
        if len(items) < 800:
            raise AssertionError(f"一期菜单偏少: {len(items)}")
        bad = [x for x in items if x.get("库") != "lqylerp"]
        if bad:
            raise AssertionError(f"一期条目的库标记不是 lqylerp: {bad[0]}")

    check("一期菜单扫描（lqylerp.ctlm0011）", erp1_ok)

    print("[4] 数据库与字符集")

    def db_reachable():
        pf = server.preflight()
        if not pf.get("db_reachable"):
            raise AssertionError(f"库不可达: {pf.get('advice')}")

    check("preflight 数据库可达", db_reachable)

    def charset_ok():
        _, rows, _ = server._query_readonly(
            "select rtrim(dept_name) from ctlm1003 where dept_id='200301'", 1)
        if not rows:
            raise AssertionError("查不到 200301")
        name = str(rows[0][0])
        if "南宁" not in name:
            raise AssertionError(f"中文解码异常: {name!r}")

    check("varchar 中文解码（GBK）", charset_ok)

    print("[5] 导出防误导")

    def guard_ok():
        r = server.export_catalog_table("saft04", "不存在的报表名", preview_only=True, allow_fuzzy=True)
        if r.get("ok"):
            raise AssertionError("错误报表名竟然被放行")
        if not r.get("candidates"):
            raise AssertionError("拒绝时未给出候选")

    check("错名导出被拒并给候选", guard_ok)

    print()
    print(f"通过 {len(passes)} 项，失败 {len(failures)} 项")
    for f in failures:
        print("  - " + f)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())

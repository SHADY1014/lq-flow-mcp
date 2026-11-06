#!/usr/bin/env python3
"""
漓泉 cERP 通用 ERP 网关 MCP

链路:
  本机 --(公司 VPN)--> <DB_HOST> : SQL Server lqerp（本地库 + 各业务库）
                    └─ 链接服务器 lqcerp_lnk --> 三期 ERP 库（lqcerp）
                    └─ 链接服务器 lqbi_lnk   --> BI 库
                    └─ 链接服务器 lqdrp_lnk  --> 一期 ERP 库（lqdrp）

工具:
  list_offices        办事处清单
  list_reports        报表清单（可搜索，标注是否有存储过程可直接跑）
  flow_query          货物流向明细（标准 86 列）
  flow_summary        货物流向汇总
  flow_export         货物流向按客户端格式导出 xlsx
  run_report_proc     执行任意 proc_ 报表存储过程（.86）
  query_readonly      只读 SQL（默认 lqerp，可用四段名跨库）
  export_query        SQL / 存储过程结果导出 xlsx

环境变量(或用 ~/.lq-flow.env):
  LQ_DB_HOST / LQ_DB_PORT / LQ_DB_USER / LQ_DB_PASSWORD / LQ_DB_NAME
  LQ_LINK= lqcerp_lnk   LQ_OUT_DIR= 导出目录
"""
import os
import re
import json
import time
import threading
import uuid
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymssql
from mcp.server.mcpserver import MCPServer


# ---------------------------------------------------------------- 配置
def _load_env():
    p = Path.home() / ".lq-flow.env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

DB_HOST = os.getenv("LQ_DB_HOST", "")
DB_PORT = int(os.getenv("LQ_DB_PORT", "1433"))
DB_USER = os.getenv("LQ_DB_USER", "erp")
DB_PASSWORD = os.getenv("LQ_DB_PASSWORD", "")
DB_NAME = os.getenv("LQ_DB_NAME", "lqerp")
LINK = os.getenv("LQ_LINK", "lqcerp_lnk")
FLOW_PROC = os.getenv("LQ_PROC", "proc_d_app_cinvout_zone_01_new_bi_dept")
DEFAULT_OUT = os.getenv("LQ_OUT_DIR", str(Path.home() / "Desktop" / "漓泉" / "货物流向导出"))
REGIONS_FILE = Path(__file__).resolve().parent / "regions.json"
FLOW_CHUNK = max(1, int(os.getenv("LQ_FLOW_CHUNK", "1")))
FLOW_WORKERS = max(1, int(os.getenv("LQ_FLOW_WORKERS", "8")))


# ---------------------------------------------------------------- 货物流向标准列
COLUMNS = [
    ("年", "fiscal_year"), ("月", "fiscal_period"),
    ("办事处代码", "dept_id"), ("办事处名称", "dept_name"),
    ("地区代码", "zone_id"), ("地区名称", "zone_name"),
    ("县市代码", "id_dept"), ("县市名称", "name_dept"),
    ("经销商代码", "corr_id"), ("经销商名称", "corr_name"),
    ("渠道", "ptertype_name"), ("线上平台", "terlv_channel"),
    ("终端代码", "terminal_id"), ("终端名称", "terminal_name"),
    ("关注状态", "flag_gz"), ("终端状态", "tersts_name"),
    ("终端地址", "address"), ("联系人", "linkman"),
    ("业务员", "name_clerk"), ("主管", "name_leader"),
    ("联系电话", "phone_code"), ("品牌", "brand_name"),
    ("档次", "level_id"), ("产品代码", "item_id"),
    ("产品名称", "item_name"), ("终端类型代码", "tertype_id"),
    ("终端类型名称", "tertype_name"), ("终端类型内级别", "terlv_tertype"),
    ("核心大点", "flag_larger"), ("连片夜市", "street_id"),
    ("连片夜市名称", "street_name"), ("市场地位", "market_type"),
    ("专销类型", "prottype_name"), ("热点签约类型", "signtype_name"),
    ("竞品终端", "flagCompetitor"), ("线路", "weekday"),
    ("线上销量（件）", "xs_qty"), ("线下销量（件）", "xx_qty"), ("销量（件）", "opr_qty"),
    ("有奖盖（件）", "yjg_qty"), ("兑奖（件）", "dj_qty"),
    ("陈列（件）", "cl_qty"), ("陈列不计量（件）", "clbjl_qty"),
    ("陈列现配(件)", "clxp_qty"), ("陈列现配(不计量)(件)", "clxpbjl_qty"),
    ("开点（件）", "kd_qty"), ("配送（件）", "ps_qty"),
    ("热点赠酒（件）", "rdzj_qty"), ("热点现配（件）", "rdxp_qty"),
    ("热点不计量（件）", "rdbjl_qty"), ("免费赠酒（件）", "mfzj_qty"),
    ("二批赠酒（件）", "epzj_qty"), ("赠饮直配（件）", "zyzp_qty"),
    ("免饮赠酒（件）", "mzyj_qty"), ("小型公关（件）", "gg_qty"),
    ("一坨钱赠酒(件)", "ytq_qty"), ("B2C赠酒(件)", "b2czj_qty"),
    ("赠品合计（件）（不计量）", "zp_noqty"), ("赠品合计（件）", "zp_qty"),
    ("合计（件）（不计销量）", "sum_noqty"), ("合计（件）", "sum_qty"),
    ("线上销量（吨）", "xs_tqty"), ("线下销量（吨）", "xx_tqty"), ("销量（吨）", "opr_tqty"),
    ("兑奖（吨）", "dj_tqty"), ("有奖盖（吨）", "yjg_tqty"),
    ("陈列（吨）", "cl_tqty"), ("陈列不计量（吨）", "clbjl_tqty"),
    ("陈列现配(吨)", "clxp_tqty"), ("陈列现配(不计量)(吨)", "clxpbjl_tqty"),
    ("开点（吨）", "kd_tqty"), ("配送（吨）", "ps_tqty"),
    ("热点赠酒（吨）", "rdzj_tqty"), ("热点现配（吨）", "rdxp_tqty"),
    ("热点不计量（吨）", "rdbjl_tqty"), ("免费赠酒(吨)", "mfzj_tqty"),
    ("二批赠酒(吨)", "epzj_tqty"), ("赠饮直配（吨）", "zyzp_tqty"),
    ("免饮赠酒（吨）", "mzyj_tqty"), ("小型公关（吨）", "gg_tqty"),
    ("一坨钱赠酒(吨)", "ytq_tqty"), ("B2C赠酒(吨)", "b2czj_tqty"),
    ("赠品合计（吨）（不计销量)", "zp_notqty"), ("赠品合计（吨）", "zp_tqty"),
    ("合计（吨）（不计销量）", "sum_notqty"), ("合计（吨）", "sum_tqty"),
]
EN2ZH = {en: zh for zh, en in COLUMNS}
NUMERIC = {zh for zh, en in COLUMNS if en.endswith("_qty") or en.endswith("_tqty")}
SUMMARY_FIELDS = [
    ("销量（件）", "opr_qty"), ("销量（吨）", "opr_tqty"),
    ("线上销量（件）", "xs_qty"), ("线下销量（件）", "xx_qty"),
    ("赠品合计（件）", "zp_qty"), ("合计（件）", "sum_qty"), ("合计（吨）", "sum_tqty"),
]

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|merge|exec|execute|"
    r"grant|deny|revoke|backup|restore|shutdown|xp_cmdshell|sp_configure|bulk)\b",
    re.I,
)


# ---------------------------------------------------------------- DB 基础
def _connect(autocommit=False, database=None, timeout=3600):
    if not DB_HOST or not DB_PASSWORD:
        raise RuntimeError(
            "尚未配置数据库连接。请先运行：python3 setup.py\n"
            "（会先检查 VPN，再提示输入经销商系统数据库账号）"
        )
    return pymssql.connect(
        server=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=database or DB_NAME, tds_version="7.0",
        login_timeout=10, timeout=timeout, autocommit=autocommit,
    )


_rpc_lock = threading.Lock()
_rpc_ready = False


def _ensure_rpc_out(cur):
    """链接服务器 rpc out 只需打开一次；并发时只让一个线程去改。"""
    global _rpc_ready
    if _rpc_ready:
        return
    with _rpc_lock:
        if _rpc_ready:
            return
        cur.execute("select is_rpc_out_enabled from sys.servers where name=%s", (LINK,))
        row = cur.fetchone()
        if not (row and row[0]):
            cur.execute(f"exec sp_serveroption '{LINK}','rpc out','true'")
        _rpc_ready = True


def _exec_remote(stmt: str):
    """在 .86 上执行语句（首次调用时打开链接服务器 rpc out，之后保持开启）。"""
    conn = _connect(autocommit=True, timeout=7200)
    cur = conn.cursor()
    try:
        _ensure_rpc_out(cur)
        cur.execute(stmt)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall() if cur.description else []
        return cols, rows
    finally:
        conn.close()


def _remote_exec_string(command: str) -> str:
    """把远端命令包成 EXEC('...') AT link。"""
    return "EXEC('" + command.replace("'", "''") + "') AT " + LINK


def _month_range(year: int, month: int, day: int | None):
    start = dt.date(year, month, day or 1)
    if day:
        end = start + dt.timedelta(days=1)
    elif month == 12:
        end = dt.date(year + 1, 1, 1)
    else:
        end = dt.date(year, month + 1, 1)
    return start, end


def _clean(value, en=""):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if en == "flag_larger":
            return {"Y": "是", "N": "否"}.get(s, s)
        return s
    return value


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, str):
        return v.strip()
    return v


def _to_record(cols, row):
    ci = {n: i for i, n in enumerate(cols)}
    return {zh: (_clean(row[ci[en]], en) if en in ci else None) for zh, en in COLUMNS}


def _filter_channel(recs, channel):
    if not channel:
        return recs
    want = [c.strip().upper() for c in re.split(r"[,，/]", str(channel)) if c.strip()]
    return [r for r in recs if str(r.get("渠道") or "").strip().upper() in want]


def _summary(recs):
    out = {zh: round(sum(float(r.get(zh) or 0) for r in recs), 4) for zh, _ in SUMMARY_FIELDS}
    out.update({
        "行数": len(recs),
        "终端数": len({r["终端代码"] for r in recs}),
        "经销商数": len({r["经销商代码"] for r in recs}),
        "产品数": len({r["产品代码"] for r in recs}),
    })
    return out


def _run_flow(year, month, office_codes=None, day=None):
    start, end = _month_range(year, month, day)
    cond = f" 3=3 and a.out_time >= '{start}' and a.out_time < '{end}'"
    if office_codes:
        codes = ",".join("'" + str(c).strip() + "'" for c in office_codes)
        cond += f" and a.zone_id in ({codes})"
    command = f"use lqcerp; exec dbo.{FLOW_PROC} @Param1='" + cond.replace("'", "''") + "'"
    return _exec_remote(_remote_exec_string(command))


# ---------------------------------------------------------------- 大区口径
def _load_regions() -> dict:
    if REGIONS_FILE.exists():
        return json.loads(REGIONS_FILE.read_text(encoding="utf-8"))
    return {"分公司": {}, "别名": {}}


REGIONS = _load_regions()


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _iter_offices():
    for comp, c in REGIONS.get("分公司", {}).items():
        for dq, offices in c.get("大区", {}).items():
            for o in offices:
                yield comp, dq, o


def _region_codes(name: str) -> list[str]:
    """把分公司/大区/别名解析成 ERP 办事处代码。别名见 regions.json。"""
    key = _norm(name)
    if not key:
        raise ValueError("region 为空")
    alias = {_norm(k): [_norm(x) for x in v] for k, v in REGIONS.get("别名", {}).items()}
    comps = {_norm(c) for c in REGIONS.get("分公司", {})}
    dqs = {_norm(dq) for _, dq, _ in _iter_offices()}
    if key in alias:
        mode, targets = "mixed", set(alias[key])
    elif key in comps:
        mode, targets = "comp", {key}
    elif key in dqs:
        mode, targets = "dq", {key}
    else:
        names = sorted(comps | dqs | set(REGIONS.get("别名", {})))
        raise ValueError(f"未知大区/分公司: {name}；可用: {', '.join(names)}")

    picked = []
    for comp, dq, o in _iter_offices():
        if mode == "dq":
            hit = _norm(dq) in targets
        elif mode == "comp":
            hit = _norm(comp) in targets
        else:
            hit = _norm(comp) in targets or _norm(dq) in targets
        if hit:
            picked.append(o)
    codes = [str(o.get("ERP代码") or "").strip() for o in picked]
    missing = [o["名称"] for o, c in zip(picked, codes) if not c]
    if missing:
        raise ValueError(f"{name} 下有办事处未映射 ERP 代码: {'、'.join(missing)}")
    return list(dict.fromkeys(codes))


def _office_region() -> dict[str, dict]:
    return {str(o.get("ERP代码") or "").strip(): {"分公司": comp, "大区": dq, "办事处": o["名称"]}
            for comp, dq, o in _iter_offices() if o.get("ERP代码")}


def _resolve_codes(office_codes, region) -> list[str] | None:
    codes = [c for c in re.split(r"[,，\s]+", office_codes or "") if c]
    if region:
        codes += _region_codes(region)
    out = list(dict.fromkeys(c.strip() for c in codes if c.strip()))
    return out or None


def _run_flow_retry(year, month, chunk, day=None, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            return _run_flow(year, month, chunk, day)
        except Exception as e:  # 连接抖动/瞬时超时
            last = e
            if i < retries:
                time.sleep(5 * (i + 1))
    raise last


def _run_flow_multi(year, month, office_codes, day=None, channel=None, progress=None):
    """按办事处分片并发跑货物流向，返回已按渠道过滤的记录。"""
    codes = list(dict.fromkeys(office_codes or []))
    if not codes:
        cols, rows = _run_flow(year, month, None, day)
        return _filter_channel([_to_record(cols, r) for r in rows], channel)
    chunks = [codes[i:i + FLOW_CHUNK] for i in range(0, len(codes), FLOW_CHUNK)]
    results: dict[int, list] = {}
    with ThreadPoolExecutor(max_workers=min(FLOW_WORKERS, len(chunks))) as ex:
        futs = {ex.submit(_run_flow_retry, year, month, ch, day): (i, ch)
                for i, ch in enumerate(chunks)}
        for f in as_completed(futs):
            i, ch = futs[f]
            cols, rows = f.result()
            results[i] = _filter_channel([_to_record(cols, r) for r in rows], channel)
            if progress:
                progress(len(results), len(chunks), ch, len(results[i]))
    out = []
    for i in sorted(results):
        out.extend(results[i])
    return out


def _check_sql(sql: str) -> str:
    s = (sql or "").strip()
    if not s:
        raise ValueError("SQL 为空")
    while s.endswith(";"):
        s = s[:-1].rstrip()
    if ";" in s:
        raise ValueError("只允许单条语句")
    if not re.match(r"(?is)^\s*(select|with)\b", s):
        raise ValueError("只允许 SELECT / WITH 查询")
    if FORBIDDEN.search(s):
        raise ValueError("包含被禁止的关键字（写操作/执行/权限）")
    if re.search(r"(?is)\binto\s+[\[\w#]", s):
        raise ValueError("不允许 SELECT INTO")
    return s


def _query_readonly(sql: str, limit: int, database=None):
    s = _check_sql(sql)
    lim = max(1, min(int(limit), 10000))
    conn = _connect(autocommit=True, database=database, timeout=600)
    cur = conn.cursor()
    try:
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        cur.execute(s)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(lim + 1) if cur.description else []
        truncated = len(rows) > lim
        return cols, rows[:lim], truncated
    finally:
        conn.close()


def _write_xlsx(path: Path, cols, rows, sheet="数据", total_row=False):
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(list(cols))
    fill = PatternFill("solid", fgColor="DDEBF7")
    for i in range(1, len(cols) + 1):
        c = ws.cell(1, i)
        c.font = Font(bold=True)
        c.fill = fill
        c.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append([_jsonable(v) for v in r])
    if total_row and rows:
        total = ["合计"] + [""] * (len(cols) - 1)
        for i in range(1, len(cols)):
            vals = []
            for r in rows:
                try:
                    vals.append(float(r[i] or 0))
                except (TypeError, ValueError):
                    vals = None
                    break
            if vals:
                total[i] = round(sum(vals), 8)
        ws.append(total)
    ws.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


# ---------------------------------------------------------------- MCP
mcp = MCPServer(
    name="lq-flow",
    version="0.3.0",
    instructions=(
        "漓泉 cERP ERP 通用网关。工具: list_regions(大区/分公司/办事处口径) / "
        "list_offices(ERP办事处) / list_reports(报表清单) / "
        "flow_query, flow_summary, flow_export(货物流向，支持 region=广西/桂北公司/南宁大区) / "
        "run_report_proc(跑报表存储过程) / query_readonly(只读SQL) / export_query(导出xlsx)。"
        "按大区取数必须用 region，不要手挑办事处。需要公司 VPN（TopSAP）在线。"
    ),
)


@mcp.tool()
def list_offices(year: int | None = None, month: int | None = None) -> dict:
    """列出办事处(代码/名称)。给年月则只返回该期间有出库的办事处。"""
    conn = _connect()
    cur = conn.cursor()
    try:
        if year and month:
            start, end = _month_range(year, month, None)
            cur.execute(
                """select distinct c.dept_id, rtrim(c.dept_name)
                   from cinvout_02 a with(nolock)
                   join ctlm2005 m with(nolock) on a.corr_id = m.corr_id
                   join ctlm1003 c with(nolock) on m.dept_id = c.dept_id
                   where a.sts_flag='S' and a.out_time>=%s and a.out_time<%s
                   order by c.dept_id""", (start, end))
        else:
            cur.execute("""select dept_id, rtrim(dept_name) from ctlm1003 with(nolock)
                           where len(rtrim(dept_id))=6 order by dept_id""")
        rows = [{"办事处代码": str(r[0]).strip(), "办事处名称": str(r[1]).strip()} for r in cur.fetchall()]
        return {"count": len(rows), "offices": rows}
    finally:
        conn.close()


@mcp.tool()
def list_reports(keyword: str | None = None, only_runnable: bool = False,
                 limit: int = 100) -> dict:
    """列出 ERP 报表菜单。only_runnable=True 只返回数据库里能找到对应存储过程的。"""
    conn = _connect(timeout=300)
    cur = conn.cursor()
    try:
        kw = (keyword or "").strip()
        sql = """select rtrim(menu_name), rtrim(table_id), rtrim(model_window)
                 from lqcerp_lnk.lqcerp.dbo.hjappmenu
                 where rtrim(isnull(table_id,''))<>'' and model_window like 'w_app%'"""
        params = []
        if kw:
            sql += " and menu_name like %s"
            params.append(f"%{kw}%")
        sql += " order by menu_name"
        cur.execute(sql, tuple(params))
        menus = cur.fetchall()

        cur.execute("select name from lqcerp_lnk.lqcerp.sys.procedures where name like 'proc_%'")
        procs = [r[0] for r in cur.fetchall()]

        items = []
        for name, tid, win in menus:
            proc = None
            if tid:
                for cand in (f"proc_{tid}", f"proc_{tid}_new", f"proc_{tid}_bi_dept"):
                    if cand in procs:
                        proc = cand
                        break
                if not proc:
                    hits = [p for p in procs if tid in p]
                    proc = hits[0] if hits else None
            if only_runnable and not proc:
                continue
            items.append({
                "报表名称": str(name).strip(),
                "table_id": str(tid).strip(),
                "窗口": str(win).strip(),
                "可直连存储过程": proc,
            })
        return {"count": len(items), "reports": items[:max(1, min(limit, 500))]}
    finally:
        conn.close()


@mcp.tool()
def list_regions(company: str | None = None, region: str | None = None) -> dict:
    """按 OA 大区划分表列出分公司/大区/办事处（含 ERP 代码）。

    company/region 可留空看全量；region 名称不存在时会报错并列出可选值。
    广西 = 桂北公司 + 桂南公司（不含特通 KA广西）。
    """
    want_comp = _norm(company) if company else None
    picked = set(_region_codes(region)) if region else None
    items = []
    for comp, dq, o in _iter_offices():
        if want_comp and _norm(comp) != want_comp:
            continue
        if picked is not None and str(o.get("ERP代码") or "").strip() not in picked:
            continue
        items.append({"分公司": comp, "大区": dq, "办事处": o["名称"],
                      "OA部门编码": o.get("OA部门编码"), "ERP代码": o.get("ERP代码")})
    return {"count": len(items),
            "companies": sorted(REGIONS.get("分公司", {})),
            "aliases": REGIONS.get("别名", {}),
            "items": items}


@mcp.tool()
def flow_query(year: int, month: int, office_codes: str | None = None,
               region: str | None = None, day: int | None = None,
               channel: str | None = None, limit: int = 200, offset: int = 0) -> dict:
    """货物流向明细(标准 86 列)。office_codes 逗号分隔；region 用大区/分公司名
    （如 广西、桂北公司、南宁大区）。两者可同时给，取并集。"""
    codes = _resolve_codes(office_codes, region)
    if not codes:
        raise ValueError("office_codes 或 region 至少给一个")
    recs = _run_flow_multi(year, month, codes, day, channel)
    page = recs[offset:offset + max(1, min(limit, 2000))]
    return {"columns": [zh for zh, _ in COLUMNS], "total_rows": len(recs),
            "returned": len(page), "offset": offset, "office_codes": codes,
            "summary": _summary(recs), "rows": page}


@mcp.tool()
def flow_summary(year: int, month: int, office_codes: str | None = None,
                 region: str | None = None, day: int | None = None,
                 channel: str | None = None, group_by: str = "office") -> dict:
    """货物流向汇总。group_by: office / region / channel / dealer / product / none。
    region 用大区/分公司名（如 广西）。"""
    codes = _resolve_codes(office_codes, region)
    if not codes:
        raise ValueError("office_codes 或 region 至少给一个")
    recs = _run_flow_multi(year, month, codes, day, channel)

    if group_by == "region":
        zmap = _office_region()
        for r in recs:
            m = zmap.get(str(r.get("办事处代码") or "").strip(), {})
            r["分公司"], r["大区"] = m.get("分公司", ""), m.get("大区", "")
    keys = {"office": ["办事处代码", "办事处名称"], "region": ["分公司", "大区"],
            "channel": ["渠道"],
            "dealer": ["办事处代码", "经销商代码", "经销商名称"],
            "product": ["产品代码", "产品名称"], "none": []}.get(group_by, ["办事处代码", "办事处名称"])
    if not keys:
        return {"group_by": "none", "summary": _summary(recs)}

    groups: dict[Any, dict] = {}
    for r in recs:
        k = tuple(r.get(x) for x in keys)
        g = groups.setdefault(k, {"行数": 0})
        for zh, _ in SUMMARY_FIELDS:
            g.setdefault(zh, 0.0)
            g[zh] = round(g[zh] + float(r.get(zh) or 0), 4)
        g["行数"] += 1
    items = []
    for k, v in groups.items():
        d = dict(zip(keys, k))
        d.update(v)
        items.append(d)
    items.sort(key=lambda d: -float(d.get("销量（件）") or 0))
    return {"group_by": group_by, "count": len(items), "total": _summary(recs), "items": items}


def _export_impl(year, month, codes, day, channel, out_dir, file_name, tag, progress=None):
    recs = _run_flow_multi(year, month, codes, day, channel, progress)
    name = file_name or (f"货物流向_{tag}_{year}{month:02d}"
                         + (f"{day:02d}" if day else "")
                         + (f"_{channel}" if channel else "") + ".xlsx")
    path = Path(out_dir or DEFAULT_OUT) / name

    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill
    hdr = [zh for zh, _ in COLUMNS]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "货物流向"
    ws.append(hdr)
    fill = PatternFill("solid", fgColor="DDEBF7")
    for i in range(1, len(hdr) + 1):
        c = ws.cell(1, i)
        c.font = Font(bold=True)
        c.fill = fill
        c.alignment = Alignment(horizontal="center")
    for rec in recs:
        ws.append([rec.get(zh) for zh in hdr])
    total = ["合计", ""] + [""] * (len(hdr) - 2)
    for i, zh in enumerate(hdr):
        if zh in NUMERIC:
            total[i] = round(sum(float(r.get(zh) or 0) for r in recs), 8)
    ws.append(total)
    for row in ws.iter_rows(min_row=2):
        for i, cell in enumerate(row):
            if hdr[i] in NUMERIC:
                cell.number_format = "0.00000000"
    ws.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {"ok": True, "path": str(path), "rows": len(recs), "summary": _summary(recs)}


@mcp.tool()
def flow_export(year: int, month: int, office_codes: str | None = None,
                region: str | None = None, day: int | None = None,
                channel: str | None = None, out_dir: str | None = None,
                file_name: str | None = None) -> dict:
    """按客户端标准 86 列格式导出货物流向 xlsx（含合计行），同步返回。

    region 用大区/分公司名（如 广西、桂北公司、南宁大区），不要手挑办事处。
    office_codes 与 region 至少给一个；同时给取并集。
    办事处多、耗时长时建议改用 flow_export_async + export_status。
    """
    codes = _resolve_codes(office_codes, region)
    if not codes:
        return {"ok": False, "error": "office_codes 或 region 至少给一个，避免误导全量"}
    return _export_impl(year, month, codes, day, channel, out_dir, file_name, region or "_".join(codes))


_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _job_set(job_id, **kw):
    with _JOBS_LOCK:
        _JOBS.setdefault(job_id, {}).update(kw)


@mcp.tool()
def flow_export_async(year: int, month: int, office_codes: str | None = None,
                      region: str | None = None, day: int | None = None,
                      channel: str | None = None, out_dir: str | None = None,
                      file_name: str | None = None) -> dict:
    """批量导出（异步）。立即返回 job_id，用 export_status 查进度。

    适合跨大区/多办事处的大批量导出，避免长调用被宿主超时中断。
    """
    codes = _resolve_codes(office_codes, region)
    if not codes:
        return {"ok": False, "error": "office_codes 或 region 至少给一个"}
    job_id = uuid.uuid4().hex[:12]
    _job_set(job_id, status="running", done_chunks=0, total_chunks=len(codes),
             rows=0, path=None, error=None, office_count=len(codes))

    def worker():
        def progress(done, total, chunk, n):
            with _JOBS_LOCK:
                j = _JOBS[job_id]
                j["done_chunks"] = done
                j["total_chunks"] = total
                j["rows"] += n
        try:
            res = _export_impl(year, month, codes, day, channel, out_dir, file_name,
                               region or "_".join(codes), progress)
            _job_set(job_id, status="done", rows=res["rows"], path=res["path"],
                     summary=res["summary"], done_chunks=len(codes))
        except Exception as exc:  # noqa: BLE001
            _job_set(job_id, status="failed", error=str(exc)[:400])

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "job_id": job_id, "office_count": len(codes),
            "message": "已开始，用 export_status(job_id) 查进度"}


@mcp.tool()
def export_status(job_id: str) -> dict:
    """查询 flow_export_async 的进度/结果。"""
    with _JOBS_LOCK:
        job = dict(_JOBS.get(job_id) or {})
    if not job:
        return {"ok": False, "error": "未知 job_id"}
    return {"ok": True, "job_id": job_id, **job}



# ---------------- 报表目录（首次登录后从系统实时抓取）
CATALOG_FILE = Path.home() / ".lq-flow-catalog.json"

_DB_CURATED = {
    "d_app_cinvout_zone_01": ("proc", "proc_d_app_cinvout_zone_01_new"),
    "d_app_cinvout_zone_01_new": ("proc", "proc_d_app_cinvout_zone_01_new"),
    "d_app_cinvout_zone_01_bi_dept": ("proc", "proc_d_app_cinvout_zone_01_new_bi_dept"),
    "d_app_cinvout_zone_01_new2": ("table", "d_report_hjDMS_app_cinvout_zone_01_new2"),
}


def _catalog_raw() -> dict:
    if not CATALOG_FILE.exists():
        return {}
    try:
        return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_catalog() -> list[dict]:
    data = _catalog_raw()
    return list(data.get("db") or []) + list(data.get("web") or [])


def _scan_db_catalog() -> list[dict]:
    """从 ERP 菜单表 + 对象表生成报表目录（需要数据库账号）。"""
    conn = _connect(timeout=900)
    cur = conn.cursor()
    try:
        cur.execute("""select rtrim(menu_id), rtrim(menu_name),
                              rtrim(isnull(table_id,'')), rtrim(isnull(model_window,''))
                       from lqcerp_lnk.lqcerp.dbo.hjappmenu""")
        menus = cur.fetchall()
        cur.execute("select rtrim(name) from lqcerp_lnk.lqcerp.sys.tables")
        tables = {r[0] for r in cur.fetchall()}
        cur.execute("select rtrim(name) from lqcerp_lnk.lqcerp.sys.views")
        views = {r[0] for r in cur.fetchall()}
        cur.execute("select rtrim(name) from lqcerp_lnk.lqcerp.sys.procedures")
        procs = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()

    objs = sorted(tables | views)

    def resolve(tid: str):
        if tid in _DB_CURATED:
            kind, src = _DB_CURATED[tid]
            return kind, src, "人工"
        if tid in objs:
            return "table", tid, "精确"
        suf = [o for o in objs if o.endswith(tid)]
        if suf:
            return "table", sorted(suf, key=len)[0], "后缀"
        key = tid[2:] if tid.startswith("d_") else tid
        cont = [o for o in objs if key and key in o]
        if cont:
            return "table", sorted(cont, key=len)[0], "包含"
        if ("proc_" + tid) in procs:
            return "proc", "proc_" + tid, "精确"
        pre = [p for p in procs if p.startswith("proc_" + tid + "_")]
        if pre:
            return "proc", sorted(pre)[0], "前缀"
        return "datawindow", None, "—"

    out = []
    for mid, name, tid, win in menus:
        tid = (tid or "").strip()
        if not tid:
            continue
        kind, src, how = resolve(tid)
        out.append({"menu_id": mid, "报表名称": (name or "").strip(), "table_id": tid,
                    "窗口": (win or "").strip(), "类型": kind, "数据源": src,
                    "匹配方式": how, "来源": "ERP数据库"})
    return out


def _scan_web_catalog() -> list[dict]:
    """从 DMS 网页版菜单接口生成目录（需要 dms_login.py 的会话）。"""
    import urllib.request

    header = _dms_cookie_header()
    if not header:
        raise RuntimeError("没有 DMS 会话，先运行 python3 dms_login.py")
    req = urllib.request.Request(f"{DMS_BASE}/servlet/MenuServlet?default=default&",
                                 headers={"Cookie": header})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except Exception:
        raise RuntimeError("网页版会话已失效（返回的不是菜单 JSON），先运行 python3 dms_login.py")
    roots = (data.get("dataSource") or {}).get("tree") or []

    out: list[dict] = []

    def walk(nodes, parent=""):
        for n in nodes or []:
            label = (n.get("label") or "").strip()
            kids = n.get("tree") or []
            out.append({"报表名称": label, "类型": "group" if kids else "web",
                        "数据源": None, "来源": "DMS网页版", "上级": parent,
                        "入口": f"{DMS_BASE}/indexServlet"})
            walk(kids, label)

    walk(roots)
    return out


@mcp.tool()
def refresh_catalog(sources: str = "all") -> dict:
    """从系统实时拉取报表目录并缓存到本机（不再随仓库分发）。

    sources: all / db / web。db 需要数据库账号，web 需要 dms_login.py 的会话。
    """
    data = _catalog_raw()
    result = {"ok": True, "path": str(CATALOG_FILE)}
    if sources in ("all", "db"):
        try:
            db_items = _scan_db_catalog()
            data["db"] = db_items
            result["db_count"] = len(db_items)
        except Exception as exc:  # noqa: BLE001
            result["db_error"] = str(exc)[:200]
    if sources in ("all", "web"):
        try:
            web_items = _scan_web_catalog()
            data["web"] = web_items
            result["web_count"] = len(web_items)
        except Exception as exc:  # noqa: BLE001
            result["web_error"] = str(exc)[:200]

    data["updated_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if data.get("db") or data.get("web"):
        CATALOG_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.chmod(CATALOG_FILE, 0o600)
        result["ok"] = True
    else:
        result["ok"] = False
    return result


@mcp.tool()
def report_catalog(keyword: str | None = None, kind: str | None = None,
                   only_exportable: bool = False, limit: int = 100) -> dict:
    """经销商系统报表目录（首次使用需先 refresh_catalog）。kind: table/proc/datawindow/web。"""
    items = _load_catalog()
    if not items:
        return {"count": 0, "need_refresh": True,
                "hint": "目录为空：先运行 refresh_catalog()（需要数据库账号；抓网页版还需 dms_login.py）"}
    kw = (keyword or "").strip()
    if kw:
        items = [x for x in items
                 if kw in x.get("报表名称", "") or kw in x.get("table_id", "")
                 or kw in (x.get("上级") or "")]
    if kind:
        items = [x for x in items if x.get("类型") == kind]
    if only_exportable:
        items = [x for x in items if x.get("类型") in ("table", "proc")]
    return {"count": len(items), "updated_at": _catalog_raw().get("updated_at"),
            "reports": items[:max(1, min(limit, 800))]}


@mcp.tool()
def report_detail(name_or_id: str) -> dict:
    """查单个报表的登记信息 + 数据源字段（可导时）。"""
    key = (name_or_id or "").strip()
    hit = [x for x in _load_catalog()
           if key and (key in x.get("报表名称", "") or key == x.get("table_id"))]
    if not hit:
        return {"ok": False, "error": f"未找到报表：{key}（目录可能未刷新，先跑 refresh_catalog）"}
    item = hit[0]
    out = {"ok": True, **item}
    src = item.get("数据源")
    if item.get("类型") == "table" and src:
        conn = _connect(timeout=300)
        cur = conn.cursor()
        try:
            cur.execute(
                "select c.name from lqcerp_lnk.lqcerp.sys.columns c "
                "join lqcerp_lnk.lqcerp.sys.objects o on c.object_id=o.object_id "
                "where o.name=%s order by c.column_id", (src,))
            out["columns"] = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    return out


@mcp.tool()
def export_catalog_table(table_id: str, where: str = "", out_path: str = "",
                         sheet: str = "数据", total_row: bool = False,
                         limit: int = 200000) -> dict:
    """导出报表目录里登记的表。where 为可选 SQL 条件（只允许比较/IN/LIKE）。"""
    cat = {x["table_id"]: x for x in _load_catalog() if x.get("table_id")}
    item = cat.get((table_id or "").strip())
    if not item or item.get("类型") != "table":
        return {"ok": False, "error": "该 table_id 不在目录中或不是表类型，先用 report_catalog 查"}
    src = item["数据源"]
    cond = (where or "").strip()
    for bad in (";", "--", "/*", "insert", "update", "delete", "drop", "alter", "exec"):
        if bad in cond.lower():
            return {"ok": False, "error": f"where 含不允许内容: {bad}"}
    sql = f"select * from lqcerp_lnk.lqcerp.dbo.[{src}] with(nolock)"
    if cond:
        sql += " where " + cond
    cols, rows, trunc = _query_readonly(sql, limit)
    if not out_path:
        return {"ok": False, "error": "out_path 必填", "columns": cols, "rows": len(rows)}
    path = _write_xlsx(Path(out_path).expanduser(), cols, rows, sheet, total_row)
    return {"ok": True, "path": str(path), "rows": len(rows), "truncated": trunc,
            "source": src, "match": item.get("匹配方式")}


# ---------------------------------------------------------------- 办事处变更检测/同步
def _erp_active_offices(months: int = 3) -> dict[str, str]:
    """近 N 个月出库数据里实际出现的办事处 -> 名称（名称取 ctlm1003）。"""
    conn = _connect(timeout=600)
    cur = conn.cursor()
    try:
        cur.execute(
            """select distinct rtrim(zone_id) as code
               from cinvout_02 with(nolock)
               where sts_flag='S'
                 and out_time >= dateadd(month, -%s, getdate())
                 and rtrim(isnull(zone_id,'')) <> ''""", (int(months),))
        codes = [r[0] for r in cur.fetchall()]
        cur.execute("""select rtrim(dept_id), rtrim(dept_name), rtrim(isnull(sdept_id,''))
                       from ctlm1003 with(nolock) where len(rtrim(dept_id))=6""")
        info = {r[0]: {"name": r[1], "parent": r[2]} for r in cur.fetchall()}
        return {c: info.get(c, {}).get("name", "") for c in codes}
    finally:
        conn.close()


_VOID_PAT = re.compile(r"作废|失效|停用|虚拟|测试|已删除")


def _is_void(name: str) -> bool:
    return bool(_VOID_PAT.search(name or ""))


def _json_offices() -> dict[str, dict]:
    out = {}
    for comp, dq, o in _iter_offices():
        code = str(o.get("ERP代码") or "").strip()
        if code:
            out[code] = {"名称": o["名称"], "分公司": comp, "大区": dq}
    return out


@mcp.tool()
def check_office_changes(months: int = 3) -> dict:
    """对比「近 N 个月实际出库的办事处」与 regions.json，列出新增/消失/改名。

    办事处代码调整后先跑这个；需要写入用 refresh_regions(apply=True)。
    """
    erp = _erp_active_offices(months)
    js = _json_offices()
    raw_new = sorted(set(erp) - set(js))
    new = [c for c in raw_new if not _is_void(erp.get(c, ""))]
    ignored = [c for c in raw_new if _is_void(erp.get(c, ""))]
    gone = sorted(set(js) - set(erp))
    renamed = [
        {"代码": c, "原名称": js[c]["名称"], "ERP名称": erp[c]}
        for c in sorted(set(erp) & set(js))
        if erp[c] and _norm(erp[c]) != _norm(js[c]["名称"])
    ]
    return {
        "对比月份数": months,
        "ERP出库办事处数": len(erp),
        "regions.json办事处数": len(js),
        "新增": [{"代码": c, "名称": erp[c]} for c in new],
        "忽略的作废代码数": len(ignored),
        "消失": [{"代码": c, "名称": js[c]["名称"], "分公司": js[c]["分公司"], "大区": js[c]["大区"]} for c in gone],
        "改名": renamed,
        "结论": "无变化" if not (new or gone or renamed) else "有变化，确认后执行 refresh_regions(apply=True)",
    }


@mcp.tool()
def refresh_regions(apply: bool = False, months: int = 3) -> dict:
    """把新增办事处补进 regions.json。

    apply=False 只返回将写入的内容；apply=True 才落盘（新增项分公司/大区标为「待确认」，
    需人工补大区归属后再用于 region 过滤）。
    """
    erp = _erp_active_offices(months)
    js = _json_offices()
    new = [c for c in sorted(set(erp) - set(js)) if not _is_void(erp.get(c, ""))]
    if not new:
        return {"ok": True, "changed": 0, "message": "没有新增办事处，无需写入"}
    plan = [{"代码": c, "名称": erp[c], "分公司": "待确认", "大区": "待确认"} for c in new]
    if not apply:
        return {"ok": True, "changed": 0, "dry_run": True, "will_add": plan}
    path = REGIONS_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    bucket = data.setdefault("分公司", {}).setdefault("待确认", {}).setdefault("大区", {}).setdefault("待确认", [])
    existing = {str(x.get("ERP代码")) for x in bucket}
    for item in plan:
        if item["代码"] not in existing:
            bucket.append({"名称": item["名称"], "OA部门编码": "", "ERP代码": item["代码"]})
    data["更新时间"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["说明"] = (data.get("说明") or "") + "；新增办事处自动补充，分公司/大区待人工确认"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "changed": len(plan), "added": plan, "file": str(path)}



# ---------------------------------------------------------------- DMS 网页版（Cookie 会话）
DMS_BASE = os.getenv("LQ_DMS_BASE", "http://192.168.199.26:9090/hjerp")
DMS_COOKIE = Path.home() / ".lq-dms.cookie"


def _dms_cookie_header() -> str | None:
    if not DMS_COOKIE.exists():
        return None
    try:
        data = json.loads(DMS_COOKIE.read_text(encoding="utf-8"))
        cookies = data.get("cookies") or []
        return "; ".join(f"{c['name']}={c['value']}" for c in cookies) or None
    except Exception:
        return None


@mcp.tool()
def dms_status() -> dict:
    """检查 DMS 网页版登录会话是否有效（Cookie 由 dms_login.py 手动登录生成）。"""
    import urllib.request

    if not DMS_COOKIE.exists():
        return {"logged_in": False,
                "advice": "未找到会话：运行 python3 dms_login.py，在弹出的浏览器里登录一次"}
    header = _dms_cookie_header()
    try:
        req = urllib.request.Request(f"{DMS_BASE}/indexServlet", headers={"Cookie": header or ""})
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = r.status == 200 and len(r.read(200)) > 0
        saved = json.loads(DMS_COOKIE.read_text(encoding="utf-8")).get("saved_at")
        return {"logged_in": ok, "base": DMS_BASE, "saved_at": saved,
                "advice": "会话有效" if ok else "会话已失效，重新运行 python3 dms_login.py"}
    except Exception as exc:  # noqa: BLE001
        return {"logged_in": False, "base": DMS_BASE, "error": str(exc)[:150],
                "advice": "会话失效或网络不通；重新运行 dms_login.py"}


@mcp.tool()
def dms_fetch(path: str, params: str = "", save_to: str = "", limit: int = 200000) -> dict:
    """带登录 Cookie 请求 DMS 网页接口。

    path 例如 "servlet/DataSetQueryServlet"；params 为查询串（a=1&b=2）。
    返回文本/JSON；若响应是二进制（如 xlsx），需给 save_to 保存到文件。
    """
    import urllib.parse
    import urllib.request

    header = _dms_cookie_header()
    if not header:
        return {"ok": False, "error": "没有会话 Cookie，先运行 python3 dms_login.py"}
    url = f"{DMS_BASE}/{path.lstrip('/')}"
    if params:
        url += ("&" if "?" in url else "?") + params.lstrip("?")
    req = urllib.request.Request(url, headers={"Cookie": header})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            ctype = r.headers.get("Content-Type", "")
            raw = r.read(limit)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200], "url": url}

    if any(k in ctype.lower() for k in ("excel", "spreadsheet", "octet-stream")) or path.lower().endswith("export"):
        if not save_to:
            return {"ok": False, "error": "响应是二进制，请提供 save_to", "content_type": ctype}
        out = Path(save_to).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        return {"ok": True, "saved": str(out), "bytes": len(raw), "content_type": ctype}

    text = raw.decode("utf-8", "replace")
    result = {"ok": True, "content_type": ctype, "bytes": len(raw), "url": url}
    try:
        result["json"] = json.loads(text)
    except Exception:
        result["text"] = text[:4000]
    return result



DMS_REPORT_BASE = os.getenv("LQ_DMS_REPORT_BASE", "http://192.168.199.26:9095/hjreport")
DMS_FLOW_RPT = "d_sale/d_report_sale_2dcinvout_detail.rptdesign"


def _dms_meta() -> tuple[str, str]:
    data = json.loads(DMS_COOKIE.read_text(encoding="utf-8")) if DMS_COOKIE.exists() else {}
    return data.get("user_id", ""), _dms_cookie_header() or ""


@mcp.tool()
def dms_flow_report(year: int, month: int, day: int | None = None,
                    dept_code: str | None = None, dept_name: str | None = None,
                    out_dir: str | None = None, file_name: str | None = None,
                    keep_raw: bool = True) -> dict:
    """导出 DMS 网页版「二级货物流向明细表」（BIRT 报表，按月/日、可按办事处）。

    需要先 python3 dms_login.py 建立会话。day 为空则导出整月。
    """
    import base64
    import calendar
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET

    uid, cookie = _dms_meta()
    if not cookie:
        return {"ok": False, "error": "没有 DMS 会话，先运行 python3 dms_login.py"}

    if not dept_code and dept_name:
        for comp, dq, o in _iter_offices():
            if _norm(o["名称"]) == _norm(dept_name):
                dept_code = str(o.get("ERP代码") or "").strip()
                break

    start = dt.date(year, month, day or 1)
    end = dt.date(year, month, day) if day else dt.date(year, month, calendar.monthrange(year, month)[1])
    cond = f"a.date_out >= '{start}' and a.date_out <= '{end}'"
    perm = (f"(g.id_dept in(select id_dept from ctlm1221 where id_user = '{uid}' "
            f"and id_user in(select id_user from ctlm1203 where id_post in (select id_post from ctlm1004 where flag_post = 'I' ))) "
            f"<br/>or<br/>a.id_corr in (select id_corr from ctlm1127 where id_user = '{uid}' "
            f"and id_user in(select id_user from ctlm1203 where id_post in (select id_post from ctlm1004 where flag_post = 'O' ))))")
    sql = f"{cond} and {perm}"
    if dept_code:
        sql += f" and g.id_dept = '{dept_code}'"
    sql = sql.replace("<br/>", "\n")  # 页面里的换行标记，SQL 里不能出现

    qs = urllib.parse.urlencode({
        "__report": DMS_FLOW_RPT, "__format": "xls",
        "reportArg": sql, "userid": uid,
    })
    url = f"{DMS_REPORT_BASE}/frameset?{qs}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Cookie": cookie}), timeout=300) as r:
            raw = r.read()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}

    NS = "{urn:schemas-microsoft-com:office:spreadsheet}"
    try:
        root = ET.fromstring(raw)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"返回不是 Excel XML：{exc}"[:200], "bytes": len(raw)}

    rows = []
    for r in root.findall(f".//{NS}Row"):
        vals = []
        for c in r.findall(f"{NS}Cell"):
            idx = c.get(f"{NS}Index")
            if idx:
                vals.extend([""] * (int(idx) - 1 - len(vals)))
            d = c.find(f"{NS}Data")
            vals.append(d.text if d is not None and d.text is not None else "")
        rows.append(vals)
    if not rows:
        return {"ok": False, "error": "报表为空"}

    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill
    name = file_name or (f"货物流向_{(dept_code or '全部')}_{start:%Y%m%d}"
                         + ("" if day else f"-{end:%Y%m%d}") + "_网页版.xlsx")
    path = Path(out_dir or DEFAULT_OUT) / name
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "货物流向"
    fill = PatternFill("solid", fgColor="DDEBF7")
    for ri, row in enumerate(rows, start=1):
        ws.append(row)
        if ri == 2:
            for ci in range(1, len(row) + 1):
                c = ws.cell(ri, ci)
                c.font = Font(bold=True)
                c.fill = fill
                c.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A3"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)

    if keep_raw:
        raw_path = path.with_suffix(".raw.xls")
        raw_path.write_bytes(raw)
    total = None
    for v in rows[-1]:
        try:
            total = float(v); break
        except (TypeError, ValueError):
            continue
    return {"ok": True, "path": str(path), "rows": max(0, len(rows) - 3),
            "dept": dept_code or "全部", "date_from": str(start), "date_to": str(end),
            "total": total}



# ---------------------------------------------------------------- OA 协同办公
OA_BASE = os.getenv("LQ_OA_BASE", "https://oa.lqp9.cn")
OA_SESSION = Path.home() / ".lq-oa.cookie"


def _oa_session() -> dict:
    if not OA_SESSION.exists():
        return {}
    try:
        return json.loads(OA_SESSION.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _oa_service(service: str, params: dict | None = None, timeout: int = 30) -> dict:
    """调用 OA 的 /exoa/services（只认 HEADER 里的 token，不需要 Cookie）。"""
    import urllib.parse
    import urllib.request

    data = _oa_session()
    token = data.get("token", "")
    if not token:
        raise RuntimeError("没有 OA 会话，先运行 python3 oa_login.py")
    qs = urllib.parse.urlencode({
        "SERVICE": service,
        "HEADER": json.dumps({"device_id": "PC", "token": token}, ensure_ascii=False),
        "PARAMS": json.dumps(params or {}, ensure_ascii=False),
    }, quote_via=urllib.parse.quote)
    req = urllib.request.Request(f"{OA_BASE}/exoa/services?{qs}",
                                 headers={"User-Agent": "Mozilla/5.0 (lq-flow-mcp)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text[:2000]}


@mcp.tool()
def oa_status() -> dict:
    """检查 OA 会话是否有效（token 由 oa_login.py 手动登录生成）。"""
    data = _oa_session()
    if not data.get("token"):
        return {"logged_in": False, "advice": "运行 python3 oa_login.py，在弹出的浏览器里登录一次"}
    try:
        res = _oa_service("OA.MenuService.getAllRooMenusOfUser")
        menus = (res.get("RESULTS") or {}).get("list") or []
        return {"logged_in": bool(menus), "user": data.get("user", ""),
                "user_code": data.get("user_code", ""), "saved_at": data.get("saved_at"),
                "top_menus": [m.get("name") for m in menus][:10],
                "advice": "会话有效" if menus else "会话可能失效，重跑 oa_login.py"}
    except Exception as exc:  # noqa: BLE001
        return {"logged_in": False, "error": str(exc)[:150], "advice": "会话失效，重跑 oa_login.py"}


@mcp.tool()
def oa_menu(keyword: str | None = None, limit: int = 300) -> dict:
    """OA 菜单树（扁平化）。keyword 按名称/编码过滤。"""
    res = _oa_service("OA.MenuService.getAllRooMenusOfUser")
    roots = (res.get("RESULTS") or {}).get("list") or []
    items: list[dict] = []

    def walk(node, parent=""):
        for m in node or []:
            name = (m.get("name") or "").strip()
            items.append({"名称": name, "编码": (m.get("code") or "").strip(),
                          "id": m.get("id"), "上级": parent,
                          "地址": (m.get("viewUrl") or "").split("?")[0]})
            walk(m.get("subMenus"), name)

    walk(roots)
    kw = (keyword or "").strip()
    if kw:
        items = [x for x in items if kw in x["名称"] or kw in x["编码"]]
    return {"count": len(items), "menus": items[:max(1, min(limit, 500))]}


@mcp.tool()
def oa_services(service: str, params: str = "") -> dict:
    """调用任意 OA 服务，如 "OA.UserAddressBookService.getUserByUserId"。

    params 为 JSON 字符串（如 {"userId": "jiangkaihao"}）。
    """
    try:
        p = json.loads(params) if params else {}
    except Exception:
        return {"ok": False, "error": "params 不是合法 JSON"}
    try:
        return {"ok": True, "service": service, "result": _oa_service(service, p)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}



def _oa_post_json(path: str, payload: dict, timeout: int = 20) -> dict:
    import urllib.request

    data = _oa_session()
    if not data.get("token"):
        raise RuntimeError("没有 OA 会话，先运行 python3 oa_login.py")
    cookie = "; ".join(f"{c['name']}={c['value']}" for c in data.get("cookies", []) if c.get("name"))
    req = urllib.request.Request(
        OA_BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Cookie": cookie,
                 "User-Agent": "Mozilla/5.0 (lq-flow-mcp)"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text[:500]}


@mcp.tool()
def oa_user(user_id: str) -> dict:
    """按 OA 账号查用户（如 yangyinwen），返回姓名/部门。"""
    try:
        r = _oa_service("OA.UserAddressBookService.getUserByUserId", {"userId": user_id})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:150]}
    u = r.get("RESULTS") or {}
    if not u.get("USER_NAME"):
        return {"ok": False, "error": f"未找到用户：{user_id}"}
    return {"ok": True, "账号": user_id, "姓名": u.get("USER_NAME"),
            "部门": u.get("DEPT_NAME"), "手机": u.get("USER_MOBI")}


@mcp.tool()
def oa_send_message(to_user_id: str, content: str) -> dict:
    """给 OA 用户发即时消息（会真实发出，请谨慎）。

    to_user_id 是 OA 账号（如 yangyinwen），不是姓名；可先用 oa_user 确认。
    """
    data = _oa_session()
    if not data.get("token"):
        return {"ok": False, "error": "没有 OA 会话，先运行 python3 oa_login.py"}
    me = data.get("user_code") or ""
    payload = {
        "username": data.get("user", ""), "avatar": "", "id": me,
        "type": "friend", "mine": True, "content": content,
        "fromid": me, "toid": to_user_id,
    }
    try:
        res = _oa_post_json("/api/LayIm/SendMessage", payload)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": res.get("code") == 0, "to": to_user_id, "content": content,
            "response": res}



@mcp.tool()
def oa_flow_list(formset_id: int = 2036, status: int = 2, page_size: int = 10,
                 page: int = 1) -> dict:
    """查询 OA 流程实例（默认「自由流程(2026)」，status=2 为在办）。

    返回：发起人、发起时间、当前步骤、当前处理人、单号。
    """
    import urllib.parse
    import urllib.request

    data = _oa_session()
    if not data.get("token"):
        return {"ok": False, "error": "没有 OA 会话，先运行 python3 oa_login.py"}
    qs = urllib.parse.urlencode({
        "SERVICE": "OA.MissiveService.queryMissive.V2",
        "HEADER": json.dumps({"device_id": "PC", "token": data["token"]}, ensure_ascii=False),
        "rnd": "0.1",
        "type": "10",
        "queryCondition": json.dumps({
            "orderByColumn": 1, "orderDirection": 1,
            "formsetIds": [str(formset_id)],
            "formSetInstStatus": status,
            "pageSize": max(1, min(int(page_size), 50)),
            "pageNum": max(1, int(page)),
        }, ensure_ascii=False),
        "formSetInstStatus": str(status),
    }, quote_via=urllib.parse.quote)
    cookie = "; ".join(f"{c['name']}={c['value']}" for c in data.get("cookies", []) if c.get("name"))
    try:
        req = urllib.request.Request(f"{OA_BASE}/exoa/services?{qs}",
                                     headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0 (lq-flow-mcp)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}
    items = ((res.get("RESULTS") or {}).get("list") or [])
    out = []
    for it in items:
        out.append({
            "单号": it.get("PROC_ATT2"), "流程": it.get("PROC_NAME"),
            "发起人": it.get("AUTH_NAME"), "发起时间": it.get("PROC_STAR"),
            "当前步骤": it.get("CUR_ACTI"), "当前处理人": it.get("CUR_RECI_NAME"),
            "PROC_ID": it.get("PROC_ID"),
        })
    return {"ok": bool(res.get("success")), "count": len(out), "flows": out}


@mcp.tool()
def run_report_proc(proc: str, param: str = " 3=3 ",
                    limit: int = 200, offset: int = 0) -> dict:
    """执行 .86 上的 proc_ 报表存储过程。param 是 @Param1 的条件片段，例如
    " 3=3 and a.out_time >= '2026-10-01' and a.out_time < '2026-11-01' "。"""
    p = (proc or "").strip()
    if not re.match(r"^proc_[A-Za-z0-9_]+$", p):
        raise ValueError("只允许 proc_ 开头的存储过程名")
    conn = _connect(timeout=300)
    cur = conn.cursor()
    try:
        cur.execute("select 1 from lqcerp_lnk.lqcerp.sys.procedures where name=%s", (p,))
        if not cur.fetchone():
            raise ValueError(f"存储过程不存在: {p}")
    finally:
        conn.close()
    command = f"use lqcerp; exec dbo.{p} @Param1='" + str(param).replace("'", "''") + "'"
    cols, rows = _exec_remote(_remote_exec_string(command))
    lim = max(1, min(int(limit), 2000))
    page = rows[offset:offset + lim]
    return {"proc": p, "columns": cols, "total_rows": len(rows),
            "returned": len(page), "offset": offset,
            "rows": [[_jsonable(v) for v in r] for r in page]}


@mcp.tool()
def query_readonly(sql: str, limit: int = 200, database: str | None = None) -> dict:
    """只读 SQL 查询（SELECT/WITH）。默认 lqerp；跨库可用四段名
    如 lqcerp_lnk.lqcerp.dbo.xxx 或 [lqdrp].dbo.xxx。"""
    cols, rows, truncated = _query_readonly(sql, limit, database)
    return {"columns": cols, "returned": len(rows), "truncated": truncated,
            "rows": [[_jsonable(v) for v in r] for r in rows]}


@mcp.tool()
def export_query(sql: str, out_path: str, sheet: str = "数据",
                 total_row: bool = False, limit: int = 200000) -> dict:
    """执行只读 SQL 并把全部结果导出 xlsx。out_path 是完整文件路径。"""
    s = _check_sql(sql)
    conn = _connect(autocommit=True, timeout=1800)
    cur = conn.cursor()
    try:
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        cur.execute(s)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(int(limit))
    finally:
        conn.close()
    path = _write_xlsx(Path(out_path).expanduser(), cols, rows, sheet, total_row)
    return {"ok": True, "path": str(path), "rows": len(rows), "columns": cols}


@mcp.tool()
def preflight() -> dict:
    """连通性自检：判断当前是哪套 VPN 在承载（TopSAP / EasyConnect）、库通不通。"""
    import socket
    import subprocess

    if not DB_HOST or not DB_PASSWORD:
        return {"configured": False, "db_reachable": False,
                "vpn_clients_running": [], "tunnels": [],
                "advice": "尚未配置：先连接公司 VPN，然后运行 python3 setup.py 输入账号密码"}

    result = {
        "configured": True,
        "db_host": f"{DB_HOST}:{DB_PORT}",
        "vpn_clients_running": [],
        "tunnels": [],
        "db_reachable": False,
        "advice": "",
    }

    try:
        out = subprocess.run(["ps", "-axo", "comm"], capture_output=True, text=True, timeout=5).stdout
        if "LocalTopserv" in out:
            result["vpn_clients_running"].append("TopSAP(天融信)")
        if "ECAgent" in out or "EasyMonitor" in out:
            result["vpn_clients_running"].append("EasyConnect(深信服)")
    except Exception:
        pass

    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
        cur = None
        for line in out.splitlines():
            if not line.startswith(("\t", " ")):
                cur = line.split(":")[0]
            elif cur and cur.startswith("utun") and "inet " in line:
                parts = line.split()
                result["tunnels"].append({
                    "iface": cur,
                    "ip": parts[1],
                    "peer": parts[3] if len(parts) > 3 and parts[2] == "-->" else None,
                })
    except Exception:
        pass

    try:
        sock = socket.create_connection((DB_HOST, DB_PORT), timeout=5)
        sock.close()
        result["db_reachable"] = True
        result["advice"] = ("正常，可以取数" if result["tunnels"]
                            else "正常（未检测到本机 VPN 隧道，走了路由器/旁路由转发）")
        try:
            chk = check_office_changes(3)
            result["office_changes"] = {
                "新增": len(chk["新增"]), "消失": len(chk["消失"]), "改名": len(chk["改名"]),
                "明细": chk["新增"][:10],
            }
            if chk["新增"] or chk["消失"] or chk["改名"]:
                result["advice"] += "；办事处目录有变化，可跑 check_office_changes 查看"
        except Exception as exc:  # noqa: BLE001
            result["office_changes"] = {"error": str(exc)[:120]}
    except Exception as exc:
        result["db_error"] = str(exc)
        if not result["tunnels"]:
            result["advice"] = "没有 VPN 隧道，请先连接 TopSAP 或 EasyConnect"
        elif not result["vpn_clients_running"]:
            result["advice"] = "隧道存在但客户端进程异常，建议重连 VPN"
        else:
            result["advice"] = "VPN 在跑但库不可达，检查线路或被防火墙拦截"
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")

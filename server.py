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
    return (list(data.get("db") or []) + list(data.get("erp") or [])
            + list(data.get("web") or []))


def _resolve_obj(tid: str, objs: list[str], procs: set[str], curated: dict):
    if tid in curated:
        kind, src = curated[tid]
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


def _scan_db_catalog() -> list[dict]:
    """三期经销商库 lqcerp（.86）的报表目录。"""
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
    out = []
    for mid, name, tid, win in menus:
        tid = (tid or "").strip()
        if not tid:
            continue
        kind, src, how = _resolve_obj(tid, objs, procs, _DB_CURATED)
        out.append({"menu_id": mid, "报表名称": (name or "").strip(), "table_id": tid,
                    "窗口": (win or "").strip(), "类型": kind, "数据源": src,
                    "匹配方式": how, "来源": "ERP数据库", "库": "lqcerp"})
    return out


def _scan_erp_catalog() -> list[dict]:
    """本机 lqerp（.19，一期/三期 ERP）的报表目录。

    table_id 为空时，尝试从窗口名推断底表（如 w_app_saft04_2019 -> saft04），
    这类推断标记为「窗口推断」，可信度低，导出需显式确认。
    """
    conn = _connect(timeout=900)
    cur = conn.cursor()
    try:
        cur.execute("""select rtrim(menu_id), rtrim(menu_name),
                              rtrim(isnull(table_id,'')), rtrim(isnull(model_window,''))
                       from hjappmenu""")
        menus = cur.fetchall()
        cur.execute("select rtrim(name) from sys.tables")
        tables = {r[0] for r in cur.fetchall()}
        cur.execute("select rtrim(name) from sys.views")
        views = {r[0] for r in cur.fetchall()}
        cur.execute("select rtrim(name) from sys.procedures")
        procs = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()

    objs = sorted(tables | views)
    out = []
    for mid, name, tid, win in menus:
        tid = (tid or "").strip()
        win = (win or "").strip()
        if tid:
            kind, src, how = _resolve_obj(tid, objs, procs, {})
        else:
            # 从窗口名推断：w_app_<表名>[_后缀]
            src, kind, how = None, "datawindow", "—"
            m = re.match(r"w_app_([a-zA-Z][A-Za-z0-9_]+)", win)
            if m:
                cand = m.group(1)
                for cut in re.split(r"_\d{4}$|_\d+$", cand):
                    if cut in objs:
                        src, kind, how = cut, "table", "窗口推断"
                        break
        if kind == "datawindow" and not tid:
            continue
        out.append({"menu_id": mid, "报表名称": (name or "").strip(),
                    "table_id": tid or (src or ""), "窗口": win,
                    "类型": kind, "数据源": src, "匹配方式": how,
                    "来源": "ERP三期", "库": "lqerp"})
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
    if sources in ("all", "erp"):
        try:
            erp_items = _scan_erp_catalog()
            data["erp"] = erp_items
            result["erp_count"] = len(erp_items)
        except Exception as exc:  # noqa: BLE001
            result["erp_error"] = str(exc)[:200]
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


def _confidence(item: dict) -> str:
    how = item.get("匹配方式") or ""
    if how in ("精确", "人工"):
        return "高"
    if how == "后缀":
        return "中"
    return "低"


@mcp.tool()
def report_catalog(keyword: str | None = None, kind: str | None = None,
                   only_exportable: bool = False, limit: int = 100) -> dict:
    """经销商系统报表目录（首次使用需先 refresh_catalog）。

    返回 ambiguous=True 时表示关键字命中多条，必须挑一条，不要自己猜。
    """
    items = _load_catalog()
    if not items:
        return {"count": 0, "need_refresh": True,
                "hint": "目录为空：先运行 refresh_catalog()"}
    kw = (keyword or "").strip()
    if kw:
        items = [x for x in items
                 if kw in (x.get("报表名称") or "") or kw == (x.get("table_id") or "")
                 or kw in (x.get("上级") or "")]
    if kind:
        items = [x for x in items if x.get("类型") == kind]
    if only_exportable:
        items = [x for x in items if x.get("类型") in ("table", "proc") and _confidence(x) != "低"]
    for x in items:
        if x.get("类型") in ("table", "proc"):
            x["可信度"] = _confidence(x)
    hit = items[:max(1, min(limit, 800))]
    return {"count": len(items), "ambiguous": len(items) > 1,
            "updated_at": _catalog_raw().get("updated_at"),
            "hint": ("命中多条，请指定具体 table_id，禁止自行挑选" if len(items) > 1 else ""),
            "reports": hit}


@mcp.tool()
def report_detail(name_or_id: str) -> dict:
    """查单个报表的登记信息 + 数据源字段（可导时）。"""
    key = (name_or_id or "").strip()
    hit = [x for x in _load_catalog()
           if key and (key == (x.get("table_id") or "") or key == (x.get("报表名称") or ""))]
    if not hit:
        return {"ok": False, "error": f"未找到报表：{key}（目录可能未刷新，先跑 refresh_catalog）"}
    item = hit[0]
    out = {"ok": True, **item, "可信度": _confidence(item)}
    src = item.get("数据源")
    if item.get("类型") == "table" and src:
        conn = _connect(timeout=300)
        cur = conn.cursor()
        try:
            if item.get("库") == "lqerp":
                cur.execute("select c.name from sys.columns c where c.object_id=OBJECT_ID(%s) order by c.column_id", (src,))
            else:
                cur.execute(
                    "select c.name from lqcerp_lnk.lqcerp.sys.columns c "
                    "join lqcerp_lnk.lqcerp.sys.objects o on c.object_id=o.object_id "
                    "where o.name=%s order by c.column_id", (src,))
            out["columns"] = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    return out


@mcp.tool()
def export_catalog_table(table_id: str, expect_name: str, where: str = "",
                         out_path: str = "", sheet: str = "数据",
                         total_row: bool = False, limit: int = 200000,
                         preview_only: bool = False,
                         allow_fuzzy: bool = False) -> dict:
    """按目录导出某张报表底表。**必须提供 expect_name（报表中文名）做一致性校验**。

    防误导规则：
    - 同一 table_id 对应多个报表名 → 拒绝，先指定清楚；
    - expect_name 与目录登记名不匹配 → 拒绝；
    - 匹配方式为"包含"（猜出来的底表）默认拒绝，确需导出要 allow_fuzzy=True；
    - preview_only=True 只返回前 3 行，不写文件。
    """
    tid = (table_id or "").strip()
    cands = [x for x in _load_catalog()
             if (x.get("table_id") or "").strip() == tid
             or (x.get("数据源") or "").strip() == tid]
    if not cands:
        return {"ok": False, "error": f"table_id 不在目录中：{tid}"}
    want0 = (expect_name or "").strip()
    if not want0:
        return {"ok": False, "error": "必须提供 expect_name（报表中文名）用于校验",
                "candidates": sorted({(x.get("报表名称") or "").strip() for x in cands})}
    exact = [x for x in cands if (x.get("报表名称") or "").strip() == want0]
    hit = exact or [x for x in cands if want0 in (x.get("报表名称") or "")]
    if len(hit) != 1:
        return {"ok": False,
                "error": "报表名无法唯一定位该表，请用目录里的完整报表名",
                "expect_name": want0,
                "candidates": sorted({(x.get("报表名称") or "").strip() for x in cands})}
    item = hit[0]
    if item.get("类型") != "table":
        return {"ok": False, "error": f"该条目类型为 {item.get('类型')}，不是可导出表"}
    conf = _confidence(item)
    if conf == "低" and not allow_fuzzy:
        return {"ok": False,
                "error": f"底表是模糊匹配（{item.get('匹配方式')}），默认不导；确认无误可传 allow_fuzzy=True",
                "贴出的报表名": item.get("报表名称"), "数据源": item.get("数据源")}
    want = (expect_name or "").strip()
    if not want:
        return {"ok": False, "error": "必须提供 expect_name（报表中文名）用于校验"}
    if want not in (item.get("报表名称") or ""):
        return {"ok": False, "error": "expect_name 与目录登记不符",
                "expect_name": want, "目录登记": item.get("报表名称")}

    src = item["数据源"]
    cond = (where or "").strip()
    for bad in (";", "--", "/*", "insert", "update", "delete", "drop", "alter", "exec"):
        if bad in cond.lower():
            return {"ok": False, "error": f"where 含不允许内容: {bad}"}
    table_ref = (f"[{src}]" if item.get("库") == "lqerp"
                 else f"lqcerp_lnk.lqcerp.dbo.[{src}]")
    base = f"select * from {table_ref} with(nolock)"
    if cond:
        base += " where " + cond

    cols, rows, _ = _query_readonly(base, 3)
    preview = [[_jsonable(v) for v in r] for r in rows]

    if preview_only:
        return {"ok": True, "preview_only": True, "报表名称": item.get("报表名称"),
                "数据源": src, "可信度": conf, "columns": cols, "rows": preview}

    cols, rows, trunc = _query_readonly(base, limit)
    if not out_path:
        return {"ok": False, "error": "out_path 必填", "rows": len(rows)}
    path = _write_xlsx(Path(out_path).expanduser(), cols, rows, sheet, total_row)
    return {"ok": True, "path": str(path), "rows": len(rows), "truncated": trunc,
            "报表名称": item.get("报表名称"), "数据源": src, "可信度": conf,
            "来源": item.get("来源"), "库": item.get("库"), "preview": preview}


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

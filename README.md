# lq-flow MCP —— 漓泉 cERP 货物流向网关

把公司 ERP 的取数和导出能力封装成 MCP 工具，装一次就能在 ZCode 里用自然语言取数。

**第一次安装请按下面 4 步走**，不要跳过第 2 步。

---

## 前置条件

| 项 | 要求 |
|---|---|
| 系统 | macOS（Windows/Linux 理论可用，脚本未测） |
| Python | 3.10 及以上 |
| 网络 | **必须先连公司 VPN**（TopSAP 天融信 或 EasyConnect 深信服，任选其一） |
| 账号 | 经销商系统数据库账号（和佳客户端 `hjapp.ini` 里的 `LogId` / `LogPass`） |

> 注意：这里要的是**数据库连接账号**，不是软件登录账号。如果不确定，找 IT 或系统管理员要。

---

## 1. 安装依赖

```bash
git clone <本仓库地址> ~/lq-flow-mcp
cd ~/lq-flow-mcp
python3 -m pip install -r requirements.txt
```

## 2. 运行配置向导（关键）

```bash
python3 setup.py
```

向导会依次做三件事：

1. **检查 VPN**：连不上会直接提示你先连 VPN，不会继续往下走；
2. **提示输入账号密码**：输入经销商系统数据库账号，当场验证登录；
3. **保存配置**：写入 `~/.lq-flow.env`（权限 600，仅本机可读）。

账号密码**不会**出现在仓库里，也不会被打印。

只想检查网络、不改配置：

```bash
python3 setup.py --check
```

## 3. 注册到 ZCode

编辑 `~/.zcode/cli/config.json`，在 `mcp.servers` 下加入（路径按实际安装位置改）：

```json
"lq-flow": {
  "type": "stdio",
  "command": "python3",
  "args": ["/Users/<你的用户名>/lq-flow-mcp/server.py"],
  "timeoutMs": 600000
}
```

保存后**重启 ZCode**。

## 4. 验证

重启后对 agent 说：

> 跑一下 preflight

正常会返回 `db_reachable: true` 和 `advice: 正常，可以取数`。看到这个就装好了。

---

## 工具清单

| 工具 | 用途 |
|---|---|
| `preflight()` | 连通性自检：VPN/隧道/数据库是否正常 |
| `list_regions(company?, region?)` | 按分公司/大区/别名（如「广西」）列办事处 |
| `list_offices(year?, month?)` | ERP 办事处清单 |
| `list_reports(keyword?, only_runnable?)` | 报表菜单清单，标注哪些能直连 |
| `flow_query(...)` | 货物流向明细（标准 86 列） |
| `flow_summary(...)` | 按办事处/大区/渠道/经销商/产品汇总 |
| `flow_export(...)` | 导出 Excel（小批量，同步返回） |
| `flow_export_async(...)` | **批量导出（异步）**，立即返回 `job_id` |
| `export_status(job_id)` | 查异步导出进度/结果 |
| `run_report_proc(proc, param)` | 执行报表存储过程 |
| `query_readonly(sql)` | 只读 SQL |
| `export_query(sql, out_path)` | 任意查询结果导出 Excel |
| `report_catalog(keyword?, kind?, only_exportable?)` | **经销商系统全部报表目录**（按系统菜单，不预设业务口径） |
| `report_detail(name_or_id)` | 单个报表的登记信息 + 数据源字段 |
| `export_catalog_table(table_id, where?, out_path)` | 按目录导出某张报表数据表（受白名单约束） |

### 报表目录

目录由 ERP 自己的菜单表（`hjappmenu`）生成，`reports_catalog.json` 里共 **657 条**：

| 类型 | 数量 | 含义 |
|---|---|---|
| `table` | 479 | 有对应数据表/视图，可用 `export_catalog_table` 直接导 |
| `proc` | 4 | 有对应存储过程，用 `run_report_proc` |
| `datawindow` | 174 | SQL 编译在客户端里，数据库无对象，只能界面导出或复刻 |

每条还带 `匹配方式`：`精确`（表名完全一致）、`后缀`、`包含`、`人工`。**模糊匹配的条目导出前建议先 `report_detail` 核一次字段**。

用法：

```
report_catalog("O2O")                       # 按系统菜单名搜索
report_catalog(kind="table", only_exportable=True)
report_detail("O2O电商平台订单日报表")        # 看数据源和字段
export_catalog_table("d_app_cinvout_zone_01_new2",
                     where="fiscal_year=2026 and fiscal_period=10",
                     out_path="/path/导出.xlsx")
```

### 常用示例

```
preflight()
list_regions(region="广西")
flow_summary(2026, 10, region="广西", channel="O2O", group_by="office")
flow_export(2026, 10, region="南宁大区", channel="O2O")
flow_export_async(2026, 10, region="广西", channel="O2O")   # 大批量用这个
export_status("job_id")
```

### 为什么有同步和异步两个导出

- 单个办事处、单月：`flow_export` 几十秒就回来了，直接用。
- 整个大区/全省：可能 3–5 分钟，**用 `flow_export_async`**，拿到 `job_id` 后轮询 `export_status`，不会因为调用太久被中断。

---

## 常见问题

**Q: preflight 显示 db_reachable=false**
先确认 VPN 已连接。如果 VPN 客户端在跑但仍不通，断开重连一次；还不行联系 IT。

**Q: setup.py 登录失败（错误 18456）**
账号密码不对，或该账号没有 `lqerp` 库权限。确认用的是数据库账号而不是软件登录账号。

**Q: 查询很慢**
报表每次要在数据库端建临时表，单办事处单月约 20–90 秒属正常。多办事处请用异步导出。

**Q: 导出的"线上/线下销量"跟预期不一样**
这是 ERP 按出库单的 B2B 标记拆的，不是按渠道名称，属于系统口径。

---

## 安全说明

- 仓库内**不含**任何账号、密码。
- 密码只保存在本机 `~/.lq-flow.env`（600 权限）。
- 换机器需要重新跑 `python3 setup.py`。
- `query_readonly` 只放行 `SELECT/WITH`，拦截写操作与执行类关键字。

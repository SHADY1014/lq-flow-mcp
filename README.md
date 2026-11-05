# lq-flow MCP —— 漓泉 cERP 货物流向网关

把公司 ERP 的取数和导出能力封装成 MCP 工具，装一次就能在你的 agent 里用自然语言取数。

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

## 3. 注册到你的 Agent 客户端

任何支持 stdio 的 MCP 客户端都能用（Claude Desktop、WorkBuddy、Cursor、ZCode、Cherry Studio 等）。
在客户端的 MCP 配置里加入（路径按实际安装位置改）：

```json
"lq-flow": {
  "command": "python3",
  "args": ["/Users/<你的用户名>/lq-flow-mcp/server.py"]
}
```

常见位置：

- Claude Desktop：`~/Library/Application Support/Claude/claude_desktop_config.json` 的 `mcpServers`
- WorkBuddy / Cursor / ZCode：各自「设置 → MCP Servers」里粘贴上面的配置
- 其他客户端：找到 MCP servers 配置项，按同样字段添加即可

两点差异要注意：有的客户端需要额外的 `"type": "stdio"`；有的用 `timeoutMs` 控制单次超时，
批量导出建议设到 **600000**（10 分钟）以上。保存后重启客户端。

## 4. 验证

重启后对 agent 说：

> 跑一下 preflight

正常会返回 `db_reachable: true` 和 `advice: 正常，可以取数`。看到这个就装好了。

---



## 从 GitHub 直接安装

仓库**不含**账号密码，所以从 GitHub 装时会自动进入配置向导：

```bash
git clone https://github.com/SHADY1014/lq-flow-mcp.git ~/lq-flow-mcp
cd ~/lq-flow-mcp
python3 install.py
```

`install.py` 检测不到预置配置时，会自动调用 `setup.py`：先检查 VPN →
扫描本机和佳客户端 ini 自动取账号 → 找不到就提示手工输入数据库账号。
配置成功后继续自动注册客户端并做连通性检查。

也可以 clone 之后直接双击 `install.bat`（Windows）或 `install.command`（macOS），
效果等同于 `python3 install.py`。

如果只想手工控制每一步，跳过 `install.py`，按上面「1. 安装依赖 → 4. 验证」走即可。

## 内部懒人包（给同事用，零配置）

上面的 1–4 步适合开发者。给同事分发时用懒人包，对方不用填任何东西：

**发放人**（你自己的机器，需已配好 `~/.lq-flow.env`）：

```bash
python3 build_package.py
# 产物：~/Desktop/lq-flow-懒人包.zip
```

包内含代码、`reports_catalog.json`、预填好的数据库连接、一键安装脚本。
⚠️ 这个 zip **带数据库密码**，只走内部渠道（企业微信/共享盘），不要传 GitHub。

**使用者**：

1. 连公司 VPN
2. Windows 双击 `install.bat`；macOS 双击 `install.command`
3. 重启客户端，对 agent 说「跑一下 preflight」

安装脚本会自动：装依赖 → 把配置复制到用户目录（600）→ 检测并注册到
ZCode / Claude Desktop / Cursor → 做一次 1433 连通性检查。

如果对方**没装经销商系统客户端**也没关系：懒人包不依赖客户端，账号已在包里；
想换账号则删掉 `~/.lq-flow.env` 后运行 `python3 setup.py`（支持从客户端 ini 自动提取）。


### DMS 网页版会话（手动登录一次）

网页版有验证码，无法自动登录，但可以手动登录一次后复用会话：

```bash
python3 dms_login.py
```

弹出独立 Chrome 窗口 → 你登录 → 脚本通过调试协议读取 Cookie（含 HttpOnly），
保存到 `~/.lq-dms.cookie`（600 权限），然后自动关闭浏览器。会话失效后重跑一次即可。

MCP 侧对应工具：

| 工具 | 说明 |
|---|---|
| `dms_status()` | 会话是否有效 |
| `dms_fetch(path, params, save_to?)` | 带 Cookie 请求网页接口（如 `servlet/DataSetQueryServlet`） |
| `dms_flow_report(year, month, day?, dept_code?, out_dir?, file_name?)` | 导出网页版「二级货物流向明细表」（BIRT，按月/日、按办事处） |

网页版流向报表链路（已验证）：

```
POST /hjerp/servlet/LoadReportServlet?reportFile=d_sale/d_report_sale_2dcinvout_detail.rptdesign
  → 返回 BIRT 元数据（:9095/hjreport/frameset）
GET  http://192.168.199.26:9095/hjreport/frameset?__report=...&__format=xls&reportArg=<SQL>
  → Excel 2003 XML
```

`reportArg` 是**明文 SQL**（不是 base64），页面里的 `<br/>` 要换成换行符；
`__format=xls` 才是导出，`spreadsheet` 会回落成 HTML。

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
| `check_office_changes(months?)` | 对比近 N 个月实际出库办事处与 regions.json，列出新增/消失/改名 |
| `refresh_regions(apply?, months?)` | 把新增办事处补进 regions.json（默认 dry-run） |


### DMS 网页版目录

`dms_web_catalog.json` 登记了 DMS 网页版（`http://192.168.199.26:9090/hjerp`）的 **99 个菜单**，
类型标为 `web`（分组标 `group`），`report_catalog` 会与数据库目录合并返回，
用 `来源` 字段区分（`ERP数据库` / `DMS网页版`）。

注意：网页版登录需要**图形验证码**，密码在前端 RSA 加密，MCP 无法无人值守登录；
这些条目只用于"知道有这张表、去哪导"，不能通过 `export_catalog_table` 导出。
如需接口级导出，需要提供已登录会话（Cookie）后再做对接。

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

## 办事处变更

办事处代码/名称调整后，不需要手工改 `regions.json`：

1. `preflight()` 会**自动比对**近 3 个月实际出库的办事处，发现变化会在 `office_changes` 里提示；
2. `check_office_changes(3)` 看明细：新增 / 消失 / 改名（作废、虚拟、测试类代码自动忽略）；
3. 确认后 `refresh_regions(apply=True)` 写入：新增项会补到「分公司=待确认、大区=待确认」，
   需要人工补上真实大区，`region="广西"` 这类过滤才会包含它。

权威源说明：以**近 N 个月有实际出库的 `zone_id`** 为准，名称取 `ctlm1003`。
ERP 的 `invalid_flag` 不可靠（大量作废代码仍为 0），所以不用它判断。

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

# lq-flow MCP —— 漓泉 cERP 货物流向网关

把公司 ERP 的取数和导出能力封装成 MCP 工具，装一次就能在你的 agent 里用自然语言取数。

**第一次安装请按下面 4 步走**，不要跳过第 2 步。

---

## ⛔ 使用铁律：只读 + 导出（不可协商）

**这个 MCP 只做「查数 + 导出」，任何时候都不写库。**

- 只允许 `SELECT` / `WITH` 查询与 xlsx 导出；`INSERT / UPDATE / DELETE / MERGE / TRUNCATE / DROP / ALTER / CREATE / EXEC / 授权 / DBCC / 备份恢复` 一律禁止。
- **即使数据库账号是 `db_owner` / `sysadmin` 高权限账号，也不得借它越权修改数据库内容。** 账号权限高 ≠ 可以写；需要改数据（主数据修正、单据调整、价格维护等）走 OA / DMS 正式流程，不要用本 MCP 代劳。
- `run_report_proc` 的 `param`、`export_catalog_table` 的 `where` 只接受**条件片段**，代码已硬编码拦截 `;`、`--`、`/* */`、写关键字与 `xp_*`/`sp_*`，防止借参数注入写语句。
- 正确姿势永远是：**读出来 → `export_query` / `flow_export` 落到本机 xlsx → 人工在系统里走流程**。
- `preflight()` 会回显当前数据库账号及其 `sysadmin` / `db_owner` 身份，便于核对；即使有写权限，MCP 侧仍强制只读。

代码实现（`server.py`）：`READONLY_POLICY` / `FORBIDDEN` / `FRAGMENT_FORBIDDEN` / `_check_sql` / `_check_fragment`。**改代码只许收紧，不许放宽。**

> 唯一例外：`_ensure_rpc_out()` 首次调用时会把链接服务器 `lqcerp_lnk` 的 `rpc out` 打开（`sp_serveroption`），属于连接选项、不是业务数据；不开就没法 `EXEC(...) AT link` 调报表过程。除此之外不做任何写入。

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

如果发放人已经直接给了你数据库账号，跳过扫描一步装完（把 `***` 换成实际密码）：

```bash
python3 setup.py --user erp --password '***'
```

也可以不给 `--password`，改用环境变量，避免密码留在 shell 历史里：

```bash
LQ_DB_PASSWORD='***' python3 setup.py --user erp
```

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

## 内部懒人包（已停用）

懒人包会把数据库账号密码预置进 zip，一发出去就是明文凭据在流转（微信、共享盘、转发、截图），
已停止生成和分发，`build_package.py` 已从仓库移除。**不要再做包，也不要再传包。**

新同事走上面「2. 运行配置向导」即可：`setup.py` 会从本机和佳客户端 ini 自动取账号，
密码只留在本机 `~/.lq-flow.env`（600 权限），不进任何分发包。

确实要帮同事省事，就**当面**把自己机器上的 `~/.lq-flow.env` 拷给他一次，用完让他自己改密码。

## DMS 网页版会话（手动登录一次）

网页版有验证码，无法自动登录，但可以手动登录一次后复用会话：

```bash
python3 dms_login.py
```

弹出独立 Chrome / Edge 窗口 → 你登录 → 脚本通过调试协议读取 Cookie（含 HttpOnly），
保存到 `~/.lq-dms.cookie`（600 权限），然后自动关闭浏览器。会话失效后重跑一次即可。

MCP 侧对应工具：

| 工具 | 说明 |
|---|---|
| `dms_status()` | 会话是否有效 |
| `dms_fetch(path, params, save_to?)` | 带 Cookie 请求网页接口（如 `servlet/DataSetQueryServlet`） |
| `dms_flow_report(year, month, day?, dept_code?, out_dir?, file_name?)` | 导出网页版「二级货物流向明细表」（BIRT，按月/日、按办事处） |
| `oa_status()` | OA 会话是否有效 |
| `oa_menu(keyword?, limit?)` | OA 菜单树（扁平化） |
| `oa_services(service, params?)` | 调用任意 OA 服务（`/exoa/services`） |
| `oa_flow_list(formset_id?, status?, page_size?)` | 查 OA 流程实例（发起人/时间/当前步骤/当前处理人） |

网页版流向报表链路（已验证）：

```
POST /hjerp/servlet/LoadReportServlet?reportFile=d_sale/d_report_sale_2dcinvout_detail.rptdesign
  → 返回 BIRT 元数据（:9095/hjreport/frameset）
GET  http://192.168.199.26:9095/hjreport/frameset?__report=...&__format=xls&reportArg=<SQL>
  → Excel 2003 XML
```

`reportArg` 是**明文 SQL**（不是 base64），页面里的 `<br/>` 要换成换行符；
`__format=xls` 才是导出，`spreadsheet` 会回落成 HTML。

## OA 协同办公（token 会话）

```bash
python3 oa_login.py
```

弹出的浏览器里登录一次即可。OA 的服务接口 `/exoa/services` **只认 HEADER 里的 token**
（`localStorage.UserInfo.SESS_GUID`），不需要 Cookie，所以只保存 token 到 `~/.lq-oa.cookie`（600）。

示例：

```
oa_status()
oa_menu(keyword="公文")
oa_services("OA.UserAddressBookService.getUserByUserId", '{"userId":"jiangkaihao"}')
```

token 随 OA 会话过期，失效时 `oa_status()` 会提示重跑 `oa_login.py`。

查流程实例（默认「自由流程(2026)」）：

```
oa_flow_list(2036, status=2)   # 2=在办，返回单号/发起人/当前处理人
```

抓包辅助：`oa_record.py` 可启动带调试端口的浏览器，记录 OA 的 `/exoa/services` 调用，
用于逆向未公开接口（产物 `~/.lq-oa-capture.json`）。

## 工具清单

| 工具 | 用途 |
|---|---|
| `preflight()` | 连通性自检：VPN/隧道/数据库是否正常 |
| `check_office_changes(months?)` | 对比近 N 个月实际出库办事处与 regions.json |
| `refresh_regions(apply?, months?)` | 把新增办事处补进 regions.json（默认 dry-run） |
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
| `dms_status()` / `dms_fetch(path)` / `dms_flow_report(...)` | 网页版会话、接口、流向报表导出 |
| `oa_status()` / `oa_menu()` / `oa_services()` / `oa_user()` / `oa_send_message()` / `oa_flow_list()` | OA 会话、菜单、服务、用户、发消息、流程查询 |
| `refresh_catalog(sources?)` | **从系统实时抓取报表目录**（`db`/`web`/`all`），缓存本机 |
| `report_catalog(keyword?, kind?, only_exportable?)` | 查询报表目录（需先 refresh_catalog） |
| `report_detail(name_or_id)` | 单个报表的登记信息 + 数据源字段 |
| `export_catalog_table(table_id, where?, out_path)` | 按目录导出某张报表数据表（受白名单约束） |
| `check_office_changes(months?)` | 对比近 N 个月实际出库办事处与 regions.json，列出新增/消失/改名 |
| `refresh_regions(apply?, months?)` | 把新增办事处补进 regions.json（默认 dry-run） |


### DMS 网页版目录

运行 `refresh_catalog("web")` 后，DMS 网页版（`http://192.168.199.26:9090/hjerp`）的 **99 个菜单**
会并入同一份目录，类型标 `web`（分组标 `group`），用 `来源` 字段区分。

注意：网页版登录需要**图形验证码**，密码在前端 RSA 加密，MCP 无法无人值守登录；
这些条目只用于"知道有这张表、去哪导"，不能通过 `export_catalog_table` 导出。
如需接口级导出，需要提供已登录会话（Cookie）后再做对接。

### 报表目录

目录**不随仓库分发**，首次使用时用 `refresh_catalog()` 从系统实时抓取，缓存在
`~/.lq-flow-catalog.json`（600 权限）。这样仓库里不含任何报表名称/表名清单。

| 来源 | 抓取方式 | 数量 |
|---|---|---|
| 经销商库 lqcerp | `refresh_catalog("db")`：扫 `hjappmenu` + 系统对象 | 657 |
| **ERP 三期 lqerp** | `refresh_catalog("erp")`：本机 `.19` 的 `hjappmenu`（2300+ 菜单） | 2162 |
| DMS 网页版 | `refresh_catalog("web")`：调 `MenuServlet`（需 dms_login.py） | 99 |

每个条目带 `库` 字段（`lqcerp` / `lqerp`），导出时自动走对应的库：
`lqcerp` 通过链接服务器 `lqcerp_lnk`，`lqerp` 走本机连接。

| 类型 | 数量 | 含义 |
|---|---|---|
| `table` | 479 | 有对应数据表/视图，可用 `export_catalog_table` 直接导 |
| `proc` | 4 | 有对应存储过程，用 `run_report_proc` |
| `datawindow` | 174 | SQL 编译在客户端里，数据库无对象，只能界面导出或复刻 |

每条还带 `匹配方式`：`精确`（表名完全一致）、`后缀`、`包含`、`人工`。**模糊匹配的条目导出前建议先 `report_detail` 核一次字段**。

用法：

```
refresh_catalog()          # 首次使用先跑这个（db 约 2 秒；web 需先 dms_login.py）
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


## 数据库账号从哪来（18456 排查）

和佳客户端安装目录里通常有**两份** ini：

| 文件 | 密码形态 | 说明 |
|---|---|---|
| `hjapp.ini` | 密文（32/56 位数字串） | 客户端启动时在内存解密，**不能直接拿来填** |
| `hjapp1.ini` | **明文** | 安装包里自带的模板，`LogPass` 就是明文密码 |

步骤：

1. 打开 `hjapp1.ini`，找 `[database]` 段；
2. 看同一段 `DBParm` 里的 `DATASOURCE=` —— 决定连哪台库：
   - `192.168.199.19` → 库 `lqerp`（本工具默认）
   - `192.168.199.86` → 库 `lqcerp`（**账号密码与 .19 不同，别混用**）
3. 用该段的 `LogId` / `LogPass` 填向导。

仍然 18456 时按顺序排查：

- 主机和库是否配对（`.19/lqerp`、`.86/lqcerp`）；
- 密码有没有多空格、大小写；（`hjapp1.ini` 里是带引号的，引号不要复制进去）
- 是否连续失败多次被 SQL Server 锁定 —— 等 15 分钟或让 DBA 解锁；
- `12010005` / `00010013` 这类纯数字账号是**客户端登录账号**，SQL Server 不认，
  向导会在尝试前直接拦下。

### 18456 为什么会"传染"到很多机器

多人共用 `erp` 这一个账号，每台机器装的时候试错几次，累计失败就会触发 SQL Server
的锁定策略，之后所有人都是 18456——看起来像"密码不对"，其实是账号被锁。

向导已做三件事防止它扩散：

1. 纯数字账号在尝试**之前**就拦掉（不消耗失败次数）；
2. 同一账号连续失败 **2 次即停止**，不再重试；
3. 识别 `18486`（账号锁定）与 `18456`（密码错/被锁），给出对应处置。

已经被锁时的处置：停止尝试，等 15 分钟自动解锁，或让 DBA 执行
`ALTER LOGIN [erp] WITH PASSWORD = '<原密码>'` 解锁。

最省事的规避方式：先找 IT/DBA 确认一个可用账号，**只发一次**，
不要让每个人自己去猜账号密码（每猜一次都在消耗失败次数）。

权限上不需要额外申请：这个账号本身就有 `lqerp` 的 SELECT、临时表、`EXEC`，
以及链接服务器 `lqcerp_lnk` 的访问权限。


### 导出防误导规则（重要）

报表目录里有相当一部分底表是**推断**出来的（`匹配方式` = 后缀/包含），且存在
**同一张底表被多个报表共用**的情况。为了避免"导错表"，`export_catalog_table` 设了四道闸：

| 规则 | 行为 |
|---|---|
| 同一底表被多个报表共用 | 用 `expect_name` **精确消歧**：唯一匹配才放行，否则拒绝并列出候选 |
| `expect_name` 与目录登记名不符 | **直接拒绝**（`expect_name` 为必填） |
| 匹配方式为「包含」「窗口推断」（猜出来的底表） | 默认拒绝，确需导出要显式 `allow_fuzzy=True` |
| 每次导出返回 `preview`（前 3 行）+ 报表名 + 数据源 | 便于当场核对是否导错 |

另外 `report_catalog` 命中多条时返回 `ambiguous: true` 并提示"禁止自行挑选"。
`only_exportable=True` 会自动排除低可信度条目。

**调用约定**：先 `report_catalog` 拿到确切 `table_id` 和中文名，再把两者一起传给
`export_catalog_table`，不要凭关键字猜表。


### 自检（回归）

```bash
python3 selftest.py
```

覆盖 5 项：工具齐全（>=27）、`refresh_catalog('web')` 不抛 NameError、数据库连通、
varchar 中文解码、错名导出被拒并给候选。提交前跑一遍。

### 中文乱码

本库部分 `varchar` 列存的是 **GBK 字节**。`_connect()` 默认已按 `cp936` 连接；
若仍有乱码，用环境变量覆盖：

```bash
LQ_DB_CHARSET=cp936
```


## 第一次安装：数据库密码从哪来

**仓库不含任何账号密码**（共享数据库账号不能进公开仓库）。新机器按下面顺序取：

1. **自动找明文**：`setup.py` 先扫默认安装目录；找不到会再**浅层扫 C:/D:/E: 盘**
   （深度 4、最多 25 秒）寻找 `hjapp1.ini`——它存的是明文密码，`hjapp.ini` 里是密文。
2. **找 IT/DBA 要账号**：`lqerp` 库 SELECT + 临时表 + `EXEC`，以及 `lqcerp_lnk` 访问权限。
3. **跟已经装好的同事要一份** `~/.lq-flow.env`：当面拷，别走聊天记录；用完改密码。

> 密文不需要"反解"：`hjapp1.ini` 与 `hjapp.ini` 是同一账号的明文/密文两份，
> 安装包（如 `ERP应用系统-三期.exe`）解开后根目录就带 `hjapp1.ini`。
> 归档目录（`D:\安装\...`、`D:\公司资源\...`）里也常留副本。

拿到后：跑 `setup.py` 或直接写 `~/.lq-flow.env`（600），再 `preflight` 验证。

**最快路径**：找发放人问一句「cERP 的库账号密码」，拿到后一条命令装完：

```bash
python3 setup.py --user erp --password '***'     # 密码问发放人
```

Windows 上不确定密码对不对，先加 `--diag` 只验证不写配置：
`python setup.py --diag --user erp --password '***'`，输出 `✓ 成功：db=lqerp login=erp` 就说明通了。

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
- `query_readonly` 只放行 `SELECT/WITH`，拦截写操作与执行类关键字（含 `openrowset`/`dbcc`/`waitfor` 等）。
- `run_report_proc` 的 `param`、`export_catalog_table` 的 `where` 有独立的片段校验，禁止分号、注释与写关键字。
- **只读是硬约束，不靠账号权限兜底**：哪怕连库账号是 `sysadmin`，MCP 也不会代你执行任何写操作。详见开头《使用铁律》。

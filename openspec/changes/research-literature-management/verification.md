# 文献模块交付核验（2026-09-19）

## 结论与范围

手动文献核心、独立 UI、生产 HTTP 浏览器闭环与本地确定性协议已落盘；本 change **未全量完成，不授权启用真实任务**。
本子线程只改文献模块、独立 static/literature-*、test_literature* 与本 change 核验材料；生产 web/MCP/Skills/README/shared helpers/smoke 接线由主线程完成。未改真实用户数据、未启用自动化、未提交工作仓库。

开工已读仓库/插件 AGENTS 与 proposal/design/spec/tasks，保留主线程在途修改。原文献基线50项存在4 failures、3 errors：旧测试调用不存在的 load、错误 create_item 参数、Windows 导入路径、迁移幂等/回滚假设。后续按当前 spec 修正，不为旧错误假设新增空接口。

## 已修改文件

插件根 `plugins/llmwiki-lite/` 下：
- `scripts/literature_catalog.py`：项目目录、身份规范化、人工字段保护、来源去重、移除/恢复、条目 revision、短锁、原子写、路径与资料上限。
- `scripts/literature_catalog_web.py`：列表/详情/编辑/显式导入、独立生产 HTTP dispatcher。
- `scripts/literature_collection.py`（新）：冻结来源、分页、日槽/租约、候选证据校验与 finish 回执。
- `scripts/literature_web.py`：仅明确收藏/原文/笔记绑定的阅读与对照入口，必要路径保护。
- `scripts/static/literature-catalog.js`、`.css`（新）：原位操作、失败留输入、IME、冲突反馈、折叠状态。
- `tests/test_literature_catalog.py`、`test_literature_catalog_web.py`；新增 `test_literature_support.py`、`test_literature_collection.py`、`test_literature_schedule.py`、`test_literature_browser.py`、`test_literature_browser.cjs`、`test_literature_performance.py`。

浏览器文件用 `test_literature_browser.cjs` 命名以遵守本线程 test_literature* 写入范围，未额外创建第二份 runner。

## 最终稳定公共接口

```python
# literature_catalog.py
literature_collect(project_id, locator, request_id, *, title=None, authors=None,
                   year=None, doi=None, arxiv=None, source=None, paper_file=None,
                   reading_note_paths=None, home=None)
# literature_collection.py
literature_plan(home=None, max_projects=3, *, now=None, source_provider=None)
literature_sources(run_id, cursor=None, home=None, *, now=None, source_provider=None)
literature_finish(run_id, outcome, candidates=None, error_code=None, home=None,
                  *, now=None, source_provider=None)
collection_status(project_id, home=None)
collection_retry(project_id, home=None)
# literature_catalog_web.py
dispatch_literature_http(handler, home) -> bool
```

MCP 只暴露非测试参数：`now/source_provider` 不对外注册。四工具为 `llmwiki_literature_collect/plan/sources/finish`。request_id/run_id 为32位小写 hex。collect 返回 item_id/item_revision/action/warnings/project_id/url，不返回完整目录。

finish outcome 为 reviewed/failed；候选字段 locator/title/authors/year/doi/arxiv/source_ids/evidence。evidence 为分片ID到冻结正文原样子串的映射（每段1–1000字符），每个身份均须在证据中出现；所有证据先验证再落盘。failed 仅接受安全 error_code，不接受错误原文。sources 必须读完本 run 全部分页。

### HTTP 接线与载荷

生产 `do_GET/do_POST` 原文献分发之前调用 dispatcher，返回 True 即停止，不能再次读请求体。该接线现由主线程实现，最终 HTTP/浏览器测试均直接使用 `create_server`，**没有 dispatcher monkeypatch/shim**。

- GET `/project/{id}/literature`（q/page）；`/literature/catalog` 兼容；`/literature/item/{item_id}`；`/literature/migrate`。
- POST `/api/project/{id}/literature/add`：locator、request_id、可选 title。
- POST `.../item/{item_id}/update`：title、locator、authors 数组、year（可 null）、expected_item_revision，全部字段须提供。
- POST `.../item/{item_id}/delete`：expected_item_revision。
- GET `.../collection`；POST `.../collection/retry`：空对象，只排队。
- POST `.../migrate/apply`：selected_paths；POST `.../migrate/rollback`：migration_id。
- dispatcher 自行服务 `/static/literature-catalog.js/.css`；read/compare/source/page 仍交共用服务器。
- 写请求必须同源 loopback Host/Origin、JSON、`X-Literature-Request: 1`，上限2 MiB；错误 `{ok:false,error:{code,message}}`；409保留输入。错误类 `LiteratureCatalogError` 提供 code/status。

## 来源真实接入状态与契约

主线程已交付 `research_reports.literature_inventory/read_source/authorize_source`；不再标“缺接口”。本次通过真实 saved-record helpers 和真实 MCP dispatcher 验证记录、手动笔记到冻结包/finish 的闭环，仅覆盖**已保存科研记录+手动笔记**。没有原始聊天、Git/活动全源接入；helper gap 如实披露原始对话未接入。测试提供候选，不证明宿主能正确判别论文语义。

```python
literature_inventory(project, *, start_date, now, home) -> (descriptors, gaps)
literature_read_source(project, descriptor, *, start_date, now, home) -> source | None
literature_authorize_source(project, source, *, start_date, now, home) -> bool
# descriptor: id, project_id, kind(record|notebook|conversation), locator,
#             revision, occurred_at, observed_at, certainty
# source: descriptor + text
```

id 必须同源修改后稳定，版本放 revision；locator 为稳定项目内定位（可含 entry fragment）；时间为带时区 ISO、start_date 为上海日期 YYYY-MM-DD。无可信发生时间时只接受有可靠观察证明的 observed/event/verified_range，不把 mtime 当发生时间；不套日报14天窗口。read 仅确认删除返回 None，不可读/失权抛异常。授权需重查项目、来源范围、ignore、脱敏、路径链接边界、排除自动化自身/报告/知识。提交授权收到冻结分片：id 是分片ID，source_id 才是 helper ID，另有 part_index/part_count/text；不因授权仍有效的正文正常更新就自动否认冻结证据。曾发现 helper ID 含 revision，已通知主线程修正；不由本线程改共用 helper。

## 实际验证结果

所有命令均显式 workdir 为本仓库根，数据均在临时目录。

- 文献单测：**54 tests，12.931s，OK**。HTTP 测试直接生产 create_server；真实 MCP 四接口及 saved helpers 闭环通过。
- 并发不是仅重跑：复现 Windows 空锁文件初始化 flush 与持锁进程冲突；移除锁前初始化写，改为空文件上锁。另复现 resolve 偶发保留扩展前缀导致同路径误判越界；只正规化 Windows 等价路径拼写，保留目标包含与链接检查。修后双进程测试连续12轮通过，另加空锁字节、锁超时和扩展前缀针对性回归。测试现在保留子进程异常 traceback，而非丢失原因的布尔断言。
- 失败 finish 回执与 state 写中断恢复新增回归；失败次数只计一次。Markdown 括号 DOI 证据回归通过。
- 生产浏览器：**通过**，无 shim，headless Chrome、1280×900；地址回车、IME、错误留输入、刷新持久化、搜索返回、编辑、真实双页409、移除、页面添加明确恢复、取消、原文/笔记/对照HTTP200、关闭状态。截图位于 `%TEMP%/llmwiki-literature-evidence/literature-{list,detail,error,conflict}.png`。列表截图已人工查看。尚未精确量化浏览器滚动位置恢复。
- 集成 smoke：**244 tests，140.277s，OK（skipped=2）**，末尾 `LLM Wiki smoke test passed`。包含既有回归；没有修改 smoke。该数字是当时实际运行结果，不保证主线程随后新增测试数量不变。
- 文献范围 Ruff：通过。全 scripts/opencode/tests Ruff：**157 errors**（含文献范围外遗留测试 bare except/unused/import 等）；未擅改共用文件，不能宣称全仓 lint 通过，也未逐项断定全部为开工前基线。
- opencode installer `--dry-run`：通过；七 Skills quick_validate：通过；plugin manifest validator：通过；`openspec validate research-literature-management --strict`：通过。

### 5000条与60秒宿主挂起性能

`test_literature_performance.py` 实际生产 HTTP；两个临时项目各独立 source/wiki/state/home，各初始5000条、目录5,040,616字节。Windows 11 10.0.26200，Python 3.13.9；CPU型号本次环境未提供。每项目、每模式、每操作热身1次、计20次；p95 为排序第19个样本。模拟线程实际等待60.00秒，所有挂起模式采样均确认线程尚未返回，**不是真实模型调用**。

|模式|项目|列表 median/p95 ms|新增 median/p95 ms|保存 median/p95 ms|
|---|---|---|---|---|
|空闲|0|73.71 / 97.84|394.76 / 412.58|404.50 / 597.14|
|空闲|1|91.63 / 104.71|437.84 / 538.14|458.63 / 575.58|
|宿主等待60秒|0|72.69 / 84.45|398.95 / 420.81|397.57 / 408.32|
|宿主等待60秒|1|74.44 / 97.78|404.59 / 449.00|403.12 / 432.74|

均满足 median≤1秒、p95≤2秒。同步断言首屏50条、不 rglob、不 stat source_root 内资料、不启动子进程、不连接测试 loopback HTTP 以外地址；网络防线使用 socket connect guard，未把测试自身 HTTP 禁掉。资料上限包括来源100/地址20/附件20/笔记20及50MiB原文拒绝回归。原始指标 `%TEMP%/llmwiki-literature-evidence/literature-performance.json`。

## A-01～A-30 证据映射

“本地通过”不等于真实共享宿主已验收。

|场景|结果/证据|
|---|---|
|A-01、A-02|本地通过：项目独立目录/HTTP/修改移除不跨项目。|
|A-03|部分：浏览器搜索与返回通过；精确滚动位置尚未实际测量。|
|A-04～A-06|本地通过：空项目不扫描，纯地址收藏与失败留输入，HTTP持久化。|
|A-07～A-10|本地通过：身份规范化、多命中冲突、同名独立、人工字段清空保护。|
|A-11|本地通过：条目 revision、无关新增不冲突、浏览器双页409保留输入。|
|A-12～A-14|本地通过：纯链接、明确绑定阅读/笔记/对照、失效资料提示和路径边界。|
|A-15、A-16|本地通过：移除不删文件、迟到 finish 不恢复、页面明确重收复用原ID。|
|A-17、A-18|确定性工具通过：自动关闭仍手动收藏、拒绝无项目/无locator；真实宿主澄清行为未测。|
|A-19|部分：真实 saved helpers+MCP 和证据校验通过；候选由测试给出，宿主论文语义判断及全源覆盖未测。|
|A-20|部分：真实 helper 披露原始对话缺口；无原始聊天接入，不冒充完整覆盖。|
|A-21|未完成真实验收：共享阶段顺序/失败隔离由主线程整合，未运行真实宿主任务。|
|A-22|本地 gate/撤权测试通过；没有真实绑定或启用回执。|
|A-23|部分：fake-clock 日槽、跨日长源分片、晚到下一槽、租约/模型失败重试通过；读取失败预算仍见下节。|
|A-24|部分：中途落盘重放、人工编辑/移除保护、回执/state中断恢复通过；极窄统计窗口见下节。|
|A-25|本地结构与浏览器详情通过：来源/时间折叠、状态不轮询；未测试全部辅助功能交互。|
|A-26|本地通过：失败不损旧目录、retry仅排队；真实宿主与报告隔离未测。|
|A-27、A-28|本地通过：旧目录只读不重写、显式选文件导入/重复/回滚、请求体仅读一次。|
|A-29|本地通过已测危险协议/控制字符/脚本转义/越界/同源/上限；不声称穷尽所有OS竞态。|
|A-30|本地浏览器/5000条/模拟60秒通过；真实自动运行未授权未测。|

## 剩余项（不减范围、不宣称整体完成）

1. 原始对话/全源接入、helper ignore/授权撤回各真实渠道的完整验收、真实宿主论文语义判断与同任务执行回执未完成；本次没有启用真实任务。任务3.1/3.2及5.4保守保留未完成。
2. **读取/inventory失败**现会记录 READ_FAILED 且不推进 checked，但尚未完整套用“同指纹3次/30分钟”预算；该预算已覆盖模型 failed/租约到期，不应宣称所有失败类型完成。
3. helper读取仍发生在 collection state锁内（不持 catalog锁）；慢源时短锁占用预算、全项目 pending_count 精确性、过期 active 在下次 plan 前的状态展示尚需收尾。
4. catalog写入后、applied_results落盘前的极窄中断可幂等重放且不丢条目，但 created/updated计数与last_updated_at未有完整崩溃窗口证明；不能以数据无重复等同精确统计已完成。
5. 浏览器精确滚动恢复、同路径原文内容替换后的附件版本/哈希行为、跨不同用途嵌套存储根的额外防护尚未完整验收；已有安全测试仅代表测试过的边界。
6. 全仓 Ruff 157错误尚在；共用模板/文档/真实阶段执行由主线程负责，子线程未单独证明真实任务可用。

因此本交付可合并手动闭环和本地协议实现，但不是“可直接授权无人值守全源收录”的完成声明。

## 主线程最终集成复核（2026-09-19）

- 全量 `smoke_test.py`：249项，247通过、2跳过，133.795秒；末尾 `LLM Wiki smoke test passed`。日志 `%TEMP%/phase34-smoke-final.log`。其中测试仓库中的临时commit不涉及开发仓库；开发HEAD仍为 `edc41d3`。
- 知识专项31项通过；主线程整合的知识/文献/报告/网页/MCP/测试文件Ruff通过。全仓157项Ruff错误尚未清理，综合门禁不冒称全绿。
- 两份OpenSpec严格校验通过。开发站点8766已从该源码无窗口重启，首页、项目页、文献、日报、知识状态接口均HTTP200；不代表真实自动化已启用。未写入真实科研正文、未发布插件、未改安装缓存。


## 2026-09-19 文献恢复独立写集补充证据

本节仅追加本线程的确定性恢复证据，不改写以上历史结果、不勾选 tasks，不替主线程证明真实来源/授权渠道/官方共享计划的执行验收。

### 修改范围与已补齐行为

- 修改 `plugins/llmwiki-lite/scripts/literature_collection.py`、`plugins/llmwiki-lite/tests/test_literature_collection.py`、`plugins/llmwiki-lite/tests/test_literature_schedule.py`，新增 `plugins/llmwiki-lite/tests/test_literature_recovery.py`；仅追加本 verification。没有修改共用 reports/MCP/Skills/采集/调度接线。公共函数签名和 `source_provider.inventory/read/authorize` 契约保持不变。
- 对应上文剩余项2及 A-23：inventory/read 失败使用稳定输入指纹，最多3次、失败后冷却30分钟；不推进 checked，跨日/observed_at 变化/当日晚到 locator 不绕过预算。读取、模型失败与到期共享预算；排队来源新版本或手动 retry 可重置。冷却/封顶期间只允许探测已排队来源的元数据版本，不继续读全文；若 inventory 探测本身失败，也受预算约束，不无限重试。
- 对应上文剩余项3及 A-23/A-24/A-26：短 state 锁只领取 preparing 租约、复核和落盘；inventory/read/authorize 都在锁外调用。慢 helper 返回时重新验证租约、当前时钟、设置和项目根，迟到结果不复活旧租约、不覆盖 successor。status 只读投影已过期 active 为 failed/RUN_EXPIRED，并展示次数及 retry_after；retry 可在下次 plan 前恢复，不能重置有效 active。恢复信封先落盘，state 写入中断后不重复计失败。pending_count 包含 max_projects 截断后的全部已持久化项目队列，不代表未扫描来源总数。
- 对应上文剩余项4及 A-24：利用现有 run 文件保存单候选写意图与条目/来源证明，没有新增通用事务层或修改共用 catalog。对同 run 的 catalog 已落盘但 applied_results 未落盘窗口，create/update 重放可恢复计数及原 last_updated_at；catalog 未落盘不虚报成功，人工移除不复活，人工修改不覆盖。同 digest 并发 finish 只产生一次创建和同一回执，不同 digest 返回 RECEIPT_CONFLICT。
- 配合主线程的 research_sources 授权对话接入，`test_saved_helpers_and_registered_mcp_end_to_end` 现明确断言未授权临时项目的缺口含“对话未接入”，不再依赖固定“原始对话”措辞。此测试仍走真实 saved helpers 和 MCP dispatcher，但没有连接或读取真实用户对话。

### 本次实际验证

所有注册表、项目、来源和目录写入均为临时测试数据；没有创建/启动真实自动化、迁移真实资料、commit 或 push。

- `python -B -m unittest discover -s plugins/llmwiki-lite/tests -p 'test_literature*.py' -v`：主线程来源接入及缺口断言调整后，**79项全部通过，23.701秒**。分项为 collection 19、schedule 17、recovery 18、catalog 17、catalog_web 8。日志 `%TEMP%/llmwiki-literature-recovery-20260919-integrated-unittest.log`。
- recovery 18项连续跑3轮：**54/54通过，12.717秒**，覆盖慢 helper、撤权/到期复核、并发 finish、恢复中断和窄窗口重放。日志 `%TEMP%/llmwiki-literature-recovery-20260919-repeat.log`。
- `ruff check --no-cache plugins/llmwiki-lite/scripts/literature_collection.py plugins/llmwiki-lite/tests/test_literature_collection.py plugins/llmwiki-lite/tests/test_literature_schedule.py plugins/llmwiki-lite/tests/test_literature_recovery.py`：**全部通过**。
- 本轮没有重跑全仓 smoke/Ruff、真实浏览器或5000条性能测试；unittest discover 不执行 opt-in 浏览器/性能脚本，不能据此宣称这些验收已重做。

### 仍保留的真实边界

1. 极窄窗口中，若纯元数据更新的证明又被后续人工编辑覆盖，无法可靠区分写入前后；不猜 created/updated 计数，返回 `replay_statistics_unproven` 并标记 partial，统计可能保守为 unchanged。上文剩余项4显著收敛，但不是所有统计窗口均已证明。
2. 精确统计恢复主要针对同 run 重放；旧 run 到期后的新 run 续跑，未建立跨 run 的精确统计合并。稳定身份/来源仍保持数据幂等，不能以无重复等同跨租约统计完全准确。
3. 本线程不验证或声明真实来源全覆盖、各授权渠道撤回、官方同任务绑定及宿主论文语义执行完成；这些由主线程独立给出实际证据。以上修复不改变未获真实运行验收的状态，也不把本地测试当无人值守全链路完成声明。


## 共享入口与授权来源后续集成

已由主线程接入统一外层、官方计划回执核验及 `ReportSourceProvider` 的真实授权活动库来源；不依赖报告14天窗口。报告或知识无任务/失败仍执行文献检查；source helper 再次验证授权、项目和时间。无可靠事件时间但有可信观察时间的活动明确保持 `certainty=observation`，不伪装事件日期。已检查来源保存locator/revision，查询限量前排除旧批，修订重新入选。前述读取失败预算、并发finish、租约与窄窗口恢复已集成；极窄统计不确定仍以partial说明，不升级成精确统计保证。

最终smoke322项（319通过、3符号链接环境跳过），包含文献79项及共享实际存储测试；文献浏览器通过。三阶段隔离链路真实落盘一条来自临时会话的论文地址，不下载；同一来源再次运行不新增，撤权拒绝、同ID修订和观察时间消费均有回归。夹具中的论文选择/总结是人工测试输入，不是宿主真实语义验收。

通过官方工具创建的共享任务id=`automation`，当前PAUSED，未绑定或触发生产执行；不得再称“没有任何实际任务”，也不得称“自动运行已验收”。未填写真实项目/时间/归档位置，未授权真实会话取材。8766旧服务重启被策略拒绝，未绕过。任务5.4保持未完成，其他UI专项与全仓157项既有lint错误也不掩盖。统一结果见 [报告核验](../research-daily-weekly-reports/verification.md#共享链路补齐源码联调结果后续状态以本节为准)。


## 2026-09-20 工作台汇总与真实计划启用（当前状态）

- 上文 PAUSED/未授权/未绑定是旧时点记录；用户现已授权全部9个注册项目。本机对话仅读授权起点以后的 Codex 文本，不自动扩大到新项目或其他宿主。
- 唯一官方 heartbeat `automation` 已 **ACTIVE**，每天北京时间18:00；周五同轮先日报后周报，再依次检查知识与文献。日报周报独立于科研项目，文献收录仍按来源项目维护。三阶段真实绑定已核验，本阶段开关已开启。
- **尚无首次非手工触发回执，真实定时验收任务仍未勾选。** 9月20日首个日报时刻、9月25日首个周报时刻尚未到达；连接状态不等于自动整理已经运行。
- 本阶段最新临时网站浏览器 **10组通过**（`%TEMP%/workspace-literature-browser-recheck.log`）。共享 smoke 本轮332项、329通过、3环境跳过；不能据此替代正式定时验收或并行 UI 专项。
- 9项目授权、独立报告存储、分批预算、24条会话元数据检查、现有8766旧后端需手动重启、全仓lint/其他UI失败等统一证据见 [报告验证末节](../research-daily-weekly-reports/verification.md)。不重复维护第二份共享状态，不修改其他线程的任务勾选。


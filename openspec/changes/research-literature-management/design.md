## Context

动机和范围见 [proposal](proposal.md)，行为以 [spec](specs/research-literature/spec.md) 为准。本文件只固定实现契约，不再复制一份产品需求。

以下是起草时的基础与缺口快照，不是当前待修清单；当前实施状态以最新 `verification.md` 和实际源码为准，新增 UI 必须复用已交付的目录/来源/共享链路。

起草时已检查的基础：`literature_catalog.py` 已有项目内 JSON 清单、历史、移除/恢复；`literature_catalog_web.py` 已有列表、详情和显式导入；`literature_web.py` 已有原文/对照阅读。尚有实际断点：添加 handler 传了 `create_item` 不支持的参数，URL 缺少必需 id；表单 action 与 POST 路由不一致；编辑入口没有对应保存闭环；详情没有展示已构造的来源和笔记入口。重复身份目前返回冲突，不是补来源；写文件虽原子替换但没有跨进程互斥。不能把这些文件存在当成功验收。

第二份报告设计已规定材料适配与真实共享计划，但代码尚不能当作已交付依赖。第三份仅扩展共享计划外层的知识阶段。本 change 的 D-06 是再加入文献阶段的唯一集成增量；前三份正文不在本轮改写。

## Goals / Non-Goals

**Goals:** 以现有目录、原文阅读和七个 Skill 为基础，把手动收藏闭环先做通；再增加有界、可重试的每日收录。Python 只做身份/路径/来源校验与落盘，文献语义由宿主判断。

**Non-Goals:** 不新建全局文献数据库、跨项目关联文档、下载器、编辑器、通用队列或模型服务；不扩大聊天采集权限，不给第一份补无人值守生成器。使用标准库与现有原生网页，不增运行时依赖。

## Decisions

### D-01 实施落点与顺序

以下相对路径均从 `plugins/llmwiki-lite/` 起算：

| 文件 | 本期职责 |
| --- | --- |
| `scripts/literature_catalog.py` | 归一化、项目内 upsert、人工修改、移除保护、短写锁与条目 revision |
| `scripts/literature_catalog_web.py` | 简化现有列表/详情/显式导入，接通阅读和来源入口 |
| `scripts/web_server.py` | 接通 D-04 路由及同源校验；不在请求中运行模型或扫描项目 |
| `scripts/literature_collection.py`（新增） | D-05/D-07 的每日材料计划、来源分页、收录回执、状态；不启动后台 |
| `scripts/mcp_server.py` | 注册四个确定性文献工具，实际调用上述模块 |
| `scripts/research_reports.py`（第二份交付后） | 仅增加文献设置/绑定状态及复用原材料 helper；不把文献写入塞入报告生成器 |
| `skills/llmwiki-literature/SKILL.md` | 增加“仅收藏”与每日增量分支，保留用户明确选择后的下载/精读流程 |
| 现有设置/网页模块、`README.md` 与相关测试 | 最小集成与使用说明；不重构整站导航 |

先做目录和手动网页，再做确定性来源工具，最后接入共享任务。公共服务文件串行修改，保护第一份执行者在途成果；不改写 `research_capture.py`。报告材料 helper 未交付时，用受控样本开发独立逻辑，但真实集成任务保持未完成，不复制第二套采集实现。

选择复用而非重建：现有目录已能保留身份、原文和笔记；问题是链路不通与自动收录缺失，不是缺少另一套平台。

### D-02 存储、来源与并发

```text
<wiki_root>/.literature/catalog.json         # 保留 schema_version=1 和现有条目
<wiki_root>/.literature/history/             # 沿用现有目录历史，不做新的版本平台
<wiki_root>/.literature/.lock                # 目录短写锁
<source_root>/references/papers/             # 新下载原文仍由原 Skill 放这里
<wiki_root>/literature/                     # 原有阅读笔记；允许既有合法 wiki 相对路径
<state_root>/literature-collection/state.json
<state_root>/literature-collection/.lock
<state_root>/literature-collection/runs/<run_id>.json
```

- 项目参数统一使用注册 `project_id`，从注册表解析三个根，不接受调用者自定义写入路径。不移动旧原文；附件仍是 source_root 内的明确相对路径，笔记是 wiki_root 内相对 Markdown 路径。遇到不同项目注册为同一个或相互包含的文献存储目录时，拒绝混用并提示先调整存储，不擅自迁移。
- 保留目录根的 `items`、`dismissed_candidates` 及未知旧字段；缺可选字段只读时给默认值，不因浏览批量重写。移除仍保存 `{item,dismissed_at}`，恢复沿用原 id。
- 条目沿用现有字段。原阶段新增 `manual_fields`（被用户明确编辑过的字段名数组，默认空）和 `title_is_placeholder`（缺标题时显示定位串，默认 false）；已确认原型增量另加 `is_read`（缺字段按 false 只读兼容，仅用户能修改），不增加阅读进度状态机。用户填写/编辑的字段即使清空也受保护；旧非空标题不猜成占位。每日不能更改阅读状态、归档、标签或已有笔记正文。
- 新 `source_refs` 固定为 `{id,kind,locator,revision,occurred_at,observed_at,collected_at,summary}`。kind 为 `manual|conversation|record|notebook`；id 为来源身份与版本的稳定 hash，不能含本次读取时间；summary 最多1000字符。页面收藏的 locator 为 `manual:<request_id>`，无原讨论链接；对话无可验证定位时标“本次明确收藏”，不能造聊天深链。原有非此结构的来源保留、按纯文本兼容展示。
- 沿用现有资料上限（来源100、地址20、附件20、笔记路径20及现有长度限制）。达到上限时保留原资料、返回具体 `limit_reached`，不静默截断、不挤掉人工资料；自动状态披露本条未保存的新来源。不会因此回滚其他论文。
- `item_revision` 为当前条目规范 JSON 的 SHA-256，包含来源和元数据，不含全目录 revision/读取时间。人工保存与移除必须提交该值；相同条目变化返回409，其他条目新增不造成冲突。新增收藏不要求客户端持有全目录 revision。
- 所有目录写入在同一短锁内重新读取、验证、合并、原子替换并更新既有历史；复用 notebook 的 Windows/POSIX 标准库文件锁做法，不复制其存储位置。模型调用、材料取读和全文哈希不持目录锁；并发操作不得通过固定 `.tmp` 互相覆盖。锁超时返回可重试错误并保留输入，不让请求无限等待。
- 收录状态另有短锁；需要同时使用时固定先收录状态锁、再目录锁。先以稳定来源 id 幂等更新目录，再写已检查边界/回执；中途崩溃可重放，不标未落盘来源已完成。不为两个 JSON 文件引入通用事务框架。

选择原目录加少量状态，而不是新增数据库；条目级 revision 解决实际前后台并发，不让全局修订号阻塞无关操作。

### D-03 身份规则与写入权限

1. 输入 `locator` 必须是 HTTP(S) URL、DOI 或 arXiv 标识。去首尾空白；DOI 去 `doi:`/解析站前缀后小写；arXiv 支持新旧编号、`arXiv:`、`arxiv.org/abs/` 与 `/pdf/`（去 `.pdf`），去结尾版本 vN 后作身份，保留用户原版本地址用于打开。不用标题、文件名或嵌入相似度合并。
2. URL 仅小写 scheme/host、移除默认端口、fragment 及 `utm_*` 参数；其余 path 大小写、尾斜杠与业务 query 保留，不擅自把 HTTP 改 HTTPS。拒绝 userinfo、无主机、非法端口、控制字符及危险协议。DOI/arXiv 转成对应解析地址供用户打开，但绝不发起解析请求。
3. 将本次所有 DOI/arXiv/URL 与活动条目和移除记录一起查找，不能命中第一个就提前返回。多个条目命中，或任一相同身份携带矛盾非空强标识，返回 `IDENTITY_CONFLICT`，不自动并条。自动将此候选记为跳过，继续其余候选。
4. 未命中则创建；只命中一个活动条目则仅补新来源、不同地址、空缺且未受 `manual_fields` 保护的元数据。占位标题允许被有依据的真实标题补齐；人工标题不覆盖。完全相同输入返回 unchanged，不改 updated_at。来源 id 已存在不重复追加。
5. 只命中移除记录：网页添加和用户明确收藏可恢复旧条目再补缺；每日 finish 必须返回 `skipped:dismissed`。写入时在锁内再次查移除记录，取材时未移除不能作为晚到写入的授权。
6. 人工编辑只接受 `title,locator,authors,year`：标题非空，authors 为字符串数组，year 为 null 或四位整数。locator 是主打开地址，保存时替换原主地址并解析所携身份，保留未涉及的辅助地址与标识；新值与现有强标识矛盾则拒绝，提示移除后以新论文收藏。不能借编辑改 id、来源时间、根路径、移除记录或自动状态。
7. 本地显式导入沿用原流程，只引用用户选择的文件；不把后缀判定当论文语义。已有明确 `paper_file`/`reading_note_paths` 才建立阅读关联，路径解析后必须留在本项目根。不按标题模糊匹配，不把另一个项目的笔记绑定过来。

选择保守身份 upsert 而不是自动合并审核平台；冲突只解释原因，不新建待办、弹窗或人工消歧系统。

### D-04 网页、HTTP 与阅读闭环

**页面细节：** 沿用网站字号、间距与线性图标。列表默认按 created_at 降序/id稳定次序，每页50项；搜索为标题/作者/DOI/arXiv 的不区分大小写子串，输入150ms防抖，中文输入法组合结束后触发。URL保存 `q,page`，会话级保存滚动位置；条目返回链接保留筛选，不使用可跳到外站的任意 return URL。添加成功在当前列表显示该条并给普通行内反馈，即使不匹配当前搜索也临时置顶一次，不静默清掉用户筛选。

添加区按已确认原型使用轻量 dialog，初始焦点是 locator；Enter（非输入法组合中）等同收藏，Esc/取消关闭并还原触发按钮焦点，失败留输入和焦点。重复收藏反馈“已在本项目文献中”并定位原条目；恢复反馈“已重新收录”。只禁用正在提交的按钮，不锁整站。

详情默认是标题、可选作者/年份、已读切换和可用阅读入口；编辑信息使用轻量 dialog，不另造 editor 页面。时间/来源在 `<details>`；移除在次要菜单，主动确认仅移除清单、不删文件。旧 `/edit` 链接兼容跳转到详情编辑态。列表不扫描 source_root、不 stat 每条附件；“有原文/笔记”按清单提示，打开详情才核验当前条目绑定文件。

| HTTP 路由 | 输入与结果 |
| --- | --- |
| GET `/project/<id>/literature`，兼容 `/literature/catalog` | 现项目列表；不自动调用 migration scan |
| GET `/project/<id>/literature/item/<item_id>` | 详情、条目 revision、折叠来源；不存在返回404 |
| POST `/api/project/<id>/literature/add` | `{locator,title?,request_id}`；返回 `{ok,action,item_id,item_revision,url,warnings}`，action=created/updated/unchanged/restored |
| POST `/api/project/<id>/literature/item/<item_id>/update` | `{title,locator,authors,year,expected_item_revision}`；返回新 revision，409保留客户端输入 |
| POST `/api/project/<id>/literature/item/<item_id>/delete` | `{expected_item_revision}`；只移除条目，返回列表 URL；已移除重复请求无副作用 |
| GET `/api/project/<id>/literature/collection` | 只读持久化状态：`status,last_checked_at,last_updated_at,pending_count,gaps,last_error,executor_status` |
| POST `/api/project/<id>/literature/collection/retry` | 空对象；仅重置当前失败输入次数、标记重试请求；返回 accepted，不直接启动 AI |

前端统一调用以上 `/api/project/...`，修复现有 `/project/.../api/...` 表单错配。写接口使用 JSON、同源 Host/Origin 验证及 `X-Literature-Request: 1`，复用 notebook 校验逻辑但不要求文献冒充 notebook；请求体上限2 MiB、只读取一次。失败统一 `{ok:false,error:{code,message}}`：400输入、403授权/同源、404不存在、409条目/身份冲突、413过大，意外错误不带私密正文。GET不写目录。原显式导入入口仍在更多菜单，沿用 preview/apply/rollback，并修复 apply 二次读 body；页面渲染不得触发它。

原文入口复用 `/project/<id>/literature/read/<source-relative-path>`；对照阅读使用现有 `/compare/` 严格 paper_file 匹配；笔记用原 `/page/<wiki-relative-path>`。已有不支持格式保持原只读行为，不新增转换器。有多份原文/笔记就列出各自名称供选，不猜主版本；失效项不可点击并说明，其他入口继续可用。外链安全转义、仅 HTTP(S)、新窗口加 noopener，不在网页抓取远程标题/PDF。

收录详情放列表次要 `<details>` 中；仅展开时读取状态，页面重新聚焦时可刷新一次，不轮询整站。重试按钮只在失败/待重试时出现；未绑定显示“文献收录待连接”，不得让按钮启动终端。

### D-05 四个 MCP 工具与来源契约

以下均是**待实现接口**，本规格不声称现已可调用。输入项目由注册表解析，工具返回有界 JSON；home 可省略，不能传任意目标写路径。

| 工具 | 固定输入 | 固定行为/输出 |
| --- | --- | --- |
| `llmwiki_literature_collect` | `project_id,locator,title?,authors?,year?,doi?,arxiv?,source?,paper_file?,reading_note_paths?,request_id,home?` | 只用于当前明确收藏；source 可含本次已知 `locator,occurred_at,summary`，不扫描其他对话；无定位用 manual request id。按 D-03 upsert，可恢复；返回同 add 结果，另含 project_id，不等待计划、不下载 |
| `llmwiki_literature_plan` | `home?,max_projects?`（默认/上限3） | 校验 D-06，到期项目各最多领取一个10分钟 run；返回 `runs[{run_id,project_id,slot,input_fingerprint,expires_at}],pending_count,gaps`，无材料不创建模型工作 |
| `llmwiki_literature_sources` | `run_id,cursor?,home?` | 返回冻结 `items,coverage,gaps,next_cursor`；每页正文总计≤20000字符，items遵循下述源结构；不重复读取原始会话 |
| `llmwiki_literature_finish` | `run_id,outcome=reviewed|failed,candidates?,error_code?,home?` | reviewed 包含已判定的论文数组（可空），failed 不写目录、不推进来源；返回每候选的 created/updated/unchanged/skipped、原因、计数及项目URL；同结果重复回执幂等 |

`request_id/run_id` 为32位小写 hex；来源无事件时间允许 null，不把收录时间冒充讨论发生时间。collect 的 DOI/arXiv 可选，仅用于明确已知的补充身份，不凭空推断两种编号对应。Skill 在项目/论文不明确时先问必要问题，不调用工具猜归属。

collect 的 `paper_file` 和 `reading_note_paths` 仅供本次用户明确导入/下载/精读完成后绑定已经存在的本项目文件；都必须是对应注册根内的相对路径。工具为原文生成现有附件所需 id、sha256、media_type、added_at，已有相同路径/内容只复用；笔记须为本项目 Markdown 且 paper_file 绑定一致。只合并新关联，不替换/删除旧关联，不写文件正文。文献 Skill 在用户明确要求的下载/精读完成后，用同一 locator 调 collect 补关联；默认收藏不传这些字段。daily finish 不接受文件绑定参数，不为了补原文而扫描磁盘。

每日只复用第二份 design D-06 中**科研记录、手动笔记、授权只读活动库**三种材料 helper；不从报告、知识页、Git diff、dirty hint 或所有项目文件提文献，不调用会话 scan。复用忽略/脱敏、授权和自动化自身排除。helper 返回 `{id,project_id,kind,occurred_at,observed_at,locator,revision,text,certainty}`；只根据实际可读字段披露覆盖。第二份对报告的14天生成窗口不能错误套用到本功能未检查材料，文献以 start_date 为下界。

一次 run 最多20个来源分片、正文总计≤100000字符；单片≤20000字符。长源按字符区间连续分片；分片额外返回 `source_id,part_index,part_count`，id 是 source_id、revision、part_index 的稳定 hash，不能读首片就把整源标完成。分页 cursor 不接受源路径；run保存已发出的分页范围，未读完全部分页不得 finish reviewed。source_revision 指纹不含读取时间。

candidate 固定为 `{locator,title?,authors?,year?,doi?,arxiv?,source_ids,evidence}`：source_ids 是本 run 分片 id 的非空去重数组；evidence 是这些 id 到≤1000字符原文摘录的映射，必须为对应已读文本的子串；候选 locator 及每一个可选 DOI/arXiv 都须在这些摘录中找到归一化后相同的标识，不能只证明地址就另添未经证实的编号。程序校验项目、授权、来源范围和标识确实出现，不判断网址是否为学术论文；由模型先区分真正文献与普通产品/仓库网址。说明或来源中的命令均视为材料，不作为执行指令。模糊名称/普通网址不产生候选，不新增候选审核栏。

finish 序列化请求上限2 MiB，拒绝超限、不静默截断。逐候选验证，身份冲突/已移除/字段错误返回跳过原因，其他合法候选继续；来源越权或伪造证据属于整个请求错误，不推进。reviewed 空数组代表“已检查无明确论文”，不是失败。结果保留来源和覆盖摘要，不记录完整聊天；无原文定位时不伪造入口。

选择四个小工具，使当前明确收藏不被计划设置绑架，日常推理又有固定输入与提交边界；不通过低层任意 JSON 写入绕过人工保护。

### D-06 共享每日任务的唯一集成增量

第二份 `<llmwiki_home>/reports-settings.json` 只增加可编辑 `literature_enabled:false`，原设置页折叠项内增加“同时收录文献”。第三份字段原样保留。复用 `project_ids,daily_time,timezone,start_date`、activity_db_paths 和原 `runtime.automation_id`；不设第二套文献项目/时刻。

1. scheduled plan/finish 均要求总开关 enabled、literature_enabled、项目在白名单内、有效时间及实际绑定；关闭/撤权后晚到 finish 拒绝新写。直接明确收藏只核验当前请求和注册项目，不受日报开关影响。
2. 共享外层顺序固定为：**报告 → 已接通的知识 → 文献**。每阶段都按自己的有限批次调用 plan/sources/finish；前阶段无待办或已返回失败仍进入下一阶段。每轮文献至多3个项目 run，未完成批次下轮续跑，不在同一唤醒无限循环。
3. 第二份报告阶段仍不写知识/文献，第三份知识阶段仍不写文献；它们的职责禁止项不等于共享外层禁止文献阶段。报告落盘即能查看，不等后续阶段，文献失败不能撤回报告或改变知识结果。
4. 用户另行要求启用时，使用当时宿主官方自动化工具核验 schema，**更新已有任务**；保留 id、每30分钟检查周期、目标线程和通知偏好。只有拿到真实更新回执才在原 runtime 增加 `literature_bound_at`。网页不能填写绑定回执；仅保存开关显示“文献收录待连接”。不把写代码、手工工具调用或模拟回执当真实启用。
5. 第二、三份已定义的暂停/恢复、最近回执和缺检查提示继续有效。共享任务被删除/暂停不能由网页假装实时感知。没有执行能力不得兜底后台 exec、系统计划、shell 循环或隐藏模型进程；本次写规格不创建任何真实任务。
6. 重试 HTTP 仅标记本模块 failed run 的重新处理意图；共享任务下次检查接收，在本功能关闭或未绑定时不读取来源。用户要求宿主当次重试，也调用同一套计划工具，而非网页发起模型。

共享任务追加的固定语义：
> 报告及已启用知识阶段结束（含无任务或已返回失败）后，独立检查文献收录计划。逐 run 读完授权来源分页，仅提取材料里有明确地址/标识的真正文献，调用文献 finish；不下载、不精读、不改人工资料、不恢复用户已移除文献。没有明确论文也提交已检查结果。普通成功安静完成，持续失败或配置问题沿用原通知规则；未接入的对话如实记录缺口。

采用串行有限批次而非新后台；“不阻塞”指前台和已完成结果不等待本阶段，不承诺共享宿主整体挂起时其他阶段还能独立运行。

### D-07 增量状态与恢复算法

JSON 状态 schema_version=1；last_result 取 updated/no_change/partial/failed，存在覆盖缺口或候选跳过记 partial，模型或读取/落盘失败记 failed。网页 status 结合设置和 active_run 显示 disabled/pending_connection/idle/running/failed；这些不是新的调度状态机。`state.json` 保存 `project_id,last_daily_slot,last_checked_at,last_updated_at,last_attempt_at,pending_sources,checked_sources,active_run,retry_requested,failure_fingerprint,failure_count,retry_after,last_result,last_error,gaps`。pending_sources 只存定位/版本及待检查分片，不复制全账户；checked_sources 以 `{kind,locator}` 为键，保存版本与已完成分片集合。run 保存项目/slot、冻结来源包、输入指纹、分页进度、到期时间与 finish 摘要/回执。

1. 时区与可测试 clock 复用报告实现（北京时间，持久化 UTC）。先继续已有 pending_sources，不因跨日丢弃未完成分片；没有积压且已过 daily_time、当天尚未开槽时，再在后台获取启用后未检查/变化材料清单，固定为新槽 pending_sources 并记录 last_daily_slot。项目按 last_daily_slot 较早、last_attempt_at 较早、project_id 排序，防止大项目饿死其他项目。没有项目新材料时只写检查结果，不调用模型。
2. 每个槽内按实际发生/观察时间、locator、part_index 稳定排序分批。slot处理完成后不在每30分钟重新对模型投送晚到材料，等下个每日槽；已有积压或明确失败重试仍可同日继续。离线恢复只处理 start_date 后尚未检查的现有材料，不逐日重复回放同一讨论。
3. 每批领取时读取并冻结该批文本。排队源在取读前已变则采用最新版本、清该源旧版本的分片进度，不把丢失的历史快照算已覆盖；新出现的 locator 留下个槽。源真实已删除标 coverage gap 并移出本槽，不删旧文献；不可读是失败，不能冒充删除。来源无日期且无法证明范围时披露缺口，不默认为启用后发生。
4. reviewed 成功（包括零候选、移除保护或身份冲突等已说明的跳过）才标该批分片已检查；全分片完成才记整源 checked_revision。模型/读取/落盘失败保留 pending，不推进。对 catalog 的部分写入可按稳定身份/来源 id 重放，不重复条目/来源；last_updated_at 只在实际文献变化时更新。
5. 同项目已有有效 run 不重复领取；10分钟到期旧 run 不接收新写，30分钟后可重试。同一输入指纹最多自动尝试3次，之后保留失败与重试按钮；新版本或用户重试重置次数。分页按原包重复读取不改变 id。失败结果也需 finish 回写；宿主中断则靠到期恢复。
6. finish 首次提交记录输入摘要，重复同 run/同摘要返回既有回执；不同摘要拒绝。无成功回执但中途已落盘时，重放仍执行最新授权/移除检查，不能为了恢复计数覆盖用户后来修改。成功后只保留引用、≤1000字符摘要、指纹、计数与回执，释放冻结全文。

选择有限槽与稳定来源指纹而非每消息推理：当天整理有明确边界，休眠/中断可续，前台只读状态。已有源被覆盖且从未留痕的旧内容无法还原，必须显示缺口而不是承诺完整历史。

### D-08 验收与性能预算

- 目录、HTTP/MCP、fake-clock、浏览器分别验证；映射 spec A-01～A-30。修正现有测试对 `load()`、不支持的 create 参数和 dismissed 结构的错误假设，不能为通过旧错误测试增加无用接口。
- 新增 `tests/test_literature_collection.py`、`tests/test_literature_schedule.py` 和 `tests/literature_browser_test.cjs`，扩展现有目录/页面测试及 smoke。浏览器复用现有临时测试服务/已安装测试工具，不自行安装依赖。
- 浏览器实际操作至少覆盖：只贴地址并回车；失败不丢输入；搜索→详情→返回；编辑与两窗口冲突；移除→普通讨论不恢复→明确重收恢复；原文/笔记入口；时间与来源折叠。保留列表/详情/错误态的少量截图，不只断言 HTML 字符串。
- 两个临时项目，各自 source/wiki/state/home；5000条目录的列表首50项和新增/保存各热身一次、测20次，记录环境、median和p95；median≤1秒，p95≤2秒（无争用、无模型调用）。同时模拟宿主60秒未返回，前台仍满足此预算。用测试桩断言页面/收藏不联网、不扫描 source_root、不启动子进程；预算失败先定位现有全目录历史/序列化成本，不默认引入数据库。
- 校验共享任务无报告/知识失败时仍检查文献，文献失败时报告仍可读，关闭文献只停该阶段；长源、晚到材料、离线、过期run、重复finish及写入中途崩溃有确定性测试。伪造来源、越界路径、恶意HTML/协议、跨站写入均有拒绝用例。
- 真实每日链路另需用户授权临时项目与真实共享计划，验证有依据论文入库、普通网址不入库、无额外窗口、同一任务id和实际运行回执；测试后按授权恢复原配置或移除临时任务。不具备授权/能力时该验收保持未完成，不用 mock、文档校验或当次手动工具调用替代。

### D-09 文献原型对齐及阅读笔记增量

共用壳与权威 HTML 见 [第二份 design D-11](../research-daily-weekly-reports/design.md)。本项目列表只显示真实条目，详情按原型提供返回、编辑信息、已读/未读、打开地址、可用本地原文、编辑阅读笔记与移出本项目；时间与来源详情折叠。添加/编辑使用同一轻量 dialog；原直接 `/edit` 链接仍转到详情并打开编辑弹窗。原文阅读/对照等旧能力放已有入口，不为贴近原型而删除。

已读状态扩展原 update 接口：允许 `{is_read,expected_item_revision}`，或原资料编辑字段；只改明确传入的字段，不由前端把空资料整包回写。原 MCP/每日收录不得写 is_read，打开论文也不自动标已读。移除/恢复保留此字段。同一论文在其他项目中的阅读状态不随之变化。

阅读笔记不是再次收藏或自动精读。复用原 `reading_note_paths`：无笔记时点编辑打开空白连续文档，但到首次显式保存才创建 `wiki_root/literature/<item_id>.md` 并绑定；路径已有非本条目文件时返回冲突，不覆盖。已有一篇直接打开；多篇明确选择原条目已绑定的一篇，不能猜主版本。新笔记 frontmatter 写 `literature_item_id` 和项目ID作明确关联，只有确有原文绑定时才写真实 `paper_file`，不能为了满足旧校验伪造PDF路径。旧 paper_file 关联继续有效，更新匹配器支持本条目的显式ID关联；其他同名文件不能自动绑定。

小型端点：`GET/POST /api/project/<id>/literature/item/<item_id>/note`。GET 可带 `path`（必须在本条目已有 reading_note_paths），缺省无笔记返回空稿、一篇返回该篇、多篇返回选择列表。POST 为 `{path?,body,expected_note_revision,expected_item_revision}`，路径只允许上述明确目标；仅保存当前笔记，保留原 frontmatter、原子写、409保留输入。首次创建重试复用同一 item_id 路径；若正文已落盘而关联失败，返回“笔记已保存，关联待完成”，重试只补同一关联不重写较新的正文。不建立通用事务系统，不在浏览时修复目录。移除文献不删除笔记。

正文编辑/预览/Markdown/粘贴复用第二份 document-editor，图片复用项目资产上传并按当前笔记路径计算相对链接；无PDF时也可手写笔记，单纯收藏不下载或生成笔记。原详情内预览当前笔记，编辑保存后回到同一条目并保留列表筛选；不另造文献块编辑器。回归明确ID/旧paper_file/跨项目隔离及真实重启后持久化，原自动收录和真实调度验收不因此标完成。

## Risks / Trade-offs

- [跨项目会存重复论文] → 这是用户选择的独立边界，不加全局去重或关联文档“优化”掉。
- [材料有地址不等于真实论文] → 宿主判断语义，确定性工具核验证据与身份；不把所有 URL/PDF 自动收录，也不以程序评分代替判断。
- [部分会话未授权或不可读] → 披露覆盖缺口，先利用已有记录；不扩权扫描全部聊天。
- [共享宿主整体中断] → 已落盘结果可用、run到期重试；不承诺进程级故障隔离，也不另起 exec。
- [JSON目录与来源数量有限] → 先按预算测实际闭环，上限显式反馈；不提前上数据库/通用审计系统。

## Migration Plan

1. 先在临时项目实现和验收；本轮不执行。旧目录只读兼容，不批量搬迁原文/笔记，不扫描真实项目做导入。
2. 新字段按需创建，literature_enabled 缺省 false；原文献入口保留，旧 /catalog、/edit 链接导向新闭环，显式导入仍需选择文件。
3. 手动闭环通过后再接报告 helper 与共享任务，真实启用另行授权；不动插件缓存、不提交/推送。
4. 回退先关闭文献开关，按用户授权从原共享任务移除文献阶段，保留报告/知识及原id。保留已收藏条目、原文和笔记，不用删除资料模拟回滚。

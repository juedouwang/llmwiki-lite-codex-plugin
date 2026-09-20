## Context

动机见 [proposal.md](proposal.md)。用户行为唯一标准为 [spec.md](specs/research-knowledge-maintenance/spec.md)，含原业务要求及新增 UI 条款，共 15 条要求、A-01 至 A-30。以下是实现约束，不新增产品范围。

以下是起草时的只读盘点，不代表当前仍未实现；实施进度以最新 `verification.md` 和实际源码为准。新增 UI 复用已交付业务，不重做来源/共享链路：

- `llmwiki_core.py` 已有 `_walk_files`、忽略规则、哈希、`snapshot`、`wiki_write`、`wiki_check`。`snapshot(save=True)` 会替换共享 `manifest.json`；`wiki_write(overwrite=True)` 直接覆盖且整理末尾换行，没有网页确认或正文版本门槛。本功能不能把这两者直接串成安全自动更新。
- `_frontmatter_sources` 可读源路径；`_update_wiki_index` 有显式生成区域。历史知识页没有统一的作者/机器分区，不能通过缺少标记推断“全部可覆盖”。
- `research_records.py` 能读取助手日档和旧记录，`research_notebook.py` 有原生文件锁及原子写；`record_change.py` 只记 fail-open 提示。
- `research_web_ui.py` 的 `project_page`、`page_view` 已提供简洁知识列表、阅读页和更多菜单；本期在这里增加状态与确认入口，不增加顶级导航。
- 第二份报告 change 只有规划，第一份仍由其他 agent 执行；不能把 tasks 的勾选视为本线程已验收，也不能回滚到 HEAD 丢弃在途文件。

## Goals / Non-Goals

**Goals:** 复用已有文件与页面；一个小模块完成增量材料、建议和确认；将自动新增与改旧内容分开；保证前台不等推理。

**Non-Goals:** 不引入第三方依赖、数据库、通用队列、知识图谱、固定分类树或新的聊天采集器；不统一重构第一、二份；不增加 Wiki 全功能编辑器、批量接受、文件删除/重命名或整库版本管理。

## Decisions

### D-01 代码落点与顺序

实施顺序固定为：保留并完成第一份 → 实施第二份 → 接入本 change。第一份未经本线程验收不自动重做；第二份未完成时本模块的手动维护、单测可独立开发，但每日共享执行验收仍未完成。

| 文件 | 本期职责 |
| --- | --- |
| `scripts/knowledge_maintenance.py`（新） | 项目状态、独立基线、材料冻结/分页、操作校验、建议/去重、采用/保留、到期判断 |
| `scripts/static/knowledge-maintenance.js`、`.css`（新） | 原知识页的轻量状态、原生 dialog、新旧对比、两个确认动作；复用现有主题变量 |
| `scripts/llmwiki_core.py` | 仅在必要时修正生成索引区域的字节保护；不改共享 snapshot 语义 |
| `scripts/research_web_ui.py`、`web_server.py` | 现有页面和 HTTP 路由最小接入；不改进度与报告正文语义 |
| `scripts/mcp_server.py` | D-07 的 3 个工具；保持所有原有工具可用 |
| `scripts/research_reports.py` 及报告设置 UI | 仅增加 D-02 的开关/共享入口接线；不让报告生成器写知识 |
| `skills/llmwiki-maintain/SKILL.md` | 改成准备→读取→判断→提交建议的流程，不再在自动维护中直接覆盖 Wiki |
| `skills/llmwiki-web/SKILL.md`、插件 README、测试 | 更新说明和验收；不增第八个 Skill、不发布新版本 |

如第二份实施采用了同职责但不同 helper 名，只绑定其已实现的确定性函数，不另复制一套报告采集/调度器。接口与存储行为以本文为准，不重新选型。

### D-02 与第二份共用计划：明确唯一的集成增量

在第二份 `<llmwiki_home>/reports-settings.json` 的可编辑配置中只增加 `knowledge_enabled: false`；缺字段按 false 兼容。设置页原“报告自动整理”折叠项内增加一项“同时维护知识库”。复用 `project_ids`、`daily_time`、`timezone`、`start_date` 和原 `runtime.automation_id`；不另选知识项目/时刻，不造第二个配置文件或后台进程。

定时启用条件为报告设置 `enabled=true`、`knowledge_enabled=true`、项目已选、时间有效且真实计划已绑定；关闭总开关同时停止本共享计划下的新知识运行。单独关闭 knowledge_enabled 不影响日报；对话明确要求的手动维护不受这两个开关限制。

**第二份当前提示词有“无到期报告就结束”和“不改知识库”。两份一起落地时，按以下规则精确扩展，不让 agent 自己解决冲突：**

1. 第二份报告 `plan/sources/finish` 及报告生成阶段仍禁止修改知识库。
2. 只在现有内置计划的外层增加知识阶段：先按第二份处理本轮有限数量的报告，报告结束或返回失败后，独立调用一次知识 plan；无到期报告也需要检查知识 plan。
3. 报告先落盘即可浏览，不等待知识阶段；知识失败不回滚报告。共享同一宿主不意味着模型并行或进程级故障隔离，不承诺宿主整体挂起时仍能继续另一个阶段。
4. 更新已有自动化而不是新建另一个；保留原 id、周期、目标线程和通知偏好。无实际执行能力时保留“未接通”，不得兜底 `codex exec`、系统计划、隐藏循环或自建模型服务。
5. 原暂停规则和工具绑定回执仍按第二份执行；仅网页保存 knowledge_enabled 不等于原自动化提示词已更新。只有宿主确实更新共享任务并拿到回执后，在原 runtime 增加 `knowledge_bound_at`。未有此字段时显示“知识维护待连接”；报告自身绑定状态不受影响。

共享提示词的新增阶段固定语义：
> 报告阶段结束（包括没有报告或已返回失败）后，独立检查已启用的知识维护计划。依照维护 Skill 分页读取原始材料及既有 Wiki，由宿主判断长期价值；新建与不冲突的末尾补充走知识 finish，改写旧内容只提出待确认建议。不把报告正文当原始证据，不改任务/Git/文献，不直接使用低层 Wiki 覆盖工具。没有待处理事项安静结束；普通待确认建议只在网站提示，持续失败或配置问题才沿用宿主通知策略。

本期不改变第二份每 30 分钟检查的约定，也不重新定义其日报周报补齐逻辑。仅写文档或实现代码不创建/更新真实自动化；用户另行授权时使用当时可用的官方自动化工具，并核验实际 schema，不手写宿主配置。

### D-03 最小持久化结构

全部路径由注册项目解析；参数只接受 project_id，不接受任意目标目录。

```text
<state_root>/knowledge-maintenance/
  state.json                 # 配置版本、已检查材料版本、页面依据索引、最近结果、当前运行
  .lock                      # 短写入锁，不在模型执行期间持有
  runs/<run_id>.json          # 此轮冻结材料、分页阅读进度、处理回执
  proposals/<proposal_id>.json # 待确认/采用/拒绝/过时建议以及自动新增的落地回执
<wiki_root>/                 # 继续使用原有知识页；候选不作为知识页混进搜索和索引
```

JSON 均 `schema_version: 1`，UTF-8；写入使用已有原子替换方式。互斥复用 notebook 中已验证的标准库文件锁做法（Windows msvcrt / POSIX fcntl），本模块锁文件放上述 state 内；不引入锁服务。

`state.json` 固定保存：`project_id, last_checked_at, last_updated_at, last_daily_slot, last_attempt_at, failure_fingerprint, failure_count, sources, page_sources, proposal_index, recheck_pages, active_run, pending_source_count, last_result, last_error`。

- `sources` 按 `locator` 索引，保存 `checked_revision` 与上次检查所需的有界文本；删除源使用 `revision="deleted"`，不可读不算删除。未检查、处理失败的版本不推进。
- `page_sources` 保存本功能处理页面的相关 locator 集合、最近实际写入时间及 action_id；补充历史 frontmatter 的 sources 映射，不回写或重排旧 YAML。
- `proposal_index` 只存建议 id/页路径/状态/时间/简短原因，供前台计数和分页，不逐条加载历史正文；`recheck_pages` 只存需要重新核实的页路径，不是通用任务队列。
- `last_result` 取 `updated|no_change|partial|failed`。有建议但无正文写入也算完成了一次检查，不能伪造 last_updated_at；另报自动新增数和待确认数。
- `run_id/proposal_id/action_id` 均工具生成或校验的 32 位小写 hex；action_id 在同一次 finish 重试中保持相同。

正文哈希及 base_text 从原始字节读取/UTF-8解码，不通过会改变换行的文本模式隐式归一化。每条 proposal 保存 `id, run_id, page_path, mode, base_sha256, base_text, proposed_text, reason, evidence_refs, evidence_key, status, created_at, decided_at, result_sha256`。status 仅为 `pending|applied|rejected|stale`；自动 create/append 也保存 applied 回执，便于详情追溯，不出现确认弹窗。不新增知识内容生命周期。

来源引用固定为 `{source_id, locator, revision, excerpt}`；excerpt 每条最多 1000 字，必须来自工具已冻结的原材料。状态和候选属于机器工作材料，不是正式知识页。运行完成后可去掉来源全文缓存，但候选、回执和其有界摘录保留；本期不做后台清理服务。

### D-04 材料范围、扫描与正确的增量基线

1. 项目源码/文本：复用 core 的忽略规则与 text_candidate，另按注册的真实绝对路径排除 wiki_root、state_root（包括自定义名称）、Git 内部文件、默认构建目录和敏感配置；不跟随越界链接。大小上限 128 KiB/单文件，超限、非 UTF-8 和不可读项记录 coverage gap，不静默截断为完整文件。
2. 助手记录/手动笔记：从已注册 Wiki 的 records 中单独取回，复用 `research_records` 的既有解析和 notebook Markdown 展示逻辑；按实际文件内容版本识别变化。排除 `records/reports/`、图片二进制和本功能输出；不能因为 Wiki 根整体排除就漏掉这些明确授权的输入。
3. 对话入口固定为已经落盘的科研讨论记录。首版不再次扫描原始会话或活动数据库，不新增解析器。第二份报告若已接入更多聊天材料，不能据此声称知识维护也全量覆盖；缺口在详情明确显示“本次仅使用已保存的讨论记录”。
4. 现有 Wiki 作为待核对上下文，不作为新原始证据。日报/周报默认不加入材料包；宿主在对话中看到报告线索时必须返回原始文件/记录核实。
5. PDF/截图不做新 OCR 或抽取管线；只凭文件名、存在性或图注不能声称已读图片。没有已保存可核实的文字记录时列入未覆盖资料，不从图片推断实验结论。

后台扫描可遍历文件元数据并计算受限文本内容哈希；“增量”指模型与内容处理只消费变更集，不宣称文件系统无需扫描。不能在页面 GET、状态轮询或确认请求中扫描整个项目。

知识基线独立存在 state.json，不调用 `snapshot(save=True)` 替代它，不清空 events.jsonl。Hook 只作提示；Hook 缺失也用后台扫描校验。首轮以空知识基线对比，分批处理，不把全部历史文件立即标成已总结。

选材顺序：待重检页面的有关材料 → 尚未处理的新增/修改材料 → 删除线索；同级按 locator 排序。一次 run 最多 20 项原始材料且正文总计不超过 200000 字符，超出留到下轮；每项本身仍受 128 KiB 上限。首次大量材料也不扩成整库一次调用。

`locator` 固定为 `source:<项目相对路径>` 或 `record:<Wiki相对路径>#<可选entry_key>`；文件哈希按原始字节，record 引用绑定其文件内容哈希。`source_id=sha256(locator + '\n' + revision)`；读取时间不进入指纹。删除线索带上次已检查版本/摘录，不把“文件不存在”推断成方法失效。

### D-05 语义责任与输出边界

宿主按以下顺序执行，不让 Python 根据关键词推断科研结论：读取变化材料和 Wiki 目录 → 检查有无同主题页面 → 定位受影响页与其 sources → 按需核实有关未变文件 → 判断新增/补充/修改/无需更新 → 对照原文和来源提交结果。

有 `frontmatter.sources` 或 page_sources 时据此缩小范围；缺少来源映射的旧页不能直接判为无关，由宿主参考目录和相关页面判断。只修改有直接依据的少量页，不遍历重写整个 Wiki。已存在同主题知识时优先补充原页，不能为了绕过修改确认另开一篇互相矛盾的新页。

新页默认放 `knowledge/<slug>.md`（slug 为有意义的小写 ASCII 连字符名称，1–64 字符），不是新建分类树；标题使用中文，正文保存有用知识而非模板。既有页保持原路径。不可写 `records/`、`literature/`、`papers/`、隐藏目录、带 `paper_file` 的阅读笔记或 index.md 正文。新页 frontmatter 只用原能力支持的 title/sources；来源记录等无法用源码相对路径表示的依据存 sidecar，不伪造 sources 路径。

允许的动作仅有：

| mode | content 的含义 | 确定性落地规则 |
| --- | --- | --- |
| create | 新文件全文 | 目标必须不存在且有实际依据；不存在性再次核验后自动写入 |
| append | 仅新增的尾部 Markdown | base_sha256 与当前页一致；保持全部原始字节为前缀，按需要在后面补换行再附 content；不使用会 rstrip 旧正文的覆盖写法 |
| replace | 建议的完整新版本 | 只保存 pending 建议；即使原页被标记机器生成也不能自动覆盖 |

append 必须由宿主明确判断没有矛盾；替换原词、修改标题/来源 YAML、移动段落、调整旧格式、删除旧句均属于 replace。Python 校验动作类型、版本、依据和前缀，不声称能证明自然语言“不冲突”。现有正文由用户写或修改过都不能被自动触碰；末尾追加不代表接管整页。空全文、删除文件、改名和批量接受不在本期接口内。

### D-06 版本、去重和失败边界

- 保存/采用前在短项目写锁内重读当前原文哈希；自动 append 或 replace 基线不匹配时不写。重新排入待核对，不能直接拿旧 proposed_text 换一个新哈希继续用。
- 使用已冻结的证据版本；自动落地或用户采用前只核对该动作相关的当前文件/记录版本，不扫描全库。来源已改变或不可读时不落地，显示需要重检。
- proposal 的 `evidence_key=sha256(page_path + 按locator/revision排序的实际evidence_refs)`；不纳入正文措辞、原页哈希、时间戳或整轮不相关材料。同页同依据已 rejected 时，所有 mode 都跳过，不能换成 append 绕过用户拒绝；相同 pending 只保留一条，不以新运行号复制。
- 证据真正变化时由新一轮判断产生新建议。旧建议基线或证据过时则为 stale；旧项退出待确认计数并留在历史，已打开的对比仍显示过时原因且禁用“采用修改”；将该页放入 recheck_pages，下次到期或手动维护重检。重检完成后移除该页的重检标记；有修改则形成新建议，没有则记录无需修改，不能留下永远不可处理的提醒。
- 每页每轮最多一个动作；同一次 finish 最多 10 个动作。无内容变化的 replace 不生成建议，重复创建/追加通过 action_id 和 resulting hash 回执跳过。
- 一页冲突不回滚其他独立页。被其失败影响的源版本不推进 checked_revision，成功或已存建议的源可以推进；无关未读源保持未处理。已落地动作回执保证重试不会重复添加。
- JSON/路径/未知 source_id 等请求结构错误在写入前整体拒绝；执行时的某页版本冲突/磁盘失败按页返回 `stale|failed`，不能谎称全成功。写入中断后重试先核对持久化 action 与正文结果哈希，已完成结果只补回执，不再次追加；不做跨全部知识页的大事务。
- index 仅在知识页确实落地后更新既有生成标记区域，区域之外原字节不动；没有标记时只在末尾添加生成区。不是自动润色 index 的豁免。候选/拒绝/仅检查不更新索引。机械索引内容不需要人为确认。

### D-07 固定 MCP 契约

新增以下 3 个工具，均支持可选 home，项目由注册表解析；工具不调用模型。旧低层 `llmwiki_wiki_write` 保留给既有明确写入流程，但维护 Skill 和自动任务禁止绕过本契约直接覆盖。

| 工具 | 输入 | 输出/行为 |
| --- | --- | --- |
| `llmwiki_knowledge_plan` | `trigger=scheduled|manual`、`project_id?`、`home?`；manual 必须给项目 | scheduled 在允许的项目中选一个最早到期/未处理项目；manual 只选明确请求项目。建立一个 10 分钟 run，固定变更集；返回 `run_id,project_id,expires_at,source_count,pending_source_count,gaps`，无工作返回 `run_id=null,reason`，已扫描无变化也更新最近检查时间、不调用模型。有活动 run 时返回 busy，不创建第二个 |
| `llmwiki_knowledge_sources` | `run_id,view=changes|catalog|page|source,cursor?,page_path?,locator?,home?` | changes 分页返回冻结原始材料及旧/新版本；catalog 返回知识页元数据和 relevant rejected 键；page 读取指定既有知识页并固定 base_sha256；source 读取核实所需的单个授权上下文源并固定版本。page/source 的路径参数与 view 绑定，不能兼用 |
| `llmwiki_knowledge_finish` | `run_id,outcome=reviewed|failed,reviewed_source_ids?,actions?,error_code?,home?` | reviewed 必须列本轮实际完整读取的原始材料 id，可无 actions；逐页执行 D-05/06，返回 `created,appended,pending,rejected_suppressed,stale,failed,remaining` 及详情 URL。failed 不推进基线。相同 run/action 已完成的重试先返回原回执（即使运行已过期）；过期但未完成的动作不接收新写入 |

sources 中正文每响应最多 20000 字符，长文用稳定 cursor 分片；catalog 每页最多 100 条，包含 path/title/source locators，不一次附全部正文。工具记录完整读取到末片的 source_id/page_path；未读完的内容不能在 finish 声称已检查或用作证据。单个 Wiki 上下文最多 256 KiB、一次最多读 10 个页面；超限不截断伪装完整，记录缺口并不改该页。额外 source 仍受 128 KiB/个，最多 10 个；它们是核实上下文，不挤掉已选择的变更材料。

actions 元素固定为 `{action_id,mode,page_path,base_sha256,content,reason,evidence_refs}`。create 的 base_sha256=null；其他值为 page 返回的原始字节 sha256；content 非空且最多 256 KiB。每个动作至少一条已完整读取、实际支持该动作的原始来源，excerpt 必须是冻结源文本的子串；删除线索只能引用冻结的旧摘录并标明“来源已删除”。不能引用仅为 catalog/wiki_context 的文本来假装原始证据。

finish 只推进传入且确实完整读取的源；未读完/来源变动/涉及失败动作则仍待处理；因rejected证据键跳过属于成功检查，不反复排队。create 必须已读完本轮 catalog；append/replace 必须已完整读取对应 page。新材料在此轮准备之后到达只在后续处理；不把当前扫描全部标为已检查。宿主若无值得沉淀内容，明确提交 reviewed 且 actions=[]，不是失败。

### D-08 每日、补处理和可用性

北京时间沿用第二份固定 UTC+08:00；每日槽为 `(project_id,local_date,daily_time)`。到期前不定时取材；到期后首次准备当槽，已完成且无本槽剩余材料则当日不因新文件再次触发，留到下一个槽或用户手动要求。

首轮/中断后的剩余材料在后续共享唤醒中分批继续；一次共享唤醒最多处理一个知识 run。项目优先级按最近检查槽最早、然后 project_id 排序，未处理批次用 last_attempt_at 排序，避免一个大项目一直挤占其他项目。文件扫描不在前台进行。

plan 还需校验已有 pending 的基线/相关证据变化，发现过时则持久化 stale 并排入 recheck_pages，即使当天只有知识页被人工改过也不能漏掉。

run 不锁住文件等待模型；10 分钟未完成即可释放占用，旧 run 不再接收新写入。单项目同一来源版本失败最多自动尝试 3 次，间隔至少 30 分钟；上限后保留可读状态和未处理材料，等相关材料变更或用户手动重试。无需改知识的 reviewed 不是失败；等待用户确认也不是失败。

机器休眠多日，只检查尚未处理的最新原始材料，不回放每一天的知识更新。正在生成时关掉定时开关，scheduled finish 必须重新核验设置并拒绝新写；manual run 只核验注册项目和本次显式范围，不会因此被误停。报告成功/失败不决定知识 run 是否到期。

### D-09 网页与 HTTP 契约

知识库页按已确认原型呈现目录/阅读两区：标题右侧“维护设置”和“查看更新”（有 pending 时带数量），阅读区保留“编辑”“来源”，维护运行信息折叠。原“待确认更新 · N”数据及 D-09 对比行为复用；N=0 时更新入口显示空态，不能假装有建议。继续上次只在科研进度显示，不再夹在知识阅读区；搜索、旧知识深链与更多菜单保留。详情展示真实检查/更新时间、覆盖缺口，不把时间摆在正文。

点击项目入口打开待处理列表；点击条目才显示原生 dialog。宽屏并排原文/建议，窄屏上下排列；每栏用现有安全 Markdown 渲染，折叠“查看文字差异”使用转义后的 diff，不执行源 HTML/脚本。原因和依据在正文对比下面；页底只有采用/保留两个主要按钮，右上角关闭和 Esc 不执行这两个动作，关闭后恢复焦点。点击提交期间只禁用当前条目按钮，不锁全站。

| HTTP | 固定行为 |
| --- | --- |
| GET `/api/project/<id>/knowledge-maintenance` | 返回 `status,last_checked_at,last_updated_at,pending_count,remaining,gaps,executor_status`，只读已保存状态 |
| GET `/api/project/<id>/knowledge-maintenance/proposals?scope=pending|history&offset=0&limit=20&page_path=...` | 返回分页元数据 `items,total,has_more`，limit 上限20，page_path可省略；不返回所有正文 |
| GET `/api/project/<id>/knowledge-maintenance/proposals/<proposal_id>` | 返回原文、建议、原因、依据、status及当前基线是否有效；仅检查当前页及本建议相关证据，不全库扫描 |
| POST 同上 | `{action:accept|keep,expected_base_sha256,expected_proposal_sha256}`；accept重新核验基线/证据后写入；keep只保存拒绝。只因原页变化不会阻止keep；proposal已被新结果替代则先返回409刷新。重复已完成相同动作返回原结果 |

GET 详情只返回本次计算的 effective_status，不改变提案/检查基线；POST遇到过时或后台plan检查时才持久化stale与重检标记。轻量计数基于已保存索引，下一次后台检查后与过时状态一致，不为角标扫描源文件。

proposal_sha256 只覆盖提案内容/依据/基线，不含轮询时间等字段。409 返回 `STALE_PAGE|STALE_EVIDENCE|PROPOSAL_CHANGED`，前端保留对比并说明，不自动重试覆盖；跨项目 id、非法路径用现有 4xx 规范拒绝。POST 沿用既有同源/路径校验，不能通过 GET 采用建议。

可见知识页仅在本项目有运行或待处理项时每 15 秒刷新轻量状态；后台标签页停止轮询，重新聚焦补一次。无 pending 时后台新建议在重新聚焦/刷新出现；不为实时角标常驻全站轮询。禁止自动弹 dialog、强制刷新、发起模型请求或弹桌面窗口。来源、时间均在 details；日期展示沿用网站北京时间格式，底层保存 UTC ISO 时间。

### D-10 验收、交付与性能

- 新建 `tests/test_knowledge_maintenance.py`、`tests/test_knowledge_schedule.py`，分别覆盖存储/动作/基线与 fake-clock/共享计划开关。使用临时 source/wiki/state/home，测试不扫描真实聊天或用户项目。
- 新建 `tests/knowledge_maintenance_browser_test.cjs`，挂接既有临时测试服务；复用已安装 Node/Playwright，不自行装新依赖。至少保留待确认角标、正文新旧对比、过时建议禁用采用三类截图证据。
- A-01/A-07/A-10 除结构断言外需要受控材料下的宿主生成检查，不能用关键词测试宣称已证明科研真实性；日志仅留用到的引用和结果，不打印敏感原文。
- A-25：模拟宿主 60 秒未返回，热启动条件下对普通≤50 KiB知识页/笔记进行20次读取/保存/状态请求，记录机器与p95≤500ms；验证 HTTP handler 不调用模型、源目录扫描或 Git。既有路由测试通过不代替本次延迟验收。
- 回归 notebook/progress/reports/smoke，确保第一份在途成果和第二份行为未变。路径越界、脚本注入、两窗口重复提交和刷新重复写入都有测试。
- 真实自动验收需用户另行授权临时项目/真实共享计划：确认报告与知识各执行一次、前台可用、无新终端窗口；再验证关闭知识开关只停知识。未授权、工具不可用或没有真实回执则对应任务保持未完成，不用 mock/手动调用冒充。

### D-11 知识页原型对齐与显式编辑增量

共用壳/HTML依据/响应式/集成顺序见 [第二份 design D-11](../research-daily-weekly-reports/design.md)，本份不再定义一套全局视觉。桌面目录在左、阅读在右；窄屏目录收成可展开列表，阅读占满主区。默认选择本项目最近打开且仍存在的知识页，否则沿用现有目录首项；空库只给真实空态，不生成示例架构页。切页以原 `/page/<relative>` 深链记录选择，返回保留本项目目录位置；助手记录、手动笔记、报告和文献阅读笔记继续使用各自入口，不误纳入知识目录。

“编辑”在同一阅读区域切连续 Markdown 源码，复用第二份的小型文档交互，不另建富文本系统。保存/取消只影响当前知识页，取消回到保存前正文；有未保存改动时离开需先保存或明确放弃，不因目录点击丢稿。来源读取现有 frontmatter.sources 与 page_sources 索引，没有来源就如实为空。“维护设置”打开现有共享报告设置的 knowledge_enabled 项；“查看更新”继续走 D-09，不能从按钮直接启动模型/终端。

为现有 `page_view` 增加小型同源读写端点 `GET/POST /api/project/<id>/knowledge/page?path=<wiki-relative-md>`。GET 返回可编辑正文和以完整原文件字节计算的 revision；POST 只接收 `{body,expected_revision}`，仅允许当前知识目录中的普通 Markdown 页，不能借任意路径改笔记/报告/论文/外部文件。保存保留原 frontmatter 字节，正文上限2MiB，沿用路径/同源校验与原子替换；提交前在与维护写入共用的页面短锁内重查 revision，冲突409保留编辑内容，不覆盖较新原文。浏览/取消不写盘，不以“用户打开了编辑模式”触发维护。

保存成功后旧建议按原基线核验自然失效，不把用户保存解释为采用 AI 建议；后台仍只能无冲突新建/追加，修改旧正文必须先确认。这里只新增显式页面编辑及原型内容布局；不重写知识取材/增量基线/共同调度。使用临时知识页验证手动保存与后台建议同时到达的保护、刷新持久化及原文件frontmatter保持。

## Risks / Trade-offs

- [语义“无冲突”无法由结构检查证明] → 宿主对照已有正文和原始依据；只开放严格追加，改旧内容必经人审；不承诺两个模型产生逐字相同文章。
- [同一宿主是共享执行端] → 串行、有限批次、状态独立，报告先发布；不新增第二个守护进程来伪装完全并行。
- [外部编辑器不参与文件锁] → 提交前即时重读哈希，冲突停写；只对合作的维护/网页写入提供短锁，不宣称任意外部进程具备跨文件事务。
- [首次材料多、文件/媒体超限] → 分批和显式缺口，不丢弃为“已完成”；较大文件和新媒体解析不是这期暗含任务。
- [旧维护 Skill 可直写 Wiki] → 本期维护入口统一改用知识 finish；其他明确写入工具不删，说明用户直接命令与无人值守维护的权限区别。
- [两份规格共享入口的文字冲突] → 按 D-02 明确扩展调度外层；报告工具本身仍不写知识。不趁此扩大第二份内容职责或第一份范围。

## Migration Plan

1. 不迁移历史数据；state 惰性创建，knowledge_enabled 缺省 false，旧网站与命令仍可用。
2. 在开发仓库和临时项目实施/测试；不操作插件缓存，不自动 commit/push 或发布。
3. 用户确认启用后更新已有共享计划并写真实绑定回执；仅修改配置文件不算真实接通。
4. 回退时先关闭 knowledge_enabled，并在用户授权下移除共享计划的知识阶段，保留报告阶段和原 id；保留已有知识及提案元数据，不批量删除或自动撤销知识正文。

用户运行时选择的项目、每日时刻、是否开启计划属于配置输入，不留给 agent 猜默认值。没有待自由选型的架构问题；实际宿主是否支持绑定及真实执行必须由实施验收说明。

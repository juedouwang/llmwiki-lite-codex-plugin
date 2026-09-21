# 科研工作台：文档入口与 Agent 交接

更新：2026-09-20（北京时间）。本文只管文档导航、交接状态和待细化范围，不另写一套功能规格，也不授权启动实施或自动任务。

## 1. 看哪份文档

| 文档 | 用途 | 不用于什么 |
| --- | --- | --- |
| 本文 | 找到各功能规格，了解交接顺序和后续范围 | 不定义字段、接口或重复验收场景 |
| [产品原则](product-contract.md) | 全局职责、数据与低打扰原则 | 不规定页面布局、不充当旧版功能清单 |
| [科研连续性说明](research-continuity.md) | 当前开发版的进度/笔记实现记录、限制和交互审查；由第一份执行者维护 | 其中早期“推荐链路”不是第二、三份的新执行规格，不能代替验收结果 |
| [刘亚宁周报模板](liu-yaning-weekly-report-template.md) | 用户原始模板和八项风格要求，供报告功能引用、打包 | 示例科研任务不是实际发生的工作 |

**用户审核：看对应功能的 `spec.md`。执行 agent：读该 change 的四份材料。**

| OpenSpec 文件 | 唯一职责 |
| --- | --- |
| `proposal.md` | 为什么做、做什么、不做什么 |
| `specs/<功能>/spec.md` | 用户行为和验收场景，唯一功能行为标准 |
| `design.md` | 实现落点、接口、存储和集成约定 |
| `tasks.md` | 实施与验证清单；勾选不等于用户验收完成 |

后续需求直接更新对应 OpenSpec，不再另建 `docs` 版需求摘要或总执行规格。材料之间发生实质冲突先指出并澄清，不从旧文档自行挑选行为。当前五个 change 尚未归档，`openspec/specs/` 暂无主规格；不要再复制一份充当“正式版”。

## 2. 功能入口与交接状态

下表是本次文档交接状态，不是运行检测结果；不因任务被勾选就宣称功能已验收或定时任务已启用。

| 顺序与功能 | 行为规格 | 执行材料 | 状态 |
| --- | --- | --- | --- |
| 1. 科研进度、Todo / Done、继续上次 | [spec](../openspec/changes/research-progress-todo-done/specs/research-progress/spec.md) | [proposal](../openspec/changes/research-progress-todo-done/proposal.md) · [design](../openspec/changes/research-progress-todo-done/design.md) · [tasks](../openspec/changes/research-progress-todo-done/tasks.md) | 进度交付与验收已完成；自动摘要生成端不属于第一份 |
| 2. 日报、周报、连续 Markdown 编辑 | [spec](../openspec/changes/research-daily-weekly-reports/specs/research-reports/spec.md) | [proposal](../openspec/changes/research-daily-weekly-reports/proposal.md) · [design](../openspec/changes/research-daily-weekly-reports/design.md) · [tasks](../openspec/changes/research-daily-weekly-reports/tasks.md) | 本地编辑/来源/生成及共享调度入口已接线；既有授权任务ACTIVE，首次真实定时验收仍待完成，见 verification.md |
| 3. 知识库自动维护与修改确认 | [spec](../openspec/changes/research-knowledge-maintenance/specs/research-knowledge-maintenance/spec.md) | [proposal](../openspec/changes/research-knowledge-maintenance/proposal.md) · [design](../openspec/changes/research-knowledge-maintenance/design.md) · [tasks](../openspec/changes/research-knowledge-maintenance/tasks.md) | 增量、修改确认与共享入口已接线，含授权对话；待实际计划运行，见 verification.md |
| 4. 项目独立文献管理 | [spec](../openspec/changes/research-literature-management/specs/research-literature/spec.md) | [proposal](../openspec/changes/research-literature-management/proposal.md) · [design](../openspec/changes/research-literature-management/design.md) · [tasks](../openspec/changes/research-literature-management/tasks.md) | 项目文献/授权来源/失败恢复及共享入口已接线；未声称全平台全量或定时实跑，见 verification.md |
| 5. 可视化 Git | [spec](../openspec/changes/research-visual-git-management/specs/research-visual-git/spec.md) | [proposal](../openspec/changes/research-visual-git-management/proposal.md) · [design](../openspec/changes/research-visual-git-management/design.md) · [tasks](../openspec/changes/research-visual-git-management/tasks.md) | 真实代码页、保存/分支/合并/恢复/显式远端已接入；临时仓库主流程通过，桌面与部分专项未齐，见本份 verification.md；不另建Git后台 |

第三、四份当前为**本地阶段交付，未全量验收、不归档**。详细证据和剩余项分别见：
- [知识维护核验](../openspec/changes/research-knowledge-maintenance/verification.md)：包括增量/确认协议、浏览器和非阻塞性能；共享入口与三阶段隔离联测已完成，真实定时运行及知识页真实隐藏标签专项仍未完成。
- [文献管理核验](../openspec/changes/research-literature-management/verification.md)：包括生产网页、并发与5000条目录性能；已接保存记录/手动笔记及授权本机对话，补齐失败预算与中断恢复；其他宿主/全量回填及真实定时验收仍未完成。

已依据用户明确授权，通过官方工具复用共享 heartbeat：`automation`（科研工作台自动整理），当前 **ACTIVE**。纳入现有9个注册项目，已保存本机项目对话读取授权、知识维护和文献收录开关；北京时间每天18:00触发，周五在同轮先日报后周报。两种报告都是工作台独立的多项目文档，不归档到任何项目。首次启用日为2026-09-20，因此不补造启用前周五的周报。

实际配置：`C:/Users/lyn/AppData/Local/LLMWiki/reports-settings.json`；源码bind已核验官方任务id、目标线程、配置路径及ACTIVE状态，三阶段绑定已写入，connection=configured。**尚无首次非手工触发的运行回执，last_success_at为空**；连接成功不等于已自动生成。项目范围为这次9个项目快照，新项目不会自动授权；日志仅读授权后文本。只读会话元数据检查共命中24个会话，3个项目存在索引与原文项目不一致的跳过提示，均不猜归属。

共享功能最新复跑：smoke 339项（336通过、3环境跳过）；笔记/site/进度组合、报告13组、知识7组、文献10组均通过，旧进度选择器失配已修。全仓Ruff仍有153项既有范围问题，不宣称全站全部验收。共享UI证据见第二份verification；Git实现、回归及剩余项见第五份verification，第三/四份沿用原共享状态。

当前8766仍是旧后端（只读请求未返回workspace范围）；此前重启被策略拒绝，未绕过。用户需手动重启源码网页服务，才会显示新的独立报告入口；任务直接用源码CLI，不依赖旧网页进程或旧安装缓存。

已按授权启用真实计划，但未伪造运行或测试科研正文；未修改插件安装缓存、未提交或推送。开发源码功能不能视为已安装插件功能；共享任务可按研究记录 Skill 使用源码 `research_cycle.py`，无需修改缓存。

### 交接必须保留的边界

- 第一份只交付任务和自动上下文的接收/保存/展示，不含真实无人值守生成器。注入摘要成功不能当作自动生成已完成；不因第二至第五份增加第一份任务。
- 第一份由原执行者完成并提供验收；后续获得统一授权后按第二份 → 第三份 → 第四份 → 第五份接入。共用网页/MCP/服务文件串行集成，保护已有成果和在途修改。
- **第二份与第三份的唯一调度集成增量见第三份 [design D-02](../openspec/changes/research-knowledge-maintenance/design.md)。** 报告生成器仍不能写知识；共享任务外层独立检查知识阶段，即使该轮无报告或报告返回失败。复用原任务 id 和每日时刻，不建第二个后台。
- **第四份加入文献阶段的唯一集成增量见第四份 [design D-06](../openspec/changes/research-literature-management/design.md)。** 同一任务外层依次检查报告、已接通知识、文献，各自独立写入；前阶段无任务或返回失败不直接结束整轮。各阶段原有职责限制不变，不另建文献后台。
- **第五份是用户显式点击的代码管理，不接入每日调度或新增后台。** 已确认示意见其 [design](../openspec/changes/research-visual-git-management/design.md)；真实Git行为以spec为准，示意中的模拟成功不是验收。实施验证仅使用临时仓库/临时bare远端，真实GitHub访问和插件发布另行授权。
- 日报和周报均为工作台独立的多项目文档，不归档到科研项目。新用户自行选择时间和项目；当前用户已明确授权现有全部9个项目、本机对话、每天18:00日报/周五18:00周报，并要求接通知识/文献链路。新项目不自动扩大授权；新用户的两个附加开关仍默认关闭。
- 只在开发仓库实施，用临时项目验证；不操作插件缓存、不自动 commit/push/reset；本次允许按用户明确授权写入生产设置/采集同意及启用计划，不为测试伪造真实科研正文。按所选 change 的 tasks 提交验收证据和未完成项，不把文档校验、mock 或手工调用当作真实自动运行验收。

统一交接时指定要实施的 change，使用 `openspec-apply-change`，先读仓库及插件 `AGENTS.md`、检查 Git 状态，再读所选 change 的 proposal/spec/design/tasks 四份材料。**本页不是“实施全部功能”的指令。**

## 3. 尚未细化的范围保留

以下从旧总计划保留，避免清理文档丢失已经讨论的目标。它们不是已审核的执行规格；下一轮逐功能确认页面、操作和验收后再交给 agent，不让 agent 自行补全接口或数据结构。

### 工作留痕与其他助手：保留目标，另行确认接入规格

- 留痕范围为授权项目会话、手动笔记、任务变化、文件变更/提交摘要；每条有项目、事件时间、来源，重复采集不重复入库，不跨项目猜归属。
- 主流程优先验证 Codex 的实际可用来源，再给 Claude Code、OpenCode 接消息来源；不重做报告/知识维护。未接入的对话明示缺口，不把 Hook 路径提示当完整聊天。
- 只读用户授权的项目/会话，不扫全账户；排除密钥、依赖和工具生成的报告，避免自我重复采集。ChatGPT 网页端不单独接入，继续手动记笔记。
- 本轮按用户继续接通授权，增加最小项目索引适配器：只查匹配已授权项目的会话元数据，再读命中正文，排除配置线程/自动任务。仅用合成会话验证，不把实现存在等同真实科研会话覆盖已验收；其他宿主接入仍另行确认。
- 每个宿主接入后用获授权真实样本核对覆盖与缺口；后续整体验收以连续三天科研使用、任务能找到记录、周报能找到依据为目标。

其余后置：云端全天运行、多用户、独立实验数据库、知识图谱/每次保存或提交即时推理、全历史回填、自动投递周报/PPT/DOCX、通用调度队列和分布式框架。不要把这些变成主要功能的前置条件。


### 2026-09-20 后续共享 UI 交付状态

第二份第7节7.1–7.4已完成共用六栏目壳、连续手动笔记和独立报告编辑；笔记/进度/站点组合与报告浏览器已重跑通过，覆盖并修复上文旧选择器失配。完整证据、写集和未完成项见第二份 `verification.md` 的「共享壳与连续文档 UI 验收」；不重复业务规格。7.5六内页总体验收、其他功能UI增量及首次真实定时验收仍未完成。保持既有真实计划与授权，不改缓存、不提交发布；8766仍需用户手动重启源码服务。


### 第五份开发源码交付（真实Git主流程）

Git写集已集成共享壳，没有复制侧栏或改动连续编辑/报告调度。网页可实际完成选文件保存、建/切分支、普通合并/有限文本冲突、保留历史恢复，以及显式检查后更新和单分支普通上传；恢复提交失败会保留文件并允许后续保存。全部写入证据来自隔离临时仓库/bare远端，不是科研项目或GitHub。

唯一交付索引：[第五份核验](../openspec/changes/research-visual-git-management/verification.md)及其tasks。真实Windows无弹窗观测、部分浏览器异常专项与10个旧Git测试错误仍未闭环，故未宣布第五份全部验收或归档；不改第二份7.5/真实定时验收。现有9项目ACTIVE授权保持，未更新已安装缓存、未提交推送。8766仍沿用上文手动重启源码服务说明，本线程没有再次停止/替换该进程。


### 2026-09-20 正常工作台开发对话漏采已修

调度和共享来源不再按定时线程id排除整个聊天，改为仅过滤自动轮次；本次按用户授权，将旧插件缓存目录下的当前唯一聊天明确关联到原工作台项目，并补采北京时间9月20日范围。原全局9月20日日报已补入工作台开发、验证与待办缺口，保留其他项目内容和草稿状态。118项专项中117通过/1环境跳过，真实网页API可读到补记。详见第二份OpenSpec tasks第10节、D-15、A-19b/A-19c及verification末节。没有改自动化/日程、插件缓存或提交推送；知识阶段6项目审阅未完成等既有缺口未冒充修复。

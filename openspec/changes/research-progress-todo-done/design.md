## Context

动机见 proposal.md，用户行为以 specs/research-progress/spec.md 为准。以下是 2026-09-19 对 `edc41d3` 工作树的只读核对，不表示已实现本规格。

| 已有实现 | 本次处理 |
| --- | --- |
| `research_progress.py`：四状态、tasks.json、revision、原子写入、100 条历史、关联记录 | 复用；补完成时间和人工字段归属，不另造任务库。 |
| `research_web_ui.py`：原知识首页最多三个继续上次任务 | 复用上下文/任务链接；最终展示移到进度页的单张摘要，项目列表保留单行任务入口，见 D-06。 |
| `static/progress.js` / `progress.css`：时间轴、未排期、折叠 Done、任务详情 | 原地补行为，不换组件库或视觉体系。 |
| 现有 GET progress 同时扫描科研记录以提供关联及旧候选 | 增加轻量读取分支；页面首屏/刷新不走记录扫描。 |
| MCP 尚无任务上下文读写工具，Hook 只写变化提示 | 只增加确定性接收接口，不把 Hook 改成总结执行器。 |
| `research_capture.py` 与采集测试未跟踪，smoke_test.py 有在途修改 | 不是本次依赖，不覆盖、不提交、不重写；不擅自宣称采集可用。 |

现有 smoke、progress、notebook 及隔离浏览器测试需保留。没有核实到可直接复用的无人值守生成器，故本次不承担其实现或启用。

## Goals / Non-Goals

**Goals:** 用小范围增量实现 P-01 至 P-09；明确字段归属、短路径读取和任务/摘要独立写入，让下游 agent 无需再自行选产品方案。

**Non-Goals:** 不建立后台队列、调度器、常驻模型进程、采集适配器或报告系统；不改用户科研内容；不动插件缓存、Git/文献/笔记模块；不重构既有锁与路径保护。

## Decisions

### D-01 两个小文件，各自负责一种写入

沿用 `<wiki_root>/.research-progress/tasks.json` 的 version 1；只增加可选字段。另用同目录 `contexts.json` 保存自动上下文，结构为 `version: 1` 和按任务 ID 索引的 `items`。两者使用不同 revision 与文件锁，原子写入；锁内只校验/落盘，不执行模型或等待生成。

| 位置/字段 | 确定含义 |
| --- | --- |
| task.completed_at | UTC 时间字符串或 null；显式从非 done 切到 done 时由服务端填 now；done 状态内修改不变；重开清为 null。客户端不能指定。 |
| task.context_mode | checkpoint、next_step 各为 manual 或 auto；控制显示来源。 |
| task.checkpoint / next_step | 原有人工文本，模式切为 auto 时也不删除，用于后续回看/编辑。 |
| history 中新增同名字段 | 和原字段一起保存显式任务修改；仍使用原最多 100 条策略，保留第一条，不新增无限事件日志。 |
| contexts.items[task_id] | checkpoint、next_step、source_record_id、generated_at、revision。两段文本各最多 4000 字符；只保留最新自动版本，不另建摘要历史库。 |

自动上下文成功保存只改变 contexts.json，不能改变 tasks.json 的 revision、updated_at 或历史。生成时间取服务端 UTC；展示时用现有本地时间格式。选择两个小文件而不是同一个任务对象中的混合写入，是为避免后台更新导致无意义的人工编辑冲突；不需要数据库或通用事件总线。

### D-02 兼容与有效内容规则

旧任务未带 context_mode 时：非空人工字段视为 manual，空字段视为 auto；旧 done 缺 completed_at 时为 null，不补造完成日期。只读归一化不写盘；下一次明确人工保存时才将规范字段保存。

有效字段 = mode 为 manual 时取原人工字符串（包括空串），否则取自动字符串或空串。只打开详情/修改其他字段，不改变模式。用户编辑某段上下文则把该字段标为 manual；“使用自动内容”只把对应模式设为 auto，按原保存按钮提交。提交失败保留输入与模式选择。

旧客户端未发送模式时，只把本次确实修改过的人工上下文字段切到 manual；未修改字段保留服务端模式。人工更新入口不接纳客户端提供的 completed_at 或自动上下文。每次显式状态变更将变更后的状态和完成时间一起纳入历史；重开前已有的完成快照保持在既有历史窗口内。

### D-03 接口固定，不增加模型服务

| 接口 | 输入/输出与边界 |
| --- | --- |
| 既有 GET `/api/project/<id>/progress` | 保留原响应兼容旧调用者；不作为新首屏和轮询路径。 |
| GET 同路径 `?view=summary` | 只读任务和自动上下文；返回 ok、人工 revision、tasks；每个 task 附 context_revision、effective_context；不返回候选/笔记全文，不扫描 records。每个任务含独立 context_revision，没有自动结果时为空串。 |
| GET 同路径 `?view=records` | 返回 records 与 candidates，复用现有最多 500 条记录的边界；仅详情打开加载笔记选项或用户点导入时调用，不阻止任务字段输入。 |
| 既有 POST progress | 保留 create/update/import，人工 revision 校验仍有效；允许规范 context_mode；按 D-01 维护完成时间。返回更新后的轻量任务快照，使页面立即显示有效内容。 |
| MCP `llmwiki_progress_get` | project_root 必填，state_root 可选，task_id 可选。给 task_id 返回单任务和 context_revision；不提供时最多返回按 P-04 排序的 20 个 active/blocked 任务短摘要及 ID/revision，明确 truncated，不承诺列出所有任务。无写入。 |
| MCP `llmwiki_progress_context_write` | 必填 project_root、task_id、checkpoint、next_step、source_record_id、base_revision；state_root 可选。额外业务字段拒绝。只写自动上下文，返回 ok、changed、context_revision、generated_at。 |

上述字段均为 JSON 字段。HTTP summary 中 effective_context 是每任务 `{checkpoint, next_step}`，context_revision 同样随各 task 返回，不另造全局自动 revision。MCP 列表中的上下文预览每字段最多 200 字符，完整内容通过 task_id 读取，保证结果有界。

写入时明确解析注册项目；校验 task_id 属于该项目、source_record_id 能由现有记录读取器在该项目解析。标题相似、最近打开项目或当前浏览器选中项目都不能替代明确关联。source_record_id 不覆盖人工 record_id。找不到/越界的来源返回错误，不尝试全盘搜索。

在自动文件锁内，先判断“两个文本及来源 ID 与当前值完全相同”：相同则 changed=false，不更新时钟；否则比较 base_revision，不符返回明确版本冲突并保留原值。新 revision 使用规范化记录内容（含 generated_at）的 SHA-256。不同任务的摘要不互相制造版本冲突。生成失败不写入错误文本代替上次摘要；本次不提供失败重试任务。HTTP/MCP 保留既有同源、路径及错误处理惯例。

将逻辑留在 research_progress.py，MCP 和 HTTP 仅调用它；不新增另一套领域模型。该 MCP 是未来执行端的接入口，存在接口不代表存在定时执行端。

### D-04 展示与刷新保持轻量

复用 active_tasks 的“仅任务文件”思路，接入有效上下文但不读记录正文。继续上次的排序仍以人工 updated_at 为准，自动批量刷新不会让任务跳来跳去。首页项目条目增加独立任务链接，不能在原整行链接中嵌套另一个链接。

保留 progress.js，把轻量摘要刷新用于科研进度页（知识库不再重复任务摘要）；项目列表仅在打开时服务端读取，不为每个项目创建常驻计时器。可见的项目页每 5 秒一次，隐藏后取消计时，重新可见立即请求；用单个在途标志防重叠，请求超时 3 秒后释放，下一周期再尝试读取。读取重试不是重新运行 AI。

首屏/刷新走 summary；任务详情打开时再异步读 records，加载期间可以编辑其他字段。已有 record_id 保持可选，不能因为选项未加载被自动清空。导入按钮按需拉候选，无候选显示空态，不偷偷自动导入。

背景刷新更新任务列表/只读摘要，不重建打开的表单。人工编辑使用打开时的人工 revision；不能拿后台最新 revision 覆盖该基线来绕过真实冲突。保存成功或明确重新读取后更新基线。新自动数据不改变人工 revision，因而不导致伪冲突。

没有自动内容时在折叠说明里显示“自动整理尚未接通”；已有内容显示真实生成时间和来源，不能仅凭收到一条内容宣称有常驻自动化。来源可用性仅在详情/打开来源时验证，不让首屏扫描记录。自动文件读错时人工任务仍可读写，返回局部 warning，不重写损坏文件。

### D-05 测试落点与验收证据

- test_progress.py：跨天、完成/重开/未知旧时间、日期验证、人工 revision、旧导入和数据不变。
- 新 test_progress_context.py：字段归属、显式空值、source/task 归属、独立 revision、幂等和乱序、MCP schema/dispatch、轻量读取不扫描记录、文件失败保护。
- site_browser_test.cjs / run_notebook_browser.py：隔离项目中的入口、Done、表单焦点、后台摘要刷新、关联选项加载和桌面/窄屏回归；复用现有测试依赖，不自动安装。
- 用延迟 30 秒的测试接入端/未交付结果模拟器验证 A-12；必须同时证明页面、保存、Hook 不会调用它，不能只测一个与产品无关联的 sleep。
- 在隔离临时数据上测 A-14，输出样本数和 P95；不使用用户 Wiki 跑造数或性能测试。若未满足目标，先定位文件扫描或共享锁等待，不通过换成同步总结“兜底”。

### D-06 已确认整站原型增量

共用壳、权威源稿和集成顺序仅引用 [第二份 D-11](../research-daily-weekly-reports/design.md)，不维护第二套导航/CSS规格。P-04 取代旧“项目首页叠加三项摘要”的布局，原数据/四状态/字段权限不变；原任务勾选保留，新 tasks 第6节专门验收。

复用 `research_web_ui.todos_page/resume_block`、`progress.js/css` 和已有轻量读写接口。默认以本地当前周周一为起点7天，不硬编码原型日期；上一周/下一周偏移7天，“本周”回当前周。14/28天选项保留在次要范围菜单，前后切换按当前范围步长，换范围保持所选周起点。时间条裁切仅影响可见区，不改存储日期；未知完成时间仍遵循 P-03/P-04，不能用计划结束时间冒充完成时间。

新建按钮/任务条/任务标题都打开同一既有任务编辑逻辑；顺序、状态、上下文和关联笔记规则沿用 P-01 至 P-09。任务信息中的时间/来源不摆到摘要正文。共享布局由第二份接入，此处只合并进度内容区，不能重建笔记、文献或知识模块。测试沿用原浏览器夹具，增加 A-15/A-16，不重开模型/调度职责。

## Risks / Trade-offs

- [没有真实后台模型执行端] → 本次只验收任务与结果接入，自动生成整条链路仍未交付；后续单独确认运行方式，不降级为前台等待，不启动 exec。
- [历史只保留既有 100 条] → 当前 Done 长期保留，但反复重开很多次的全部历史不保证无限回放；日报后续消费已留存材料，不能假设存在无限事件日志。
- [旧版客户端不认识人工模式] → 旧数据只读兼容，变更部署后应刷新网页；不能同时用旧版写入器维护新字段并宣称人工保护有效。
- [原业务已验收但新视觉尚未验收] → 保留旧完成项，按新增 UI 任务验证；不能凭旧截图宣布整站原型已落地。
- [工作区有其他在途修改] → 实施前核对差异，只合并本任务所需片段；不 reset、不自动提交/推送、不更改实际科研数据。

## Migration Plan

1. 用户审核主规格后才允许执行任务清单；无需等待日报/知识库完成。
2. 用临时项目验证旧 version 1 无新增字段、已有笔记及旧记录导入；读取不得写盘。
3. 发布时保持 source_root/wiki_root/state_root、任务 ID 和已存在路径不变；自动文件按首次显式接入结果时创建，不预先造假内容。
4. 如需停止自动结果展示，停止接入并保留文件；不能删除人工或自动内容。旧版写入器可能丢弃新字段，因此不把直接降级当作无损回退；回退方案须保留现有数据并先验证读取兼容，不对用户目录自动迁移或删除。

## Context

动机见 [proposal](proposal.md)，唯一用户行为标准见 [spec](specs/research-reports/spec.md)。本设计依据 2026-09-19 的本地工作区；HEAD 为 `edc41d3`，但工作区有另一 agent 的未提交修改，不能以 HEAD 代替现有文件，也不能覆盖它们。

以下基础盘点是起草时的代码快照，不是当前缺陷清单。实施进度以本 change 最新 `verification.md` 和实际源码为准；接入新 UI 前先复核，复用已交付的报告/来源/共享链路，不按旧盘点重复开发。

起草时已核实：
- `research_notebook.py` 已有图片校验、哈希去重、原子写入、冲突检测和 block 笔记；`notebook.js` 已有粘贴/本机恢复稿逻辑。复用这些机制，不把新报告伪装成一个大 block。
- `markdown_renderer.py` 已支持标题、加粗、列表、代码、表格、本地图片；远程图片转链接。现有 `###` 已能渲染为 h3，不需要安装编辑器实现此需求。
- `research_web_ui.records_page` 是改造前入口；最终独立报告入口见 D-04/D-11，旧链接兼容；`research_records.list_records` 会递归扫描 records 下的 Markdown，因此必须明确排除新增报告目录，防止报告反复进入原始材料/旧记录列表。
- HTTP 服务、MCP 框架、注册项目的 `wiki_root/state_root` 已存在。未发现已接通的报告生成/后台调度器。
- `research_capture.py` 和相关测试在其他人的在途改动中；`workbench_store.py` 有 activities 表，但无统一已部署活动库接入口。不得调用采集器的全账户扫描或把文件存在当作服务已接通。
- 用户最新决定取代旧需求记录中的复杂实时编辑要求：本期就是连续源码编辑 + 渲染预览；不引入 CodeMirror、富文本框架或公式排版依赖。

## Goals / Non-Goals

**Goals:** 一份纯 Markdown 正文、一个小型报告模块、一组确定性工具和一个宿主周期任务；把生成与页面彻底分离，明确每个操作的落盘和冲突行为。2026-09-20 增量只增加已确认原型的共用壳、独立报告入口及连续手动笔记界面（D-11/D-12），不重做各领域业务。

**Non-Goals:** 不实施旧总规格中的队列/租约平台、全宿主采集器、知识库自动维护或 Git 管理；不批量重构旧笔记存储（用户实际保存单篇旧笔记时按 D-12 无损转写），不读取用户的未授权全账户聊天，不安装/启动真实自动化作为写规格的副作用。

## Decisions

### D-01 文件职责及允许修改范围

路径均相对插件 `plugins/llmwiki-lite/`：

| 文件 | 本期职责 |
| --- | --- |
| `scripts/research_reports.py`（新增） | 报告/设置存储、来源清单、周期判定、单次运行占用与提交；Python 只做确定性逻辑 |
| `scripts/static/reports.js`、`reports.css`（新增） | 连续文档、模式切换、粘贴、保存、候选/历史；选择器限定在报告容器 |
| `scripts/research_web_ui.py` | 六栏共享导航、记录列表与独立报告页布局；共享壳由本 change 单一接入，其他页面只消费 |
| `scripts/web_server.py` | 下述路由、保护头和新静态文件白名单；不启动生成线程/子进程 |
| `scripts/mcp_server.py` | 注册下述三个报告工具；适配既有 schema/dispatch，不改旧工具语义 |
| `scripts/research_records.py` | 旧记录扫描排除 `records/reports/`；其他路径/格式不变 |
| `scripts/research_notebook.py`、`static/notebook.js/css` | 复用 upload/image_path、历史快照和冲突保护；按 D-12 接入连续笔记，保留旧数据读取 |
| `scripts/static/document-editor.js/css`（小型共享资源） | 仅共用编辑/预览、粘贴及光标行为；业务保存适配器分别调用笔记/报告接口，不造通用编辑器框架 |
| `scripts/static/style.css`、`app.js` | D-11 的共享壳样式、导航、窄屏与焦点；不替换整个技术栈 |
| `scripts/markdown_renderer.py` | 仅补当前报告验收实际缺失的渲染/安全缺陷，禁止替换解析器 |
| `templates/weekly-report.md`（新增） | 原样复制用户周报模板，确保安装插件后仍可读取模板 |
| README、现有记录/web Skill、测试 | 增加报告的明确调用流程、限制和验收，不新增第八套工作流体系 |

不修改 `research_progress.py` 的业务规则、未交接的采集器、Git模块、插件缓存、用户实际科研文件。共享路由可能已改动，按现有代码插入，不回退整文件。

### D-02 身份与落盘：不用新增数据库

新报告键固定为工作台 `{kind}/{period_start}`，内部使用 `__workspace__` 存储上下文，不把它注册为科研项目。kind 仅 daily/weekly；日报 start=end 为当天，周报 start 必须周一、end=start+6 天。两者 project_ids 均为参与项目有序集合，不参与键；改变范围更新同一文档或候选，不另造报告。旧 `{owner_project_id}/{kind}/{period_start}` 身份和正文只保留兼容，不批量迁移。

```text
<llmwiki_home>/workspace/records/reports/<kind>-<YYYY-MM-DD>/
  draft.md                 # 存在才表示有当前草稿
  candidate.md             # 仅保留最新 AI 候选
  previous-draft.md        # 明确采用候选前保留的一份人工稿
  v0001.md, v0002.md, ...   # 只在用户确认时新增，不覆盖
<llmwiki_home>/workspace/.state/reports/
  index.json               # 仅列表元数据，不含正文
  <kind>-<YYYY-MM-DD>.json  # 本报告元数据/运行状态
  sources.json             # 已见材料的指纹与发生/观察日期，不复制全账户资料
  runs/<run_id>.json       # 本次固定输入清单和分页材料；提交成功后转为来源摘要
<llmwiki_home>/reports-settings.json
```

所有 Markdown UTF-8，无用于状态管理的正文时间戳或 block 编码；以页面的逻辑 `current_page=records/reports/<key>/draft.md` 渲染该目录内的所有版本。本地图片共用 `records/assets/<hash>.<ext>`，正文写 `../../assets/<hash>.<ext>`，正式版与候选和草稿同级，切换版本不破坏相对引用。

报告元数据字段固定：

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 1，仅本小文件格式，不引入通用 Schema 层 |
| `scope, owner_project_id, kind, period_start, period_end` | 新报告 scope=workspace、owner_project_id=null；身份和周期由服务端生成，旧报告继续保留原 owner |
| `project_ids` | 日报/周报均为已注册且用户勾选的有序去重项目列表，自动运行固定当前全部已选项目；候选/正式快照各自冻结范围，人工稿不随配置悄悄变动 |
| `created_at, updated_at` | UTC 秒级 ISO 时间；详情转换北京时间 |
| `draft` | null 或 `{sha256, human_edited, updated_at, generation_id, sources, coverage_until, gaps, comments}` |
| `candidate` | null 或与 draft 类似的生成来源信息（不冒充人工稿） |
| `previous_draft` | null 或替换前草稿的来源/哈希/批注元数据 |
| `versions` | `{number, sha256, confirmed_at, generation_id, sources, coverage_until, gaps, comments}` 数组，按确认顺序 |
| `last_input_fingerprint` | 最近成功/无材料处理的输入指纹，不是原始源 mtime |
| `generation` | `{requested, state, run_id, input_fingerprint, started_at, expires_at, attempts, retry_after, last_error}`；state=idle/running/failed/no_evidence |

`revision` 仅由报告身份、当前编辑目标（draft/最新正式版号）、该正文的实际 SHA-256 及当前文档批注内容指纹合成；加载/保存都核实磁盘哈希，发现外部正文修改纳入新 revision。候选、执行状态、来源和时间变化不改变正文 revision，避免后台提示让正常打字产生伪冲突；保存时锁内重读元数据并保留这些并行变化，不拿客户端旧元数据覆盖。采用候选额外校验 `expected_candidate_sha256`，避免用户看过的候选在确认前被换掉。正式快照被外部修改时标“文件已在外部改变”，不能显示为原确认原文；不自动修复或覆盖。HTTP只操作工作台或旧报告所属项目已验证根路径，客户端不能提交任意读写路径。

写入用每报告短文件锁和原子替换；锁只包本地状态写入，不包模型调用/取材/Git读取。多个 Markdown/JSON 的一次提交用同目录暂存和小型待提交记录完成可恢复提交；中断下次读时完成或回退到提交前文件，不允许“正文新、正式索引旧”。复用笔记的安全路径/锁思路，禁止添加通用事务框架。index 为可重建派生索引，不作为正文真值。

### D-03 状态动作固定表

| 动作 | 前置条件 | 结果 |
| --- | --- | --- |
| create | 周期合法、项目已注册 | 若不存在建空 draft，human_edited=true；已存在返回已有报告 |
| save | expected_revision 匹配、正文 ≤2 MiB | 仅更改 draft；实际文字或批注变化设置 human_edited=true；空字符串也保护；无变化幂等 |
| start_edit | 无 draft、有正式版 | 复制最新正式版正文及来源为 human_edited=true 的 draft |
| confirm | 非空 draft、保存完成、无待上传图片 | 写下一个 vNNNN.md 并存来源快照，再移除当前 draft 引用；保留 candidate |
| restore | 指定正式版本存在 | 复制该版为人工 draft；当前有不同 draft 时须用户确认替换，先存 previous-draft |
| adopt_candidate | candidate 存在，用户明确同意替换 | 当前 draft 存 previous-draft；候选复制到 human_edited=true 的 draft；清 candidate，不自动正式 |
| regenerate | 项目/周期合法 | 标 requested=true、清本输入的自动重试上限；立即返回；不调用模型 |
| publish_generated | run_id 有效、输入与运行匹配 | 重新读当前状态；首次生成（无draft/正式版）或无人改自动稿且无正式版时写 draft，否则写 candidate |

自动计划为不存在的报告仅创建元数据/运行状态，不调用人工create、不预设human_edited=true；首次成功生成才落自动draft。明确文字或批注改动才保护；仅浏览/切换模式不算人工改稿。正式版进入编辑即创建受保护修订稿，保护优先于节省一次候选。生成任务开始时的 human_edited 值不能用于最终写入判断。正常自动保存携 expected_revision；生成提交不以旧 revision 覆盖用户稿，而在锁内重新路由到 draft/candidate。过期 run_id 直接拒绝。

人工修改后的来源保留生成依据并标 `human_edited`；明确不把人工新添句子伪称源证据已证明。正式确认保存当时 sources 和 comments 副本。只有确认产生 vNNNN，不保留每次键盘输入版本。候选更新不产生正式历史。重复 confirm 的旧 revision 返回当前最新版本及冲突提示，不新增第二版。

### D-04 页面、模式与保存细节

- 正式列表路由为 `/reports?view=daily|weekly&project=<id>&status=draft|formal`，缺省为 daily/全部项目/全部状态；新详情为 `/reports/<kind>/<period_start>`，旧详情 `/project/<owner>/reports/<kind>/<period_start>` 继续可读写，列表与详情均高亮“日报与周报”。列表内部只有日报/周报切换，不再混入记录。`/records` 只列科研记录；旧 `/records?view=daily|weekly` 以 302 转到对应报告列表，`q` 等有效筛选保留，非法 view 仍显示记录。新页面不生成旧报告列表链接。
- 已有稿默认同区域预览；新空稿直接聚焦一个自适应高度的原生 textarea，没有框内再嵌套块卡片。预览为空时点击空白处也可聚焦编辑。非空预览通过顶部“编辑”或正文双击进入编辑；末尾留一个不画按钮的可点击空白写作区域，点击进入文末。
- 所有模式规则按 R-03：空白文本首次粘贴→预览；预览粘贴→预览；明确编辑的非空正文粘贴→继续源码。保留上次 selectionStart/End 和两种模式各自 scrollTop；不把预览 DOM 反向转成 Markdown。
- 只截获当前文档正文及其空白区的粘贴；手动笔记标题框的纯图片特例按 D-12，搜索框、其他文档和浏览器快捷键不截获。文本使用 text/plain 原样插入；文件型 image 优先，避免同一次图片又插入 HTML。无纯文本的 HTML-only 剪贴板按惰性 DOM 取 textContent（不挂载、不执行、不发资源请求），不保留标签。
- 文本键入用 textarea 原生 undo。程序插入图片/预览粘贴维护最小可撤销事务，Ctrl+Z/Ctrl+Shift+Z 或 Ctrl+Y 应撤销整次插入而非整个文档；浏览器测试必须覆盖，不在每次输入后重建 textarea。Ctrl+S 保存、Ctrl+Enter 切换编辑/预览；不占用系统粘贴/中文组合键。
- 预览请求携本地递增序号，晚到旧响应忽略；编辑中不重绘 DOM。文本粘贴渲染的请求从当前内存正文产生，不要求先保存成功；预览更新防抖 150 ms。
- 图片插入先记录带本地 token 的待上传位置，异步成功后用 URL 替换该 token；用户删掉 token 则不复活图片。多个图按剪贴板/拖入顺序，不按网络完成顺序。上传期间其他文字可保存，但含待上传 token 的稿件不得确认；token 不写入正式版/复制导出内容。失败保留位置和重试按钮；刷新前未上传图像不承诺能从剪贴板恢复，必须提示。
- 600 ms 单飞保存：正在保存时记录后续 dirty 序号，返回后继续存最新稿；旧响应不得清除新输入。compositionstart 到 compositionend 期间不提交半成品。保存失败不清 textarea/本机恢复稿。
- 本机恢复稿键：`llmwiki-report:<owner>:<kind>:<date>:<tab-id>`，含 base_revision/body/comments/updated_at；成功保存且内存序号未变才删除相应恢复稿。内容冲突以对话框给出“保留当前稿 / 加载服务器稿 / 取消”，覆盖仍携用户刚审阅的最新 revision；又变化则再次冲突，不强写。
- 详情为更多菜单打开的侧面区/对话框；显示三组：时间与范围、材料及缺口、执行/版本。不得在正文顶部铺满日志、时间、按钮。
- 不新增 npm/运行时依赖；原生编辑模式是此次明确选择，不再执行旧需求中的同区富文本实时排版。报告用 `white-space`/现有 prose 样式；h3 字重至少 600。

### D-05 HTTP 接口（统一 POST 动作，减少路由）

沿用 loopback、既有同源/Host 校验；新 POST 统一要求 `Content-Type: application/json` 和 `X-Notebook-Request: 1`，图片复用旧上传接口及格式。成功 `{ok:true,...}`；失败 `{ok:false,error:{code,message}}`，400 输入错误、404不存在、409冲突/过期、413超限；错误不返回秘密或绝对资料路径。

| 方法/路径 | 参数 → 响应 |
| --- | --- |
| GET `/api/project/<id>/reports?kind=daily|weekly&offset=0&limit=30` | 保留旧接口语义；limit 固定最高30，列表不读所有正文 |
| GET `/api/reports?kind=daily|weekly&project=<id>|all&status=all|draft|formal&offset=0&limit=30` | 独立页面使用；优先列工作台报告，并列出明确标注的历史项目报告；按参与项目筛选，按存储身份/kind/start去重；`items,total,has_more`，items只含 key/title/period/status/owner/link。draft=存在修订草稿，formal=无草稿且有正式版；candidate不改变此分类。按周期倒序、owner/key稳定排序；不扫描源码或调用模型 |
| GET `/api/reports/<kind>/<date>`（旧 `/api/project/<owner>/reports/<kind>/<date>` 保留） | 返回 `revision,body,comments,mode=draft|formal,metadata,has_candidate`；不一次带出全部历史正文 |
| GET 同上 `?version=N` 或 `?view=candidate|previous` | 按需只读正文及该版来源；不能设置两个互斥参数 |
| POST `/api/reports`（旧项目API保留） | `action=create,kind,period_start,project_ids` → 已有/新建报告及 URL |
| POST `/api/reports/<kind>/<date>`（旧项目API保留） | `action=save|start_edit|confirm|restore|adopt_candidate|regenerate,expected_revision`；save另含body及可选comments（省略保留、空数组显式清空），restore另含version，adopt_candidate另含expected_candidate_sha256；返回最新revision/status或202等待生成 |
| POST `/api/reports/preview`（旧项目API保留） | `kind,period_start,body` → `html`；只渲染不写盘 |
| POST `/api/reports/upload` | 复用既有 `{data:base64}` 图片校验，保存到工作台 records/assets；GET `/reports/asset/records/assets/<hash.ext>` 仅读有效图片；旧项目附件接口不变 |
| GET/POST `/api/reports/settings` | GET脱敏设置/配置状态；POST全量可编辑设置和expected_revision（仅可编辑字段的hash），不接受从网页伪造automation_id/成功回执；后台runtime更新不得导致配置保存伪冲突 |

生成状态由详情读取/每10秒低频轮询，仅页面可见且有等待任务时轮询；不采用全站不停刷新。不要用 `sendBeacon` 不经冲突校验写正文。复制/下载是当前可见版本的 Markdown（有批注时末尾添加独立批注段，不带状态注释），文件名 `<kind>-<date>.md`；历史版也可下载。报告内部图片文件不打成 ZIP，本期不做附件导出包。

### D-06 材料入口：先复用已有数据，不偷偷开发采集平台

每天固定材料类型及取法：

1. 助手科研记录：读取原 `records/YYYY/MM/YYYY-MM-DD.md` 的条目时间/内容及旧格式记录；复用记录解析。只处理新增/内容变更文件，排除 reports 和图片二进制。
2. 手动笔记：读取 `records/manual` 中有创建/修改发生于目标日的笔记。注明“本日修改的笔记”，不能把整个旧笔记当当日新增成果；有旧快照则给差异，无快照则给现文及此限制。
3. 任务：读取 `.research-progress/tasks.json` 中已存在的任务历史事件及完成时间；只读，明确“用户标记状态”；旧数据缺完成时间不倒推。
4. Git与变化提示：读 `<state_root>/events.jsonl` 的 file-change-hint，及项目 source_root 中 `git log` 的提交元数据/受限文本差异。提交归属按 committer timestamp 转北京时间；当前 `status/diff` 仅记本次观察，不能追溯休眠时未捕获的工作区状态。非 Git 项目只用可用文件变化提示。
5. 可选活动库：只有该项目设置了显式 `activity_db_path`，且现有采集授权允许该 host/capture/source_text，才通过 SQLite只读连接查询已有 activities 表的 active 条目。路径必须位于该项目 state_root；按project_id、occurred_at（无则observed_at并标观察）和host过滤，仅读取当前已存在字段。不开库建表/迁移，不调用 `RolloutCaptureAdapter.scan`。缺配置/授权/已知表结构就标“对话未接入/不可用”，不是失败整个报告；不凭猜测去寻找数据库。通过已有科研记录 Skill 保存的对话摘要仍属于第1类。

sources缓存的键为 `{project_id,kind,locator,source_revision}`；单个材料统一输出 `{id,project_id,kind,occurred_at,observed_at,locator,revision,text,certainty}`。certainty=event/observation，Python不判科研结论。时间戳指事件而非最近读取时间；缺时间不默认放昨天。过滤敏感路径、现有忽略规则、采集 redacted 状态、自动化线程会话和 `records/reports/`；不要把生成报告的文件变化提示/Git提交当新的科研活动。检测到敏感字段按既有脱敏规则处理，不在日志打印内容。

只在后台工具中读取源内容/Git，前台页面不扫描代码库。Git统一只读 argv 调用，`shell=False`、禁止外部diff/textconv、`GIT_OPTIONAL_LOCKS=0`、超时10秒；Windows子进程统一无窗口标志，不允许连“只读git”也弹终端。无Git/超时写coverage gap，其他材料继续。

来源包在一次运行准备时固定；id与revision形成稳定有序指纹，不把读取时间、任务触发时间、缓存路径纳入指纹。文件未变复用缓存；仅新材料/变化部分+上次未人工编辑的机器稿供增量整理。若只有人工/正式稿可用，将其明确标作用户稿参考，输出仍按候选处理，不能当作旧机器稿自动覆盖。

来源详情存该轮**实际使用**的source_ids、原始locator/revision与每条≤1000字的当时摘要；来源全文包成功后可释放，不承诺保存所有原始聊天。来源被删除保留摘要并显示原文不可用。若单篇材料很长按20000字符分片，MCP分页返回，不能静默截断后标覆盖完整。

### D-07 三个 MCP 工具与模型职责

工具均以 `home`（可选）解析既有注册表，来源身份只接受注册 project id，报告存储使用工作台上下文；设置中的项目白名单在每次执行核验。run_id 为随机ID，不允许模型提供任意磁盘路径写文件。

| 工具 | 输入 | 输出与限制 |
| --- | --- | --- |
| `llmwiki_report_plan` | `home?`, `max_reports` 默认3且上限3 | 检查开启设置/到期/手动requested；按D-09排序，仅为当前轮可执行项建立10分钟run占用，固定来源包；返回 `runs[{run_id,scope,owner_project_id:null,kind,period_start,period_end,project_ids,input_fingerprint,expires_at}],pending_count,gaps`。无变化直接跳过，不生成空AI工作 |
| `llmwiki_report_sources` | `run_id`, `cursor?`, `home?` | `items,coverage_until,gaps,next_cursor`；items的正文合计每页≤20000字符。旧机器稿、用户参考稿、已打包模板作为reference类型分页项返回，不把长稿塞入每个响应；每项role=source|machine_previous|user_reference|weekly_template，周报材料标所用日报版本 |
| `llmwiki_report_finish` | `run_id`, `outcome=generated|no_evidence|failed`, `body?`, `source_ids?`, `source_summaries?`, `error_code?`, `home?` | 校验未过期运行及实际source_ids，按D-03写稿/候选或错误。generated需正文/依据，body为字符串，source_ids为本包source角色id去重数组，source_summaries为这些id到当时摘要字符串的映射（每项≤1000字）；unknown id/缺摘要拒绝。未读完来源页不得声称全覆盖。返回 `target=draft|candidate|none,revision,url`；finish重复相同结果幂等，不重复落盘 |

plan 可以一次最多准备3项，但按顺序逐篇立即取材/完成；若后续run因前篇耗时过期，下轮重新计划，不强行提交。plan对同一报告的占用和失败计数用报告元数据即可，不另造 jobs 表/通用队列。未来内核变更只在该小模块内，不接旧复杂工作台调度。

宿主 AI 的固定执行顺序：plan → 逐项读取完整分页 → 判断有无足够材料 → 根据日报内容规则或周报模板生成中文 Markdown → 对照source_ids核实完成程度 → finish。不能自行补事实、调用Git写操作/知识维护、读未授权会话或把生成内容再写入旧科研记录。新材料到达只在下一轮处理，finish不得推进未读材料游标。

周报源选择顺序：该日最新正式版 > 无正式版时的当前草稿 > 无日报时的同日原材料。正式版存在但有修订草稿时，仍读正式版。候选永不自动成为周报来源。每条引用带原version/hash与当时正文摘要；正式日报覆盖后若原材料新增，只提示可能有新材料，不能绕过已确认内容将其偷偷升级成已完成结论。

自动生成的周报在finish时作结构检查：正确日期H1、三个约定H2、没有模板说明标题或 `【...】` 占位符。检查只适用于生成稿，失败记 `INVALID_GENERATED_REPORT` 等待下一次尝试；人工保存/确认不被模板强制拦截。科学真实性由宿主核对与人工确认，不写关键词伪判断器。

### D-08 配置与内置计划任务连接

配置文件可编辑字段如下；null代表尚未填写，不默认为此前建议的时间：

```json
{
  "schema_version": 1,
  "enabled": false,
  "timezone": "Asia/Shanghai",
  "project_ids": [],
  "daily_time": null,
  "weekly_weekday": null,
  "weekly_time": null,
  "start_date": null,
  "activity_db_paths": {}
}
```

时间字符串HH:mm；weekday按ISO 1=周一至7=周日；周报owner必须是勾选项目之一；start_date在启用时默认本地当天，允许用户明确选更早日期。北京时间用标准库固定UTC+08:00计算，不要求Windows额外安装时区包。不支持任意多时区选型。

设置放既有设置页的“报告自动整理”折叠项。用户保存上述字段时只记录意图；只要 enabled=false 就不自动处理周期任务。启用指令必须带实际已保存的配置标识/项目名，不能把报告正文、原始聊天或token复制进任务prompt。

由执行者在用户明确要求启用时使用本线程可用的官方 `automation_update` 能力：先查找同名/同配置已存在的任务，优先更新，不重复创建；类型选择 heartbeat，目标为用户用于配置报告的当前本地线程，按配置时刻运行；本次日报/周报同为18:00，因此唯一任务每天18:00触发，周五同轮生成两类报告；保留已有通知偏好。没有工具时告知在支持自动化的桌面宿主执行，不能自己写 automation.toml、系统计划任务或 shell 循环。调度表达式交给官方工具调用构造，不在网页展示。

宿主自动化配置成功后，通过源码 `research_cycle.py bind` 核验真实官方回执（id、heartbeat、目标线程、配置路径、ACTIVE），再写入设置的只读 `runtime={automation_id,target_thread_id,bound_at,last_started_at,last_success_at,last_error}`；网页POST不能修改runtime。首次实际工具执行更新started/success；“已配置”与“最近成功”分开。设置启用意图/绑定与自动任务三者不齐时显示待连接，不触发后备CLI。

暂停：网页把enabled=false，立即禁止下次plan/finish新生成；宿主下次检查发现暂停只返回，不读取源。用户在宿主要求暂停时同时暂停内置任务。恢复用同一个id，不新建；项目列表变更由下一次plan读取；时刻变更必须用官方工具更新同一任务日程后重新bind，不创建重复任务。若宿主任务被外部删除/暂停，网页只能显示上次回执和最近检查时间，不能声称能实时探测宿主；是否超期应依据配置日程和最后检查时间判断；每日任务两小时未检查不是失败证据。

自动化 prompt 的正文固定含义（实现时只替换已验证配置路径，不填入私密内容）：
> 使用研究记录 Skill 和源码共享入口 begin/call/finish 维护唯一配置的日报、周报、知识与文献。begin无轮次则停止取材；否则按报告→知识→文献独立处理，前阶段无工作或失败仍检查后阶段。报告汇总已选项目、不归档项目，周五先日报再plan周报；每阶段预算见begin。完整读取分页来源，由宿主推理，结果保存草稿或候选；不编造无依据工作，不自动确认报告、替换旧知识或下载论文，不运行后台exec、不改任务/Git。最后写入真实工具回执；通知偏好只由官方任务配置管理。

只讨论/写spec不调用automation_update。实施时也不自动替用户启用；真实验收使用用户授权的临时项目/测试任务，测试后通过同一工具暂停/删除该临时任务，不影响其他自动化。

### D-09 到期、增量与重试算法

1. 注入可测试clock；以北京时间生成period，存储UTC时间；所有日区间左闭右开。周一日期由本地日期计算，周报标题结束日周日，不以生成日替换。
2. 手动requested优先，随后最近14个已结束自然日从早到晚、今日已过daily_time、上一周周报、本周已过weekly_weekday/time；均不得早于start_date；周报的到期日也须不早于start_date，首次周日启用不补上周五的计划（周范围可包含启用前日期，但详情标未覆盖）。同一周期按project_ids设置顺序汇总所有参与项目，不生成逐项目日报；每轮上限3个真正待生成报告，未变化/无材料不占模型名额。
3. 无报告但没有材料，写no_evidence与输入指纹，不写“今日无事”的正文；有新材料则重新变为可处理。无变化、已成功的fingerprint跳过。fingerprint含源id/revision、项目集合、使用的日报版本和模板hash，不含clock。
4. 有效run占用期间跳过同报告；10分钟后旧占用失效、旧finish拒绝；该次按失败计入attempt并设置retry_after=expires_at+30分钟，到期才可新占用。attempt在每次成功领取时加1，finish不再重复加。单输入attempt最多3次；失败的retry_after=失败时间+30分钟。达到3次后等新fingerprint或明确regenerate。单项目失败不阻断其他项目。
5. 晚间/迟到材料按occurrence date使窗口内旧日报变更；无法确定旧日则归本次观察并写gap。超过14天的日报不自动取材，依据start_date与缺失状态给出待手动补齐区间；周报只自动本周与上周。用户可对任一具体旧日期/周点生成，不增设全历史重建按钮。
6. 一轮先日报；提交已到期日报后再次report_plan，在同一共享轮次领取周报；只有当3篇总预算用尽或相关日报仍排队/运行/等待重试时才推迟下一轮。当日尚未到日报时刻而周报已到时，可读取当日已有稿/原材料生成周报，不等待当晚日报；未来日期仅标未覆盖。无材料/已耗尽重试视为真实缺口，周报可继续而不一直等。周报生成后本周新增日报/版本变化触发新指纹，正式周报只能有候选。
7. 暂停/取消选择项目后，旧报告保留；正在生成的finish重新核验当前启用/项目集合，失去权限拒绝结果且不继续读源。手动编辑不受自动设置开关限制。未启用计划时，已选项目的regenerate仅保留请求，并明确“待连接执行端”，不是挂起前台。报告含未被勾选授权项目时regenerate返回409 `PROJECT_NOT_ENABLED` 并提示先选择项目，不擅自扩大自动配置；手工编辑/确认照常可用。

### D-10 验收实现与性能预算

原行为 A-01 至 A-27 与新增界面 A-28 至 A-34 都必须有对应断言/人工证据。旧界面的通过记录不等于新界面对齐已验收；不用“已实现类似功能”代替。

- 单测：`tests/test_reports.py`（数据/来源/状态），`tests/test_report_schedule.py`（fake clock/去重/失败/权限）。新报告API纳入现有smoke；用临时home/source/wiki，绝不对真实数据跑写测试。
- 浏览器：新增 `tests/reports_browser_test.cjs` 并接入现有 `run_notebook_browser.py` 临时服务；复用已有Node/Playwright，不自动安装依赖。截图至少保存“原始###例子默认预览”“截图在段落中”“正式版+候选”三张作为验收证据。
- 延迟注入：模型端模拟60秒不返回，同时执行20次小稿加载/保存。普通≤50KiB正文、本机热启动条件下加载/保存p95≤500ms、粘贴后预览可见≤1秒；记录机器/样本，不以2MiB极限稿要求同预算。验证HTTP handler没有调用生成器/全项目扫描。
- 兼容：原 `test_notebook.py`、`test_progress*.py`、`smoke_test.py`、旧浏览器用例通过；未改动原论文/旧日档/任务数据。HTML脚本、危险路径、跨站POST沿用原防护测试。
- 最后一项必须是真实内置任务触发一次生成，用户本机观察无窗口，再用人为设定漏一天/fake clock单测证明补齐；不可声称关闭电脑也能执行。无用户授权/宿主能力时标未验证，保留该任务未勾选。

### D-11 整站已确认原型与唯一共享界面约定（2026-09-20）

**源稿而非截图猜测。** 权威源文件为 `C:/Users/lyn/.codex/visualizations/2026/09/18/01a0b5b0-347d-7f42-a151-7c50cc08fe64/research-workbench-complete.html`；本次核验 SHA-256 为 `2abb585862148b1defbd9df3883b2cf138cdd9d692fa9bb5ce629a94762bcaaf`。读取原 HTML/CSS/SVG 和事件逻辑提取布局、控件顺序、间距与交互，不用截图反推，不复制示例研究内容、内存成功提示、base64 演示图片或宿主 Tweak/openai 接口进生产。源稿缺失或 hash 不同先核对，不擅自换参考。原 Git 独立参考保留，只作为 Git 子区域补充，不再定义整站导航。

优先级：本节及各功能新 UI 条款决定位置/默认交互；既有业务规格决定数据、人工保护、正式快照、Git 安全和真实调度。原型中的“修改正式版”必须走 start_edit 生成新草稿，不能修改旧正式版；演示可点不等于后台已完成。原型未画出的历史/来源/候选/错误恢复保留在更多菜单或按需状态内，不添加常驻管理面板。

| 项目内一级栏目（固定顺序） | 正式页面与原有深链 |
| --- | --- |
| 科研进度 | `/project/<id>/todos`；从项目总览“进入项目”默认到此 |
| 科研记录 | `/project/<id>/records`；新建/详情继续用 `/notebook`、`/notebook/<note_id>` |
| 日报与周报 | D-04 的独立 `/reports`；保留原报告详情 URL |
| 知识库 | 保留 `/project/<id>` 与 `/page/<relative>`，目录和阅读在同一页面区域呈现 |
| 文献 | 保留 `/project/<id>/literature` 及条目/原文深链，只看当前项目 |
| 代码 | 保留 `/project/<id>/code`；名称不再是“代码图谱” |

左上项目切换、六栏目、底部项目总览/设置按源稿顺序；原 `/search` 仍可用，放设置/更多中的工具链接，不变成第七个项目栏目。项目总览复用注册表，不建立另一套项目实体；新建仍需要真实 source_root 与现有注册校验，不能照抄演示只输名称便造假知识页。项目切换保留所在一级栏目、清除旧项目详情；已保存表单正常离开，正在保存等待该次落盘，失败保留本机恢复稿并允许用户取消离开。旧项目请求晚到不得写入新页面。日报与周报始终使用工作台范围，项目筛选只改变列表可见项，不改变报告归属；单项目笔记/文献/知识/任务/Git不混用。

视觉直接提取源稿的 `--rw-*` tokens、布局、线性 SVG 路径与响应式规则到现有资源，图标本地内联、完整节点，不从 CDN 加载字体/图标。保持明暗色、轻边界、无装饰统计卡；动画遵守 reduced-motion。不是把 fragment 整页嵌入 iframe，也不是更换 React/Vue 或启动另一个前端服务。原网站继续由现有服务与原端口提供。

**已核对的复用/调整清单：**

| 现有落点 | 本轮实施应做什么；不能宣称什么 |
| --- | --- |
| `research_web_ui.layout`、`style.css/app.js` | 旧五栏目/顺序改为上述壳；只一位集成执行者修改共享布局 |
| `research_reports.tabs/list_page/editor_page`、`reports.js/css` | 数据/确认/候选保留；移入口、统一工具条、增加项目/状态筛选；旧记录三项 tabs 不保留 |
| `research_notebook.py`、`notebook.js/css` | 已有截图粘贴，但还是 blocks 和 file input；按 D-12 改连续正文，不把“有paste监听”算作完成 |
| `research_progress.py`、`progress.js/css` | 保留四状态、上下文保护和轻量接口；新布局/默认周视图由第一份新增 UI 任务接入 |
| 知识维护模块、`project_page/page_view` | 保留增量/建议；第三份接目录阅读布局与显式编辑，不把自动维护接口当网页编辑已完成 |
| `literature_catalog_web.py`、`literature_catalog.py` | 保留项目清单、真实 CRUD/原文绑定；第四份补已读和可编辑阅读笔记，不重做收录协议 |
| 现有 Git 模块与 `/code` | 只复用已有逻辑，按第五份接真实图和写操作；不得用原型的模拟仓库验收 |

**集成顺序与写集：** 先核对未提交工作和最新 verification；保留已完成业务与历史勾选。先执行本 change 新增 7.1→7.2/7.3→7.4（共享壳、笔记、独立报告），随后第一份 UI 增量、第三份 UI 增量、第四份 UI 增量、第五份 Git 依次接入共享文件；已有后台功能的剩余任务继续按各自清单，不因视觉改造重开已交付业务。不得让多个执行者同时整文件覆盖 `research_web_ui.py/web_server.py`。任务描述中的旧验收仅是历史基线，新 UI 项目单独验收；不得去改他人的 verification 来假装已通过。

**整站验收：** 临时注册两个项目与空项目；至少 1280、1024、736、580、375、320px，浅/深色、键盘 Tab/Esc 与减少动画。按源稿逐页核对六栏目、项目总览、列表/详情/空态/编辑/确认/错误态；图/代码允许自己的内部滚动，整页不横向溢出。笔记与报告保存后刷新、重启服务仍能打开；旧深链、附件、批注、时间信息与报告正式历史不丢。原型截图是视觉参考，真实临时服务截图与后端文件断言才是完成证据。真实调度及 Git 验收仍遵从各领域原标准，不随 UI 验收放宽。

### D-12 连续手动笔记与截图粘贴：小范围兼容，不批量迁移

**界面复用。** `/records` 只列手动笔记和原助手记录；新建直接进入一个连续正文，标题可后填，没有图片按钮、文件输入控件、块类型/添加/排序按钮。已有非空文档默认预览；新建空稿编辑且焦点在正文。Markdown 模式、撤销/重做、600ms 保存与恢复采用 R-03/R-05 和 D-04 同一行为；笔记显式保存、代码围栏快捷插入、批注、编辑/预览、更多中的文档信息/历史/导出按源稿。助手追加式日档仍按原只读/追加机制展示，不转换成手动笔记。

**输入行为固定。** 正文 Ctrl+V 图片在当前选择处插入；预览使用最后光标或文末，并保留可再次接收粘贴的焦点。连续粘贴两次是两次插入，底层相同图片可哈希去重。标题获得焦点时，含 text/plain 的剪贴板优先正常粘贴标题；纯图片则插到正文最后光标/文末，标题不变。搜索框不接管图片粘贴。拖拽保留现有能力，但不是上传截图的必经步骤；落在正文外不能让浏览器用图片替换当前网页。待上传位置、重试、乱序完成沿用 D-04，不锁其他输入。科研记录图片使用 `../assets/<hash>.<ext>`，报告仍用 `../../assets/...`，不得互相误用。

**最小数据适配。** 复用同一 note_id、`records/manual/<note_id>.md`、GET/POST notebook、upload、原子写、revision、本机草稿、`.notebook-history/<note_id>/<revision>.snapshot`；不新增笔记数据库。连续笔记在原 Markdown 的状态注释中保存 v2 文档 `{format:"markdown",title,tags,body,comments,created_at,updated_at}`，marker 改为 `llmwiki-notebook-v2`。正文不含 marker/状态时间戳；创建/更新时间服务端维护，只在“文档信息”显示。读端兼容 v1/v2，旧客户端向已有 v2 提交 blocks 时返回明确冲突，不把新正文误清空。正文与所有批注仍受现有 2 MiB 文档限制，超限报错保留输入，不静默截断。

v1 只在内存映射：按原序列化规则把 heading/code/image/callout/divider/markdown 转为连续 Markdown；代码围栏保持可包含反引号的原规则、图片相对路径与说明不变。批注从正文中分离为 `{id,quote,text,created_at,legacy_block_id}`：旧批注的 quote 为对应原块内容快照，id由 block_id+原批注序号稳定生成，原文逐字保留，未知时间为 null（不编造）。仅打开/预览/切模式不能写盘。用户第一次真正修改并保存时，先以原 revision 保留原始字节快照，再保存 v2；笔记 id/链接/创建时间不变。恢复 v1 快照后依然可读取。无法无损解析的旧文件保持原只读阅读和另存为新笔记出口，不自动猜测修复。

新批注使用 `{id,quote,text,created_at}`，quote 是用户选中文字快照（未选时为空，即整篇批注），不是需要不断跟随编辑的字符偏移；正文变化不悄悄重定位旧批注。点击“批注”再输入提交，只有明确提交才新增；显示在正文后的批注区，系统时间只在信息中可查。批注不计入 Markdown 正文、不用于强行解释研究事实；导出在正文末追加独立“批注”段，保留引用文字，不输出内部 marker。报告复用同一批注结构，放原报告JSON的 draft/previous_draft 与每个正式版本元数据中（缺字段按空数组兼容），不新建数据库；confirm/start_edit/restore 同时复制正文与该版批注。自动生成不写人工批注；采用候选保留现有引用快照批注，并在 previous_draft 中保留替换前状态。正式版批注只读，必须先“修改正式版”建立草稿才新增/修改；候选状态变化不改文档 revision。

**不改变真实材料边界。** 报告和知识的笔记取材读取 v1/v2 的实际正文与明确标记的批注，排除状态注释，不把兼容序列化当新的科研成果。本次使用临时旧笔记夹具验证哈希、图片、中文、围栏、每条批注及恢复；不批量改用户真实历史记录。

## Risks / Trade-offs

- [没有全聊天采集] → 本期只消费现有记录和显式授权的可选活动库；来源详情列缺口。完整多宿主采集是独立工作，不顺手扩大本change。
- [自然语言总结不可完全确定] → 数据/格式/覆盖可程序验收，完成程度由宿主核对+用户确认；不声称两个模型写出逐字相同周报。
- [原生源码编辑没有Obsidian实时富文本] → 这是用户最新接受的简化；以粘贴即预览和切换完成，不回头安装复杂编辑器。
- [本机休眠/任务能力不可用] → 明确待连接和最后检查；恢复后本工具补齐，不依赖宿主自动补漏；不提供exec兜底。
- [原型需要批注但已有报告仅正文] → 只扩展原JSON版本元数据；批注纳入人工修改和正式快照，自动状态不引发伪冲突。
- [来源/图片跨项目] → 个人周报正文引用其他项目材料仅用链接和摘要；不复制论文/图片，不改变归属。日报、周报自身粘贴图片存工作台 records/assets，不写入参与项目。
- [旧records递归导致反馈循环] → 新报告目录在旧列表、采集、Git报告差异识别中全部排除，只允许周报显式读取日报。

## Migration Plan

1. 只新增空目录/惰性创建报告文件，不迁移现有记录；新设置默认disabled/null。
2. 在开发仓库和临时项目完成代码测试，再按用户另行要求同步插件安装版本；不直接改缓存或自动提交发布。
3. 用户填写配置、明确要求绑定计划后才创建真实自动化。返回网站确认“已配置”以及第一次真实成功时间。
4. 回退功能时先暂停相应宿主任务、禁用报告设置，回退代码但保留所有报告Markdown/来源元数据；不删除正式历史、不回滚其他agent修改。

没有留给实施agent自由选择的产品/技术问题。用户运行时填写的项目、时刻、起始日期和采集授权是配置输入，不是待agent猜测的默认值；宿主工具可用性是必须报告的验收条件。

## D-13 · 2026-09-20 用户确认的显示调整与独立演示

- 网页品牌统一“野人工作台”，只改品牌、页签名，不改插件标识、用户项目名称、历史科研正文。
- 基于已批准原型的尺寸，应用内统一 125%；采用根元素 CSS zoom，媒体断点乘 1.25，导航的 JS 断点同步。不是要求用户缩放浏览器，不使用 transform 形成虚假点击坐标；打印恢复 100%。
- 原型侧栏 166px 显示为 207.5px；补“当前项目”小标题、项目名省略与完整悬浮提示；六栏目保留本地 SVG；代码页补项目面包屑；内容标题距顶部按原型收紧。深色导航悬停沿用原型颜色变量。
- 进度默认本周七天，周一开始，日期带星期；继续保留两周/四周范围切换及原有任务/上下文持久化规则。今天使用轻底色、进行中任务使用原型紫色，继续上次采用次级标签与任务标题层级。连续文档正文宽度 780px、标题 22px/500（均为放大前尺寸）。
- 应用缩放时，弹窗和菜单的 vw/vh/dvh 尺寸除以工作台倍率，避免放大后超过视口；画布点击按实际边框与内部坐标比例映射。Git 分支选择器只保留一个下拉箭头。
- 演示是外部目录下代码快照，独立 source/wiki/state，独立 Git 初始提交和演示分支，无远程、不共享 Git 对象/硬链接。可以有显式标识的演示笔记、知识和任务；不能加入真实报告或采集计划，不能伪造用户科研完成情况。
- 原始 reference/approved-workbench.html 保持原样，不用本次品牌/尺寸改动冒充原始原型。此增量不把尚未验收的六页内布局、真实自动任务标成已完成。

## D-14 · 2026-09-20 报告导航不得隐式切项目

- 项目侧栏的报告入口携带 `context=<project_id>`；它只决定侧栏显示及其他项目栏目的链接，不决定报告归属、参与项目或自动整理授权。保留已有 `project` 参与项目筛选参数，两者互不覆盖。
- 报告列表的日报/周报切换、搜索/筛选、分页、详情、新建及返回携带 context。切类型/筛选重新从第一页开始，返回恢复筛选、页码和行锚点。列表左上主动切项目保留报告类型与筛选，只改变 context；详情左上主动切项目返回相同类型的列表，不携带旧文档 ID。
- 不用注册表写入、全局 localStorage 或 cookie 传递当前浏览项目；两个标签页各自保持自己的 URL 上下文。GET 不修改报告或用户默认项目。
- 无 context 的工作台直接入口可用注册表默认项目初始化；无 context 的旧项目报告深链以原项目初始化。显式 context 优先，旧报告仍读写原 owner/API。
- 无效/删除的 context 归一为空但不重新选默认项目；显示“选择项目”，隐藏有归属的栏目链接，项目菜单仍可重选。空 context 经类型切换/筛选/刷新继续为空，不因 query parser 丢弃空值而恢复旧默认。
- 不改报告正文、存储路径、来源授权或计划；不扩大到全站搜索/设置的导航改造。开发源码修复不代表常驻进程已加载。


## D-15 混合会话取材与工作台开发补记（2026-09-20）

- 删除调度入口与共享来源层按 `runtime.target_thread_id` 排除整线程的规则。解析器由真实用户的 heartbeat 根消息进入自动轮次，下一条普通用户消息恢复；注入上下文、压缩交接和异步代理通知不是新用户。自动状态随字节游标持久化，旧游标从同一授权文件的前缀重建，不重新扫描账户。
- 非项目 cwd 不能按正文猜项目。由用户明确授权后，`research_capture_runtime.bind_project_session(project, session_id, since=...)` 将唯一会话与起点保存到项目 state 的 `workbench/capture-sessions.json`；索引只增加该 id 的精确查询，仍核验路径/session_meta并拒绝独立自动会话。未指定历史起点默认从关联时刻开始。
- 共享来源提供器的时间界限按 host/session 取已授权起点；SQL 仍先筛项目、宿主、状态、日期与界限，再读正文。不扩大未绑定聊天的时间范围；每次读取核验授权与关联快照，报告冻结授权摘要包含关联配置。普通研发文字提及 `records/reports/` 不代表报告自循环；实际报告来源路径/自动标记及工具材料仍排除。
- 工作台自身也是已选科研项目，不按主题过滤。当天修复人工补记使用报告 `load/update(save)` 的乐观锁，保存原跨项目草稿，保留其他章节、批注、生成来源元数据与正式版本；正文明确新补记的依据，原来源面板不伪装成重新生成。该改动不更改日程、启动后台、改任务状态或更新安装缓存。

## Context

动机和范围见 [proposal](proposal.md)，唯一行为标准见 [spec](specs/research-visual-git/spec.md)。原 Git 桌面示意保留为 [截图](reference/approved-desktop.png) 和 [交互源稿](reference/approved-mockup.html)；最新确认的整站原型及哈希只在 [第二份 design D-11](../research-daily-weekly-reports/design.md) 定义，整站共享壳/导航以它为准。源稿只是设计参考，包含固定数据和模拟成功，不得直接作为生产代码或真实 Git 验收。

本次只读核对到的基础：
- 现有 `/project/<id>/code`、`/api/project/<id>/code/graph` 和导航“代码图谱”可复用；当前页不能视为完整可视化管理已交付。
- 已有 `git_service.py`、`git_graph.py`、`git_operations.py`、`git_merge.py`、`git_revert_restore.py`、`git_recovery.py` 及相应测试。优先修补并连接，不重写一套 Git 库。
- `commit_changes` 目前直接提交整个索引，没有“仅选中文件”契约；`compute_state_token(..., strength="full")` 目前只含路径/状态且截断到1000项，不能检测同为 modified 的再次修改。
- 当前图在每页独立分配泳道，snapshot 只考虑部分状态，不能直接保证跨页连线和分支移动时的稳定性；远端标签也需补齐。
- 网页已有 Host/Origin/JSON/`X-Notebook-Request` 同源写入保护、ThreadingHTTPServer、共享导航与本地静态资源。新接口复用，不另开端口或服务。
- 第一份仍有在途改动；本 change 不覆盖其文件内容，也不改第二至第四份功能规格。

## Goals / Non-Goals

**Goals:** 以薄适配层接入已确认网页；固定操作语义、请求契约、必须校验的 Git 后置条件及真实测试；无需执行者重新决定保存/恢复/冲突的产品含义。

**Non-Goals:** 不实现 proposal 排除的高级功能；不重建通用操作编排、事件总线、历史数据库、恢复中心或新前端框架；不自行实现其他四份的页面业务；整站入口已在第二份明确批准，不再把它们当虚构侧栏，也不由本 change 重做。仅网页暴露本次明确列出的动作，已有高级 Python 函数不自动变成 API。

## Decisions

### D-01 修改落点与集成顺序

以下路径均相对于本插件开发仓库：

| 文件 | 本 change 的限定工作 |
| --- | --- |
| `plugins/llmwiki-lite/scripts/git_web.py`（新增） | 项目绑定、白名单请求适配、预览、状态复核、错误转换；调用现有 Git 功能，不承担科研推理 |
| `plugins/llmwiki-lite/scripts/git_service.py` | 仓库/差异读取、可靠执行环境、内容级复核所需辅助；保留现有兼容调用 |
| `plugins/llmwiki-lite/scripts/git_graph.py` | 真实父图、引用快照及跨页一致性 |
| `plugins/llmwiki-lite/scripts/git_operations.py` | 完整文件选择提交、分支/远端/本地作者配置的有限适配 |
| `plugins/llmwiki-lite/scripts/git_merge.py` | 普通合并、有限文本冲突、完成/取消及网页持有状态 |
| `plugins/llmwiki-lite/scripts/git_revert_restore.py` | 仅树恢复加新提交路径；不把 reset/revert 一并开放 |
| `plugins/llmwiki-lite/scripts/research_web_ui.py` | 代码页布局和导航名称/图标，复用既有 `layout` 与 `ui_icon` |
| `plugins/llmwiki-lite/scripts/web_server.py` | 代码页、白名单静态资源和下表 HTTP 路由；移出旧页内混杂脚本 |
| `plugins/llmwiki-lite/scripts/static/code.css`、`code.js`（新增） | 仅代码页样式、SVG 图和原生表单交互 |
| `plugins/llmwiki-lite/tests/test_git_web.py`、`code_browser_test.cjs`、`run_code_browser.py`（新增） | 临时仓库 API/浏览器测试及独立启动器 |

现有 `git_recovery.py` 仅复用仓库锁及确有必要的既有状态读取，不以整修恢复框架为前置任务。需要修复的已有 Git 辅助函数用原有单测证明不退化；不能靠删除旧测试使新测试通过。共享网页文件等第二份统一壳接入后串行集成，不覆盖其他执行者的整段文件。

实施时同步更新插件 AGENTS/README/网页 Skill：新增的是**用户主动操作代码仓库与目标远端**的窄例外，不是放开自动改源项目或独立外发。其他宿主、Hook、MCP 和科研自动整理边界不变；无需给 MCP 增加 Git 写入工具，也不改市场版本/发布缓存。

选择局部适配而非整体重构，避免已确认四份功能被重新实现。

### D-02 页面规格与视觉定稿

桌面默认与整站源稿的代码区域一致：共享侧栏、主题与完整线性分支图标使用第二份壳；主区24px左右内边距；工具条单行优先；工作区摘要一行加一行弱提示；版本行68px；详情宽286px，可关闭。字体、浅/深色、图标优先使用现有网站 tokens/图标，不从 CDN 新载字体、图标或框架。禁止复制源稿的宿主 Tweak/openai 接口。

| 部位 | 固定内容/默认行为 |
| --- | --- |
| 顶部 | 项目名 / 代码；不放“交互示意”字样 |
| 工具条 | 当前分支下拉、合并；右侧拉取、上传及待上传数。无 upstream 时不编造数字，按钮仍可进入目标选择 |
| 修改条 | 有变化：N 个文件有改动 / 尚未保存为版本 + 查看并保存；无变化：所有修改已保存 / 工作区干净 |
| 图 | 全部分支；正文版本标题；次行分支标签、短哈希；右侧弱时间。当前分支深中性色，其他泳道克制区分，标签保证不只靠颜色 |
| 默认选择 | 当前 HEAD；历史节点点击后替换详情，不改变分支/文件；刷新仍保留可达的选中节点 |
| 详情 | 版本说明、完整元数据可展开查询；文件列表；单个文件差异；从此创建分支、恢复到此版本；一个关闭按钮 |
| 保存弹窗 | 文件复选框、点文件看差异、说明 textarea、取消、保存到本地；“本次仅保存在本地”弱提示 |
| 冲突处理 | 代替代码页中部图/详情的专用区域；顶部说明源→目标；左侧文件；右侧当前内容/合入内容/可编辑最终内容；已解决计数、完成合并、取消本次合并 |
| 成功与失败 | 页脚或操作附近的一条信息，不浏览器 alert，不持续叠加 toast。错误时保留表单，不整页重载 |

宽度 <=580px 时详情和冲突的内容列纵向排列；项目导航沿用网站现有收起行为。长标题单行省略、详情完整显示；路径允许换行；图泳道过多时仅图区域横向滚动，不带动整页。桌面初始只显示已批准的区域，不加分支管理侧栏、Git 技巧教学卡片等。

按钮和行使用原生 button，模态框支持 Esc、焦点回到触发按钮和键盘约束；差异用纯文本节点。新增/选择变化动画150ms，仅位移或颜色过渡，遵从 reduced-motion，不循环闪动。首版不交付主题/密度/布局自定义；示意中的设计选择器不是产品要求。

### D-03 仓库识别、状态与有限存储

1. 每次请求用注册 project id 解析真实 `source_root`，canonical root 必须等于 `git rev-parse --show-toplevel`；不接受客户端传 cwd、git 可执行路径或绝对文件路径。普通单工作树为写入支持范围，检测结果在 status 中说明；空仓库另行处理 HEAD，不当损坏处理。
2. Git 可执行文件沿用现有探测及最低2.43能力基线；不要求安装 Python Git 包。SHA-1/SHA-256 的 OID 长度取仓库对象格式，不硬编码40位。
3. 必要机器状态在 `<registered state_root>/git-web/`；预览仅保存在服务器内存，5分钟失效、重启失效；不把差异正文存成新知识文档。冲突期间持久化已有合并状态的适配结果及本网站 operation id，用于刷新/重启后识别是否属于本网站。
4. 仓库锁依据真实 common-dir 标识复用既有 `RepositoryLock`，锁文件放在该 common-dir 的 `llmwiki-web/locks/`，保证同一仓库不同网页请求共用锁，不靠 project id 分别上锁。锁超时1秒后返回 busy；不等锁无限排队。Git 自有锁继续保留。
5. 用户若把 `state_root` 自定义进源码仓库且未被 Git 忽略，网页写入禁用并提示去现有设置移到仓库外或自行忽略；不能自动改 `.gitignore`，也不能让自己的临时状态变成未提交文件阻塞后续流程。默认注册的外部 state_root 不受影响。
6. 没有存全仓库日志的新数据库，没有通用历史回填。读取状态/图不创建恢复副本；只有明确写入才允许必要机器状态落盘。读取命令设置 `GIT_OPTIONAL_LOCKS=0`，避免只看状态时刷新真实索引；读取/预览不执行 add、fetch 或其他写操作。

这比新建“每项目仓库注册表＋操作数据库”更小；Git 本身和已有项目注册是唯一权威。

### D-04 HTTP 契约

路由前缀 `P=/api/project/<project_id>/code`。继续使用 JSON `{ok:true,...}`；失败统一 `{ok:false,code,message,...}`，message 中文且脱敏。所有 POST 复用现有同源 Host/Origin、JSON 和 `X-Notebook-Request: 1` 校验，不能绕过为 GET 写入。

| 方法/路径 | 请求 | 成功内容 |
| --- | --- | --- |
| GET `P/status` | 无 | capabilities、head、branches、remotes、upstream、files、ongoing、last_fetch_at |
| GET `P/graph` | `page` 从0起，`page_size` 固定100；后页带 `snapshot_id` | 延续已有图字段，补齐远端 refs、边界父节点和一致 snapshot_id |
| GET `P/commit/<oid>` | 只接受合法完整OID，且存在于本项目可浏览提交集合 | 提交元数据、父列表、默认diff基准和文件列表 |
| GET `P/diff` | `kind=commit|worktree|restore`、`file_id`；commit另带oid；worktree/restore另带preview_id | old/new路径、变化类型、增删数、文本行或unsupported_reason、truncated |
| POST `P/preview` | `{action,params}`；动作白名单见下表 | `{preview_id,expires_at,summary,files,can_execute,reason}`；除明确fetch接口外无网络、无仓库写入 |
| POST `P/execute` | `{preview_id}`；save另带`file_ids,message`；create_branch另带`name` | `{action,outcome,head_before,head_after,commit_oid?,operation_id?,message}`；outcome为done/no_change/conflicts/partial |
| POST `P/fetch` | `{remote_id,branch}`，对应用户“检查远端更新” | 缓存更新时刻、目标完整oid、ahead/behind、关系equal/ahead/behind/diverged；不更新工作区 |
| GET `P/merge` | 无 | 网站持有的当前合并状态和冲突列表；没有则null；外部操作只返回说明 |
| POST `P/merge/resolve` | `{operation_id,file_id,expected_revision,resolution,content?}` | 更新后的冲突状态；resolution仅current/incoming/manual |
| POST `P/merge/complete` | `{operation_id,expected_revision}` | 新提交和真实合并结果 |
| POST `P/merge/abort` | `{operation_id,expected_revision}`，取消前网页确认 | 原HEAD、取消结果和剩余文件状态 |
| POST `P/identity` | `{name,email,scope:"local"}`，显式确认 | 当前仓库本地身份；不自动重放失败的提交 |

预览动作与参数：
- `save`：无参数，获取当时所有可选变更及内容版本；用户随后只能从该集合选 `file_ids`，提交说明后填；不允许客户端改路径或增加集合外文件。
- `create_branch`：`target_oid`；执行时补合法分支名称。允许当前工作区有改动，因为不切换文件。
- `switch_branch`：`branch`，只能选当前仓库已存在本地分支。
- `merge`：`source_branch`，目标固定当前本地分支；预览绑定两边OID。
- `restore`：`target_oid`，目标固定当前本地分支；返回完整影响清单和可逐项读取的差异。
- `pull_apply`：`remote_id,branch,fetched_oid`；必须是刚检查过且仍一致的缓存目标；关系决定快进或普通合并。
- `push`：`remote_id,target_branch`；源固定当前本地分支完整OID；响应含现有缓存是否最新、目标是否将新建、待上传列表。

字段约定：`file_id` 为服务器生成的不透明项目内标识，绑定 old_path/new_path/status；`remote_id` 映射服务器读到的已有远端，不接受URL；`head` 含oid/branch/unborn/detached；`capabilities` 含can_read/can_write及reasons；`files` 含路径、类型、index_status/worktree_status、partial_staged。status/commit/preview 的 files 返回完整轻量元数据清单，不携带差异正文；前端按每批100项追加显示，默认全选按完整集合计算而非只选已显示的100项。`kind=worktree` 只能使用 save 预览的 file_id，`kind=restore` 只能使用 restore 预览的 file_id；后者比较预览固定的当前HEAD树与目标树，不读取当前工作区差异冒充恢复影响。每次差异读取先核对所属预览及内容版本，失效返回409。外部名字、说明、文件内容均转义。未知action/字段类型/引用一律400，不能透传任意git参数。

预览保留在服务器，执行时使用服务端记录的固定参数；save的选中文件和说明、create的名称是仅有的补充字段。预览用后失效；一次执行失败也消费预览，重试必须重新查看。状态改变返回409 `state_changed`、过期409 `preview_expired`、锁占用409 `repo_busy`；保留输入后重取预览，禁止后台自动确认。其他错误：400 `invalid_request`，403 `forbidden_origin`，409 `dirty_worktree|operation_in_progress|unsupported_repo`，422 `identity_required|auth_required|unsupported_conflict|no_remote`，504 `git_timeout`，500 `git_failed`。网络回执不明用 `outcome:partial` / `result_uncertain` 描述，不虚构确定失败。

接口返回的错误不得直接转发完整 stderr/远端URL；技术细节按需展开，剔除凭据。普通JSON请求上限2MiB，冲突文本上限1MiB，复用现有响应和异常机制，不新增万能RPC/异步任务服务。

### D-05 Git读取、图和差异

- 读取沿用现有命令封装；使用 NUL 分隔的 porcelain/name-status/numstat 解析文件路径，覆盖中文、空格、换行、前导短横线及重命名；不能按空格简单split路径。
- 图仅汇总本地和远端跟踪分支可达历史及当前HEAD；不把插件恢复用的私有refs当用户实验分支。拓扑序、真实parent OID是连线依据；相同快照按 Git 输出顺序稳定排泳道，追加历史时复用未结束泳道，不逐页重新从0分配。超出首屏的parent显示“延续到更早版本”，不能画成根节点。
- snapshot_id覆盖仓库、HEAD和全部可见分支引用的名字/OID；分页前后重新核对。不做独立Git日志存储或后台全量图缓存。
- 短hash按Git唯一缩写规则，至少7位；完整hash在详情提供复制。时间列表为本地时间短格式，详情展示带时区的完整提交时间，不把读取时间伪装成提交时间。
- diff使用真实对象/索引/工作区，不调用外部diff或textconv。保存弹窗的工作区diff以HEAD为基准（空仓库以空树为基准），表示选中完整文件会提交的内容；同时标出外部暂存事实。合并提交详情默认第一父基准明确标注，不把父列表丢失。
- 单文件差异上限1MiB或5000行，达到任一上限返回truncated；文件元数据完整返回、前端每批100项追加显示；图的100条则是服务端分页，不混用这两种机制。二进制和非UTF-8不做乱码文本编辑。diff读取不得改变文件或索引。

选择按需读取而不是一次性返回全仓库差异，保证常规首屏快、长列表可用。

### D-06 保存、分支、合并和恢复的固定语义

**共同前置与复核**：写操作持仓库短锁执行；预览记录HEAD/当前分支、相关refs、真实索引摘要和相关文件内容SHA-256（含路径存在性、模式；符号链接只检查链接本身，不读取外部目标）。执行再次读取比较。保存只复核最终选中文件及索引/HEAD；其他改变不偷偷纳入提交。切换/合并/恢复重新确认所有非忽略变更为零；此外检查目标会覆盖的未跟踪/忽略路径。不能用现有仅状态字符串的token当完整校验，不截断待校验集合来声称安全。

预览读取前后检测HEAD/索引/相关文件是否稳定，读取过程中变化则重新获取而非签发混合预览。网站锁只约束本网站，不声称可以阻止所有外部编辑器同时写文件；Git自身锁和最后复核共同限制风险，执行后以真实Git结果为准。

| 操作 | 固定执行语义/后置条件 |
| --- | --- |
| 保存 | 仅对选中文件（重命名包含两路径）执行完整文件暂存，然后使用限定路径的提交语义，只提交这些文件；未选文件的工作区与原索引条目不变。禁止`git add .`后普通提交整个索引 |
| 创建分支 | 对验证过的完整target_oid创建local ref；名称用Git自身check-ref-format；不切换，不推送 |
| 切换 | clean且无在途操作后普通switch；不force、不stash；成功复核HEAD.branch和文件状态 |
| 合并 | 普通Git合并策略：已包含→no_change，可快进→ff-only，分叉→非交互普通merge且带明确说明。不得继承配置而变成rebase或无故制造no-ff提交 |
| 恢复 | `restore --source=<target_oid> --staged --worktree -- .`在根目录恢复目标树，校验write-tree等于目标tree，创建说明为“恢复到 <short_oid>：<subject>”的新提交，再验证唯一parent=旧HEAD且tree=目标tree |

选择提交的推荐落点是在现有 operations 新增明确的 `commit_selected_files`：选定路径以literal pathspec/NUL文件列表传递，先`add -A`这些路径，再`commit --only`同一集合并使用输入说明；选中文件部分暂存时用户已获完整文件提示。必须用测试验证新增/删除/重命名及未选已暂存项，不以命令执行成功代替树比较。失败后不执行整库reset，允许已选文件留在暂存区并明确反馈。

身份缺失先阻止即将提交的操作，网页保存 `user.name/user.email --local` 后让用户重新确认，避免恢复代码后才首次发现身份缺失。不修改global配置。

restore只调用树恢复能力，不调用reset_branch、revert单提交或checkout游离HEAD；不删除忽略文件。树本来一致返回no_change。restore成功但commit失败保留修改并返回partial、当前HEAD和变更列表；后续“保存到本地”完成即可，不再设计恢复向导。

### D-07 冲突状态、解决与取消

沿用现有合并状态，网页持有 `operation_id`、project/repo、source/target OID、开始前HEAD、状态revision和文件revision。只接受由本网站记录且Git MERGE_HEAD仍匹配的操作；metadata与Git不一致时显示“请在原工具处理后刷新”，不自动推断完成。

- 支持集固定为普通文件双方修改（UU）、UTF-8、无NUL且三边每文件<=1MiB。用索引stage 2/3作为“当前/合入”，文件名不推断左右方向。最终内容初始为当前Git冲突工作区内容，不静默选择赢家。
- “采用当前/采用合入”仅把**该侧完整文件**填入最终编辑框，按钮旁标明是整份文件；直到“标记此文件已解决”才保存最终内容并stage。编辑框自动保存不等于已解决；首版不引入自动保存，刷新前有未提交编辑遵从原站离页提示。
- manual内容需UTF-8、<=1MiB、不得含Git冲突标记；按该文件有效 `conflict-marker-size`（未配置取7）识别行首连续相应数量的 `<`、`=`、`>` 或 `|` 且后接行尾/空白的标记行，包含diff3的基线标记；保留换行风格，避免额外整文件格式化。resolve使用文件revision检测外部改动，409时保留浏览器输入。
- complete再次读取unmerged项、所有已解决文件及索引、MERGE_HEAD和HEAD，确认无未解决/残留标记才提交。除冲突处理中合法变化外的外部索引/文件写入不能被自动一起提交；显示state_changed并停止，不能根据“无unmerged”就直接提交整个外部新增索引。
- abort确认说明会放弃本次冲突编辑；只对网站持有操作调用`merge --abort`，验证回到原HEAD与受跟踪树。执行前识别本网站操作记录之外的新工作区/索引修改；有外部改动时停止提示，不用强制reset回退。额外出现的未跟踪/忽略文件始终保留。
- 不支持冲突保留文件清单、类型与取消入口；不造“选择一边即可解决”按钮，不用网页自动处理delete/rename/binary。外部合并不显示网站的abort按钮。

选择有限文本编辑而非引入复杂三方编辑器；这补齐主要科研分支合并流程，不引入IDE。

### D-08 远端与非交互执行

远端数据取现有Git配置，不另建平台账号层。已有upstream默认带入弹窗并可明确修改本次目标；无upstream先让用户选择已有远端和目标分支。只列已有缓存分支，允许输入经验证的目标名称；不能为填充下拉框偷偷联网。

1. `fetch`是用户点击“检查远端更新”触发的唯一网络读取。仅更新选定远端的tracking refs；显式refspec限制目标到`refs/remotes/<remote>/...`，不能使用任意配置把fetch映射到本地heads。不开tags/prune/submodule递归，不更新工作区。缺少目标分支则明确不存在，不当已同步。
2. `pull_apply`不直接调用可能受用户pull.rebase影响的`git pull`。对刚fetch确认的固定OID按D-06合并逻辑执行；目标远端缓存变化使预览失效。取消检查后的弹窗只保留更新后的远端缓存，代码不变。
3. `push`只使用服务器拼接的 `refs/heads/<current>:refs/heads/<chosen>`，force固定false且API没有force字段；不使用--all/--mirror/--tags，不提供“强推解决”错误建议。空/未知upstream的首次成功push才设置本地跟踪关系；失败不抢先写配置，已有upstream不因单次改目标而偷偷改写。
4. 顶部待上传数以缓存upstream计算，说明中标注上次检查时间；没有缓存时不显示伪精确数。上传可不预先fetch，确认框说明“基于本地远端缓存”；普通push由远端最终校验，拒绝后引导先检查。
5. 非交互环境沿用并补齐 `GIT_TERMINAL_PROMPT=0`、`GCM_INTERACTIVE=never`、关闭pager/editor自动打开、Windows `CREATE_NO_WINDOW`、shell=False/参数数组。SSH仅允许非交互已信任主机和已配置认证；缺认证/主机信任时提示在原工具配置，不后台打开登录页。
6. 不静默禁用真实仓库hook、签名或自定义clean/smudge/SSH策略来制造成功。检测到相关操作会调用无法保证不弹窗的自定义执行器/签名时，该操作明确不可用并说明在现有Git工具中处理；普通无此类扩展仓库完整可用。读取diff固定禁止ext-diff/textconv。不能声称一个CREATE_NO_WINDOW标志就能约束任意用户脚本派生窗口。
7. 本地Git调用默认30秒超时，fetch/push90秒；UI持续显示本次操作状态但不阻塞其他项目。使用现有HTTP线程，不新增常驻worker、定时轮询exec或调度器。网络超时不能确认push结果时提示用户再点击检查，不自动重推。

这是显式Git网络交互，不是科研内容自动外发；首版不包含账号登录、PAT保存或远端配置编辑。

### D-09 刷新、低延迟与跨功能边界

进入页面、操作完成、浏览器重新获得焦点时刷新本地状态；焦点刷新1秒防抖、已有请求时复用。表单打开时不得覆盖说明、勾选和冲突草稿；检测状态变化只显示提示，实际执行仍以预览复核为准。离开项目后忽略旧请求响应，不通过AbortController声称已经取消服务器端Git写入。

图首批100条，差异点击再取；只对确认所涉文件做内容校验，不每次页面刷新哈希整个项目。任何Git操作都不等待日报、文献或知识维护，不调用模型。沿用已有增量/提交读取机制即可，本change不新增“Git提交立即总结”的hook；未接通留痕采集则如实保留缺口，交由下一份接入规格。

性能基准固定为本地SSD、普通仓库1000提交/1000受跟踪小文件/最多10个变更文件、无第三方hooks和网络读取。桌面环境5次测量记录首屏状态及首100节点可交互时长，目标中位数<=2秒；按钮忙碌态<=100ms。大型网络仓库不承诺2秒，需展示进行中/失败，不把超时当成功。

### D-10 验证矩阵与交付证据

| 测试层 | 必须证明 |
| --- | --- |
| Git/API真实临时仓库 | 选中文件提交含首提交、增删重命名、中文/空格路径、未选已暂存项保留；同modified再次修改使确认失效；多标签写入不重复提交 |
| 分支/合并 | 创建不切换；dirty时阻止切换/合并/恢复；快进/no_change/双父合并；UU编辑完成、刷新恢复、取消；不支持冲突和外部操作不接管 |
| 恢复 | R.parent=H且R.tree=T.tree；旧H可达；忽略文件覆盖被拒；提交失败显示partial而不是成功 |
| 远端 | 临时bare远端和第二个clone制作落后/分叉/拒绝；检查不改变本地代码；明确确认后更新；普通单分支push，未提交文件不入远端 |
| 浏览器 | 点击实际按钮完成A-35；节点/文件切换有真实内容变化；冲突编辑/取消；缺身份页内填写；320/580/1024px、浅/深色、键盘及无全页溢出 |
| 隔离/低打扰 | 非同源写入403、跨项目/路径越界拒绝、凭据脱敏；真实Windows操作观察无窗口；运行参数断言无shell/交互，再配实际桌面证据，不能只靠mock |
| 回归 | 现有Git相关单测、项目/笔记/进度/文献/知识浏览、smoke通过；并发第一份的验收不由本change代替 |

测试fixture创建提交/分支允许由测试脚本准备；核心A-35操作只能通过网页交互产生，测试再只读断言仓库。网络/权限失败可注入确定性故障，但正常主流程不能mock git。交付在tasks相关项注明测试命令、通过/失败和证据路径；不用另建一套功能规格。

### D-11 整站集成边界

第二份 design D-11 是共享导航/视觉的唯一约定，本份 D-02 至 D-10 仍是 Git 业务与真实验收依据。实现时从整站原 HTML 读取代码区域布局、SVG与事件逻辑，复用原 Git 参考细节；不要按截图猜测，也不要复制“点击即成功”的模拟分支数组。代码栏目图标取完整源 SVG，节点圆环完整；图中 HEAD/普通提交的形状语义由真实图例说明，不用缺失圆环表达状态。

只在已集成的共享壳内接 `/code` 内容、所需静态白名单与 HTTP 路由。第二份共享壳未集成时可先做原临时仓库测试，不能以此另起第二套导航。真实功能按原 tasks 执行，新增第9节只验收整站整合；不新建调度器，不因 Git提交触发知识/报告写入。

## Risks / Trade-offs

- [已有Git代码范围大且部分契约不足] → 只接本规格路径，修复选中文件提交/状态token/跨页图等实际缺口；文件存在或旧用例通过不能直接勾选完成。
- [网页写代码与其他agent同时编辑] → 预览绑定版本、执行前内容复核、Git与网页锁；检测冲突就停，不建设跨编辑器事务系统。
- [第三方认证/hooks/过滤器可能弹窗] → 明确非交互支持边界，不偷偷绕过策略；无弹窗是实际Windows验收项，不用假反馈掩饰不支持状态。
- [大差异及非常规冲突] → 分页/明确截断，有限UTF-8冲突范围；提供退出/本地处理，不假装完整IDE。
- [源码内也可能包含被Git跟踪的笔记/资料] → 恢复按整个仓库受跟踪树定义，影响列表必须包含这些文件；用户确认前不能隐去它们。机器状态保持在源码外或被忽略。
- [第一份仍在修改共享页面] → 先由原执行者交付，再串行接入；不reset、不整文件覆盖、不发布缓存。

## Migration Plan

1. 在开发仓库新增适配与测试，沿用原`/code`及图API的page/page_size语义；没有真实项目注册迁移、Git历史迁移或自动远端修改。
2. 本地临时项目验证全部场景后替换代码页，并保留既有其他入口。首次打开仅只读，不能产生提交或访问远端。
3. 失败回退仅撤回本change的页面/API代码；已有真实Git提交和分支不由回退删除，不能自动reset用户仓库。没有一键恢复平台上线任务。
4. 更新本change tasks与交接入口的验证状态；是否发布市场、升级已安装插件及验证真实GitHub另等用户授权。

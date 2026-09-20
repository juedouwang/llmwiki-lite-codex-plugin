# 可视化 Git — 开发源码阶段验收

## 结论与边界

真实 Git 主流程已接入项目“代码”页，复用共享六栏目壳；不是静态原型或模拟成功。已具备选择完整文件保存、本地作者身份、真实提交/分支图、节点差异、建/切分支、合并及普通文本冲突处理、保留历史的恢复、显式检查远端和普通单分支上传。

**尚不等于本 change 全部验收通过，不归档。** 真实 Windows 桌面无弹窗专项、若干浏览器异常/多目标场景以及旧 Git 回归错误仍单独保留。第二份7.5和真实定时验收未被本次勾选。

- 所有写入验证均在独立临时注册表、wiki/state、工作树及 bare 远端中运行；生产9项目授权、ACTIVE计划、报告/知识/文献正文未改。
- 只在开发仓库改源码；未向真实科研仓库执行版本操作，未访问真实 GitHub，未提交/推送/发布、未修改插件缓存。
- Git不接入每日调度，不调用模型，不将提交/文件变动转为科研完成状态。
- 锁和确认复核用于防止网页重入与已检测到的外部变化，不承诺能阻断所有外部Git进程的竞态，亦非全事务回滚。

## 修改落点

- `scripts/git_web.py`：项目绑定的确定性HTTP适配、预览/复核/执行、冲突与远端操作。
- `scripts/git_web_page.py`、`scripts/static/code.js`、`code.css`：共享壳内的真实Git页面与交互。静态内容不包含示意提交。
- `scripts/web_server.py`：仅局部接入代码页面、同源JSON API与静态白名单；保留共享layout的project_id/active/code/home。
- `scripts/git_service.py`、`git_graph.py`、`git_operations.py`、`git_merge.py`、`git_revert_restore.py`：非交互命令、图快照/泳道、选择提交、固定OID合并及保留历史恢复；未为网页开放旧reset/rebase等能力。
- 新增测试：`test_git_web.py`、`test_git_web_primitives.py`、`test_git_web_http.py`、`test_git_web_failures.py`、`test_git_web_reads.py`、`run_code_browser.py`、`code_browser_test.cjs`。
- 使用边界同步至插件AGENTS、README和网页Skill；导航交接只维护`docs/research-workbench-handoff.md`。

## 可重复运行与已取得证据

命令均在开发仓库根目录运行；本机Python为`E:/Anaconda3/python.exe -X utf8`。浏览器依赖由环境变量指定，不修改生产安装。

```powershell
python -B -m unittest discover -s plugins/llmwiki-lite/tests -p 'test_git*.py'
python -B -m unittest discover -s plugins/llmwiki-lite/tests -p 'test_git_web*.py'
python -B -m unittest discover -s plugins/llmwiki-lite/tests -p test_acceptance_git_graph.py
python -B plugins/llmwiki-lite/tests/run_code_browser.py
python -B plugins/llmwiki-lite/tests/smoke_test.py
python -B plugins/llmwiki-lite/tests/run_notebook_browser.py
python -B plugins/llmwiki-lite/tests/run_reports_browser.py
python -B plugins/llmwiki-lite/tests/run_knowledge_browser.py
python -B plugins/llmwiki-lite/tests/test_literature_browser.py
openspec validate research-visual-git-management --strict
```

| 验证 | 结果 / 证据 |
|---|---|
| Git网页真实API | `test_git_web.py`49项，48通过、1跳过（Windows没有创建symlink的权限）；底层primitives19项通过；HTTP4项通过 |
| 恢复/保存commit失败、push超时、属性过滤器保护 | `test_git_web_failures.py`5项通过；只注入失败命令，其余恢复/读取/后续保存执行真实Git，不mock成功 |
| 最终全Git回归与新增边界测试 | 169项：158通过、10个旧基线错误、1个环境跳过；无新增失败。新增`test_git_web*.py`范围81项为80通过、1跳过（由本轮全套执行结果及loader计数核对）；新增reads4项全过 |
| 浏览器真实主流程 | 最终13组全过：本地全流程、失效输入保留、远端检查/二次确认、冲突刷新/取消/完成、首提交身份、恢复提交失败后重新保存、跨栏目/项目、差异、深浅窄屏、性能、分页；JS pageerror为空，服务日志无500/Traceback |
| 页面性能 | 1000提交/1000文件、首批100节点，5次首屏1182/1184/1185/1210/1217ms，中位1185ms；17次点击至aria-busy反馈最大2.2ms。都是本机隔离测试数值，不是任意仓库的性能保证 |
| Git图验收 | `test_acceptance_git_graph.py`5项通过 |
| Smoke | 339项：336通过、3环境跳过 |
| 共享站点/编辑/其他功能 | notebook组合site+progress+连续文档21组通过；reports13组、knowledge7组、literature10组通过 |
| 插件边界校验 | opencode安装dry-run、7个Skill quick_validate、validate_plugin通过；未实际改宿主配置 |
| 静态检查 | 新Git适配/页面/图/命令/HTTP与浏览器runner范围Ruff、JS语法检查通过；全仓Ruff153项既有范围问题，不能称全仓绿色；本change OpenSpec strict校验通过 |

日志位于`%TEMP%/llmwiki-git-final.log`、`llmwiki-code-browser-final.log`、`llmwiki-git-smoke.log`、`llmwiki-git-validations.log`及`git-regression-*.log`。浏览器截图、结果、性能在`%TEMP%/llmwiki-code-evidence/`。这些是本机临时证据，不是持久产品数据；复现以仓库测试脚本为准。最终1024浅色及320深色截图已人工查看：详情真实加载、父边完整、无全页溢出；删去重复呈现的提交正文段落，完整说明仍可在元数据中查到。

## A-01至A-37证据索引

“通过”仅覆盖所列层级；未完成项明确列出，API通过不冒充真实桌面或全部浏览器专项通过。

| 场景 | 证据与状态 |
|---|---|
| A-01 项目切换 | 浏览器从进度进代码、项目A→B→A，断言project_id/节点不串用；跨项目token/file_id由API拒绝。尚未专项制造导航时长延迟响应 |
| A-02 不支持的仓库 | API已验多worktree、filter只读；reads已通过普通目录/父仓库子目录/bare/仓库内未忽略state。子模块、sparse、partial等组合未全部专项跑完 |
| A-03 空仓库/游离HEAD | API首提交/游离限制；浏览器首提交补身份再保存通过 |
| A-04 默认页 | 浏览器选HEAD并加载真实详情、非操作时收起文件清单通过 |
| A-05 详情/窄屏 | 浏览器关闭/重新选节点，320/580/1024深浅色无全页溢出、键盘可用通过 |
| A-06 真实父边 | primitives真实300余提交分叉/双父/泳道断言；浏览器真实合并后图展示通过 |
| A-07 快照分页 | primitives非当前分支/远端/tag变更失效及跨页泳道通过；浏览器100→200节点无重复。浏览器分页中外部改refs尚未单独触发 |
| A-08 节点差异 | 浏览器点击历史文件并核对只读差异；API首提交/恢复H→T与工作区隔离 |
| A-09 大/二进制差异 | reads已通过实际NUL、非UTF8、5000行截断；尚未逐一浏览器显示验收 |
| A-10 部分文件保存 | API/浏览器都核对HEAD、未选index和未选工作区通过 |
| A-11 部分暂存 | API完整选中文件包含全部修改，未选暂存不夹带通过 |
| A-12 新增删除重命名/失败 | API已暂存删除/重命名、中文空格前导短横线路径通过；提交失败保留文件/index的故障注入通过 |
| A-13 建分支 | API/浏览器先创建不切换，再显式切换通过 |
| A-14 脏工作区切换 | API已暂存/未暂存/未跟踪和确认后变脏均拒绝；浏览器正常显式切换通过，dirty拒绝尚未独立浏览器专项 |
| A-15 普通合并 | API/浏览器真实双父及源分支保留通过 |
| A-16 快进/已包含 | API真实FF/no_change且不制造提交通过 |
| A-17 文本冲突 | API整侧/手动、标记/大小/外部index保护；浏览器两文件逐一确认后真实merge通过 |
| A-18 刷新/取消 | 浏览器刷新恢复已保存结果、取消回原HEAD后重做完成；API保留额外未跟踪文件通过。实际服务进程重启专项尚未做 |
| A-19 外部/不支持冲突 | API二进制与外部merge禁止接管通过；symlink因权限跳过。添加/删除/重命名等冲突分类尚未逐一浏览器覆盖 |
| A-20 恢复 | API/浏览器均验证R.parent=H、R.tree=T.tree、历史仍可达通过 |
| A-21 同树/覆盖风险 | API同树no_change、忽略文件覆盖停止通过；不自动搬移/删除 |
| A-22 恢复提交失败 | 失败注入后HEAD不动、目标文件保留、网页明确未保存；关闭弹窗后从保存入口生成真实新提交通过 |
| A-23 远端选择 | 已实现已有远端列表/显式目标；API远端和分支大小写、确认后URL改变保护通过；多远端无upstream的浏览器专项未完成 |
| A-24 无远端 | 本地完整网页流程在无远端仓库通过；无远端提示已实现，按钮提示未独立专项 |
| A-25 检查/更新 | API/浏览器都验证fetch前后HEAD不变，第二次确认才快进，缓存变更旧确认被拒绝 |
| A-26 分叉拉取/失败 | API临时bare/peer真实分叉双父、不rebase，本地不可达远端失败与脱敏通过；浏览器分叉拉取/网络失败尚未专项 |
| A-27 只上传已提交 | API/浏览器仅指定branch，未提交文件、其他分支/tag不上传；首次成功才写upstream通过 |
| A-28 拒绝/不明确 | API真实非快进拒绝、loopback401认证失败；push超时故障注入仅一次且不设置upstream通过；非快进/超时网页反馈尚未专项 |
| A-29 预览后变化 | API相同size/mtime内容变更、index/refs/config变化复核；浏览器文件改动后拒绝并保留说明通过 |
| A-30 越界/重复 | API跨项目、伪造字段、消费后重用/过期；HTTP同源/Host/JSON/header/大小/无效UTF8保护通过。真实双tab同时写入与进程重启失效尚未专项 |
| A-31 无桌面打扰 | 非交互环境/CREATE_NO_WINDOW参数、真实loopback缺认证拒绝已验；**未做真实Windows桌面连续观测，不能标通过** |
| A-32 速度/外部更新 | 已测5次首屏及忙碌反馈；实现focus/visibility刷新、编辑区保留。外部工具并行修改+真实切换焦点尚未单独观测 |
| A-33 身份 | API及浏览器补仓库local身份、保留说明/选择、不自动重放通过；reads补恢复前身份阻止 |
| A-34 科研解耦 | Git路由未调用模型/报告/任务状态写入；既有功能回归通过。尚未做正式报告/Todo/知识全套写前写后跨模块专项 |
| A-35 网页完整流程 | 保存→建/切分支→保存实验→回主线→合并→选旧节点→恢复全由网页点击；Git仅只读后置断言通过 |
| A-36 同步与回归 | 临时bare同步及站点/进度/笔记/报告/知识/文献通过；旧Git套件仍有基线错误，真实GitHub未测 |
| A-37 整站代码接入 | 共享六栏、完整线性图标、跨栏目/项目返回、深浅窄屏及真实主流程通过；原型总验收/无窗口桌面证据未齐，不勾9.1 |

## 保留的旧基线问题

全Git旧基线已有10个`test_git_revert_restore`错误：两个repository_constraints、full_revert_restore_reset_workflow、五个reset用例、restore_file_removes_when_not_in_target、restore_tree_rejects_submodule_affected_operations。主要为旧`GitHead.ref`字段、将预期非零的config/cat-file按成功执行、Windows fixture路径等，已与旧命令封装对照复现；不删除用例或伪造通过。网页恢复使用新`restore_tree_as_commit`，不开放旧reset路径。

中间回归曾发现首次身份检查契约测试及已暂存重命名/删除保存失败，已分别按实际预览/执行职责修正测试、修正选择提交算法并复跑；不将这两个新失败混入“旧基线”。最终169项全Git回归确认这两个新失败不再出现；仅保留上述10个既有错误。

## 剩余与下一步

1. 按tasks未勾项补真实Windows无弹窗、双tab/进程重启、远端异常及跨模块专项，保留已通过主流程不重做。
2. 旧Git错误独立治理；不因网页主流程可用宣称全仓绿色。
3. 真实8766服务沿用此前手动重启说明，本轮没有再次停止/替换它；开发源码交付不代表旧服务或已安装缓存自动更新。
4. 第五份不改变已有ACTIVE定时授权，也不声称日报→知识→文献已完成真实定时运行验收。


## 2026-09-20：代码页加载性能专项

### 已保存版本与本轮边界

- 用户确认版本由提交子 agent 保存为 `1683efded9876f2c9dea0fc1717fcd5b33240bf3`，普通推送到 `origin/task/workbench-mvp` 并核对远端 SHA；不合并 main，不发布 Marketplace/tag，不修改已安装缓存。
- `docs/product-contract.md` 已随该 checkpoint 固化“简单、优雅、好用”。下列性能优化是 checkpoint 之后的独立本地修改，不包含在上述已推送版本中。
- 演示项目只进行 GET/只读 Git 测量；所有保存、分支、恢复、合并、远端动作测试均使用临时注册表、临时仓库及本地 bare 远端。没有修改真实科研数据、授权或计划。

### 诊断与最小改动

1. Windows 每次 Git 进程启动的代价积累明显：工作区状态暖读 14 次、版本图 13 次。仓库初始化分成四个元数据命令，图/详情还提前读取无用配置，HEAD 的 OID/分支也各自启动进程。
2. 属性安全检查对同一目录下每个文件反复解析父路径。保留全部目录及祖先属性检查，但按目录去重；单次路径校验只解析父目录一次。
3. 初始状态先返回时已请求 HEAD 详情，版本图随后返回仍误判为“已有快照改变”，重复请求同一详情；聚焦刷新还可能取消未完成首屏读取。
4. 合并元数据/HEAD 命令；配置只在本次请求需要时读取。HEAD 保留空仓库、游离状态与 SHA-256 支持；图读取前后快照复核不删。窗口聚焦不打断正在读取的状态/图，手动刷新和写后刷新不被该规则拦截。
5. 未增加依赖、跨请求旧数据缓存或等待动画，没有减少写操作预览/锁/复核或 Hook、过滤器、签名、根目录保护。

### 同机同项目对比

测量对象是已存在的“野人工作台 · 演示”（5 次提交、约 170 个已跟踪文件、1 个修改文件、无远端）。不是不支持仓库的错误响应，也不是自动联网拉取。新后端在临时 loopback 服务运行，测试完成即关闭。

| 指标 | 优化前 | 优化后 |
| --- | ---: | ---: |
| status 冷读 / Git 进程 | 1240 ms / 15 | 789 ms / 10 |
| status 暖读 / Git 进程 | 1050 ms / 14 | 611 ms / 9 |
| graph 两次读取 / Git 进程 | 770、780 ms / 13 | 460、458 ms / 7 |
| 页面含默认详情完整可用，5 次中位数 | 1953 ms | 1450 ms（等待减少约 26%） |

页面前五次约 `2013/1953/1972/1948/1946 ms`，后五次约 `1530/1445/1450/1448/1450 ms`。计时口径相同：导航开始至 `#code-create-from` 可见且刷新结束，再等 50 ms；含测试轮询等待，不等于接口耗时。没有把两种计时混为一谈。

另用现有 1000 提交/1000 文件临时基准测试：首批 100 节点与工作区就绪的五次中位 **793 ms**（786/793/793/802/813 ms），17 次点击到忙碌反馈最大 **2.4 ms**。该口径不等待详情，不能拿来冒充上述完整页面指标。

本机原始证据：`%TEMP%/llmwiki-code-before.json`、`llmwiki-code-after.json`、`llmwiki-code-page-before.json`、`llmwiki-code-page-after.json`、`llmwiki-code-evidence/performance.json`。数值只代表本机及本次数据，不保证任意仓库均达到此延时。

### 验证与生效说明

- 原 `test_git_web*.py` 81 项：80 通过、1 项因 Windows symlink 权限跳过；新增 `test_git_web_performance.py` 4 项全部通过（普通/空/游离/SHA-256 HEAD，单命令元数据与请求间配置刷新，目录去重后忽略属性保护，子模块保护）。
- `run_code_browser.py` 14 组通过，无 JS pageerror：新增强制状态先返回/图先返回、聚焦不重复请求、显式刷新仍读取、关闭重开详情；原保存/建切分支/合并/冲突/恢复/远端/响应式/分页测试全部保留且通过。实际写入后的刷新由原主流程继续覆盖。
- 全 Git 回归运行时共 172 项：161 通过、10 个既有错误、1 项跳过；错误用例集合与 `%TEMP%/llmwiki-git-final.log` 原 169 项基线完全一致，无新增错误。该全套启动后追加的子模块回归已在 4 项性能测试整组复跑通过，不将其虚计入前面的 172 项。
- 修改范围 Ruff、两个 JS 语法、Web Skill 校验与 diff 空白检查通过。
- 顶层 `smoke_test.py` 尚未通过：首页仍断言旧品牌“科研助手”，已在 `1683efd` 隔离导出副本复现同一错误，非本轮性能回归；不删除断言冒充绿色，也不将这次性能修复扩展成全站旧测试重写。
- 运行中的 8766 服务在服务端修改前启动，未在本轮替换。源码测试通过不代表常驻服务或插件缓存已更新，须正常停止旧服务并重新启动后才能体验后端提速。


## 2026-09-20：四项并行修复与已读内容即时交互

本节为用户追加的即时交互要求（G-19/A-38/A-39）验收，覆盖前述“保留旧错误”的当前状态；上文数字保留为历史诊断，不代表本节完成后的结果。

### 实现与边界

- 修复旧回退辅助函数10个错误：HEAD字段、预期非零返回、Windows临时远端路径，并补恢复点回执、缺失对象不能误删、路径越界拒绝等验证。网页不开放reset，不改真实科研仓库。
- 保留服务端合并Git命令、惰性配置、属性按目录复核的优化。新增项目DOM展示缓存，提交详情按OID最多缓存48条、首屏结束后串行预取前6条。展示快照不授权写入，失效/错误明确反馈；不自动fetch/push。
- 六栏目导航保留已初始化DOM，浏览器前进后退可用；页面查询限定所属root，离开暂停轮询，未保存/上传/写入/弹窗草稿阻止丢失，恢复后台读取。科研记录仍使用原连续Markdown/Ctrl+V编辑器，不重做block交互。
- 共享壳和Git主要几何按批准原型校准，保留“野人工作台”和125%。1440×960下：侧栏207.5px、顶部66.25px、工作区状态起点158.75px、版本区起点273.25px、详情宽357.5px、行高85px。不是全站逐像素相等的声明。

### 已完成验证

| 验证 | 实际结果 |
| --- | --- |
| 全Git `python -B -m unittest discover -s plugins/llmwiki-lite/tests -p 'test_git*.py'` | 179项，178通过，1项Windows symlink权限跳过；无失败，460.378秒 |
| 顶层smoke | 374项，371通过、3项既有跳过；旧品牌断言按已批准品牌更新，恢复专项重新纳入，无删除测试 |
| 栏目真实浏览器 `run_workbench_navigation.py` | 4组通过：六栏目单document/保留原Git DOM、前进后退、两项目及报告context、外部提交刷新；零pageerror |
| 缓存栏目性能 | 注入500ms后台Git读取延迟，最终集成6次点击到两次rAF后真实内容为11.2/16.8/17.8/17.9/17.7/17.9ms，最大17.9ms；不是按钮忙碌反馈，不代表未缓存首读 |
| 页面生命周期 `run_page_lifecycle_browser.py` | 编辑器/任务/报告详情/报告列表/文献/知识6组通过 |
| 原笔记浏览器 | 21项通过，含Markdown、Ctrl+V图片、光标/撤销重做、自动保存、并发冲突、离线恢复、六宽度深浅色、不弹filechooser |
| 原报告浏览器 | 14项通过，含多项目context、编辑确认、粘贴、候选保护、冲突、窄屏及授权兼容 |
| 静态/插件 | 修改Python的Ruff、修改JS语法、七Skill、插件清单、opencode dry-run、OpenSpec strict通过 |

### 最终集成补验

- Git缓存专项6组全部通过：18次已缓存节点点击，同步实际内容更新最大1.4ms、两次rAF绘制机会最大19.1ms，零pageerror；覆盖有界串行预取、请求去重、未缓存读取后变暖、外部提交刷新、刷新失败保留内容并禁写、项目隔离。增加BFCache生命周期回归：从其他栏目离开整页后恢复，之前detach的Git根节点再次进入仍能读取最新状态；pagehide仅暂停，dispose才永久销毁。该BFCache专项使用真实DOM detach/恢复及合成生命周期事件验证处理逻辑，未将其声称为真实浏览器BFCache命中证据。
- 完整Git浏览器14组全部通过，真实临时仓库覆盖保存、建/切分支、双父合并、冲突解决、恢复为新版本、预览过期拒写、本地bare远端和分页。1000版本/1000文件首屏五次中位693ms；这仍是首次真实I/O口径，不与已缓存节点19.1ms混淆，也不承诺冷启动零等待。
- 全站浏览器通过10条桌面/移动路由及记录详情、导航键盘、搜索筛选、设置、任务时间线、继续上次、旧数据导入、并发冲突和收藏。原型专项通过11组几何/样式对照、六栏目真实切换及Git五宽度无全页横溢出；保留现有手动笔记，不以示意数据代替实际内容。
- 所有子任务合并后再次运行导航4组全部通过（真实DOM、后台500ms延迟、浏览器历史、项目/报告context隔离、外部提交），最大17.9ms，零pageerror。修改Python的Ruff、16个修改/新增JS语法、diff空白检查及OpenSpec strict均通过。

新增证据：`%TEMP%/llmwiki-code-cache-bfcache-regression.log`、`llmwiki-code-browser-bfcache-regression.log`、`llmwiki-task-b-site-prototype.log`、`llmwiki-site-prototype/`、`llmwiki-navigation-integrated.log`。

证据在本机临时目录：`llmwiki-git-final.log`、`llmwiki-navigation-evidence/performance.json`与`navigation-code.png`、`llmwiki-notebook-lifecycle-regression.log`、`llmwiki-reports-lifecycle-regression.log`、`llmwiki-task-b-final.png`、`llmwiki-task-b-prototype-aligned.png`。

### 仍需明确的范围

- HEAD隔离副本全仓Ruff原有153项问题；本轮修改文件Ruff全通过，未对无关文件做批量自动修复。全仓最终剩余117项既有问题，不宣称全仓静态检查绿色。
- 8766旧服务本轮没有停止/替换；新后端在临时服务验收，不能据此宣称当前常驻服务已加载新Python代码。需正常重启后再整体体验。
- 本轮改动未commit/push，未合并main、未更新Marketplace或已安装插件缓存；此前已推送的checkpoint仍为1683efd。
- 未改变真实日报计划/授权/科研内容，未新增codex exec或后台模型。首次真实I/O、Git写入耗时与已读界面点击耗时分别报告。

## 2026-09-20：原型右栏、三圆圈与 pull/push 补验（G-20 / A-40）

### 本轮变更

- 直接读取用户批准的 `research-workbench-complete.html`，不修改源稿；版本右栏按源稿组织标题、短哈希/作者/时间、改动文件与数量、文件图标、真实新增/删除行数、紧凑差异和底部两项操作。完整元数据点击简短信息行展开。去掉重复路径、默认展开的技术信息与多余图例。
- 差异省略重复文件头，相邻删/增块按原型逐行配对；保留全部真实代码与后续 hunk 范围标记。弹窗内原差异展示不受影响。版本节点使用实心点和选中外圈，HEAD由真实当前分支标签表达。
- `git_web.py` 通过只读 Git `--numstat -z` 返回真实行数，支持首提交、修改/删除、Unicode路径和重命名；合并相对第一父版本，二进制不伪造数字。diff读取不重复计算统计；禁止外部diff/textconv的既有边界保持。
- 顶部文字改为小写 `pull` / `push`。侧栏与分支选择器 SVG 已有完整三个 circle，本轮用浏览器断言固定，不另画缺环图标。品牌、125%尺寸、当前手动记录编辑器不变。写操作预览、确认和执行前复核不变。

### 本轮实际运行结果

| 验证 | 结果 |
| --- | --- |
| 新增 `test_git_web_detail.py` | 4项通过，最终6.349秒；真实临时仓库验证普通路径/重命名/二进制/合并与不调用外部diff；tab/newline路径仅NUL解析fixture，不冒充Windows文件测试 |
| `run_site_browser.py` / `site_prototype_browser_test.cjs` | 全站10条桌面/移动路由及记录/搜索/筛选/设置/任务/上下文/导入/并发/收藏通过；批准原型22组几何样式、六栏目切换和Git五种宽度通过；验证真实+2/−2、+8/−3计数，三circle和pull/push，完整元数据可展开 |
| `run_code_browser.py` | 14组通过，覆盖真实临时Git保存、分支、合并、冲突、恢复、过期预览、本地bare远端与分页；1000版本/1000文件首次加载五次中位747ms，非已缓存点击口径 |
| 最终 `run_code_cache_browser.py` | 6组通过，18次缓存节点切换同步内容更新最大5.9ms、两次rAF绘制机会最大19.4ms，零pageerror；不是屏幕像素呈现测量，也不代表首次读取零等待 |
| 静态检查 | 本轮修改Python的Ruff、code.js与原型测试JS语法、diff空白检查、OpenSpec strict和web Skill校验通过 |

新4项已接入smoke入口，但本轮没有重跑整套smoke或全Git单元测试；上一节的全套数字是前轮证据，不冒称本轮重新运行。

证据：`%TEMP%/llmwiki-detail-api-final.log`、`llmwiki-detail-prototype.log`、`llmwiki-detail-code-regression.log`、`llmwiki-detail-cache-final.log`。截图/22组比较数据位于 `%TEMP%/llmwiki-site-prototype/`（`actual-code.png`、`prototype-code.png`、`comparison.json`）。已实际打开检查本轮截图，取自真实临时Git项目，不是当前常驻8766服务；没有将测试项目注册到用户真实注册表。

### 运行与交付边界

只读核实8766已在2026-09-20 14:55:15由源码启动（本轮观察PID2736），不是前轮的旧PID5584；本轮Python修改发生在启动之后，仍需正常重启才能完整生效。本轮没有停止/替换常驻服务，也不把静态文件刷新或隔离截图当成服务已完整更新。未commit/push、未更新Marketplace/安装缓存，未改真实科研内容、计划或授权。


## 2026-09-20：diff放大、仓库位置与项目启动/排序（G-21/G-22、A-41/A-42）

### 本轮实现

- 依据用户图2将代码/分支图标改为主干向右上弯出的细线分叉并保留三个完整圆圈；合并图标也保留三个节点。顶部 `pull` / `push` 不变。
- 版本元数据新增真实注册仓库绝对位置；diff上方新增只读“放大”，显示同一已读原始diff，保留截断/二进制提示，支持关闭/Escape和焦点恢复。不新增网络同步或Git写操作。
- 创建分支、恢复版本紧接diff，不再被历史面板高度推至底部；既有写入预览、确认、执行前复核不变。
- 访问 `/` 按独立网页默认项目、列表第一项的优先级进入项目科研进度；无项目进入 `/projects`。品牌始终返回 `/projects` 总览。项目深链接不重定向；不修改助手的 `current_project_id`。
- 总览中星标指定/取消默认项目，拖动文件夹或聚焦后上下键调整顺序，自动持久化。失败可见回退；侧栏和缓存页面中的项目菜单同步顺序。新增项目排在已有手动顺序之后。
- 偏好写入复用本地请求校验、注册表锁和原子写入；非法项目/重复/过期顺序拒绝，不部分写入。不新增依赖或修改用户真实项目设置。

### 本轮实际验证

| 验证 | 结果 |
| --- | --- |
| 项目偏好单元/HTTP测试 | `test_project_preferences.py` 6项通过；覆盖启动/指定/取消/注销回退、新项目追加、顺序持久化、助手当前项目独立、非法输入、跨源403与空项目 |
| 全站及本轮新增浏览器 | 最终 `run_site_browser.py --refinements` 通过；全站10条桌面/移动路由及既有交互通过；新增5组覆盖图标/真实路径/只读放大/焦点、长历史布局、真实鼠标拖动/键盘排序/刷新、默认入口/品牌/新页面、失败回退/缓存菜单同步，320/580/1440宽度无横向溢出，零pageerror |
| 批准原型对比 | 本轮较早 `run_site_browser.py --refinements --prototype <批准HTML>` 通过22组几何样式、六栏目、五种Git宽度；本轮按用户要求改变的diff和操作纵向位置不再与旧稿相等比较，改用明确的紧邻断言；最后项目任务文字左对齐修正后重跑全站/新增浏览器通过 |
| Git缓存回归 | `run_code_cache_browser.py` 通过；18次已读节点同步内容切换最大2.1ms、两次rAF绘制机会最大18.7ms，零pageerror。这是缓存交互而非首次读取或屏幕像素测量 |
| 栏目导航回归 | `run_workbench_navigation.py` 4组通过；六栏目同文档/DOM保留、慢后台请求、浏览器历史、项目隔离、外部提交后台刷新；6次样本最大18.0ms，零pageerror |
| 静态检查 | 9个本轮涉及Python文件Ruff、6个JS/CJS语法、Git diff空白、OpenSpec strict、web Skill校验通过；不表示全仓既有Ruff问题已解决 |

补充说明：首次完整smoke的393项中，唯一失败是旧项目总览测试仍GET `/`、读到新的303空正文；已将该测试迁移到 `/projects`，保留任务可见性和禁止嵌套链接断言。新入口303已由专门HTTP测试覆盖。修正后重新完整运行smoke：393项，390通过、3跳过、0失败，243.409秒（日志 `llmwiki-refinements-smoke-final.log`）；包含新增项目偏好6项及真实仓库位置断言。

证据：`%TEMP%/llmwiki-project-preferences-unit.log`、`llmwiki-refinements-browser.log`、`llmwiki-refinements-browser-final.log`、`llmwiki-refinements-cache.log`、`llmwiki-refinements-navigation.log`、`llmwiki-refinements-smoke-final.log`。真实临时服务截图在 `%TEMP%/llmwiki-workbench-refinements/`：`code-detail.png`、`diff-expanded.png`、`project-order.png`、`result.json`。三张截图均已实际打开复核；项目截图取自成功保存状态，不是模拟失败状态。长历史验证通过加高历史面板至2800px施加布局压力，不冒充生成了2800px真实Git历史数据。

### 运行与交付边界

- 测试使用隔离临时仓库及注册表，未注册到用户真实配置；未改真实科研内容、定时授权、Marketplace或安装缓存；未commit/push。
- 收尾只读检查8766，观察常驻PID35092、启动于2026-09-20 18:44:03；GET `/` 仍为200，`/projects` 与偏好API仍404，说明当前服务没有加载本轮后端。此PID取代前文旧观察值。本轮未停止/替换服务，必须正常重启后才完整生效；静态刷新和隔离测试不能作为常驻服务已更新的证据。

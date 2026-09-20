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

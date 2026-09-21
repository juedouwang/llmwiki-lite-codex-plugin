# LLM Wiki Lite 科研助手

LLM Wiki Lite 是同一份源码、面向多个 AI 编码助手的轻量科研与项目知识插件：

- **Codex**：作为 Codex Plugin 安装（Marketplace 位于本仓库 `.agents/plugins/marketplace.json`）；
- **Claude Code**：作为 Claude Code Plugin 安装（Marketplace 位于本仓库 `.claude-plugin/marketplace.json`）；
- **opencode**：运行 `plugins/llmwiki-lite/opencode/install.py` 写入 `opencode.json` 配置。

三个平台共用同一套 Skill、MCP 工具、变更 Hook 和网页端。AI 助手负责理解研究问题、选择证据和写知识；程序只承担重复、确定性的文件与状态操作。

## 安装

### Codex

见仓库根目录 `README.md`：

```powershell
codex plugin marketplace add https://github.com/juedouwang/llmwiki-lite-codex-plugin
codex plugin add llmwiki-lite@llmwiki-lite
```

### Claude Code

```text
/plugin marketplace add juedouwang/llmwiki-lite-codex-plugin
/plugin install llmwiki-lite@llmwiki-lite
```

Claude Code 会自动发现本插件根目录下的 `skills/`、`hooks/hooks.json` 与 `.mcp.json`。

### opencode

```powershell
python plugins/llmwiki-lite/opencode/install.py
```

脚本把 Skill 路径、`mcp.llmwiki` 和变更 Hook 合并进 `~/.config/opencode/opencode.json`（可用 `--scope project`、`--config`、`--dry-run`、`--uninstall`）。细节见 `opencode/README.md`。

### 运行环境

三个平台都要求 `PATH` 中存在可用的 Python 3（默认命令为 `python`，只使用标准库，无需安装依赖）。Linux/macOS 若只有 `python3`，请为 `python` 建立符号链接，或把 MCP 配置改成 `python3`。

## 组成

```text
llmwiki-lite/
├─ .codex-plugin/plugin.json     # Codex 清单
├─ .claude-plugin/plugin.json    # Claude Code 清单
├─ .mcp.json                     # Codex 与 Claude Code 共用的 MCP 配置
├─ hooks/                        # 变更提示 Hook（Codex 与 Claude Code 共用）
├─ opencode/                     # opencode Hook 插件与安装脚本
├─ skills/
│  ├─ llmwiki-projects/
│  ├─ llmwiki-understand/
│  ├─ llmwiki-query/
│  ├─ llmwiki-maintain/
│  ├─ llmwiki-literature/
│  ├─ llmwiki-research-record/
│  └─ llmwiki-web/
├─ scripts/
└─ tests/
```

`.mcp.json` 与 `hooks/hooks.json` 使用同一段跨平台启动代码：Codex 通过插件根目录 / `${PLUGIN_ROOT}` 解析脚本，Claude Code 通过 `${CLAUDE_PLUGIN_ROOT}` 解析，opencode 由安装脚本写入绝对路径。

七个 Skill 可以独立触发：

1. `llmwiki-projects`：注册项目和管理存储位置；
2. `llmwiki-understand`：理解研究或软件项目并建立少量有用 Wiki；
3. `llmwiki-query`：从 Wiki 定位，再回到真实项目核验；
4. `llmwiki-maintain`：根据变化增量维护受影响页面；
5. `llmwiki-literature`：调研推荐论文，在用户选定后下载原文、生成中文精读并进入文献中心；
6. `llmwiki-research-record`：在用户明确要求后，把讨论整理为阶段性科研记录；
7. `llmwiki-web`：启动中文科研知识工作台。

## 中文输出约定

人类可读 Wiki、维护报告和查询回答默认使用简体中文，写作结构贴近中国研究生科研习惯：研究问题、方法、实验条件、证据、结果、结论、局限和下一步。

以下内容在需要精度时保留英文：代码、路径、命令、API/MCP 名、Schema 字段、算法名、模型名、论文标题、数据集名、指标名和不宜机械翻译的技术术语。

## 中文科研知识工作台

网页端是主要可视化和浏览入口，保持简洁、无前端构建链：

- **统一布局**：浅灰侧栏、白色内容区、中性色按钮与统一细线 SVG 图标（本地内置，不依赖图标字体）；一个导航入口，不再嵌套第二侧栏。
- **项目与知识库**：项目总览保留独立入口；知识库左侧筛选目录、右侧阅读正文，只列知识页，记录、报告、文献及隐藏归档各归其位。维护详情按需展开。
- **文献**：搜索、阅读状态、类型和收藏筛选；原文阅读及精读对照；辅助阅读与添加文献说明默认折叠。
- **科研记录**：平面分隔线列表，标题下显示日期、来源及真实批注数；右侧打开或删除手动笔记，搜索默认收起。连续 Markdown 编辑器支持编辑时查看截图、自动保存、草稿、历史和冲突保护；旧批注保留，新批注可直接使用 Markdown 引用。
- **阅读**：正文优先，目录/记录信息收起，复制路径与打印放进“更多”，时间不写进笔记正文。
- **统一外观**：共享批准原型的留白、行列表、细线图标和加号主按钮；新建笔记、日报、周报及任务保持同一操作形式。侧栏底部或设置页可选浅色、深色、跟随系统，浏览器记忆选择，系统变化即时响应。
- **设置**：存储与项目配置按需展开，高级路径默认收起；移动端抽屉导航支持键盘与焦点管理。

界面重构不迁移数据、不改真实 Markdown 路径、不开放任意文件编辑。手动笔记仍是明确授权的编辑入口。网站仍只监听 `127.0.0.1`；公网访问、账号权限和多设备同步尚未实施。


文献辅助阅读推荐写入 `wiki_root`，并在 frontmatter 中使用相对项目根目录的 `paper_file` 或 `sources` 精确关联原文。未显式关联时只做保守的标题/路径候选匹配；置信度不足就保持“待关联”，不会伪造对应关系。系统不自动翻译 PDF，AI 助手必须先读原文，再把原文事实、解释和待验证推断分开写。科研过程记录写入 `wiki_root/records/YYYY/MM/YYYY-MM-DD.md`，同一天的多次明确记录追加到同一个日档，每条记录通过 `#entry_key` 区分；只有用户明确触发时才创建，不会自动保存所有对话。

启动方式：

```powershell
python -I -B scripts/web_server.py --host 127.0.0.1 --port 8765
```

也可让 AI 助手调用 `llmwiki_web_start`。

## 存储模型

每个注册项目有三个位置：

- `source_root`：真实研究/代码项目，插件只读理解；
- `wiki_root`：人类可读 Markdown；
- `state_root`：快照、哈希、配置和变化提示。
- `wiki_root/records/YYYY/MM/YYYY-MM-DD.md`：用户明确触发后生成的科研过程日档；同一天追加多条记录，不覆盖历史。

未配置全局默认时，新用户使用 `<project-root>/wiki`。配置例如 `E:\wiki_obsidian` 后，新项目默认使用其独立子目录。网页修改位置时默认复制原内容，并永不自动删除旧目录。

## 安全边界

- 网站只绑定 `127.0.0.1` / loopback；
- 所有文件读写做根目录和路径穿越检查；
- Wiki 写入仅发生在配置的 `wiki_root`；
- 文献原文只从已注册项目的 `source_root` 读取，只允许 PDF、EPUB、DOCX、HTML/HTM；路径穿越和符号链接会被拒绝；
- PDF 使用 inline 与 Range 流式响应供浏览器阅读，非 PDF 作为附件打开，源项目 HTML 不在站内执行；
- 网站永不修改、移动或删除文献原文；
- Hook 仅提供 dirty-path 提示且始终 fail-open（三个平台相同：脚本缺失或输入异常都不会影响宿主工具调用）；
- 不进行独立外部网络发送；
- 不引入 React/Vue、Node 构建链、数据库或外部 CDN；
- 不生成固定十五类页面，不批量制造空模板。

## 使用示例

```text
注册当前项目，把人类可读 Wiki 放到 E:\wiki_obsidian。
```

```text
理解这个研究项目，用简体中文建立少量真正有内容的 Wiki 页面。
```

```text
核验低纹理 RGB-D 配准方案的实验依据，并指出还缺什么实验。
```

```text
精读 references/ANoCo.pdf，生成简体中文辅助阅读并写入 Wiki，然后打开文献中心进行原文对照。
```

```text
检查最近变化，只更新受影响的 Wiki，然后打开中文科研工作台。
```

```text
记录刚才的讨论，保存这次阶段性理解、依据和下一步。
```

## 验证

```powershell
$env:PYTHONUTF8='1'
ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/opencode plugins/llmwiki-lite/tests
python -B plugins/llmwiki-lite/tests/smoke_test.py
python plugins/llmwiki-lite/opencode/install.py --dry-run
python C:/Users/lyn/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/llmwiki-lite
```

## 手动科研笔记（开发中 / 未发布）

- 时间戳仅作为笔记元数据保存，不写入正文或逐块展示；点击“更多 → 笔记信息”查看创建和修改时间（北京时间，到秒），导出放在 frontmatter。兼容旧笔记，隐藏此前自动生成的时间行，不删除用户手写内容。

在项目 **科研记录 → 新建笔记** 中直接书写，不再必须通过 AI 对话。

- 列表右侧垃圾桶可删除手动笔记，须确认；当前列表提供撤销。服务器复核版本，已在别处编辑或同名笔记已重建时拒绝覆盖；助手记录不提供此按钮。
- 新建直接聚焦连续正文，没有加块、图片按钮或文件选择器。粘贴 Markdown 默认显示预览，已有正文可切换编辑/预览，编辑状态下图片直接显示；代码只展示、不执行。
- 截图后 **Ctrl+V** 连续粘贴，或拖入图片；保留插入位置，上传失败可重试，不覆盖已有图片。支持 PNG/JPEG/GIF/WebP，单张不超过 10 MiB。
- 选中文字添加批注，保存引用快照与独立批注；创建/修改时间只在 **更多 → 笔记信息** 查看，不写进正文。
- 停止输入约 600 ms 自动保存；**Ctrl+S** 立即保存，**Ctrl+Enter** 切换编辑/预览。普通键入沿用浏览器撤销，程序插入可整次撤销/重做。
- 未保存稿保留在当前站点的本机恢复存储，刷新后明确选择恢复；多标签版本冲突不静默覆盖，外部改坏的笔记只读，并可另存为新笔记。
- 更多菜单提供标签、历史、另存与 Markdown 复制，工具条提供导出；上传未结束时暂不导出或确认。历史恢复保留当前快照，时间不混入导出正文。

### 存储与兼容

- 手动笔记：`<wiki_root>/records/manual/<id>.md`。正文可直接阅读，末尾隐藏注释保留格式、批注和一致性校验数据，无额外数据库。旧 block 笔记浏览时只在内存转换，首次实际编辑保存才按单篇转为连续正文；ID、创建时间、附件路径和原始历史快照保留，不批量迁移。
- 截图：`<wiki_root>/records/assets/<内容哈希>.<扩展名>`。删除正文图片或整篇笔记不会删除附件，避免历史版本失去附件。
- 保存历史：`<wiki_root>/.notebook-history/<id>/*.snapshot`，不进入记录时间线。历史不自动清理；长期大量编辑时需要留意存储占用。
- 删除归档：`<wiki_root>/.notebook-trash/<id>/<revision>.snapshot`，不进入知识目录、记录列表或全文搜索。撤销原样恢复正文及修改时间，附件和历史始终保留。
- AI 日档仍只追加，不被手动笔记编辑器改写；手动笔记与日档统一参与记录检索、标签筛选和列表。
- 外部直接修改手动笔记的正文后，编辑器会拒绝覆盖；可先从“查看原 Markdown”阅读原文，再人工合并或另建笔记。
- 导出 Markdown 包含相对图片路径，不内嵌图片；迁移时一并保留 `records/manual` 与 `records/assets` 的相对结构。
- 当前批注是**引用文字批注**，不是图片涂鸦/箭头标注，也不包含多人实时协作或 Jupyter 代码执行。

### 验证

`python -B tests/smoke_test.py` 同时运行笔记存储、冲突、上传、安全与兼容回归。
也可单独运行 `python -B tests/test_notebook.py`。

原型/主题/删除验收：`python -B tests/run_site_browser.py --prototype <批准的HTML路径> --alignment --refinements`，在临时项目运行，并输出真实页面与原型截图、尺寸比较。

可选浏览器回归：准备已有 Node 与 Playwright，设置 `LLMWIKI_PLAYWRIGHT` 为模块路径，运行 `python -B tests/run_notebook_browser.py`。
可设置 `LLMWIKI_BROWSER_CHANNEL=chrome` 使用已安装 Chrome；`LLMWIKI_SCREENSHOT` 指定截图文件。测试不安装依赖，不使用真实项目数据，也不修改浏览器个人资料。

原生标签页可见性验收：手动运行 `python -B tests/run_progress_visibility.py`。沿用 `LLMWIKI_PLAYWRIGHT`；默认使用 `C:/Program Files/Google/Chrome/Application/chrome.exe`，可用 `LLMWIKI_CHROME` 指定其他安装路径。需要已有 Playwright 支持 CDP `noDefaults`。此项会打开独立临时浏览器，实际切走 30 秒再切回，约一分钟后自动关闭；不模拟 document.hidden，不加入 smoke 或后台运行。

## 跨天科研进度与低打扰记录

三类功能分开：科研进度保存未完成任务、精简完成事项和当前停点；日报/周报展开一天的工作过程（规格及本地编辑/生成协议已落地）；知识库仍由现有 LLM Wiki 维护项目理解。

- “科研进度”：本周/两周/四周截止日排期、未排期、折叠 Done 和继续上次。任务统一描述、一个可选截止日期（DDL）、高/中/低优先级；高优先级在前，柔和红/黄/绿区分，完成/恢复明确点击。名称、截止日、优先级和完成情况只由用户修改，不因文件变化或午夜自动完成。
- 人工任务在 `.research-progress/tasks.json`；自动上下文在同目录 `contexts.json`。人工修订优先，清空也不会被自动内容填回。页面打开、保存、Hook、Git 都不等待模型。
- 已提供 MCP `llmwiki_progress_get` / `llmwiki_progress_context_write` 作为自动摘要接入口。未接通生成端时详情显示“自动整理尚未接通”。写入摘要成功不等于无人值守自动化已完成。
- MCP 调用漏填必需参数时，返回具体缺失字段，不执行工具、不输出内部 TypeError 或 traceback；进度读写传入空项目（空串、空白、null）也会拒绝，不回退当前项目。
- 笔记直接粘贴或拖放图片；每次在正文新插入，不自动替换已有图片。
- **自动日报/周报默认关闭，启用需用户授权和内置计划绑定**。Hook 只有文件变化提示。方案见 `docs/research-continuity.md`。

验证：`python -B plugins/llmwiki-lite/tests/smoke_test.py`；`python -B -X utf8 -m unittest discover -s plugins/llmwiki-lite/tests -p "test_progress*.py"`。可选浏览器测试覆盖导入、跨天上下文、并发冲突、自动整理未接通说明及移动端。


## 日报 / 周报：工作台多项目汇总

从项目进入日报与周报会保留左侧当前项目；日报/周报切换、筛选、分页、新建、详情与返回不切回注册表默认项目。只有用户操作左上项目选择器才更换浏览项目。URL 中 `context` 表示浏览项目，`project` 仅筛选报告参与项目，两者都不改变报告归属，也不写入全局默认项目；多标签页分别保留上下文。旧项目报告深链继续使用原存储/API，失效项目提示重新选择。

开发源码新增独立“日报与周报”入口 `/reports`，不再嵌在科研记录中。日报与周报均汇总已选的多个项目，同一天/同一周各一篇，不归档到任何项目；正文存 `<LLMWIKI_HOME>/workspace/records/reports/`，旧项目报告和链接保留。支持：连续 Markdown 编辑/预览、截图粘贴、草稿自动保存、确认正式版、历史查看与候选比较。旧手动 block 笔记不批量迁移，仅在该篇实际编辑保存时无损转为连续正文。`records/reports/` 是独立报告目录，不作为新的原始科研材料反复总结。用户周报模板原样打包在 `templates/weekly-report.md`。

报告生成协议是 `llmwiki_report_plan → llmwiki_report_sources（读完分页）→ llmwiki_report_finish`，由宿主推理，网站不调用模型。人工稿和正式版不被自动覆盖，生成内容进入候选。已接已保存科研记录、笔记、任务历史、只读 Git 提交/有界差异、文件变化提示和明确授权的项目活动库。共享入口先采集授权后的本机项目对话，再冻结来源；原始消息只取用户/助手文字，不收工具原文，不将文件变化当完成证明。当前日志适配器支持 Codex，Claude Code/OpenCode 与网页聊天尚未自动接入；超限/格式变化/撤权均明示缺口，不声称全账户全量。

**设置默认关闭、时刻为空，需用户明确选择来源项目和日报/周报时刻，不再设置归档项目。** 网页保存并不创建任务；用户也可在对话中明确授权助手保存配置。共享 CLI 核验真实官方回执后绑定同一内置任务。本机的实际启用和运行状态见仓库 `docs/research-workbench-handoff.md`；不能手填 runtime、修改插件缓存或启用 `codex exec` 兜底。现有安装版也不会因开发仓库修改而自动获得新工具。

本次逐项证据及剩余工作在仓库 `openspec/changes/research-daily-weekly-reports/verification.md`。报告回归：`python -B -m unittest discover -s plugins/llmwiki-lite/tests -p test_reports.py`；浏览器回归使用已有 Playwright/Chrome，运行 `python -B plugins/llmwiki-lite/tests/run_reports_browser.py`，不安装新依赖。


### 增量知识维护（开发工作树）

- 宿主使用 `llmwiki_knowledge_plan` → `llmwiki_knowledge_sources` → `llmwiki_knowledge_finish`；确定性工具不运行模型。独立基线，不覆盖其他功能的 snapshot。
- 有依据的新页和不冲突的严格尾部追加自动落地；修改旧文只生成建议。网站原知识库入口比较并确认，双版本校验保护用户正文与已变化来源；保留原文抑制同依据重复提案。
- 来源为注册项目文本、已落盘记录及明确授权的项目对话。报告/知识输出不反馈为证据，未接宿主与图片解析不冒充接通；已检查对话在查询限量前排除，同一来源修订重新入选，避免旧批次饿死新增内容。运行、证据和确认元数据保存在 state/knowledge-maintenance；知识 Markdown 保持原位置。
- 设置中的“同时维护知识库”“同时收录项目文献”默认关闭，沿用报告共享入口，不另起进程。网页勾选只保存意图；共享源码入口和官方回执核验已经接线，生产计划经用户授权后复用唯一内置任务，具体状态见交接页；不能把默认配置当作授权。
- 临时项目验证：`python -B -m unittest discover -s plugins/llmwiki-lite/tests -p "test_knowledge*.py"`；浏览器 `run_knowledge_browser.py`；60秒待处理时前台性能 `run_knowledge_performance.py`。详细已验/未验在对应 OpenSpec change 的 verification.md。


### 开发工作树：第三、四份接入（2026-09-19）

- 知识维护新增 `llmwiki_knowledge_plan/sources/finish`。明确项目的手动维护可用；新页/严格末尾追加直接落地，改旧文进「待确认更新」，网页支持对比、采用、保留原文和历史。原文/依据变更后拒绝过时覆盖。
- 项目原「文献」入口现为独立收藏目录：粘贴 DOI/arXiv/网址即可收藏，不强制下载；支持编辑、项目内搜索、移除、明确重新收藏恢复、原文/阅读笔记绑定。旧文件仅经“更多→明确导入本地文件”导入。
- 新增 `llmwiki_literature_collect/plan/sources/finish`；每日收录协议读取已保存科研记录/手动笔记，不受日报十四天窗口限制。不把普通网站当论文，不自动覆盖人工字段或恢复已移除条目。
- 报告设置增加知识/文献两个默认关闭开关。共享三阶段已接线并完成隔离数据实际存储回归；实现初期任务曾暂停；本机任务已按后续授权连接启用，当前授权及首次真实定时验收状态统一见`docs/research-workbench-handoff.md`，不能把连接成功当作已定时产出。原始聊天取材只覆盖授权后的已支持宿主；未启动 exec 后台、未发布插件。UI可用与无人值守已接通必须区分。
- 验证记录与未完成项见第三、四份 OpenSpec `verification.md`，不是另立需求副本。


## 连接共享内置计划（新用户配置步骤，既有授权见交接）

共享入口使用标准库 `tomllib`，需 Python 3.11+。更新源码后先重启网页服务，再保存新设置；旧进程不会自动重新加载 Python 模块。

1. 打开网站“设置 → 报告自动整理”，明确选择来源项目、日报时刻、周报星期/时刻；两种报告都是工作台文档，不设置归档项目。时区固定为 Asia/Shanghai；不代填默认时刻或选择全部项目。
2. 若需纳入本机对话，显式勾选对话接入；只从授权时刻开始，取消后停止取材。知识维护和文献收录分别勾选，二者默认关闭。保存总开关不等于已创建/成功运行计划。
3. 点击“复制连接指令”交给宿主。按 `skills/llmwiki-research-record/SKILL.md` 使用官方计划工具复用同一个 heartbeat；获得真实 id/目标线程后执行源码 CLI 的 `bind`，只读官方回执并核对 ACTIVE。已有任务必须优先复用，无论当前是否暂停，不能另起第二个后台。
4. 计划从源码调用 `begin → call(report…) → call(knowledge…) → call(literature…) → finish`。来源完整分页，宿主负责理解；前阶段为空或失败仍检查后阶段。状态回执只确认实际工具结果，不接受宿主口头宣称成功。按配置时刻安排官方日程：日报/周报都在18:00时只需每天18:00触发，周五先完成日报，再次plan在同一轮取得周报。改时刻须同步同一官方任务。报告每轮最多3篇，知识/文献各按所选项目数预算分批继续，避免只覆盖前三个项目；实际完成时间取决于材料量和宿主可用性。
5. 暂停时先关闭网站总开关，必要时通过官方工具暂停同一任务；恢复时复用 id 再绑定。关闭/撤回授权后的迟到结果不能覆盖内容。人工稿与正式报告受保护；知识改旧内容要确认，文献不会擅自下载。

源码入口（从仓库根目录运行；`status` 只读，不启用任务）：

```powershell
python -I -B plugins/llmwiki-lite/scripts/research_cycle.py --home "<网站使用的 LLMWIKI_HOME>" status
```

安装缓存尚未发布新工具时，计划仍可使用上述源码入口，不需要改缓存。它是一轮一退的确定性 CLI，不是后台模型服务；没有系统定时器、独立模型 API 或 `codex exec` 兜底。宿主/电脑不在线不保证执行；恢复后按报告补齐窗口和知识/文献未处理来源继续。首次非手工触发验收以真实计划回执和产物为准，不将测试夹具的回答当模型真实生成证据。

## 已确认界面的源码接入（2026-09-20）

共享侧栏按“科研进度、科研记录、日报与周报、知识库、文献、代码”排列，项目切换保持栏目；报告仍是工作台文档。记录、报告和文献使用原型平面行列表；进度采用紧凑“继续上次”、周轴与直接完成圆圈，任务按高/中/低优先级排序，任务详情使用一个截止日与统一描述。知识库为真实目录/正文双栏，报告类型在左、筛选在右，高级搜索默认收起。连续笔记和报告复用同一编辑交互，业务存储保持独立。原型原稿保存于 `openspec/changes/research-daily-weekly-reports/reference/approved-workbench.html`；视觉/主题验收见 `openspec/changes/workbench-interface-alignment/verification.md`，Git 写操作范围仍以对应 change 为准。

日报/周报“生成设置”和知识库“维护设置”进入已有设置；“查看更新”打开真实候选，不执行模型或伪造内容。知识页保持只读，页面信息可从正文工具栏展开，不为了复制原型按钮而新增任意 Wiki 写入入口。自动总结不会修改用户任务状态。原型对比测试在同一视口与125%比例下恢复原型使用的 Lucide 图标，测量真实 DOM 并截图；不修改批准原稿，也不把示意数据写入用户项目。

本次 UI 修改不改变已绑定的真实计划、采集授权或用户设置，不修改安装缓存。静态资源与旧 Python 进程混用时会提示重启；更新后需重启源码网页服务，真实定时首轮仍须单独核验。

## 可视化代码管理（开发源码）

项目内「代码」复用工作台导航，显示真实 Git 分支与提交父子关系。点击节点查看版本/文件差异；「查看并保存」只保存勾选文件的完整当前内容（包括未暂存部分），不夹带未选的已暂存文件，也不会自动上传。支持从历史节点创建分支（不自动切换）、干净工作区切换/合并分支、逐文件解决普通 UTF-8 冲突，以及明确取消本次合并。

版本详情按批准原型直接对齐：默认仅显示标题、短哈希、作者和时间，点击元数据行可展开完整信息；文件列表显示真实新增/删除行数，二进制不伪造行数。紧凑差异省去重复文件头，保留不连续片段的范围标记；建分支/恢复操作紧跟差异，不随长历史沉到页面末端。可点击「放大」在大窗口只读查看原始 diff，Esc/关闭返回原节点和文件；完整元数据包含注册仓库的本地绝对路径。代码栏图标恢复批准 HTML 原型几何，选中版本以节点外圈标识。

「恢复到此版本」创建新恢复提交，保留旧历史；不是强制回退。涉及文件覆盖风险或预览后仓库变化时拒绝执行，保留输入供重新确认。首次缺作者身份可在页内补填，限定当前仓库。

「pull」先选择已有远端/分支并检查，再确认更新代码；检查本身不修改工作区。「push」再次显示固定分支、远端地址与本地缓存下的提交清单，只普通上传该分支已保存的版本，不上传未提交文件或其他分支/标签。没有自动联网、强推、自动重试或终端弹窗；认证/自定义 Hook/签名等不支持策略应在原工具配置后重试，不偷偷绕过。

支持已登记的普通及 linked worktree；写操作绑定当前工作树，使用共同 Git 目录锁，其他工作树占用的分支不能强行切换。子模块、LFS/自定义过滤器、稀疏/部分克隆等仍限浏览。机器状态需在仓库外或被忽略；网页不会修改 `.gitignore`。Git 不触发任务完成、日报/知识/文献推理，真实自动计划保持原配置。

代码页读取以“简单、优雅、好用”为准：合并仓库元数据和 HEAD 查询，只在需要时读取本次请求的 Git 配置，属性安全检查按目录去重；首次状态/版本图先后返回不会重复加载同一详情，窗口聚焦不会取消正在进行的读取。显式刷新、写入后的刷新仍重新读取，版本图的前后快照复核和所有写入保护保持不变。已访问栏目保留原 DOM，按项目保存已读展示快照，返回时立即显示真实内容并后台核对本地仓库；不可变提交详情按 OID 缓存，最多48条，首屏完成后串行预取前6个节点。展示快照不是写操作依据，读取失败保留旧内容并明确提示；不使用动画掩盖等待，不后台联网。首次从未读取的数据仍需真实 I/O。

栏目导航保持浏览器前进/后退和项目上下文；离开时暂停页面轮询，未保存输入/上传/写操作不会被静默丢弃。编辑详情仍保留原编辑器，不为了即时切换重做笔记交互。

隔离验证：`python -B plugins/llmwiki-lite/tests/run_workbench_navigation.py`（已读栏目真实内容显示及跨项目/前进后退）、`python -B plugins/llmwiki-lite/tests/run_code_cache_browser.py`（节点详情缓存和失效）、`python -B plugins/llmwiki-lite/tests/run_code_browser.py`（需已有 Playwright/Chrome；可用 `LLMWIKI_PLAYWRIGHT` 指定模块路径）。验收及未完成项见第五份 OpenSpec `verification.md`；本地开发实现不等于 GitHub/市场/缓存已发布。

### 项目启动与排序

打开网站根地址 `/` 时，本次网站会话已有选择则继续当前项目；没有选择或服务刚重启时才进入指定启动项目，未指定则进入排序后的第一项科研进度。无项目时进入项目总览。点击「野人工作台」始终打开 `/projects` 项目总览，不带用户跳入其他项目。

在总览拖动项目前的文件夹图标即可排序，松开自动保存；键盘聚焦文件夹后可用上下方向键调整。点击项目右侧星标指定启动项目，再点取消。设置保存在本机注册表旁的 `settings.json`（`web_default_project_id`、`project_order`），不写入项目源码，也不改助手使用的 `current_project_id`。未手动排序时保留原名称排序；手动排过后新项目追加在末尾，取消注册默认项目后回到剩余第一项。保存失败回退本次排序并明确提示，不假装保存成功。跨栏目缓存里的项目菜单同步顺序。

隔离验证：`python -B -m unittest discover -s plugins/llmwiki-lite/tests -p test_project_preferences.py`；`python -B plugins/llmwiki-lite/tests/run_site_browser.py --refinements` 覆盖拖动、默认入口、真实Git元数据及diff放大。网页没有账户登录；这里的“启动项目”用于新网站会话从根地址初始化，不改浏览器自行恢复的具体深链接。

### 野人工作台：显示与演示项目

网站品牌为“野人工作台”，应用内默认 125% 显示（浏览器仍可保持 100%），含侧栏、文字、按钮、文档和版本图。窄屏导航及各内页断点同步调整；打印使用正常尺寸。进度默认显示周一至周日，可切换两周/四周。

演示项目必须使用仓库之外的普通代码副本、独立 Wiki/state 和独立无 remote 的 Git 仓库，不能用主仓库 worktree/硬链接。演示任务和笔记显式标注，不加入已有对话采集与日报/周报计划；创建副本不得改主仓库内容。网页品牌改变不重命名插件 ID、项目目录或用户历史正文。

### Windows 桌面静默启动

桌面快捷方式使用 `pythonw.exe -I -B <插件绝对路径>/scripts/desktop_launcher.py --home <数据目录> --port <端口>`，起始位置为源码仓库。不要用 `.bat` 或可见 PowerShell 窗口包裹。双击后复用原有后台启动函数：网站已运行则只打开浏览器，否则以无控制台子进程启动并等待健康检查，再打开浏览器；启动器随后退出。正常启动没有提示框，仅失败时显示错误与 `logs/desktop-launch-error.log` 路径。

关闭网页不会停止后台 Python 服务；再次双击快捷方式即可重新打开。没有创建系统服务、开机自启、定时任务或模型后台进程。首次切换时应先停止旧前台源码服务，否则快捷方式只会复用它，不会擅自结束现有进程。快捷方式依赖设置时的 Python/源码路径，移动或删除目录后需重建。

### 科研待办体验优化（2026-09-21）

- 项目选择下拉只列项目，点击其他位置或 Escape 关闭。项目总览支持右键/更多菜单重命名与移除；重命名不改变 ID/目录，移除只取消注册，所有文件保留。
- 设置、项目管理和报告页保留进入时的项目；本次网站会话内改默认项不抢占当前选择。重新启动服务后从根地址进入，才重新按默认/排序首项初始化。
- 任务描述支持 Ctrl+V 截图与 Markdown，不再要求手选开始时间或“进行中/卡住”。旧任务两栏和排期数据仍保留、兼容读取，不会自动将任务标成完成。
- 笔记、日报/周报以自动保存为主，不再显示常驻保存、代码、批注插入按钮；编辑时看得到图片，标题输入立即更新。既有批注/历史不删除。报告正式确认仍需主动点击。
- 自动整理设置展开说明：计划决定“何时整理”，项目会话关联决定“用哪些资料”。保存设置不创建后台进程、不自动连接计划；是否已连接以真实状态为准。

定向验证：`tests/test_project_management.py`、`tests/run_project_management_browser.py`（测试目录位于 `plugins/llmwiki-lite` 下），进度/编辑器/Git 测试使用临时项目，不对真实仓库做写入测试。

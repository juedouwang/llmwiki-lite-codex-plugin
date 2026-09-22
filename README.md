# LLM Wiki Lite 科研助手（多平台）

这是与旧 `llmwiki-research` 架构完全分离的轻量版科研与项目知识助手，同一份源码同时支持 **Codex**、**Claude Code** 和 **opencode**。

- **AI 助手（Codex / Claude Code / opencode）** 负责理解、选择证据、推理和 Markdown 写作；
- **Skill** 负责给 AI 助手清晰、可独立执行的工作指引；
- **MCP** 只提供注册、扫描、检索、读取、快照、状态和安全写入等重复机械劳动；
- **Hook** 只记录可能变化的路径提示，三个平台语义一致且始终 fail-open；
- **Web** 是简体中文优先的本地科研知识工作台。

程序不会生成固定十五类空页面，也不会重新引入复杂 Research Core。

## 安装

### Codex

从 GitHub 安装 Marketplace：

```powershell
codex plugin marketplace add https://github.com/juedouwang/llmwiki-lite-codex-plugin
codex plugin add llmwiki-lite@llmwiki-lite
```

Marketplace 清单位于 `.agents/plugins/marketplace.json`，Plugin 位于 `plugins/llmwiki-lite/`。

### Claude Code

```text
/plugin marketplace add juedouwang/llmwiki-lite-codex-plugin
/plugin install llmwiki-lite@llmwiki-lite
```

Marketplace 清单位于 `.claude-plugin/marketplace.json`。Claude Code 会自动加载插件的 `skills/`、`hooks/hooks.json` 和 `.mcp.json`。

### opencode

先克隆本仓库，然后运行安装脚本：

```powershell
git clone https://github.com/juedouwang/llmwiki-lite-codex-plugin
python llmwiki-lite-codex-plugin/plugins/llmwiki-lite/opencode/install.py
```

脚本把 Skill 路径、`mcp.llmwiki` 与变更 Hook 合并写入 `~/.config/opencode/opencode.json`；支持 `--scope project`、`--config`、`--dry-run` 和 `--uninstall`，详见 `plugins/llmwiki-lite/opencode/README.md`。

三个平台都要求 `PATH` 中存在 Python 3（默认命令 `python`，仅使用标准库）。Linux/macOS 若只有 `python3`，请建立 `python` 符号链接或改配置。

完整说明见 `plugins/llmwiki-lite/README.md`。

## 中文科研工作台

网页端面向中国研究生的日常科研使用：

- 统一浅灰侧栏与白色内容区，首页仅保留项目列表；
- 知识库使用可筛选的单列表，移除重复统计、分类卡片与常驻维护说明；
- 文献使用简洁列表，可按阅读状态、类型和收藏筛选，辅助说明默认折叠；
- 自动发现项目目录中的 PDF、EPUB、DOCX 和 HTML，并区分论文、补充材料、专利与报告；
- PDF 可直接在网页内阅读，也可与 AI 生成的简体中文精读 Markdown 左右双栏对照；
- 独立 `llmwiki-literature` Skill 负责“调研推荐 → 用户选择 → 下载原文 → 中文精读 → 网页入库”的完整流程；
- 时间戳仅作为笔记元数据保存，不写入正文或逐块展示；点击“更多 → 笔记信息”查看创建和修改时间（北京时间，到秒），导出放在 frontmatter。兼容旧笔记，隐藏此前自动生成的时间行，不删除用户手写内容。
- 科研记录支持连续 Markdown 笔记：直接书写、Ctrl+V 粘贴截图并在编辑时查看图片，自动保存与版本恢复；
- 检索论文、方法、实验、指标、结论和待办；
- 阅读页提供项目导航、本页目录、更新时间、预计阅读时长、复制路径和打印/PDF；
- 在网页修改人类可读 Wiki 目录与高级机器状态目录。

网页只监听 loopback，不修改源项目，也不提供任意 Markdown 正文编辑器；科研记录和日报/周报提供连续 Markdown 文档编辑入口。文献原文始终只读；系统不会自动翻译 PDF，也不会把不确定的阅读记录强行关联到论文。

## 任务规划 Skill 与一次性 CLI

使用 `llmwiki-task-planning` Skill，例如：“读取现有进度，安排这个项目本周剩余日期的任务，每天保留检查时间，写清产出和验收标准。”范围明确且已授权即可直接安排，不需要重复确认。Skill 先查已有父目标、每日任务、逾期项与待验收项，避免重复创建；所有相对日期按用户当前本地日期转成明确日期，本周已过去的天默认不回填。

优先调用 `llmwiki_daily_tasks_get(home?, day?)` 与 `llmwiki_task_write(home?, payload)`。MCP 不可用时，从仓库根运行无后台、无新窗口、仅标准库的一次性 CLI：

```text
python -B plugins/llmwiki-lite/scripts/task_cli.py projects
python -B plugins/llmwiki-lite/scripts/task_cli.py progress --project-id "<PROJECT_ID>"
python -B plugins/llmwiki-lite/scripts/task_cli.py list --date "<LOCAL_TODAY>"
python -B plugins/llmwiki-lite/scripts/task_cli.py write --args-file "<PAYLOAD_JSON_FILE>"
python -B plugins/llmwiki-lite/scripts/task_cli.py write --args-file -
```

`--home "<HOME>"` 可加在子命令前或后；省略时沿用 `LLMWIKI_HOME`/默认配置。`list` 不带 `--date` 时取后端本地今天；用户时区不同则传明确日期。`projects` 返回注册 ID；`progress` 读取指定项目任务摘要，不分析文件或推断完成。`list` 的 `tasks/overdue/completed` 都是带项目和父目标信息的公开任务，写入 revision 必须取最新 `revisions[project_id]`。

`write` 的文件或 stdin 是一个 **UTF-8 payload JSON 对象**，不包裹 MCP 的 `{home,payload}`。例如：

```json
{
  "project_id": "__workspace__",
  "action": "create",
  "revision": "<REVISION>",
  "task": {
    "title": "整理本周待讨论问题",
    "scheduled_date": "<LOCAL_TODAY>",
    "description": "交付带证据的问题清单；验收：每项明确背景与待决策点。"
  }
}
```

以上尖括号是必须替换的说明占位：`<LOCAL_TODAY>` 是执行当下核对的用户本地 `YYYY-MM-DD`；`<PROJECT_ID>` 是注册表精确 ID（不接受显示名/路径）；`<REVISION>` 是刚读取的项目 revision，此例用 `revisions["__workspace__"]`，初始值若为空字符串就保留为空；`<HOME>` 和 `<PAYLOAD_JSON_FILE>` 是实际目录/JSON 文件。不得照示例日期硬编码。临时任务使用 `__workspace__`，不必注册项目。

- `plan` 一次原子创建父目标和每日子任务，用稳定 `request_id` 实现同 key 幂等；`create/update/delete/restore` 操作单项。遇到 revision 冲突先重读，不自动覆盖或重试。
- **交付与验收分离**：`submit` 带真实 `id` 与 `summary`，验收状态转为 `review_state="pending"` 并关联科研记录，等待用户验收；不代表 `done`。CLI 写入固定 `actor="agent"`，拒绝 `accept/complete`、伪造 actor 或直接设为 done；`restore` 原样交给后端校验，当前恢复已完成任务仍须用户在网页操作。
- CLI 成功时 stdout 输出 JSON；失败时 stderr 输出 JSON，退出码 `1` 为后端/IO 错误，`2` 为输入错误。后端 API 未就绪时只报告未写入，不绕过接口改存储。规划推理仅由当前宿主模型完成，程序不调用模型或后台执行器。

完整 MCP/CLI JSON、日期替换和防重复规则见 [任务规划 Skill](plugins/llmwiki-lite/skills/llmwiki-task-planning/SKILL.md)。针对性验证：`python -B -m unittest discover -s plugins/llmwiki-lite/tests -p test_task_cli.py`（所有写入测试使用临时注册表和目录，不触碰用户任务）。

## 跨平台实现

- 三个平台共用同一套 `skills/`、`scripts/` 和网页端；
- `.mcp.json` 与 `hooks/hooks.json` 使用同一段跨平台启动代码：Codex 用插件根目录 / `${PLUGIN_ROOT}`，Claude Code 用 `${CLAUDE_PLUGIN_ROOT}`，opencode 由安装脚本写入绝对路径；
- opencode 使用 `opencode/llmwiki-hook.js` 实现同等语义的变更提示 Hook（`tool.execute.after`）；
- Hook 在三个平台都只记录 dirty-path 提示，失败时静默跳过，绝不影响宿主工具调用。

## 默认语言

人类可读 Wiki 和报告默认使用简体中文。代码、路径、命令、API/MCP 名、算法名、模型名、论文标题、数据集名和需要保持精度的技术术语保留英文。

## 存储

- 新用户未配置时：`<project-root>/wiki`
- 当前用户推荐的人类可读根目录：`E:\wiki_obsidian`
- 机器状态与人类 Markdown 分开保存
- 网页可修改默认位置和项目位置
- 移动时默认复制内容，旧目录永不自动删除

## 与旧版的区别

轻量版独立仓库：

```text
https://github.com/juedouwang/llmwiki-lite-codex-plugin
```

旧 Research 版仓库：

```text
https://github.com/juedouwang/llmwiki-research-codex-plugin
```

两条发布线使用不同仓库名、Marketplace 名和 Plugin 名，可以并行维护。

## 发布与更新

Codex 与 Claude Code 共用同一仓库和版本号，发布维护以 `main` 分支和版本标签（例如 `v0.3.3`）为准。每次推送 `main` 或提交版本标签都会由 GitHub Actions 自动执行验证；版本标签通过验证后自动创建 GitHub Release。

安装后更新：

```powershell
# Codex
codex plugin marketplace upgrade llmwiki-lite
codex plugin add llmwiki-lite@llmwiki-lite

# Claude Code（在会话内）
/plugin marketplace update llmwiki-lite
/plugin update llmwiki-lite

# opencode（更新仓库后重新运行即可）
python plugins/llmwiki-lite/opencode/install.py
```

重新安装后请新开对话，让新的 Skill 和 MCP 配置生效。

## 验证

```powershell
$env:PYTHONUTF8='1'
ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/opencode plugins/llmwiki-lite/tests
python -B plugins/llmwiki-lite/tests/smoke_test.py
python plugins/llmwiki-lite/opencode/install.py --dry-run
python C:/Users/lyn/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/llmwiki-lite
```

### 科研连续性（当前开发版）

原待办升级为持久化科研时间轴，并记录“上次做到哪 / 下一步”；笔记支持更直接的截图粘贴、拖放与连续输入。旧待办需显式导入，不改原日档。开发源码已实现授权项目对话/只读 Git 取材和“日报周报 → 知识维护 → 文献收录”共享入口；日报和周报均为工作台独立的多项目文档，不归档到某项目。内置任务必须经用户授权配置后启用；本机真实配置与首次运行状态见 `docs/research-workbench-handoff.md`，不能把连接成功或隔离测试当作无人值守运行成功。启用步骤见插件 README 的“连接共享内置计划”；边界与全站交互审查见 `docs/research-continuity.md`。

正常开发对话不会因承载定时任务而整条排除；采集器仅过滤自动任务轮次，共享来源层也保留其中的人工研发讨论（包括工作台自身的改进）。不在项目目录内的会话只能在用户指定项目、唯一会话及时间范围后精确关联；不会自动纳入同目录其他聊天。

### 科研工作台文档与实施入口

从[文档入口与 Agent 交接](docs/research-workbench-handoff.md)查看各功能的规格、执行材料和交接状态。`docs/` 只保留导航、产品原则、实现说明和周报模板；功能行为、技术方案、实施验收统一维护在对应 OpenSpec change，不再按旧总计划实施。规划完成不代表功能已验收或自动任务已启用。

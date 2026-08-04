# LLM Wiki Lite Codex Plugin 产品契约

- 当前基线：`0.3.0`
- 日期：2026-08-03
- Plugin：`plugins/llmwiki-lite/`
- 独立仓库：`juedouwang/llmwiki-lite-codex-plugin`

## 1. 产品定义

LLM Wiki Lite 是可以直接安装进 Codex 的本地科研与项目知识助手。

它只有四类组成：

1. **Skill / Prompt**：告诉 Codex 如何注册、理解、查询、维护项目和使用网页；
2. **MCP 工具**：承担注册、扫描、检索、读取、快照、状态、存储切换和安全 Wiki 写入等机械劳动；
3. **Hook**：以 fail-open 方式记录可能发生变化的项目路径；
4. **Web**：提供简体中文优先的科研知识浏览、检索和存储管理。

核心原则：

> Codex / LLM 负责语义理解、证据选择、推理和写作；程序只负责确定性的文件、注册、状态和展示操作。

## 2. 明确验收形态

Plugin 必须具有 Codex 可发现的标准结构：

```text
plugins/llmwiki-lite/
├─ .codex-plugin/plugin.json
├─ .mcp.json
├─ skills/
│  ├─ llmwiki-projects/
│  ├─ llmwiki-understand/
│  ├─ llmwiki-query/
│  ├─ llmwiki-maintain/
│  ├─ llmwiki-literature/
│  └─ llmwiki-web/
├─ hooks/
├─ scripts/
├─ tests/
└─ README.md
```

六个 Skill 必须可以独立执行，不能把全部流程塞进一个巨大 `SKILL.md`。

## 3. 职责边界

### 3.1 Codex / LLM

负责：

- 理解研究问题和真实项目；
- 选择需要阅读的论文、笔记、代码、配置、测试、数据和实验记录；
- 区分事实、推断、冲突和未知；
- 创建少量有持续价值的 Markdown；
- 回到真实来源核验精确数值、实现细节和实验结论；
- 只更新受变化影响的页面。

### 3.2 MCP

只提供可复用的机械工具，不替代科研推理：

- 项目注册、查询、选择、取消注册；
- Wiki 与机器状态位置管理；
- 有边界的文件枚举、搜索和读取；
- 快照、哈希和变化提示；
- Wiki 页面列表、结构检查和安全写入；
- 本地网站启动。

### 3.3 Hook

Hook 只产生不可信的 dirty-path 提示。它不得自动注册、理解项目、改写 Wiki 或阻塞 Codex。

### 3.4 Web

Web 是主要的人机浏览入口，但不是第二个知识引擎。它读取注册状态和 Markdown，不自行生成科研结论。

## 4. 项目注册与存储

每个项目具有：

- `source_root`：真实研究或代码项目；
- `wiki_root`：人类可读 Markdown；
- `state_root`：快照、哈希、事件和配置。

规则：

1. 注册只建立稳定项目身份和存储位置，不代表已扫描或理解；
2. 新用户无全局设置时，默认 Wiki 为 `<project-root>/wiki`；
3. 配置外部根目录后，每个项目使用独立子目录；
4. 当前用户推荐示例是 `E:\wiki_obsidian`；
5. 网页允许修改全局默认和项目级位置；
6. 位置切换默认复制现有内容；
7. 非空目标目录不得被静默合并覆盖；
8. 旧目录永不自动删除。

## 5. Wiki 内容约定

- Markdown 是唯一人类可读知识源；
- 默认使用简体中文，符合中国研究生阅读和科研写作习惯；
- 代码、路径、命令、API/MCP 名、Schema 字段、算法名、模型名、论文标题、数据集名和精度敏感术语保留英文；
- 页面应回答真实问题，并说明证据、条件、结论、局限和未知；
- 优先少量高价值页面，不生成固定十五类页面或空模板；
- 网页科研分类只是动态浏览视图，不约束实际目录和页面数量。

## 6. 核心工作流

### 6.1 注册

`llmwiki-projects` 解析或注册项目，并确定三个根目录。停止在身份和位置管理边界。

### 6.2 理解

`llmwiki-understand` 让 Codex 做有目标的调查，读取必要来源并创建最少但有用的页面。

### 6.3 查询

`llmwiki-query` 先用 Wiki 定位，再回到真实项目核验代码、配置、数字、实验条件和引用。

### 6.4 维护

`llmwiki-maintain` 用快照确定变化候选，由 Codex 判断影响，只更新真正过期的页面。

### 6.5 文献

`llmwiki-literature` 独立完成文献调研推荐、等待用户选择、下载授权原文、中文精读、`paper_file` 绑定和网页呈现。推荐阶段不得擅自批量下载；下载必须保存到注册项目并遵守来源访问权限。

### 6.6 网站

`llmwiki-web` 启动或复用 loopback 服务，提供中文研究项目、文献中心、检索、阅读和存储设置。

## 7. 中文科研工作台验收

### 7.1 首页

必须显示：

- “中文科研知识工作台”；
- “我的研究项目”；
- 已注册项目数、知识页数、源文件数、待核对变化数；
- 项目卡片、最近扫描和人类可读 Wiki 位置。

### 7.2 项目研究台

必须显示：

- 项目研究台和研究内容；
- 按路径与标题关键词动态归类的科研流程视图；
- 最近更新；
- 待核对变化；
- 建议下一步；
- 可复制给 Codex 的简体中文维护 Prompt；
- 折叠显示源项目、Wiki 和机器状态路径。

分类可包含研究总览、文献与阅读、方法与实现、数据与样本、实验记录、结果与分析、结论与问题、计划与待办、论文与成果、决策记录、资料索引、研究笔记和其他页面，但只作为浏览视图。

### 7.3 文献中心

必须支持：

- 使用类似 Obsidian 文件库的固定左侧栏，按“全部文献、已精读、待精读”和文献类型快速筛选；
- 支持搜索、筛选后结果计数以及卡片/列表视图切换；
- 自动发现注册项目 `source_root` 中的 PDF、EPUB、DOCX、HTML/HTM 文献，并保持原文件只读；
- 把文献标识为学术论文、补充材料、专利文献、报告或其他文献格式；
- PDF 在站内直接阅读，支持浏览器 Range 请求和新窗口打开；
- 原文与 Codex 生成的简体中文辅助阅读 Markdown 左右双栏对照，窄屏时上下排列；
- 同一论文存在多份可靠候选笔记时允许切换；
- 优先使用辅助阅读 frontmatter 中的 `paper_file`、`paper_path`、`source_file` 或 `sources` 精确关联；
- 没有显式关联时只做保守的标题/路径匹配，置信度不足必须保留为“待关联”，不得强行配对；
- 没有辅助阅读时提供可复制给 Codex 的简体中文精读 Prompt；
- 辅助阅读应区分论文原文事实、LLM 解释和待验证推断，精确公式、表格、数值与引用仍需回到原文核验；
- 不自动翻译或改写 PDF，不把 LLM 笔记伪装成论文原文；
- 提供“推荐 → 选择 → 下载 → 精读 → 对照阅读”的可复制 Codex 工作流，其中用户选择是下载前的明确边界。

推荐的辅助阅读 frontmatter：

```yaml
---
title: "Exact Paper Title 中文精读"
type: literature-note
language: zh-CN
paper_file: references/path/to/paper.pdf
sources:
  - references/path/to/paper.pdf
---
```

### 7.4 Markdown 阅读

必须支持：

- 完整 UTF-8 Markdown 正文；
- frontmatter、标题、段落、列表、任务、引用、callout、表格、代码、链接、图片和 `[[wikilinks]]`；
- 中文面包屑和返回项目研究台；
- 项目页面筛选和科研类别分组；
- 本页目录、更新时间、字词数、预计阅读时长；
- 复制页面路径和打印/导出 PDF。

### 7.5 科研检索

必须支持：

- 跨项目或按项目筛选；
- 检索标题、路径和 Markdown 正文；
- 中文结果说明和科研类别标签；
- 对论文、方法、实验、结果、结论和计划的中文示例引导。

### 7.6 存储设置

必须使用用户可理解的名称：

- 人类可读 Wiki 默认根目录；
- 人类可读 Wiki 目录；
- 机器状态目录（高级设置）；
- 项目目录。

## 8. 安全边界

- 网站只绑定 `127.0.0.1`、`localhost` 或 `::1`；
- 所有路径必须规范化并阻止穿越；
- Wiki 写入只发生在 `wiki_root`；
- Wiki 附件只来自对应 `wiki_root`；
- 文献原文可以从对应注册项目的 `source_root` 读取，但只允许 PDF、EPUB、DOCX、HTML/HTM 白名单；
- 文献读取必须拒绝路径穿越和路径中的符号链接；PDF 只以内嵌方式流式读取并支持 Range，非 PDF 作为附件，源项目 HTML 不得在站内执行；
- 文献原文始终只读，网站不得修改、移动或删除；
- Markdown 原始 HTML 必须转义，不能执行注入脚本；
- 读取、搜索、表单和文件大小都有边界；
- 不独立发送外部网络请求；
- 存储切换和取消注册不删除旧文件。

## 9. 明确不做

- 不实现独立聊天机器人；
- 不实现替代 LLM 的 Research Core；
- 不要求固定十五类知识页；
- 不批量生成空页面；
- 不实现任意 Markdown 网页编辑器；
- 不引入 React、Vue、Node 构建链、数据库或外部 CDN；
- 不实现跨用户云服务；
- 不为了未来可能需求预建复杂中间层。

## 10. 最低验证

```powershell
ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/tests
python -B plugins/llmwiki-lite/tests/smoke_test.py
python C:/Users/lyn/.codex/skills/.system/skill-creator/scripts/quick_validate.py <skill-dir>
python C:/Users/lyn/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/llmwiki-lite
```

还必须检查所有产品文件为无 BOM 的严格 UTF-8，不含 `U+FFFD` 或乱码文本。
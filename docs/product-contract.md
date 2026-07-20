# LLM Wiki Lite Codex Plugin 产品契约

- 状态：轻量架构 v0.2 基线
- 日期：2026-07-20
- 产品目录：`plugins/llmwiki-lite/`
- 旧设计归档：the legacy repository `llmwiki-research-codex-plugin`

## 1. 产品定义

LLM Wiki 是一个可直接安装到 Codex 的本地 Plugin。它不实现独立科研 Agent，也不建设替代 LLM 推理的复杂 Research Core。

产品只包含四种简单构件：

1. **Skill / Prompt**：指导 Codex 如何管理项目、理解项目、查询、维护 Wiki 和使用网站；
2. **MCP 工具**：承担注册、扫描、搜索、读取、hash、增量检测、存储切换和 Wiki 写入等机械劳动；
3. **Hook**：以 fail-open 方式记录可能发生变化的路径；
4. **Web**：把 Markdown Wiki 完整、简洁地显示出来，并提供项目注册与存储位置设置。

核心原则：

> Codex/LLM 负责理解、选择、推理和写作；程序只负责确定性的文件、注册、状态和展示操作。

## 2. 验收形态

第一验收对象是一个合法、可安装、可被 Codex 发现的 Plugin：

```text
next/llmwiki-lite-codex-plugin/plugins/llmwiki-lite/
├─ .codex-plugin/plugin.json
├─ .mcp.json
├─ skills/
│  ├─ llmwiki-projects/
│  ├─ llmwiki-understand/
│  ├─ llmwiki-query/
│  ├─ llmwiki-maintain/
│  └─ llmwiki-web/
├─ hooks/
├─ scripts/
├─ tests/
└─ README.md
```

安装后，用户直接对 Codex 说：

- “注册当前项目，把 Wiki 放到指定目录。”
- “理解这个项目并建立有实际内容的 Wiki。”
- “这个实现具体如何工作？请核验代码。”
- “只更新受最近变化影响的知识页。”
- “打开 Wiki 网站。”

Codex 是唯一任务执行主体。网站是阅读和设置入口，不是另一个 Agent。

## 3. 职责边界

### 3.1 Codex / LLM

Codex 负责所有语义工作：理解目标、判断项目类型、选择文件、阅读源码、追踪调用关系、总结事实、识别矛盾、决定页面结构、编写 Markdown、回答问题和判断何时停止。

任何“哪个文件重要”“项目创新是什么”“实验支持什么结论”等语义判断，不得由 Python 机械规则伪装完成。

### 3.2 五个独立 Skill

不再把所有功能塞进一个大型 `SKILL.md`。五个 Skill 可以独立触发和执行：

| Skill | 职责 |
|---|---|
| `llmwiki-projects` | 注册、列出、选择、取消注册和存储位置管理 |
| `llmwiki-understand` | 首次理解项目并创建少量有用 Wiki |
| `llmwiki-query` | 结合 Wiki 与真实源文件回答问题 |
| `llmwiki-maintain` | 增量变化确认、影响分析和最小更新 |
| `llmwiki-web` | 启动本地网站、浏览页面和修改存储位置 |

每个 Skill 只包含自己的工作方法和必要工具说明，避免每次加载一份臃肿总流程。

### 3.3 MCP

MCP 只提供机械能力：

- 全局项目注册表和当前项目选择；
- `source_root`、`state_root`、`wiki_root` 配置；
- 文件枚举、忽略规则、hash 与变化比较；
- UTF-8 文本搜索和分段读取；
- Wiki 页面安全写入、列出、断链和来源路径检查；
- 全局设置更新；
- loopback 网站启动。

MCP 返回事实，不生成固定知识包，不决定页面分类，不生成项目语义总结。

### 3.4 Hook

Hook 只记录成功编辑后的 dirty-path 提示。它既不注册项目，也不扫描、总结或更新 Wiki。Hook 缺失、失败或数据不完整时，Codex 使用 snapshot 重新确认真实变化。

### 3.5 Web

Web 是本地可视化层：

- 读取注册表和 Markdown；
- 显示项目、页面、状态和搜索结果；
- 渲染 Markdown 的常用可见元素；
- 修改全局默认 Wiki 根目录；
- 注册项目；
- 修改单个项目的 Wiki 和状态目录；
- 取消注册但不删除文件。

Web 不总结项目、不生成语义内容、不编辑 Markdown 正文，不成为新的知识真相源。

## 4. 项目注册与存储模型

### 4.1 LLM Wiki Home

默认位置：

- Windows：`%LOCALAPPDATA%\LLMWiki\`
- 其他系统：`~/.local/share/llmwiki/`
- 环境变量 `LLMWIKI_HOME` 可以覆盖。

```text
<LLMWIKI_HOME>/
├─ registry.json
├─ settings.json
└─ projects/
   └─ <project_id>/
      └─ state/
```

注册记录保持简单：

```json
{
  "id": "my-project-a13f72c8",
  "name": "My Project",
  "source_root": "E:\\GitHub\\my-project",
  "state_root": "...\\projects\\my-project-a13f72c8\\state",
  "wiki_root": "E:\\wiki_obsidian\\my-project-a13f72c8"
}
```

项目 ID 使用名称 slug 加规范化源路径 hash，避免同名项目冲突。注册只建立身份和空存储，不等于扫描或理解完成。

### 4.2 三个根目录

- `source_root`：Codex 要理解的真实项目；
- `state_root`：`config.json`、`manifest.json`、`events.jsonl` 等机器状态；
- `wiki_root`：人和 Agent 阅读的 Markdown 与 Wiki 附件。

三者必须明确分工。机器状态不得混入人类知识目录，人类总结不得写入机器状态目录。

### 4.3 默认 Wiki 位置

选择顺序：

1. 用户注册时显式指定 `wiki_root`；
2. 全局存在 `default_wiki_root` 时，使用 `<default-wiki-root>/<project_id>`；
3. 其他新用户默认使用 Codex 打开项目下的 `<project-root>/wiki`。

当前开发机器可把 `default_wiki_root` 配置为 `E:\wiki_obsidian`。该路径只属于本机设置，绝不写死成所有用户默认值。

### 4.4 位置修改

CLI/MCP 和网页都可以修改项目位置。默认规则：

- 复制现有内容后切换；
- 目标目录非空时拒绝合并或覆盖；
- 旧目录保留；
- 不删除源项目、旧 Wiki 或旧状态；
- 取消注册也只删除注册关系，不删除文件。

## 5. Wiki 内容约定

Wiki 不规定十五类页面。除自动维护的 `index.md` 外，只在有长期价值时创建页面。

可选轻量 frontmatter：

```yaml
---
title: Model Architecture
sources:
  - src/model.py
  - docs/method.md
updated_at: 2026-07-20
confidence: medium
---
```

正文可使用 `[[Page Name]]`、项目相对来源路径、事实/推断/未知说明和任意适合内容的结构。

禁止：

- 为满足模板建立空页面；
- 把文件名猜测写成确定事实；
- 用机械分类代替阅读；
- 复制整份源码到 Markdown；
- 用页面数量或扫描百分比冒充理解质量。

## 6. 核心工作流

### 6.1 注册

1. 解析 Codex 当前目录；
2. 如果未注册，确定 `source_root`；
3. 按默认策略或用户指定路径确定 `wiki_root`；
4. 确定独立 `state_root`；
5. 写入注册表并初始化空机械状态；
6. 明确说明尚未扫描或理解项目。

### 6.2 理解项目

1. 读取已有 Wiki 和状态；
2. 小范围浏览 README、入口、配置、测试和核心模块；
3. 搜索与用户目标相关的符号；
4. 由 Codex 选择并深读真实文件；
5. 形成有来源的解释；
6. 只创建少量有用页面；
7. 检查链接和来源路径。

### 6.3 查询

1. 以 Wiki 为索引；
2. 对代码行为、数字、配置、冲突和近期变化回到真实文件核验；
3. 直接回答并给出项目相对依据；
4. 仅在答案有长期价值时更新 Wiki。

### 6.4 增量维护

1. 读取 Hook 提示；
2. 用 snapshot hash 确认变化；
3. 由 Codex 判断受影响页面；
4. 只重读必要来源；
5. 只更新必要页面。

### 6.5 网站

1. 通过 MCP 启动或复用本地服务；
2. 浏览项目和所有 Markdown 页面；
3. 搜索 Wiki；
4. 在设置页修改默认或项目级存储位置；
5. Markdown 内容继续由文件层维护。

## 7. MCP 工具清单

项目与设置：

- `llmwiki_project_register`
- `llmwiki_project_list`
- `llmwiki_project_get`
- `llmwiki_project_select`
- `llmwiki_project_storage_update`
- `llmwiki_project_unregister`
- `llmwiki_settings_get`
- `llmwiki_settings_update`
- `llmwiki_web_start`

文件与 Wiki：

- `llmwiki_init`
- `llmwiki_status`
- `llmwiki_snapshot`
- `llmwiki_files`
- `llmwiki_search`
- `llmwiki_read`
- `llmwiki_wiki_write`
- `llmwiki_wiki_list`
- `llmwiki_wiki_check`

所有工具必须限制结果尺寸、验证参数、规范化路径并拒绝越界访问。

## 8. 网站验收

网站必须：

- 仅绑定 `127.0.0.1` / loopback；
- 页面简洁、无不必要动画和装饰；
- 显示全部注册项目和三个根目录；
- 显示 Wiki 页面列表、最近快照和变化提示；
- 支持跨项目及项目内搜索；
- 直观显示 frontmatter、标题、段落、无序/有序列表、任务列表、引用、Obsidian callout、代码块、行内代码、链接、图片、表格、分隔线与 `[[wikilinks]]`；
- 转义原始 HTML，防止 Markdown 注入脚本；
- 阻止路径穿越；
- 支持网页修改全局默认 Wiki 根目录和项目 `wiki_root` / `state_root`；
- 默认复制后切换且保留旧目录；
- 不编辑 Markdown 正文。

## 9. 安全边界

- 源项目路径、状态路径、Wiki 路径和网站附件路径都必须规范化；
- Wiki 写入只发生在配置的 `wiki_root`；
- 搜索和读取必须有条数、文件大小、行数和字符上限；
- 网站只在 loopback 提供服务；
- 服务端不进行独立外部网络发送；
- 远程 Markdown 图片不自动加载，只显示为外部链接；
- Hook 永远 fail-open；
- 存储切换和取消注册永远不删除旧文件。

## 10. 明确不做

- 不实现独立聊天机器人；
- 不实现无 LLM 的项目理解 Core；
- 不要求十五类知识页面；
- 不实现 Claim/Evidence 生命周期；
- 不实现复杂任务授权和计划状态机；
- 不实现任意文件 Web 编辑器；
- 不实现跨用户云服务或外部数据库；
- 不在当前版本打包私有 Python Runtime；
- 不为了未来可能需求预建中间层。

## 11. 最低验收场景

1. Plugin、五个 Skill、MCP 和 Hook 能被 Codex 发现；
2. 同一源项目重复注册保持稳定身份；
3. 未配置全局根目录的新用户得到 `<project>/wiki`；
4. 本机配置外部根目录后，新项目得到其独立子目录；
5. Wiki 和状态目录可安全复制后切换，旧目录保留；
6. Codex 能读取真实项目并建立少量有内容页面；
7. 查询能回到真实源文件核验；
8. 增量维护只更新受影响页面；
9. 网站能展示 Markdown、搜索页面和修改位置；
10. 路径穿越、非 loopback 绑定和非空目录覆盖被拒绝。

## 12. 开发判断原则

每项新功能都先回答：

1. 这是 LLM 的语义工作，还是程序的机械工作？
2. 当前真实用户任务没有它是否无法完成？
3. 能否用一个 Skill 规则或一个小工具完成？

语义工作交给 Codex；没有真实阻塞不实现；小工具足够时不建立新框架。

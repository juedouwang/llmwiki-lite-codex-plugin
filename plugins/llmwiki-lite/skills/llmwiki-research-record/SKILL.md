---
name: llmwiki-research-record
description: "Proactively capture durable research knowledge from the conversation into the project's research record without waiting to be asked: decisions with reasons, experiment results with configs and numbers, new findings, pitfalls and fixes, conventions, open questions, and agreed next steps. Use when a piece of research work concludes, when a recording checkpoint asks you to review recent work, when the user says 记录/记一下/保存/记住, or when the user wants to review past records or continue from the last session. Also the entry point for daily/weekly reports and the shared schedule. Never records chit-chat, general explanations, or unconcluded attempts."
---

# LLM Wiki 智能科研记录

目标：用户和助手一起工作时，由助手判断哪些内容值得长期保留，写进项目的科研记录；下次无论用哪个助手，都能接着干。用户不需要说“记录”，也不要征求是否记录。你负责判断和撰写；MCP 只做字段校验和 Markdown 追加。

## 自动机制

- **会话开始**：在已注册项目目录（源码目录或 Wiki 目录）中启动会话时，Hook 注入记录规则、项目路径、最近记录和最近的下一步。记录规则以插件的 `templates/capture-rules.md` 为准，本文件只做补充。
- **记录检查**：上次记录之后又积累了一段工作（工具调用或对话轮次达到阈值）却没有记录时，Stop Hook 会请你回顾一次。有值得记的就记，然后只回复一行「📝 已记录：<标题>」；没有就只回复「（本段无需记录）」。不要借机继续做别的工作。回顾后没有记录，下次检查的间隔会自动拉长；回复以提问结尾时，检查推迟到用户回答之后。
- **开关**：`llmwiki_settings_update(capture_mode=...)`：`auto`（默认，规则 + 检查）、`passive`（只注入规则）、`off`（全关）。环境变量 `LLMWIKI_CAPTURE` 可临时覆盖。用户说“别再提醒记录”“关掉自动记录”时照做，并告诉用户怎么恢复。
- **预览**：`python <插件目录>/scripts/capture_hook.py --preview <项目目录>` 显示会话开始时会注入的内容。

## 判断：记什么

值得记录（满足任一）：

- **决策**：在几个方案里定下了一个——选了什么、为什么、放弃了什么。
- **结果**：实验、测试、仿真、上板得到的数值或现象，附配置、数据集、命令或输出路径。
- **发现**：对方法、数据、代码、硬件或论文的新认识，标明已验证还是推测。
- **踩坑**：报错或异常的根因和解决办法；失败的尝试和失败原因。
- **约定**：以后都要遵守的参数、环境、命名、流程，或用户明确表达的偏好和原则。
- **问题与下一步**：新出现的重要未决问题；用户认可的具体后续行动。

判断标准：一周后，用户或另一个助手会不会因为不知道这件事而重复劳动、重复踩坑或做出矛盾的决定？会，就记。

不记录：通用知识讲解、闲聊、没有结论的中间过程、操作流水、代码本体（写路径即可）、密钥和隐私、已经记过且没有变化的内容。

边界情况：

- 用户说“记一下”“记住”“这个很重要”“以后都这样”→ 一定记（通常是约定或决策）。
- 失败的实验只要弄清了失败原因 → 记为踩坑；原因不明但现象重要 → 记为问题。
- 用户认可、准备去验证的想法 → 记为发现，并标“推测/待验证”。
- 同一主题已有记录且有变化 → 写一条后续记录，title 体现变化，understanding 说明和上次有什么不同。
- 用户指出记错了 → 写一条以“更正：”开头的新记录，不改写旧条目。

## 写法

好的示例（写法示例，不是任何项目的真实结论）：

- 决策 —— title：`粗配准改用 FPFH+RANSAC`；understanding：`初始位姿误差大时 ICP 容易陷入局部最优；改为 FPFH+RANSAC 做粗配准，ICP 只做精配准。放弃了直接调大 ICP 搜索半径的方案（耗时约 3 倍且仍不稳定）。`；tags：`["决策", "点云配准"]`。
- 结果 —— title：`label smoothing 0.1 使验证集 top-1 从 76.1% 到 77.4%`；understanding：`只改 label smoothing，其余同 configs/base.yaml；3 个随机种子均值 77.4%±0.2%，基线 76.1%。`；evidence：`["configs/ls01.yaml", "runs/ls01/metrics.csv"]`；tags：`["结果", "训练技巧"]`。
- 踩坑 —— title：`Open3D 体素下采样使用点云自身单位`；understanding：`点云坐标是毫米时 voxel_size=0.05 几乎不降采样，后续特征计算很慢；应按毫米设 voxel_size=5，或先把点云换算成米。`；tags：`["踩坑", "点云预处理"]`。

差的示例：title `今日讨论`，understanding `讨论了配准的一些问题，做了实验`——不具体，离开对话看不懂，也没有证据。

`llmwiki_record_write` 字段：

- `project_root` / `project_id`：用会话开始注入的值；没有注入时先解析项目（见下）。
- `title`：具体到能被搜索到。
- `understanding`（必填）：离开对话也能看懂的结论；数值、路径、命令、论文名保持原文。
- `discussion_context`：为什么做这件事，一两句，可省略。
- `evidence`：真实存在的路径、命令或输出；不要编造。
- `conclusion` / `decisions` / `open_questions` / `next_steps`：只填真正发生的内容，没有就留空。
- `related_files`：项目内相对路径；`related_pages`：相关的 Wiki 或知识库页面。
- `tags`：第一个写类型（决策/结果/发现/踩坑/约定/问题），再加一两个主题词。

一条记录只讲一个主题，一轮最多记两条。自动记录后只在回复末尾单独写一行「📝 已记录：<标题>」；用户明确要求记录时，再告诉用户工具返回的记录路径，并用 2–3 条要点说明记了什么。

## 解析项目

1. 会话开始已注入项目信息时直接使用。
2. 否则调用 `llmwiki_project_get`（`current_path` 为当前目录）。项目未注册时，只有用户明确要求记录才询问是否注册；不要为了记录擅自注册。
3. 会话不在项目目录中时，只写入用户明确指定的已注册项目，不从聊天内容猜项目。
4. 记录保存在该项目 Wiki 根目录的 `records/YYYY/MM/YYYY-MM-DD.md`，每次调用追加一条，不覆盖已有条目。源码目录（`source_root`）与 Wiki 目录（`wiki_root`）保持分开。

## 科研记录与知识库的分工

- 科研记录是时间线：什么时候、做了什么、当时怎么认为。只追加。
- 知识库 `knowledge/*.md` 是当前有效的认识（项目架构、当前认识、关键概念等），由 `llmwiki-maintain` 流程从记录中提炼；替换旧内容需要用户确认。
- 记录时不要直接改写知识库页面，把相关页面写进 `related_pages` 即可。用户要求“整理知识库”“更新知识库”时，按 `llmwiki-maintain` 执行。

## 回顾与接续

- 用户说“继续”“上次做到哪了”“之前那个坑是什么”：先看会话开始注入的最近记录和下一步；需要细节时用 `llmwiki_record_list(query=...)` 检索、`llmwiki_record_read` 读全文。多条目日档要用带 `#entry_key` 的完整 ID，例如 `records/2026/08/2026-08-04.md#123000-topic`。
- 记录不等于科学事实。用户要求核实时，回到记录的 evidence 和原始文件。
- 不要用记录推断任务已完成；任务标题、日期、状态由用户控制。用户明确要求把检查点挂到已有任务时，使用 `llmwiki_progress_context_write`，并给出 `project_root`、`task_id`、`base_revision` 和已保存记录的 `source_record_id`；不要按标题猜任务，也不要用它改任务状态。
- 需要浏览时，可以用 `llmwiki_web_start` 打开本地网站的“科研记录”页面。

## 每日待办的交付标记

有关联的每日待办、且本轮确实完成了交付时，可以在最终回复末尾追加隐藏标记，由异步 Stop Hook 提交为“待验收”。助手完成不等于用户验收，Hook 不会把任务改成 done：

```text
<!-- llmwiki-research-result
{"version":1,"title":"具体标题","understanding":"交付内容与结论","evidence":["实际证据或路径"],"task_id":"32位每日待办ID","summary":"交付摘要","remaining":"","changed_paths":[]}
-->
```

没有 `task_id` 的独立科研结论直接用 `llmwiki_record_write` 记录。会话不在项目目录中时，在标记里填写所选已注册项目的 `project_id`，不要从聊天正文猜。同一标记重复触发是幂等的；异步 Hook 可能在会话立即结束时来不及完成，重要交付请在网站核对。

## 手动笔记

网站“科研记录 → ＋ 手动记录”是用户自己的笔记（`records/manual/<id>.md`，图片在 `records/assets/`），会出现在列表和检索中。不要用 `wiki_write` 覆盖手动笔记；需要补充时写一条新的科研记录，或请用户在笔记界面编辑。笔记里的评论是用户的观察，不是已验证事实；笔记的时间信息只作为元数据，不写进正文。

## 日报、周报与共享计划流程

执行日报、周报、共享计划（包括定时任务要求阅读本文件的共享计划流程）或首次连接计划时，必须先完整阅读同目录的 `references/reports-and-schedule.md`，并严格按其中流程执行。报告和科研记录互不替代：报告不回写成原始记录，科研记录也不代替报告。

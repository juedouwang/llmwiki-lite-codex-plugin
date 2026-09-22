---
name: llmwiki-task-planning
description: 在 LLM Wiki 中规划、安排或调整今日/本周/指定时段的跨项目任务，将目标拆成每日可交付子任务，并提交交付摘要。用于“安排这周研究”“拆解目标”“调整每日负载”“提交任务交付”等请求；不自动验收完成，不负责定时运行或仅保存讨论。
---

# 可复用任务规划与交付

由当前宿主模型理解目标、读取证据、选择任务并制定计划；MCP/CLI 只做确定性的读取、校验和存储，不调用模型、不启动后台代理或新窗口。

## 先读，再安排

- 先确定用户当前本地日期和时区，把“今天 / 明天 / 这周”转换为具体 `YYYY-MM-DD`，在最终结果中明确日期范围。可读取会话的可信本地时间，或在用户本机运行 `python -c "from datetime import datetime; print(datetime.now().astimezone().isoformat())"`。远程机器日期不一定是用户日期，不能直接套用 UTC 或示例日期。
- 未另行约定时，本周按周一至周日，但只安排今天到周日的剩余日期；已经过去的天默认不回填。尊重用户指定的工作日、空闲时间和截止日，时间不足时缩小范围，不把整周负载挤到最后一天。
- 用 `llmwiki_project_list` 或 CLI `projects` 获取精确注册 ID。已有授权且范围明确时直接安排，不反复确认；仅在项目/时间范围等关键条件确实不明时提问。临时且不属于注册项目的任务用 `__workspace__`，不为规划擅自注册项目。
- 先读相关项目进度：CLI `progress --project-id` 复用 `research_progress.load_summary`，保留原任务 ID、父子关系、交付状态、检查点和下一步。再用 `llmwiki_daily_tasks_get` 或 CLI `list` 读取今天及计划覆盖的各天，检查 **tasks、overdue、completed** 三个数组和 **revisions**。它们的公开任务带有 `project_id/project_name/parent_title/revision`，不能只凭标题跨项目合并。
- 先识别已有父目标、同日期子任务、逾期任务、已交付待验收任务及已完成项；已存在则复用/更新，不复制出第二套计划。未来任务也要查，不只看今天。未完成的逾期任务不自动改日期、不凭文件存在/修改/测试日志推断完成；`pending` 不等于 `done`。

## 形成可执行计划

- 每项写清行动、具体产出和可检查的验收标准，例如“运行一组基线，交付配置与结果表；验收：可复现运行并能解释失败样本”，而不是“推进研究”。放在 `description` 中；预计用时写入可选 `estimated_minutes`（1—1440 的整数分钟，不明确可省略，`null` 清空），网页列表会显示。不要发明其他存储字段。
- 根据已有日程、预计耗时、依赖和用户容量分配每日负载，留出检查/返工空间。容量未知可采用保守假设并在结果中简述；不要均分标题或安排全天无余量。超出容量的候选项明确列为未安排，不假装全部可交付。
- 多日新目标用一次 **plan** 原子创建父目标和每日子任务。父任务 `start/end` 是目标周期，子任务用 `scheduled_date`；周期外的子任务不写入。已有父目标用 `create` 加 `task.parent_id` 或 `update` 调整，不用新的 `plan` 重复创建父目标。
- 写入必须带本次最新 `list` 响应的 `revisions[project_id]`，包括初始空字符串。不要拿其他项目 revision、自己生成 token，或用 `progress` 的旧快照代替写前读取。同一项目成功写入后再读最新 revision 才进行下一次独立写入。
- 为同一个逻辑 `plan` 保存稳定的 `request_id`；结果不明时先查询确认，再用**相同 key 和原 payload**重试，幂等不创建第二组任务。新的计划才换 key。修订冲突时重新读取并核对差异，不盲目覆盖或无限重试；无法安全协调时停止并说明。

## MCP：读取与原子规划

先确认宿主已经提供 `llmwiki_daily_tasks_get(home?, day?)` 和 `llmwiki_task_write(home?, payload)`。后者应使用后端的 agent 写入入口，工具参数没有 `actor`。未提供时使用下述一次性 CLI；后端 API 也未就绪就保留草案并报告未写入，不能直接编辑任务存储文件。

**以下是 JSON 模板，不可原样调用。** 每次替换：

- `<HOME>`：用户选定的配置目录；未指定时删除 `home` 字段/`--home`，沿用现有默认配置，不随意换仓库。
- `<LOCAL_TODAY>`：刚核对的用户本地今天；`<PLAN_START>`、`<PLAN_END>`：本次授权的明确起止日；`<DAY_1>`、`<DAY_2>`：范围内实际安排的日期，全部替换为合法 `YYYY-MM-DD`，不要照抄历史示例。单日计划只保留所需子任务。
- `<PROJECT_ID>`：注册表返回的精确 ID，不是项目显示名或路径；`<REVISION>`：本次 `revisions[project_id]` 的原字符串；`<TASK_ID>`：已存在任务的真实 ID。
- `<REQUEST_ID>`：本次逻辑计划唯一且重试时保持不变的字符串，可由宿主生成 UUID；不是每天自动变化的临时值。

调用 `llmwiki_daily_tasks_get`（对计划覆盖的每个相关日期读取一次）：

```json
{"home":"<HOME>","day":"<LOCAL_TODAY>"}
```

调用 `llmwiki_task_write` 创建父目标和每日子任务（一次调用，不逐个创建后再补关系）：

```json
{
  "home": "<HOME>",
  "payload": {
    "project_id": "<PROJECT_ID>",
    "action": "plan",
    "revision": "<REVISION>",
    "request_id": "<REQUEST_ID>",
    "task": {
      "title": "验证基线并给出误差分析",
      "description": "交付可复现基线与误差表；验收：配置、输入和结果可追溯。",
      "start": "<PLAN_START>",
      "end": "<PLAN_END>"
    },
    "subtasks": [
      {"title":"运行小规模基线","scheduled_date":"<DAY_1>","estimated_minutes":60,"description":"交付运行配置、命令与结果表；验收：一个小样本可重复运行。"},
      {"title":"检查失败样本","scheduled_date":"<DAY_2>","estimated_minutes":45,"description":"交付失败样本分类表；验收：列出证据、不确定性与下一步。"}
    ]
  }
}
```

`subtasks` 可带后端支持的 `priority` 和 `parent_id`；新父目标的 ID 尚不存在时不编造 `parent_id`，由原子 `plan` 关联返回的真实父 ID。默认不指定优先级枚举，只有明确需要且已核对后端取值时才传。

交付已有任务时调用 `llmwiki_task_write`：

```json
{
  "home": "<HOME>",
  "payload": {
    "project_id": "<PROJECT_ID>",
    "action": "submit",
    "revision": "<REVISION>",
    "id": "<TASK_ID>",
    "summary": "已交付运行配置与结果表；摘要中填写实际产物路径、验证结果及未解决问题，等待用户验收。"
  }
}
```

`summary` 必须由真实交付证据改写，不把上面的说明句当作完成声明。`submit` 将验收状态置为 `review_state="pending"` 并关联科研记录，不另写重复记录，不等于验收 `done`。只有用户通过验收入口显式验收才完成；即使用户要求验收，也不通过 agent CLI 冒充 user。任务已有状态不因读取而改变。

## 一次性 CLI（MCP 不可用时）

在插件源码根的上级仓库执行；从其他目录使用实际安装路径的 `scripts/task_cli.py`。不要生成临时 Python 脚本、改安装缓存、启动后台执行器或弹出新窗口。`--home` 可放在子命令前或后，省略时沿用 `LLMWIKI_HOME`/现有默认配置。

```text
python -B plugins/llmwiki-lite/scripts/task_cli.py projects
python -B plugins/llmwiki-lite/scripts/task_cli.py --home "<HOME>" progress --project-id "<PROJECT_ID>"
python -B plugins/llmwiki-lite/scripts/task_cli.py --home "<HOME>" list --date "<LOCAL_TODAY>"
python -B plugins/llmwiki-lite/scripts/task_cli.py --home "<HOME>" write --args-file "<PAYLOAD_JSON_FILE>"
python -B plugins/llmwiki-lite/scripts/task_cli.py --home "<HOME>" write --args-file -
```

最后一条从 stdin 读取一个 UTF-8 JSON 对象到 EOF。文件支持 UTF-8 BOM。输入是**直接 payload**，不是 MCP 的 `{home,payload}` 包装；上面 `plan/submit` 示例取其 `payload` 对象即可交给 CLI。`<PAYLOAD_JSON_FILE>` 替换为实际 JSON 文件路径；也可直接把宿主生成的 JSON 通过管道送 stdin，不写任何临时脚本。

例如临时任务的文件或 stdin 内容如下。这里 `<REVISION>` 必须来自 `revisions["__workspace__"]`，不是注册项目的 revision：

```json
{
  "project_id": "__workspace__",
  "action": "create",
  "revision": "<REVISION>",
  "task": {
    "title": "整理本周待讨论问题",
    "scheduled_date": "<LOCAL_TODAY>",
    "description": "交付按优先级排序的问题清单；验收：每项有背景、证据和待决策点。"
  }
}
```

其余动作仍用相同 envelope：`{project_id, action, revision, ...}`；`update` 需要 `id` 和要改的 `task` 字段，`delete/restore` 需要真实 `id`。删除/恢复只在请求涉及相应任务时执行；不要重建相同任务来替代恢复。`restore` 是恢复已完成任务，不是撤销删除；当前后端仅允许用户恢复，agent CLI 会如实返回拒绝，应引导用户使用网页恢复入口，不切换 actor。

CLI 固定调用 `daily_tasks.mutate(home, payload, actor="agent")`，只允许 `create/update/delete/plan/submit/restore`，拒绝 `accept/complete`、指定 `actor` 或设置 `done`。没有自动重试和隐式 revision 刷新；项目 ID 必须精确匹配。成功输出一个 JSON 到 stdout；失败 JSON 到 stderr，退出码 `1` 表示后端/IO 失败（含冲突），`2` 表示参数/输入错误；无 traceback 的正常错误不能当作成功。

## 交付给用户

写后重新读取对应日期，核对父子 ID、日期、任务数量和状态；报告“实际写入/更新了什么”，不要只报告草案。简述具体日期、每日产出/验收标准、容量假设、沿用的旧任务及未安排项。交付摘要已提交就标明“待用户验收”，未执行的任务仍是计划，不宣称已完成。

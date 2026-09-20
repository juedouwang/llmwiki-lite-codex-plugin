---
name: llmwiki-web
description: Start and use the Chinese-first local LLM Wiki research cockpit. Use when the user asks to visualize Wiki Markdown, browse research projects, search papers/methods/experiments/results, or change Wiki storage locations from the web interface.
---
# 野人工作台

The website is a simple loopback visualization and interaction layer, not a second knowledge engine.

1. Call `llmwiki_web_start` with optional `home` or `port` when the user asks to open the site.
2. Return the URL from the tool. It binds to `127.0.0.1` only.
3. If a shell launch is needed, run `python -I -B <plugin>/scripts/web_server.py`; keep the host loopback. For a Windows desktop shortcut without a terminal, target `pythonw.exe -I -B <plugin>/scripts/desktop_launcher.py --home <actual-home> --port <actual-port>`. It reuses the existing background starter, opens the browser after health checks, and exits. Never wrap it in a visible terminal, create startup tasks, or kill an existing service without user intent. Closing the browser does not stop the background server.

The website brand is “野人工作台”. Its default application scale is 125%, independently of browser zoom; responsive layouts use matching breakpoints. Do not instruct the user to adjust browser zoom for the normal display.

The Chinese-first site provides:

- 一个共享侧栏：科研进度、科研记录、日报与周报、知识库、文献、代码；底部项目总览/设置，顶部保留搜索；
- 日报与周报是全局文档，但必须保留进入时的浏览项目：导航用 `context`，参与项目筛选用 `project`，不能混为归属或改写注册表默认项目；类型切换、分页、新建、详情/返回与多标签页分别保持上下文，失效项目要求重选；
- 项目首页与知识页采用单列列表，不重复展示仪表盘、统计卡和维护建议；
- 文献使用单列表与搜索/类型/阅读状态/收藏筛选，配对笔记及添加指令按需展开；
- 原文阅读与中文精读对照阅读，小屏自动改为单列；
- 科研记录按日期分组；手动笔记是连续 Markdown 正文、截图粘贴、引用批注与自动保存；
- Markdown 阅读页只有正文与折叠目录，页面操作在“更多”；
- 存储设置按需展开，高级路径默认收起，添加项目链接可直接展开对应表单。

这是本地网页，不提供公网部署或远程同步。不要为了远程访问将监听地址改为公网地址。

Paper files are served read-only from the registered `source_root` through an extension allowlist and path/symlink checks. PDFs use browser-inline streaming with byte ranges; non-PDF formats are downloaded/opened without executing source HTML. Assistant-reading Markdown from the source project is read-only, while durable notes should be stored in `wiki_root` with an exact `paper_file` binding.
The interface does not create fixed template pages or change real Markdown paths. Existing English paper titles, algorithm names, code, paths, commands, API names, and other precision-sensitive technical terms remain unchanged.

Storage changes copy existing content by default, refuse unsafe non-empty destination merges, update the registry, and never delete old directories. The website does not edit arbitrary Markdown bodies; Markdown remains the knowledge source maintained by the AI assistant and the user.

## 手动科研笔记

在“科研记录”点击“＋ 手动记录”进入连续正文，新建直接聚焦；不显示图片按钮、文件输入或加块菜单。Ctrl+V 粘贴 Markdown/截图，预览可连续粘贴；标题内文字保持标题原生粘贴，纯图进入正文。编辑/预览共用同一正文，Ctrl+Enter 切换，Ctrl+S 保存，停止输入约600ms自动保存。普通文字沿用原生撤销，插入事务可撤销/重做。代码围栏仅展示，不执行。

选中文字后添加独立批注，引用保存为快照；图片画笔标注不在此版本。创建/修改时间只在“更多 → 笔记信息”查看，不进入正文；标签、历史、另存和 Markdown 复制在更多菜单。截图上传失败保留重试，未完成上传不允许导出/正式确认。多窗口冲突或本机恢复稿需要明确选择，不能用 MCP 强制覆盖规避冲突。

旧块笔记只在内存映射，浏览/切模式不写盘，首次实际编辑保存才转写这一篇；保留原 ID、创建时间、附件和原始快照。外部改坏的笔记只读/可另存，不强行转换。笔记只写 Wiki 记录目录，不改源码或助手日档。

## 科研进度与顺手记录

科研进度默认显示本周（周一至周日），可切换两周/四周科研时间轴。任务回车添加、列表直接切换状态并保存；点击任务记录“上次做到哪、下一步”、日期和一篇关联笔记。未排期单列，已完成默认折叠，进行中/卡住任务显示“继续上次”。旧记录中的待办须选择导入，不自动生成日期或推断完成。人工任务在 `.research-progress/tasks.json`，自动上下文在 `.research-progress/contexts.json`。并发冲突须处理后保存。自动整理未接通时只说明尚未接通，不要启动模型或终端，也不要把注入摘要说成自动化已完成。

手动笔记无需点加号或选择图片文件。正文末尾空白可继续书写，不存在块排序/复制/删除菜单。不要把服务端时间写进正文。

## 日报 / 周报入口（开发源码）

“日报与周报”是独立栏目 `/reports`，不再作为科研记录内的小标签。日报和周报汇总已选项目，归工作台所有，项目筛选不改变归属；旧项目报告深链和附件保持。两者复用连续编辑器，自动保存只是草稿，明确确认产生不可变正式版本；修改正式版先产生修订草稿，时间/历史/候选/来源按需查看，批注随版本保留。

报告默认配置关闭，但用户可已明确授权并绑定内置计划：必须读真实设置/回执判断，不用“默认关闭”否认当前启用状态。网站不推理，Hook 不掌握全部聊天，文件改变不代表任务完成；宿主离线不保证报告按时生成。源码更新需重启旧网页进程，不能改缓存冒充发布，也不擅自暂停/重置已有计划。


## 知识维护确认

原知识库和知识阅读页提供“待确认更新 · N”和折叠维护详情；数量为零隐藏入口。用户主动打开建议可比较原文/新文、差异、原因、来源，选择采用修改或保留原文；Esc/关闭不会拒绝。原文或证据变化时禁止采用，不能强行覆盖。只有有待处理/运行的可见知识页每15秒刷新状态，隐藏时停止；不弹系统窗口、不调用模型。时间均在详情，不进入正文。开发源码功能不代表当前安装缓存或真实自动化已更新。


项目「文献」入口展示该项目明确收藏的论文。可仅粘贴地址，不强制下载；编辑、移除只作用于当前项目目录，原文与笔记不删除。DOI/arXiv/URL去重结果直接反馈；版本冲突保留输入。自动收录状态在折叠详情中，未绑定共享任务时明确“待连接”；不创建替代后台。阅读对照仅使用明确绑定的原文与笔记，不做文件名猜配。

## 项目代码管理

项目「代码」页只操作注册仓库根目录：真实版本图、选文件本地保存、建/切分支、普通合并、有限 UTF-8 冲突处理与恢复为新提交。恢复不抹去旧历史，未选已暂存文件不夹带进提交；缺作者身份可页内补填且只写本仓库。每次写入须由用户明确确认，预览失效必须重新查看，不允许替用户丢弃文件或自动重试。

联网例外仅是用户点击已有远端检查、或确认普通单分支上传；拉取分成检查和应用两次动作，不自动上传、不强推，不传送聊天记录。自定义 Hook/签名/认证程序不具备非交互保证时应明确拒绝，不能开终端或关闭真实策略冒充成功。代码页不推理、不改变 Todo/正式报告/知识，不新建后台计划。只在临时仓库验证，源码更新不代表已安装插件更新。

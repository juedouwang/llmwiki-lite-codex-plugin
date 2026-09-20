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

- 一个共享侧栏：科研进度、科研记录、日报与周报、知识库、文献、代码；品牌返回项目总览，底部外观/设置，顶部保留搜索；
- 日报与周报是全局文档，但必须保留进入时的浏览项目：导航用 `context`，参与项目筛选用 `project`，不能混为归属或改写注册表默认项目；类型切换、分页、新建、详情/返回与多标签页分别保持上下文，失效项目要求重选；
- 知识库采用批准原型的目录/正文分栏，小屏变单列；不混入记录、文献、索引或隐藏归档。“继续上次”在科研进度页，首屏服务端输出已保存的上下文，不等异步刷新；
- 文献使用单列表与搜索/类型/阅读状态/收藏筛选，配对笔记及添加指令按需展开；
- 原文阅读与中文精读对照阅读，小屏自动改为单列；
- 科研记录采用平面分隔线列表，显示标题及紧凑日期/来源/真实批注数，搜索默认收起；手动笔记保留连续 Markdown 正文、截图粘贴、引用批注与自动保存；
- 主操作统一加号实心按钮；侧栏底部与设置页支持浅色、深色、跟随系统，选择在当前浏览器持久化，首次绘制前生效，跟随系统响应实时变化；
- Markdown 阅读页只有正文与折叠目录，页面操作在“更多”；
- 存储设置按需展开，高级路径默认收起，添加项目链接可直接展开对应表单。

这是本地网页，不提供公网部署或远程同步。不要为了远程访问将监听地址改为公网地址。

Paper files are served read-only from the registered `source_root` through an extension allowlist and path/symlink checks. PDFs use browser-inline streaming with byte ranges; non-PDF formats are downloaded/opened without executing source HTML. Assistant-reading Markdown from the source project is read-only, while durable notes should be stored in `wiki_root` with an exact `paper_file` binding.
The interface does not create fixed template pages or change real Markdown paths. Existing English paper titles, algorithm names, code, paths, commands, API names, and other precision-sensitive technical terms remain unchanged.

Storage changes copy existing content by default, refuse unsafe non-empty destination merges, update the registry, and never delete old directories. The website does not edit arbitrary Markdown bodies; Markdown remains the knowledge source maintained by the AI assistant and the user.

## 手动科研笔记

在“科研记录”点击“新建笔记”进入连续正文，新建直接聚焦；不显示图片按钮、文件输入或加块菜单。Ctrl+V 粘贴 Markdown/截图，预览可连续粘贴；标题内文字保持标题原生粘贴，纯图进入正文。编辑/预览共用同一正文，Ctrl+Enter 切换，Ctrl+S 保存，停止输入约600ms自动保存。普通文字沿用原生撤销，插入事务可撤销/重做。代码围栏仅展示，不执行。

选中文字后添加独立批注，引用保存为快照；图片画笔标注不在此版本。创建/修改时间只在“更多 → 笔记信息”查看，不进入正文；标签、历史、另存和 Markdown 复制在更多菜单。截图上传失败保留重试，未完成上传不允许导出/正式确认。多窗口冲突或本机恢复稿需要明确选择，不能用 MCP 强制覆盖规避冲突。

列表右侧可确认删除手动笔记，当前列表提供撤销；删除/撤销复核版本，不能覆盖另一窗口的新内容或已重建同名笔记。文件归档到 `.notebook-trash/<id>/<revision>.snapshot`，不进入记录/知识/全文搜索；附件与历史保留，撤销恢复原字节和修改时间。助手记录不提供删除按钮。

旧块笔记只在内存映射，浏览/切模式不写盘，首次实际编辑保存才转写这一篇；保留原 ID、创建时间、附件和原始快照。外部改坏的笔记只读/可另存，不强行转换。笔记只写 Wiki 记录目录，不改源码或助手日档。

## 科研进度与顺手记录

进度页使用原型的“继续上次”和周时间轴：点击任务打开详情，圆圈直接完成/重新打开；鼠标移到任务行或键盘聚焦时显示独立状态菜单，仍支持计划中/进行中/阻塞/完成，不遮挡任务标题。上一周/本周/下一周常驻，14/28天范围收起。只显示真实手动/自动上下文，不根据文件变化自动完成任务。

科研进度默认显示本周（周一至周日），可切换两周/四周科研时间轴。点击“新建任务”展开输入，回车添加，取消收起；列表直接切换状态并保存；点击任务记录“上次做到哪、下一步”、日期和一篇关联笔记。未排期单列，已完成默认折叠，进行中/卡住任务显示“继续上次”。旧记录中的待办须选择导入，不自动生成日期或推断完成。人工任务在 `.research-progress/tasks.json`，自动上下文在 `.research-progress/contexts.json`。并发冲突须处理后保存。自动整理未接通时只说明尚未接通，不要启动模型或终端，也不要把注入摘要说成自动化已完成。

手动笔记无需点加号或选择图片文件。正文末尾空白可继续书写，不存在块排序/复制/删除菜单。不要把服务端时间写进正文。

## 日报 / 周报入口（开发源码）

列表左侧切换日报/周报，右侧选择项目和状态即筛选；文本搜索收起。顶部“生成设置”进入既有设置，新建日报/新建周报与新建笔记共用加号实心按钮。草稿/正式版标识来自真实状态，不把正式稿显示为草稿；确认、历史与候选保护规则不变。

“日报与周报”是独立栏目 `/reports`，不再作为科研记录内的小标签。日报和周报汇总已选项目，归工作台所有，项目筛选不改变归属；旧项目报告深链和附件保持。两者复用连续编辑器，自动保存只是草稿，明确确认产生不可变正式版本；修改正式版先产生修订草稿，时间/历史/候选/来源按需查看，批注随版本保留。

报告默认配置关闭，但用户可已明确授权并绑定内置计划：必须读真实设置/回执判断，不用“默认关闭”否认当前启用状态。网站不推理，Hook 不掌握全部聊天，文件改变不代表任务完成；宿主离线不保证报告按时生成。源码更新需重启旧网页进程，不能改缓存冒充发布，也不擅自暂停/重置已有计划。


## 知识维护确认

知识库和知识阅读页顶部常驻“维护设置”“查看更新”；后者打开真实候选列表，待确认数量为零仍可查看空列表。正文的“待确认更新 · N”仅有候选时显示，维护详情默认折叠。知识首页候选属于整个项目，深链阅读页只显示该页候选；“页面信息”可从正文工具栏展开。知识正文保持只读，不提供原型中尚无对应后端的任意编辑操作。用户主动打开建议可比较原文/新文、差异、原因、来源，选择采用修改或保留原文；Esc/关闭不会拒绝。原文或证据变化时禁止采用，不能强行覆盖。只有有待处理/运行的可见知识页每15秒刷新状态，隐藏时停止；不弹系统窗口、不调用模型。时间均在详情，不进入正文。开发源码功能不代表当前安装缓存或真实自动化已更新。


项目「文献」入口展示该项目明确收藏的论文。可仅粘贴地址，不强制下载；编辑、移除只作用于当前项目目录，原文与笔记不删除。DOI/arXiv/URL去重结果直接反馈；版本冲突保留输入。自动收录状态在折叠详情中，未绑定共享任务时明确“待连接”；不创建替代后台。阅读对照仅使用明确绑定的原文与笔记，不做文件名猜配。

## 项目代码管理

项目「代码」页只操作注册仓库根目录：真实版本图、选文件本地保存、建/切分支、普通合并、有限 UTF-8 冲突处理与恢复为新提交。恢复不抹去旧历史，未选已暂存文件不夹带进提交；缺作者身份可页内补填且只写本仓库。每次写入须由用户明确确认，预览失效必须重新查看，不允许替用户丢弃文件或自动重试。

代码页顶部按钮使用小写 `pull` / `push`，不改变检查/确认/执行流程；代码与分支图标均为三个圆圈。右栏遵循批准的 HTML 原型，简短元数据可点击展开查看本地仓库绝对路径；diff可在只读大窗口放大查看，关闭后保留节点和文件；建分支/恢复紧跟diff而非页面末端。新增/删除行数来自真实 Git，二进制不显示虚构数字，差异保留不连续片段提示。不能把原型示意提交或行数写入生产页面。

联网例外仅是用户点击已有远端检查、或确认普通单分支上传；拉取分成检查和应用两次动作，不自动上传、不强推，不传送聊天记录。自定义 Hook/签名/认证程序不具备非交互保证时应明确拒绝，不能开终端或关闭真实策略冒充成功。代码页不推理、不改变 Todo/正式报告/知识，不新建后台计划。只在临时仓库验证，源码更新不代表已安装插件更新。

代码页性能排查应分开测量工作区状态、版本图与节点详情；先减少重复 Git 进程和重复请求；已访问栏目保留真实 DOM、项目隔离的已读展示快照，提交详情按不可变 OID 缓存，回访立即显示后后台核对。必须分别测量首次读取与缓存点击到实际内容的时延，不能把按钮反馈或加载动画当作内容已显示。展示缓存不代表最新状态，更不代替写操作的预览和新鲜复核；不自动联网。窗口聚焦不打断未完成读取；显式刷新和写后刷新仍需重新读取。合并读取不得跳过根目录、属性过滤器、配置、快照和写入复核。修改服务端后需实际重启服务才生效，不能把隔离测试结果当作旧服务已更新。


## 启动项目与手动排序

网站根入口 `/` 打开用户显式指定的启动项目；未指定则打开排序后的第一项科研进度，没有项目时进入 `/projects`。品牌链接始终返回 `/projects` 总览。总览中的文件夹图标支持拖动排序及键盘上下调整，星标按钮指定/取消启动项目，修改自动保存并在失败时提示、恢复排序。设置只写注册表旁 `settings.json` 的 `web_default_project_id` / `project_order`，不改变助手 `current_project_id`，不修改项目文件。显式默认优先于顺序，新注册项目在手动列表末尾，默认项目注销后回退到剩余首项。源码修改须正常重启服务才完整生效；不将刷新静态资源说成新路由已加载。

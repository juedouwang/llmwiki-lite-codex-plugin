# 界面对齐验收 · 2026-09-20

## 结论与生效边界

源码界面对齐、三种外观与笔记删除/撤销已通过隔离验收。此结论**不等于常驻网站已切换**：21:50（Asia/Shanghai）只读复核，8766仍由18:44启动的 `pythonw` 进程35092占用；本轮停止/重启请求被执行环境拒绝，没有换手段绕过，也没有对真实数据启动第二份服务。正常重启源码服务后才可验收常驻入口。

所有写入测试只使用临时注册表、Wiki与Git仓库。未改真实科研数据、已绑定计划、安装缓存、marketplace或版本号，未提交/push。保留工作树内其他任务改动。

## 比对基线

- 只读批准HTML：`C:/Users/lyn/.codex/visualizations/2026/09/18/01a0b5b0-347d-7f42-a151-7c50cc08fe64/research-workbench-complete.html`。
- 仓库内副本 `openspec/changes/research-daily-weekly-reports/reference/approved-workbench.html` 与上述原稿SHA256相同：`2abb585862148b1defbd9df3883b2cf138cdd9d692fa9bb5ce629a94762bcaaf`，其他机器可直接用该副本复现。
- 对比视口1440×960，双方125%。原型只移除宿主嵌入卡片边框/圆角，并用测试runtime中的Lucide UMD恢复原型图标；不改HTML原稿、不从产品CSS反向改基准。
- 共享布局/Git 22组、记录7组、继续上次/周轴8组，共37组DOM几何/样式比较；几何容差1屏幕像素，保留125%下边框的像素取整。
- 参考截图只比较结构，不伪造项目、任务、批注数、Git版本/行数、报告状态。用户后续确认的“野人工作台”、新建笔记、三圆图标、删除、diff放大/按钮前置、主题优先于旧原型。
- 知识页继续只读；原型里的任意Wiki编辑按钮没有获准的写入实现，不放假按钮。维护设置/查看更新接已有真实功能，首页候选为项目范围，深链候选限当前页。

## 实现位置

| 范围 | 主要文件（仓库相对路径） |
| --- | --- |
| 共享结构、列表、知识双栏与首屏继续上次 | `plugins/llmwiki-lite/scripts/research_web_ui.py`、`scripts/static/style.css` |
| 删除/撤销、同源与版本复核 | `scripts/research_notebook.py`、`scripts/web_server.py`、`scripts/static/records.js` |
| 三主题与首屏应用 | `scripts/static/theme.js`及各模块CSS |
| 进度周轴与不遮挡标题的四状态菜单 | `scripts/research_progress.py`、`scripts/static/progress.js`、`scripts/static/progress.css` |
| 报告统一入口、即时筛选、真实草稿/正式标识 | `scripts/research_reports.py`、`scripts/static/reports.js`、`scripts/static/reports.css` |
| 知识查看更新/页面信息 | `scripts/static/knowledge-maintenance.js` |
| 自动化验收 | `plugins/llmwiki-lite/tests/workbench_alignment_browser_test.cjs`、`site_prototype_browser_test.cjs`及相关浏览器/单元测试 |

上述表格中简写的 `scripts/` 均位于 `plugins/llmwiki-lite/` 下。

## 已验证结果

- **Smoke套件**：398项，395通过、3跳过。删除取消/归档/撤销原字节与mtime/过期版本/不删附件与助手记录包含在内；没有删除用户文件做测试。
- **Site + prototype + alignment + refinements**：10路由桌面/窄屏，37组原型对比；记录紧凑搜索、真实批注、取消删除、删除、撤销与409冲突；六栏目浅/深色、跟随系统实时变化、刷新、存储不可用、缓存导航、对话框；统一日报/周报/笔记按钮；Git真实路径、diff放大、Escape与焦点、长历史下按钮位置；文件夹拖动/键盘排序、默认项目与保存失败恢复。临时项目中XSS文本先通过安全验收，再改成可读标题用于截图。
- **Notebook/Progress浏览器**：连续正文、默认Markdown预览、反复Ctrl+V截图、不打开文件选择器、IME、撤销重做、批注、历史、冲突/断网恢复；任务创建/计划/四状态、完成/重新打开、手动上下文保护、后台慢生成不阻塞编辑与旧数据只读保护。
- **Reports浏览器**：日报/周报项目上下文、导航/筛选/分页、正式版标识、连续编辑、候选保护、正式历史与确认、截图、窄屏、既有计划设置不擅自启用。
- **Knowledge浏览器**：7场景；真实候选、安全对比、取消不写、过期禁止采用、保留原文、采用与历史；顶部入口、页面信息、项目/单页范围。
- **Page lifecycle**：笔记、进度、报告编辑、报告列表、文献、知识6组通过；不因缓存导航重复事件或错用项目。
- **静态检查**：本轮涉及Python定向Ruff、9个JS/CJS语法、`llmwiki-web` Skill验证、`git diff --check`通过。全仓Ruff仍有不属于本轮的Git等模块存量问题，未以局部通过冒充全仓通过。

## 复现

从仓库根目录运行；浏览器测试runtime需要Playwright，带 `--prototype` 时还需Lucide（仅测试依赖，产品仍为标准库与原生JS/CSS）。本机使用已提供的runtime，不往产品加入npm依赖。

```powershell
$env:PYTHONUTF8='1'
$env:NODE_PATH='C:/Users/lyn/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
$env:LLMWIKI_BROWSER_CHANNEL='chrome'
python -B plugins/llmwiki-lite/tests/smoke_test.py
python -B plugins/llmwiki-lite/tests/run_site_browser.py --prototype openspec/changes/research-daily-weekly-reports/reference/approved-workbench.html --alignment --refinements
python -B plugins/llmwiki-lite/tests/run_notebook_browser.py
python -B plugins/llmwiki-lite/tests/run_reports_browser.py
python -B plugins/llmwiki-lite/tests/run_knowledge_browser.py
python -B plugins/llmwiki-lite/tests/run_page_lifecycle_browser.py
openspec validate workbench-interface-alignment --strict
```

## 本机证据

- `%TEMP%/llmwiki-workbench-alignment/actual-notes.png`：真实网页/隔离项目，不是重新绘制的原型。
- 同目录 `approved-notes.png`、`notes-comparison.json`、`progress-comparison.json`与六栏目light/dark截图、窄屏截图。
- `%TEMP%/llmwiki-site-prototype/comparison.json`：原型SHA256、22组测量及5个Git宽度。
- `%TEMP%/llmwiki-workbench-refinements/`：Git放大、排序和项目默认入口证据。
- `%TEMP%/workbench-alignment-{site,notebook,reports,knowledge,lifecycle,smoke}.log`：对应测试日志。临时证据可通过上述命令重建，不提交进真实项目或科研知识库。

## 保留的功能边界

撤销删除在当前列表会话可用；磁盘归档、附件和历史保留，不宣称已有完整回收站界面。三主题作用于UI，不反色研究图片。既有Git写入预览/确认/新鲜复核不变；没有新增自动联网或后台模型。常驻服务未成功重启、安装插件未发布，分别验收后才能宣称生效。

# 同项目工作树导航验收记录

日期：2026-09-21

## 实施范围

- 多工作树时增加“选择工作树”，被占用分支提供“进入所在工作树”。单工作树继续保持已确认的原型布局。
- 在同一科研项目内切换代码操作目录，不重复注册项目，不 checkout 原目录，不覆盖其他工作树的未提交内容。
- 全部 Git API 随请求传递已选工作树；校验登记关系、双向 Git 绑定、预览与文件标识的目录归属，保留 common-dir 共享锁和执行前重校验。
- 未创建真实工作树，未合并 main，未执行提交或 push，未修改真实注册表、科研任务和笔记。

## 自动化验证

| 检查 | 结果 |
| --- | --- |
| `python -B -m unittest discover -s plugins/llmwiki-lite/tests -p test_git_web*.py` | 112 项，111 通过、1 跳过，419.029 秒；最后一次注册目录绑定补强后另行运行下方定向复测 |
| 最新代码的 6 个 same_project 定向测试 | 6 项全部通过，23.913 秒 |
| 同项目工作树浏览器验收 | 7 组 PASS；临时注册表只注册一个科研项目，同时操作主目录与 linked worktree |
| 整站浏览器回归 | 10 个路由的桌面及窄屏检查通过 |
| 已确认的单工作树原型回归 | 3 个精确 Git SVG、22 组几何/样式、6 次栏目切换、5 种 Git 页面宽度通过 |
| 本轮 7 个改动 Python 文件 Ruff | 通过 |
| 产品 JS 与浏览器测试 JS 的 `node --check` | 通过 |
| `git diff --check` | 通过 |
| 网页 Skill 的 `quick_validate.py` | 通过 |
| `openspec validate git-worktree-navigation --strict` | 通过 |

工作树浏览器验收覆盖：旧 SSR 页面使用新静态资源、两个目录间进入/返回/刷新/历史导航、占用分支入口、Escape 和外部点击关闭、320/580/1440px 与浅深色、未确认输入离页保护、延迟响应隔离、所选文件保存、未选暂存保留、选中目录建分支/切分支、失效工作树链接拒绝及返回注册目录。所有写入均发生于临时测试仓库。

整站与原型回归完成后，补充了旧 SSR 兼容保护和菜单定位微调；最新工作树浏览器测试已包含这两项。没有重复运行整站回归。

## 现有问题与验证边界

- 全目录 Ruff 仍有 117 项诊断，分布在 11 个本轮未修改的文件中；与本轮改动文件的交集为空。本次未扩散修复，不将全目录 lint 报为通过。
- 本轮未运行整个 `smoke_test.py`；以上结果仅代表已列出的测试范围。
- 临时浏览器服务已正常结束；没有使用真实项目作写入测试。

## 本机证据

日志位于 `C:/Users/lyn/AppData/Local/Temp/`：

- `llmwiki-git-web-regression.log`
- `llmwiki-worktree-final.log`
- `llmwiki-worktree-browser.log`
- `llmwiki-worktree-site.log`

工作树截图：`C:/Users/lyn/AppData/Local/Temp/llmwiki-code-worktrees-evidence/`。
单工作树原型对照：`C:/Users/lyn/AppData/Local/Temp/llmwiki-site-prototype/`。
这些是本机临时验收证据，不属于长期持久化产物。

## 部署状态

2026-09-21 17:51 只读核对 `http://127.0.0.1:8766/project/project-d794d170/code` 返回 HTTP 200，但页面尚不含新工作树入口，说明真实服务仍在使用旧的已加载后端。

本次没有停止或重启该服务，也没有绕过既往停止策略限制。新功能已落在源码中并通过临时服务验收，尚未在当前真实服务中生效。需正常结束旧服务，再使用现有桌面“野人工作台”快捷方式启动；仅刷新网页不足以加载后端改动。

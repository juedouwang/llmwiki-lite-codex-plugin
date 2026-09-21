## ADDED Requirements
### Requirement: Prototype-derived composition
正式网站 SHALL 使用批准HTML的共享尺寸、留白、平面行列表、按钮和分栏，保留野人工作台品牌、125%比例及真实连续笔记编辑器；Git图标按最新参考图采用竖直主干、下端弯曲连至右上圆节点。
#### Scenario: Records and columns
- **WHEN** 打开科研记录和其他五个栏目
- **THEN** 记录显示左侧来源图标、中间标题及日期/来源/真实批注数、右侧打开入口的平面分隔线列表；不再显示日期分组大卡片。搜索可展开；新建笔记/日报/周报采用相同加号实心按钮。知识库目录与正文分栏，文献图标列表，保留真实功能和写入保护。
#### Scenario: Progress, report and knowledge actions
- **WHEN** 在进度、报告、知识栏目使用主要操作
- **THEN** 进度保留原型紧凑继续上次、周轴与完成圆圈，四状态不因视觉简化而丢失，行内菜单不遮住任务标题；日报/周报共用按钮与平面列表，筛选即时生效且真实正式版不显示为草稿；知识顶部常驻查看更新和维护设置，打开真实候选/既有设置，首页按项目、深链按知识页限定候选，正文只读，不提供无后端的假编辑操作。
### Requirement: Explicit notebook removal
手动笔记 SHALL 从列表右侧确认删除并提供撤销；服务复核revision，将文件移入独立隐藏回收目录，不删除助手记录、附件或历史。
#### Scenario: Cancel, remove and undo
- **WHEN** 用户取消、确认或撤销删除
- **THEN** 分别保持原文件、移出记录列表、恢复原字节及时间；删除前版本已改变或撤销目标已存在则拒绝覆盖，显示原因。
### Requirement: Persistent selectable theme
全站 SHALL 提供浅色、深色、跟随系统，默认跟随系统，在浏览器保存并在首次渲染前应用；系统变化仅影响跟随模式，所有栏目、输入、弹窗、编辑器、diff共用主题变量。
#### Scenario: Switch and retain
- **WHEN** 设置深色后跨栏目、项目、刷新，或跟随模式下系统切换
- **THEN** 全页颜色保持所选模式，跟随模式即时响应；显式浅色不被系统覆盖；存储失败不妨碍当前页面切换；图片保持原色。

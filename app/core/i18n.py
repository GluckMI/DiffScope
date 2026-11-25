"""轻量多语言支持：以中文原文为键，可选英文翻译。

三层回退：精确词条 → 术语表整句替换 → 原文。英文模式下不会再出现中文界面残留，
动态状态消息由术语表给出可读译文，正式发布前建议交由英文母语者润色。
"""

from __future__ import annotations

import re

SUPPORTED = ("zh", "en")

_CURRENT = "zh"

_EN = {
    "文件": "File", "操作": "Actions", "视图": "View", "帮助": "Help", "扩展": "Extensions",
    "打开对比…": "Open Compare…", "保存会话…": "Save Session…", "打开会话文件…": "Open Session File…",
    "最近的会话…": "Recent Sessions…", "退出": "Exit",
    "重新比较": "Recompare", "上一处差异": "Previous Difference", "下一处差异": "Next Difference",
    "保存修改": "Save Changes", "查找": "Find", "下一个匹配": "Next Match",
    "合并当前差异 左→右": "Merge Current Diff Left→Right",
    "合并当前差异 右→左": "Merge Current Diff Right→Left",
    "比较选项…": "Compare Options…", "展开全部": "Expand All", "折叠全部": "Collapse All",
    "返回目录对比": "Back to Folder Compare", "关于": "About", "帮助文档": "Help",
    "打开数据源…": "Open Data Source…", "创建文件系统快照…": "Create Snapshot…",
    "比较规则…": "Compare Rules…", "文件名对齐覆盖…": "Folder Alignment Overrides…",
    "同步模式…": "Sync Modes…", "三向合并…": "Three-way Merge…",
    "表格比较…": "Table Compare…", "十六进制比较…": "Hex Compare…",
    "图片比较…": "Picture Compare…", "标签/版本比较…": "Tags/Version Compare…",
    "注册表比较…": "Registry Compare…", "生成报告…": "Generate Report…",
    "脚本处理器…": "Script Handler…", "标签页工作区": "Tabbed Workspace",
    "独立文本编辑器": "Standalone Text Editor", "比较剪贴板内容": "Compare Clipboard",
    "查找与替换…": "Find & Replace…", "行详情": "Line Details",
    "切换书签": "Toggle Bookmark", "下一个书签": "Next Bookmark",
    "设置…": "Settings…", "切换浅色 / 深色主题": "Toggle Light / Dark Theme",
    "文件格式…": "File Formats…", "手动对齐当前行": "Align Current Lines",
    "在当前位置断开对齐": "Break Alignment Here", "清除手动对齐": "Clear Manual Alignments",
    "三向文件夹合并…": "Three-way Folder Merge…",
    "开始比较": "Compare", "刷新": "Refresh", "浏览…": "Browse…", "模式": "Mode",
    "保存会话": "Save Session", "会话…": "Sessions…", "左": "Left", "右": "Right",
    "文件夹对比": "Folder Compare", "文件对比": "File Compare",
    "状态汇总": "Summary", "全部": "All", "就绪": "Ready",
}


_MORE_EN = {
    "确认执行": "Confirm", "知道了": "OK", "确定": "OK", "应用": "Apply", "添加": "Add",
    "新建": "New", "删除": "Delete", "恢复默认": "Reset Defaults", "导出…": "Export…",
    "导入…": "Import…", "运行": "Run", "打开": "Open", "保存": "Save", "关闭": "Close",
    "上一个": "Previous", "下一个": "Next", "重新比较": "Recompare", "执行合并": "Run Merge",
    "加载并合并": "Load & Merge", "检查更新": "Check for Updates", "选择目录": "Pick Folder",
    "选择输出位置": "Pick Output", "打开所选会话": "Open Selected Session",
    "浏览本地文件/文件夹…": "Browse Local File/Folder…", "替换全部（左侧）": "Replace All (Left)",
    "设置": "Settings", "比较规则 / 忽略不重要差异": "Compare Rules / Ignore Unimportant Differences",
    "文件夹对齐覆盖": "Folder Alignment Overrides", "目录同步模式": "Folder Sync Modes",
    "脚本处理器": "Script Handler", "生成比较报告": "Generate Compare Report",
    "帮助": "Help", "创建文件系统快照": "Create Filesystem Snapshot",
    "文件格式": "File Formats", "三向文件夹合并": "Three-way Folder Merge",
    "生成后打印报告": "Print Report After Generating",
    "同时生成可点击的单文件报告": "Also Generate Linked Per-file Reports",
    "编辑忽略规则…": "Edit Ignore Rules…", "同步结果": "Sync Result",
    "打开数据源（左）": "Open Data Source (Left)", "打开数据源（右）": "Open Data Source (Right)",
    "比较选项": "Compare Options", "最近的会话": "Recent Sessions",
    "保存对比会话": "Save Compare Session", "打开会话": "Open Session",
    "路径无效": "Invalid Path", "路径不存在": "Path Not Found", "类型不匹配": "Type Mismatch",
    "无访问权限": "Access Denied", "内容读取失败": "Read Failure",
    "无法按文本方式读取": "Not Readable As Text", "写入失败": "Write Failure",
    "会话文件错误": "Session File Error", "数据源访问失败": "Data Source Error",
    "文件格式解析失败": "Format Parse Failure", "媒体文件解析失败": "Media Parse Failure",
    "二进制数据解析失败": "Binary Parse Failure", "操作前确认 · 目录同步": "Confirm · Folder Sync",
}
_EN.update(_MORE_EN)


# ---------------------------------------------------------------- 界面文案
_UI_EN = {
    "主题": "Theme", "深色": "Dark", "浅色": "Light", "跟随系统": "Follow System",
    "界面字体": "Interface Font", "界面字号": "Interface Size",
    "编辑器字体": "Editor Font", "编辑器字号": "Editor Size",
    "界面语言（重启生效）": "Language (restart required)",
    "文本自动换行": "Word wrap", "语法高亮": "Syntax highlighting",
    "显示缩略图概览": "Show minimap", "显示行号": "Show line numbers",
    "减少界面动效": "Reduce motion", "恢复默认外观": "Reset appearance",
    "外观": "Appearance", "比较与文件": "Compare & Files", "快捷键": "Shortcuts",
    "集成": "Integration", "保留备份数": "Backups to keep",
    "编辑器字体": "Editor Font", "单文件文本上限": "Max text size per file",
    "目录条目上限": "Max folder entries", "外部工具": "External tool",
    "更新源": "Update source", "保存前自动备份": "Back up before saving",
    "编辑后自动重新比较（停止输入 0.8 秒后刷新差异）": "Auto-recompare after editing (0.8 s delay)",
    "默认比较文件内容": "Compare file contents by default",
    "比较后展开到差异目录": "Expand to differing folders after compare",
    "启动时检查更新": "Check for updates on startup",
    "安装 Windows 右键菜单集成": "Install Windows context-menu integration",
    "重启应用后快捷键生效。": "Shortcuts take effect after restarting the app.",
    "比较选项": "Compare Options", "比较规则": "Compare Rules",
    "文件夹对齐覆盖": "Folder Alignment Overrides", "目录同步模式": "Folder Sync Modes",
    "同步结果": "Sync Result", "打开数据源": "Open Data Source",
    "比较选项…": "Compare Options…", "恢复默认": "Reset Defaults",
    "忽略大小写": "Ignore case", "忽略空白字符差异": "Ignore whitespace",
    "展开符号链接目录（可能导致循环，慎用）": "Follow symlink folders (may loop)",
    "对齐不同 Unicode 规范化形式的文件名": "Align Unicode-normalized filenames",
    "排除规则": "Exclude rules", "排除规则（逗号分隔）": "Exclude rules (comma separated)",
    "名称": "Name", "大小": "Size", "修改时间": "Modified", "状态": "Status",
    "仅显示差异": "Show differences only", "全部条目": "All items",
    "按名称或路径筛选（支持部分匹配）": "Filter by name or path (partial match)",
    "上一差异": "Previous Difference", "下一差异": "Next Difference",
    "左 → 右 同步": "Sync Left → Right", "右 → 左 同步": "Sync Right → Left",
    "展开全部": "Expand All", "折叠": "Collapse",
    "提示：双击文件可打开文本对比；同步操作会先列出全部动作供确认。":
        "Tip: double-click a file to open text compare; sync always lists actions for confirmation.",
    "左侧文件": "Left File", "右侧文件": "Right File",
    "← 返回目录": "← Back to Folders", "上一差异 (F7)": "Prev (F7)",
    "下一差异 (F8)": "Next (F8)", "差异 0 / 0": "Diff 0 / 0",
    "全部 *": "All *", "差别 ≠": "Diff ≠", "相同 =": "Same =",
    "差异列表": "Diff List", "合并当前块 →": "Merge Current →",
    "← 合并当前块": "← Merge Current", "全部 左→右": "All Left→Right",
    "全部 右→左": "All Right→Left", "查找文本（两侧同步高亮）": "Find (highlight both sides)",
    "区分大小写": "Match case", "上一个": "Previous", "下一个 (F3)": "Next (F3)",
    "重新比较": "Recompare", "保存左侧": "Save Left", "保存右侧": "Save Right",
    "灰色空行表示该侧缺少对应内容；文本可直接编辑，保存前会再次确认。":
        "Grey empty lines mean no counterpart; text is editable and saving asks for confirmation.",
    "≠ 修改内容": "≠ Modified", "< 仅左侧有": "< Left only", "> 仅右侧有": "> Right only",
    "左：—": "Left: —", "右：—": "Right: —",
    "差异": "Diff", "书签": "Bookmarks", "状态汇总": "Summary",
    "差异分布：点击跳转": "Diff distribution: click to jump",
    "差异分布缩略图，点击跳转到对应行": "Diff minimap, click to jump to a line",
    "手动对齐连线栏": "Manual alignment gutter",
    "差异块列表": "Difference list", "书签列表": "Bookmark list",
    "当前差异位置": "Current difference position", "查找匹配数量": "Match count",
    "读取错误提示": "Read error", "目录对比操作提示": "Folder compare tip",
    "快捷键提示": "Shortcut hints", "清空全部最近比较记录": "Clear all recent comparisons",
    "清空全部": "Clear all", "文件与文件夹对比 · 差异查看 · 合并同步":
        "File & folder compare · diff review · merge & sync",
    "选择两个文件夹开始比较": "Choose two folders to compare",
    "选择两个文件开始比较": "Choose two files to compare",
    "打开已保存的会话": "Open a saved session", "最近的会话": "Recent sessions",
    "最近比较（点击重开 · 右键删除）": "Recent (click to reopen · right-click to delete)",
    "也可以在上方工具栏直接输入左右路径后点击“开始比较”。": "Or enter both paths in the toolbar and click Compare.",
    "删除此条记录": "Delete this entry", "清空全部记录": "Clear all entries",
    "差异/叠加": "Diff / Overlay", "(无图片)": "(no image)",
    "左侧": "Left", "右侧": "Right", "左侧目录": "Left folder", "右侧目录": "Right folder",
    "左侧图像预览": "Left image preview", "右侧图像预览": "Right image preview",
    "差异/叠加图像预览": "Diff / overlay image preview",
    "类型": "Type", "变更列": "Changed columns", "左侧值": "Left value",
    "右侧值": "Right value", "两侧没有同名的数据表。": "No tables with the same name on both sides.",
    "≠ 已修改": "≠ Modified", "< 仅左侧": "< Left only", "> 仅右侧": "> Right only",
    "复制覆盖": "Copy / overwrite", "创建目录": "Create folder", "删除": "Delete",
    "创建文件系统快照": "Create Filesystem Snapshot", "快照": "Snapshot",
    "生成比较报告": "Generate Compare Report", "报告预览": "Report Preview",
    "帮助": "Help", "关于": "About", "退出": "Exit", "就绪": "Ready",
    "差异清单已导出：": "Difference list exported: ", "预演报告已保存：": "Preview report saved: ",
    "正在比较…": "Comparing… ", "正在加载": "Loading ",
    "当前没有差异块。": "No difference blocks.", "当前没有书签": "No bookmarks",
    "（Ctrl+B 在当前行切换书签）": "(Ctrl+B toggles a bookmark on the current line)",
    "无可同步操作": "Nothing to sync", "路径不完整": "Incomplete paths",
    "部分条目无法读取": "Some items could not be read",
    "请先开始一次对比": "Start a comparison first",
    "设置已保存并应用": "Settings saved and applied",
    "已切换到浅色主题": "Switched to Light Theme",
    "已切换到深色主题": "Switched to Dark Theme",
    "已切换到跟随系统主题": "Switched to Follow System",
    "差异 0 / 0": "Diff 0 / 0",
    "差异导航": "Diff Navigation",
    "也可以在上方的工具栏直接输入左右路径，然后点击“开始比较”。":
        "Or enter both paths in the toolbar above, then click Compare.",
    "还没有比较记录。选择两个文件或文件夹，或从上方工具栏输入路径即可开始。":
        "No recent comparisons yet. Choose two files or folders, or enter paths in the toolbar above.",
    "✓ 两侧内容一致，没有差异块": "✓ Both sides are identical — no differences",
    # 新手引导
    "三步开始第一次比较": "Get started in three steps",
    "① 在上方工具栏选择「文件夹对比」或「文件对比」，填写左右两个路径。":
        "① Choose Folder Compare or File Compare in the toolbar and enter both paths.",
    "② 点击「开始比较」；目录树按状态着色，双击文件可进入逐行文本对比。":
        "② Click Compare. The tree is colour-coded; double-click a file for a line-by-line diff.",
    "③ 用 F7 / F8 逐项跳转差异，需要写盘时先确认操作清单，删除优先进入回收站。":
        "③ Use F7 / F8 to jump between differences; confirm the action list before writing, deletions go to the Recycle Bin.",
    "下次启动仍显示": "Show again on next launch", "知道了": "Got it",
    "下次启动显示新手引导": "Show onboarding on next launch",
    # 长表单分区标题
    "待执行操作": "Pending actions", "确认与预演": "Confirm & preview",
    "脚本内容": "Script", "执行结果": "Output",
    "输入与输出路径": "Input & output paths", "三向窗格": "Three-way panes",
    "冲突处理": "Conflict resolution", "冲突策略与执行": "Conflict policy & run",
    "新增规则": "Add a rule", "当前规则": "Current rules",
    "比较结果": "Comparison result", "数据库文件": "Database files",
    "连接信息": "Connection", "凭证与参数": "Credentials & parameters",
    "快速开始": "Getting started", "数据源": "Data sources",
    "比较类型": "Compare types", "合并与同步": "Merge & sync",
    "自动化": "Automation",
    "路径输入提示": "Path input hint",
    "主工具栏": "Main toolbar",
    "Git 仓库改动比较…": "Git Repository Changes…",
    "SQLite 数据比较…": "SQLite Data Compare…",
    # 返回主页与会话记录
    "主页": "Home", "← 主页": "← Home", "返回主页": "Back to Home",
    "已返回主页": "Returned to Home", "返回欢迎主页": "Back to welcome",
    "返回欢迎主页（Alt+Home）": "Back to welcome (Alt+Home)",
    "会话记录": "Session history",
    "查看最近比较记录与已保存的会话（可直接重新打开）":
        "View recent comparisons and saved sessions (open directly)",
    "最近比较记录": "Recent comparisons", "已保存的会话": "Saved sessions",
    "打开所选": "Open selected", "保存当前会话": "Save current session",
    "最近比较会在完成比较时自动记录；「保存会话」会连同比较选项、过滤器与窗口位置一起保存。":
        "Recent comparisons are recorded automatically; Save Session also keeps options, filters and window geometry.",
    "（还没有比较记录：完成一次比较后会自动出现在这里）":
        "(No comparisons yet — they appear here automatically after a comparison)",
    "（还没有已保存的会话：点击「保存当前会话」即可创建）":
        "(No saved sessions yet — click Save current session to create one)",
    "双击或点击「打开所选」重新比较这两个路径":
        "Double-click or use Open selected to compare these paths again",
}
_EN.update(_UI_EN)


# ---------------------------------------------------------------- 术语回退
# 动态状态消息（含数字、路径）无法逐条收录，按最长匹配做术语替换。
_GLOSSARY = {
    "文件与文件夹对比": "File & Folder Compare", "比较选项": "Compare Options",
    "比较剪贴板内容": "Compare Clipboard", "选择性合并差异块": "Selective Merge",
    "三向文件夹合并": "Three-way Folder Merge", "三向文本合并": "Three-way Text Merge",
    "创建文件系统快照": "Create Filesystem Snapshot", "生成比较报告": "Generate Compare Report",
    "显示缩略图概览": "Show Minimap", "显示行号": "Show Line Numbers",
    "恢复默认外观": "Reset Appearance", "减少界面动效": "Reduce Motion",
    "跟随系统": "Follow System", "跟随系统主题": "Follow System Theme",
    "浅色主题": "Light Theme", "深色主题": "Dark Theme",
    "界面语言": "Language", "界面字体": "Interface Font",
    "编辑器字体": "Editor Font", "快捷键": "Shortcuts", "工作区": "Workspace",
    "数据源": "Data Source", "数据库": "Database", "十六进制": "Hex",
    "比较规则": "Compare Rules", "比较模板": "Compare Templates",
    "比较结果": "Compare Result", "比较选项": "Compare Options",
    "差异清单": "Difference List", "差异分布": "Diff Distribution",
    "差异块": "Difference Block", "读取失败": "Read Failed",
    "含差异": "Contains Differences", "类型冲突": "Type Conflict",
    "仅左侧": "Left Only", "仅右侧": "Right Only", "仅左": "Left Only", "仅右": "Right Only",
    "已修改": "Modified", "相同": "Same", "状态汇总": "Summary",
    "重新比较": "Recompare", "开始比较": "Compare", "上一处差异": "Previous Difference",
    "下一处差异": "Next Difference", "上一差异": "Previous Difference", "下一差异": "Next Difference",
    "合并当前差异": "Merge Current Difference", "合并当前块": "Merge Current Block",
    "保存会话": "Save Session", "打开会话": "Open Session", "保存修改": "Save Changes",
    "恢复上次会话": "Resume Last Session", "恢复会话": "Restore Session",
    "恢复备份": "Restore Backup", "从备份恢复": "Restore from Backup",
    "预演报告": "Preview Report", "同步模式": "Sync Mode", "同步结果": "Sync Result",
    "后台同步": "Background Sync", "执行目录同步": "Run Folder Sync",
    "导出差异清单": "Export Difference List", "导出预演报告": "Export Preview Report",
    "打开数据源": "Open Data Source", "打开工作区": "Open Workspace",
    "加载工作区": "Load Workspace", "保存工作区": "Save Workspace",
    "左侧文件夹路径": "Left Folder Path", "右侧文件夹路径": "Right Folder Path",
    "左侧文件路径": "Left File Path", "右侧文件路径": "Right File Path",
    "左侧文件": "Left File", "右侧文件": "Right File",
    "左侧目录": "Left Folder", "右侧目录": "Right Folder",
    "左侧值": "Left Value", "右侧值": "Right Value",
    "左侧数据库": "Left Database", "右侧数据库": "Right Database",
    "左侧路径": "Left Path", "右侧路径": "Right Path",
    "共同祖先": "Common Ancestor", "冲突策略": "Conflict Policy",
    "排除规则": "Exclude Rules", "忽略列": "Ignore Columns", "忽略大小写": "Ignore Case",
    "忽略空白字符差异": "Ignore Whitespace", "区分大小写": "Match Case",
    "自动换行": "Word Wrap", "语法高亮": "Syntax Highlighting",
    "行详情": "Line Details", "书签": "Bookmark", "缩略图": "Minimap",
    "图片比较": "Picture Compare", "表格比较": "Table Compare",
    "注册表比较": "Registry Compare", "标签/版本比较": "Tags/Version Compare",
    "脚本处理器": "Script Handler", "标签页工作区": "Tabbed Workspace",
    "独立文本编辑器": "Standalone Text Editor", "文件格式": "File Formats",
    "手动对齐": "Manual Alignment", "清除手动对齐": "Clear Manual Alignments",
    "文件名对齐覆盖": "Filename Alignment Overrides",
    "切换浅色 / 深色主题": "Toggle Light / Dark Theme",
    "保存左侧": "Save Left", "保存右侧": "Save Right",
    "全部操作已完成": "All operations completed", "两侧内容一致": "Both sides are identical",
    "两侧内容已一致，没有可合并的差异。": "Both sides are identical; nothing to merge.",
    "当前没有可合并的差异": "No differences to merge", "当前没有差异块": "No difference blocks",
    "当前筛选条件下没有差异": "No differences under the current filter",
    "当前筛选条件下没有匹配的差异": "No matching differences under the current filter",
    "当前模式下没有需要执行的操作。": "Nothing to do in the current mode.",
    "当前没有比较结果。": "No comparison result yet.",
    "当前没有未保存的修改": "No unsaved changes", "存在未保存的修改": "Unsaved changes exist",
    "无可同步操作": "Nothing to sync", "路径不完整": "Incomplete paths",
    "部分条目无法读取": "Some items could not be read",
    "请先开始一次对比": "Start a comparison first",
    "已自动重新比较（可在设置中关闭）": "Auto-recompared (can be disabled in Settings)",
    "已切换到": "Switched to ", "已保存": "Saved", "已取消": "Cancelled",
    "已删除": "Deleted", "已合并": "Merged", "已清空全部比较记录": "Cleared all recent comparisons",
    "已清除全部手动对齐设置": "Cleared all manual alignments",
    "已返回目录对比": "Returned to folder compare", "已打开文本对比": "Opened text compare",
    "已把剪贴板内容作为右侧进行比较": "Clipboard content used as the right side",
    "编辑器字号": "Editor font size", "（Ctrl+滚轮调整）": " (Ctrl+Wheel to zoom)",
    "设置已保存并应用": "Settings saved and applied",
    "正在比较": "Comparing", "正在加载": "Loading", "正在校验路径并比较": "Validating paths and comparing",
    "匹配：": "Matches: ", "匹配": "Match", "处差异": " differences",
    "上一次比较仍在进行，请稍候": "A comparison is still running, please wait",
    "处差异": " differences", "处": "", "个条目": " items", "个文件": " files",
    "个字节不同": " bytes differ", "个字节": " bytes", "个标签": " tags",
    "显示过滤": "Display Filter", "过滤状态下编辑/合并/保存暂停":
        "editing, merge and save are paused while filtered",
    "只影响显示与差异导航，不改数据": "affects display and navigation only, not the data",
    "文件夹": "Folder", "文件": "File", "目录": "Folder", "路径": "Path",
    "名称": "Name", "大小": "Size", "修改时间": "Modified", "状态": "Status",
    "差异": "Difference", "修改": "Modified", "相同": "Same", "冲突": "Conflict",
    "合并": "Merge", "同步": "Sync", "比较": "Compare", "会话": "Session",
    "快照": "Snapshot", "报告": "Report", "脚本": "Script", "设置": "Settings",
    "主题": "Theme", "深色": "Dark", "浅色": "Light", "外观": "Appearance",
    "字体": "Font", "字号": "Font Size", "界面": "Interface", "编辑器": "Editor",
    "左侧": "Left", "右侧": "Right", "左": "Left", "右": "Right",
    "全部": "All", "差别": "Differences", "上一个": "Previous", "下一个": "Next",
    "保存": "Save", "删除": "Delete", "添加": "Add", "新建": "New",
    "确定": "OK", "取消": "Cancel", "关闭": "Close", "应用": "Apply",
    "打开": "Open", "浏览": "Browse", "刷新": "Refresh", "查找": "Find",
    "替换": "Replace", "导出": "Export", "导入": "Import", "生成": "Generate",
    "打印": "Print", "预览": "Preview", "执行": "Run", "确认": "Confirm",
    "备份": "Backup", "恢复": "Restore", "加载": "Load", "启动": "Start",
    "完成": "Done", "成功": "succeeded", "失败": "failed", "错误": "Error",
    "警告": "Warning", "提示": "Hint", "注意": "Note", "操作": "Action",
    "选项": "Options", "规则": "Rules", "模式": "Mode", "类型": "Type",
    "内容": "Content", "结果": "Result", "输出": "Output", "输入": "Input",
    "目标": "Target", "来源": "Source", "祖先": "Ancestor", "冲突": "Conflict",
    "选择": "Select", "选中": "Selected", "勾选": "Check", "清空": "Clear",
    "折叠": "Collapse", "展开": "Expand", "返回": "Back", "就绪": "Ready",
    "排除": "Exclude", "忽略": "Ignore", "对齐": "Alignment", "覆盖": "Override",
    "权限": "Permission", "访问": "Access", "读取": "Read", "写入": "Write",
    "表格": "Table", "图片": "Image", "图像": "Image", "注册表": "Registry",
    "标签": "Tag", "字段": "Field", "参数": "Parameters", "列": "Column",
    "行": "Line", "值": "Value", "地址": "Address", "密码": "Password",
    "令牌": "Token", "用户名": "Username", "端口": "Port", "版本": "Version",
    "说明": "Description", "备注": "Note", "作用范围": "Scope", "启用": "Enable",
    "停用": "Disable", "容差": "Tolerance", "像素": "pixels", "字节": "bytes",
    "创建": "Create", "复制": "Copy", "当前": "Current", "没有": "No ",
    "已": "", "将": "Will ", "会": " will ", "可以": "You can ", "请": "Please ",
    "正在": "Loading ", "仅": "Only ", "共": "Total ", "与": " and ",
    "和": " and ", "或": " or ", "的": " ", "了": "", "在": " at ",
    "按": "By ", "从": "From ", "到": " to ", "用": " with ", "以": " by ",
    "中": " in ", "为": " is ", "不": "not ", "未": "not ",
    "项": "item", "个": "", "张": "", "条": "", "项": "item",
    "（": " (", "）": ") ", "，": ", ", "；": "; ", "。": ". ",
    "：": ": ", "、": ", ", "：": ": ",
}
# 动态状态消息：以 f-string 模板为键，`{}` 对应运行期数值 / 路径。
_PATTERN_EN = {
    "{} 侧替换 {} 处": "{} side: {} replacements",
    "{}（链接 {} 处 / 断开 {} 处）": "{} ({} links / {} breaks)",
    "{}：{} 项，{} 处差异": "{}: {} items, {} differences",
    "{}：（文件不存在，保存时可新建）": "{}: (file does not exist; will be created on save)",
    "三向合并：自动合并 {} 处，冲突 {} 处": "Three-way merge: {} auto-merged, {} conflicts",
    "三向文件夹合并完成：成功 {} 项，冲突 {} 处": "Three-way folder merge complete: {} succeeded, {} conflicts",
    "三向文件夹合并已取消": "Three-way folder merge cancelled",
    "上一次比较仍在进行，请稍候": "A comparison is still running, please wait",
    "书签：{} 个（行 {}）": "Bookmarks: {} (line {})",
    "会话已保存：{}": "Session saved: {}",
    "保存已取消": "Save cancelled",
    "全部 {}": "All {}",
    "十六进制：{} 个字节不同 / {} : {} 字节": "Hex: {} bytes differ / {} : {} bytes",
    "右 {}：{}": "Right {}: {}",
    "右侧：{}": "Right: {}",
    "右：—": "Right: —",
    "合并中 {}/{}：{}": "Merging {}/{}: {}",
    "合并仅在文本对比视图中可用": "Merge is only available in the text compare view",
    "合并已取消": "Merge cancelled",
    "合并结果已保存：{}": "Merge result saved: {}",
    "同步中 {}/{}：{}": "Syncing {}/{}: {}",
    "同步完成：成功 {} 项，失败 {} 项": "Sync complete: {} succeeded, {} failed",
    "后台同步已启动：{} 项": "Background sync started: {} items",
    "图片比较：{}×{}，差异 {} 像素 ({}%)，容差 {}{}":
        "Picture compare: {}×{}, {} differing pixels ({}%), tolerance {}{}",
    "完成：{} 处差异": "Done: {} differences",
    "完成：成功 {} 项，失败 {} 项": "Done: {} succeeded, {} failed",
    "对齐拖拽：起点 {} 第 {} 行，拖到另一侧释放":
        "Alignment drag: start {} line {}, release on the other side",
    "对齐规则已更新，点击“刷新”重新比较": "Alignment rules updated — click Refresh to recompare",
    "导出失败：{}": "Export failed: {}",
    "就绪 · 上次会话无法读取，请重新选择路径": "Ready · last session is unreadable, choose new paths",
    "就绪 · 上次会话的路径已失效，请重新选择路径": "Ready · last session paths have expired, choose new paths",
    "就绪 · 可点击「恢复上次会话」继续，或选择新的比较":
        "Ready · resume the last session, or start a new comparison",
    "就绪 · 选择两个文件或文件夹开始比较": "Ready · choose two files or folders to start",
    "工作区已保存：{}": "Workspace saved: {}",
    "左 {}：{}": "Left {}: {}",
    "左侧：{}": "Left: {}",
    "左：—": "Left: —",
    "差异清单已导出：{}": "Difference list exported: {}",
    "已从命令行载入左侧路径，请选择右侧路径后比较":
        "Left path loaded from the command line; choose the right path and compare",
    "已保存：{}": "Saved: {}",
    "已切换到{}主题": "Switched to {} theme",
    "已删除左第 {} 行的连线": "Removed the link at left line {}",
    "已删除该条比较记录": "Removed the comparison record",
    "已加载工作区：{}（{} 个标签）": "Workspace loaded: {} ({} tabs)",
    "已取消选择，未更改当前对比": "Selection cancelled; current comparison unchanged",
    "已合并 1 处差异，剩余 {} 处（原 {} 处）；请保存目标侧文件":
        "Merged 1 difference, {} remain (of {}); save the target file",
    "已合并 {} 处差异（{}）；请保存目标侧文件": "Merged {} differences ({}); save the target file",
    "已在偏移 {} 写入 {} 字节": "Wrote {} bytes at offset {}",
    "已填入{}侧：{}，可再拖入或选择另一侧": "Filled {} side: {}; drop or choose the other side",
    "已填入文件夹路径：{}；再拖入另一个文件夹（或选择另一侧）即可比较":
        "Folder path filled: {}; drop another folder (or choose the other side) to compare",
    "已应用比较模板（规则与选项已更新）": "Compare template applied (rules and options updated)",
    "已恢复备份：{}": "Backup restored: {}",
    "已打开 {}；把第二个文件拖到右侧窗格即可开始对比":
        "Opened {}; drop a second file onto the right pane to compare",
    "已打开文本对比：{}": "Text compare opened: {}",
    "已打开标签页工作区": "Tabbed workspace opened",
    "已把剪贴板内容作为右侧进行比较": "Clipboard content used as the right side",
    "已拖入{}侧路径：{}": "Dropped {} side path: {}",
    "已按「{}」解决全部冲突": "All conflicts resolved using {}",
    "已按当前编辑内容重新比较": "Recompared using the current edits",
    "已替换 {} 处（仅内存，保存后写入文件）": "Replaced {} matches (in memory only; save to write)",
    "已清空全部比较记录": "Cleared all comparison records",
    "已用 {} 打开：{}": "Opened with {}: {}",
    "已筛选差异类型，当前可导航 {} 处": "Filtered by difference type; {} navigable now",
    "已自动重新比较（可在设置中关闭）": "Auto-recompared (can be disabled in Settings)",
    "已返回目录对比": "Returned to folder compare",
    "当前没有书签": "No bookmarks",
    "当前没有可合并的差异": "No differences to merge",
    "当前没有未保存的修改": "No unsaved changes",
    "当前筛选条件下没有匹配的差异": "No matching differences under the current filter",
    "当前筛选条件下没有差异": "No differences under the current filter",
    "当前行号超出范围，无法对齐": "Line number out of range; cannot align",
    "当前视图没有需要保存的文本内容": "Nothing to save in the current view",
    "恢复上次会话：{}  ⇄  {}": "Resume last session: {}  ⇄  {}",
    "找到 {} 处：{}": "Found {} at {}",
    "报告已发送到打印机": "Report sent to the printer",
    "报告预览已加载": "Report preview loaded",
    "数据源已设置，点击“开始比较”连接并比较": "Data source configured — click Compare to connect and compare",
    "整文件合并完成，两侧内容已一致；请保存目标侧文件":
        "Whole-file merge complete; both sides match. Save the target file",
    "整文件合并已取消": "Whole-file merge cancelled",
    "文件内容读取失败，请查看上方提示": "Could not read the file; see the message above",
    "文件格式规则已保存（{} 条）": "File format rules saved ({} rules)",
    "文本加载失败：{}": "Text loading failed: {}",
    "文本对比：{} — {}": "Text compare: {} — {}",
    "文本读取失败：{}": "Text read failed: {}",
    "暂停": "Pause",
    "未选择任何差异块": "No difference blocks selected",
    "松开即导入：拖入 2 项 = 左右两侧，1 项 = 填入空侧":
        "Release to import: 2 items = left and right, 1 item = fill the empty side",
    "查找：{}": "Find: {}",
    "格式：偏移:字节，如 00000010:41 42": "Format: offset:bytes, e.g. 00000010:41 42",
    "正在加载 {} …": "Loading {} …",
    "正在取消…": "Cancelling…",
    "正在恢复会话：{}": "Restoring session: {}",
    "正在打开 {} …": "Opening {} …",
    "正在校验路径并比较…": "Validating paths and comparing…",
    "正在比较 祖先↔右侧 …": "Comparing ancestor ↔ right …",
    "正在比较 祖先↔左侧 …": "Comparing ancestor ↔ left …",
    "正在比较… {}": "Comparing… {}",
    "比较失败：{}": "Compare failed: {}",
    "比较已取消": "Comparison cancelled",
    "比较规则已更新（{} 条），点击“刷新”重新比较":
        "Compare rules updated ({} rules) — click Refresh to recompare",
    "比较选项已更新，点击“刷新”重新比较以生效": "Compare options updated — click Refresh to apply",
    "没有匹配的文本": "No matching text",
    "目录同步已取消": "Folder sync cancelled",
    "第 {} / {} 处差异：{}": "Difference {} / {}: {}",
    "第 {} / {} 处差异：左 {} / 右 {} 行": "Difference {} / {}: left line {} / right line {}",
    "筛选后显示 {} 个顶层条目，其中差异 {} 项":
        "Filter shows {} top-level items, {} with differences",
    "继续": "Continue",
    "编辑器字号：{}pt（Ctrl+滚轮调整）": "Editor font size: {}pt (Ctrl+Wheel to zoom)",
    "脚本执行完毕，退出码 {}": "Script finished with exit code {}",
    "表格比较：{} 行，{} 处差异，工作表 {} 个":
        "Table compare: {} rows, {} differences, {} sheets",
    "设置已保存并应用": "Settings saved and applied",
    "该书签行不在当前过滤视图中，请切换回「全部」后跳转":
        "That bookmark is outside the current filter; switch back to All to jump",
    "请先开始一次对比": "Start a comparison first",
    "请先打开文本对比": "Open a text comparison first",
    "请填写左右路径": "Enter both the left and right paths",
    "请填写祖先、左侧、右侧三个文件路径": "Enter the ancestor, left and right file paths",
    "过滤模式下不可合并，请先切换回「全部」": "Merging is disabled while filtered; switch back to All",
    "预演报告已保存：{}": "Preview report saved: {}",
}


def _compile_patterns():
    # 字面部分越长越具体，必须先匹配，避免短模板吞掉长模板
    ordered = sorted(_PATTERN_EN.items(),
                     key=lambda item: len(item[0].replace("{}", "")), reverse=True)
    compiled = []
    for source, target in ordered:
        parts = source.split("{}")
        expression = "".join(
            re.escape(part) + ("(.+?)" if index < len(parts) - 1 else "")
            for index, part in enumerate(parts)
        )
        compiled.append((re.compile(r"\A" + expression + r"\Z"), target))
    return compiled


_PATTERNS = _compile_patterns()


_FILE_LIKE = re.compile(r"\.[A-Za-z0-9]{1,6}\Z")


def _translate_fragment(text: str) -> str:
    """翻译模板捕获的中文片段（状态名等），文件名与路径保持原样。"""
    if not text or not _CJK.search(text) or _FILE_LIKE.search(text):
        return text
    exact = _EN.get(text)
    if exact is not None:
        return exact
    if len(text) <= 120 and not _PATHY.search(text):
        result, matched = _glossary(text)
        if matched:
            return result
    return text


def _from_pattern(text: str) -> str | None:
    for expression, target in _PATTERNS:
        found = expression.match(text)
        if found is None:
            continue
        result = target
        for group in found.groups():
            result = result.replace("{}", _translate_fragment(group), 1)
        return result
    return None


_GLOSSARY_KEYS = sorted(_GLOSSARY, key=len, reverse=True)
_CJK = re.compile(r"[\u3400-\u9fff]")
_PATHY = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:Users|home|tmp|var)/|\\\\[\w.-]+\\)")


def _glossary(text: str) -> tuple[str, bool]:
    """术语替换；仅当命中至少一个多字 UI 术语时才认为这是可翻译的界面文案。"""
    out: list[str] = []
    matched = False
    index = 0
    while index < len(text):
        for key in _GLOSSARY_KEYS:
            if text.startswith(key, index):
                value = _GLOSSARY[key]
                if (len(key) > 1 and out and out[-1] and value
                        and out[-1][-1].isalnum() and value[0].isalnum()):
                    out.append(" ")
                out.append(value)
                if len(key) > 1:
                    matched = True
                index += len(key)
                break
        else:
            out.append(text[index])
            index += 1
    result = "".join(out)
    result = re.sub(r"[ \t]{2,}", " ", result)
    result = re.sub(r"\s+([,.;:)\]])", r"\1", result)
    return result.strip(), matched


def set_language(language: str) -> None:
    global _CURRENT
    _CURRENT = language if language in SUPPORTED else "zh"


def current_language() -> str:
    return _CURRENT


def tr(text: str) -> str:
    """翻译已收录的固定文案；未收录词条原样返回（保持既有契约）。"""
    if _CURRENT == "zh" or not text:
        return text
    return _EN.get(text, text)


def tr_ui(text: str) -> str:
    """控件与状态文案翻译：精确词条 → UI 术语表 → 原文。

    只处理含多字 UI 术语的字符串，避免把任意中文数据（文件名、用户内容）误译。
    """
    if _CURRENT == "zh" or not text:
        return text
    exact = _EN.get(text)
    if exact is not None:
        return exact
    if len(text) <= 300 and _CJK.search(text):
        templated = _from_pattern(text)
        if templated is not None:
            return templated
    # 含文件路径的提示不参与术语替换，避免把用户目录名当作界面文案翻译
    if len(text) <= 300 and _CJK.search(text) and not _PATHY.search(text):
        result, matched = _glossary(text)
        if matched:
            return result
    return text

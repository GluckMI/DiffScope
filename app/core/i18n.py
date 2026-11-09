"""轻量多语言支持：以中文原文为键，可选英文翻译；未收录词条回退原文。"""

from __future__ import annotations

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


def set_language(language: str) -> None:
    global _CURRENT
    _CURRENT = language if language in SUPPORTED else "zh"


def current_language() -> str:
    return _CURRENT


def tr(text: str) -> str:
    """翻译界面文案；中文为源语言，未收录或中文模式时原样返回。"""
    if _CURRENT == "zh":
        return text
    return _EN.get(text, text)

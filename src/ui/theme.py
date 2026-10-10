"""主题与样式（QSS）。

提供：
- 浅色 / 深色两套主题配色；
- 统一的 QSS 样式字符串（扁平化、圆角、柔和中性色，符合现代 AI 助手桌面端风格）；
- 图标工厂：优先使用内嵌 SVG（不依赖外部图片资源），部分动作回退到 Qt 标准图标。

样式与逻辑分离：本文件只负责「外观」，组件逻辑在各自模块中。
"""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QStyle

# 主色调（沉稳蓝绿，类 ChatGPT/Claude 风格）
PRIMARY = "#10a37f"
PRIMARY_HOVER = "#0e8c6e"
PRIMARY_PRESS = "#0b7a5f"

_LIGHT: Dict[str, str] = {
    "bg": "#f7f7f8",
    "card": "#ffffff",
    "text": "#1f2328",
    "text_secondary": "#6b7280",
    "border": "#e5e5e5",
    "sidebar": "#fbfbfb",
    "canvas_bg": "#e9eaee",
    "hover": "#eceef0",
    "press": "#e2e4e7",
    "accent": PRIMARY,
    "accent_hover": PRIMARY_HOVER,
    "accent_press": PRIMARY_PRESS,
    "accent_text": "#ffffff",
    "disabled": "#b8bcc2",
    "highlight": "#d7f0e8",
    "highlight_border": "#10a37f",
    "icon": "#374151",
}

_DARK: Dict[str, str] = {
    "bg": "#1e1e1e",
    "card": "#2d2d2d",
    "text": "#e6e6e6",
    "text_secondary": "#9aa0a6",
    "border": "#3a3a3a",
    "sidebar": "#262626",
    "canvas_bg": "#161616",
    "hover": "#3a3a3a",
    "press": "#454545",
    "accent": PRIMARY,
    "accent_hover": PRIMARY_HOVER,
    "accent_press": PRIMARY_PRESS,
    "accent_text": "#ffffff",
    "disabled": "#5a5a5a",
    "highlight": "#15463b",
    "highlight_border": "#2dd4a8",
    "icon": "#d4d4d4",
}

THEMES = ("light", "dark")


def _palette(theme: str) -> Dict[str, str]:
    return _DARK if theme == "dark" else _LIGHT


def build_qss(theme: str) -> str:
    """根据主题生成全局 QSS 字符串。"""
    c = _palette(theme)
    return f"""
    QWidget {{
        background-color: {c['bg']};
        color: {c['text']};
        font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
        font-size: 13px;
    }}

    /* ---------- 顶部工具栏（QToolBar：窄窗口时自动把次要控件收进「»」） ---------- */
    QWidget#TopBar {{
        background-color: {c['card']};
        border-bottom: 1px solid {c['border']};
    }}
    QToolBar#TopBar {{
        background-color: {c['card']};
        border: none;
        border-bottom: 1px solid {c['border']};
        padding: 3px 6px;
        spacing: 2px;
    }}
    QToolBar#TopBar::separator {{
        background-color: {c['border']};
        width: 1px;
        margin: 7px 5px;
    }}
    /* 溢出按钮（Qt 自带，objectName 固定） */
    QToolBar#TopBar QToolButton#qt_toolbar_ext_button {{
        padding: 4px 6px;
        min-height: 30px;
    }}
    QWidget#TopBar QToolButton {{
        background-color: transparent;
        color: {c['text']};
        border: 1px solid transparent;
        border-radius: 8px;
        /* 紧凑排布：一个按钮 ≈34px，1280 宽的窗口才装得下核心组 + 翻页 + 视图 */
        padding: 3px 5px;
        min-height: 30px;
    }}
    QWidget#TopBar QToolButton:hover {{
        background-color: {c['hover']};
    }}
    QWidget#TopBar QToolButton:pressed {{
        background-color: {c['press']};
    }}
    QWidget#TopBar QToolButton:checked {{
        background-color: {c['accent']};
        color: {c['accent_text']};
    }}
    QWidget#TopBar QToolButton:disabled {{
        color: {c['disabled']};
    }}
    QWidget#TopBar QLabel {{
        color: {c['text_secondary']};
        background-color: transparent;
    }}
    QWidget#TopBar QSlider::groove:horizontal {{
        height: 4px;
        border-radius: 2px;
        background: {c['border']};
    }}
    QWidget#TopBar QSlider::handle:horizontal {{
        width: 14px; height: 14px;
        margin: -5px 0;
        border-radius: 7px;
        background: {c['accent']};
    }}

    /* ---------- 侧边栏 ---------- */
    QWidget#Sidebar {{
        background-color: {c['sidebar']};
        border-right: 1px solid {c['border']};
    }}
    QWidget#Sidebar QLabel#SideTitle {{
        color: {c['text_secondary']};
        font-weight: 600;
        font-size: 12px;
        padding: 4px 2px;
    }}
    QWidget#Sidebar QPushButton#PrimaryBtn {{
        background-color: {c['accent']};
        color: {c['accent_text']};
        border: none;
        border-radius: 8px;
        padding: 8px;
        font-weight: 600;
    }}
    QWidget#Sidebar QPushButton#PrimaryBtn:hover {{
        background-color: {c['accent_hover']};
    }}
    QWidget#Sidebar QPushButton#PrimaryBtn:pressed {{
        background-color: {c['accent_press']};
    }}
    QWidget#Sidebar QListWidget {{
        background-color: transparent;
        border: 1px solid {c['border']};
        border-radius: 8px;
    }}
    QWidget#Sidebar QListWidget::item {{
        padding: 5px 7px;
        border-radius: 5px;
        color: {c['text']};
    }}
    QWidget#Sidebar QListWidget::item:hover {{
        background-color: {c['hover']};
    }}
    QWidget#Sidebar QListWidget::item:selected {{
        background-color: {c['highlight']};
        color: {c['text']};
    }}

    /* ---------- 文档标签栏（浏览器式多标签） ---------- */
    QTabWidget#DocTabs::pane {{
        border: none;
        background-color: {c['canvas_bg']};
    }}
    QTabWidget#DocTabs > QTabBar::tab {{
        background-color: {c['card']};
        color: {c['text_secondary']};
        border: 1px solid {c['border']};
        border-bottom: none;
        padding: 6px 10px;
        margin-right: 2px;
        border-radius: 8px 8px 0 0;
        min-width: 76px;
        max-width: 190px;
        min-height: 16px;
    }}
    QTabWidget#DocTabs > QTabBar::tab:!selected {{
        margin-top: 3px;
    }}
    QTabWidget#DocTabs > QTabBar::tab:hover {{
        background-color: {c['hover']};
        color: {c['text']};
    }}
    QTabWidget#DocTabs > QTabBar::tab:selected {{
        background-color: {c['canvas_bg']};
        color: {c['text']};
        font-weight: 600;
        border-top: 2px solid {c['accent']};
    }}
    /* 注意：这里**故意不写** QTabBar::close-button 的样式。
       QSS 一旦接管 close-button 子控件，Qt 就不再绘制按钮自身的图标，
       × 会变成一块空白（曾经的真实缺陷）；× 的图标改由 QIcon 提供，
       见 main_window._style_tab_close_buttons()。 */
    QTabWidget#DocTabs > QTabBar::tab {{
        padding-right: 4px;
    }}
    QToolButton#NewTabButton {{
        border: none;
        border-radius: 6px;
        padding: 2px 8px;
        color: {c['text_secondary']};
        font-size: 15px;
    }}
    QToolButton#NewTabButton:hover {{
        background-color: {c['hover']};
        color: {c['text']};
    }}

    /* ---------- 侧边栏分页（页面 / 目录 / 搜索） ---------- */
    QWidget#Sidebar QTabWidget::pane {{
        border: none;
        background: transparent;
    }}
    QWidget#Sidebar QTabBar::tab {{
        background: transparent;
        color: {c['text_secondary']};
        border: none;
        padding: 5px 10px;
        margin-right: 2px;
        border-bottom: 2px solid transparent;
        font-size: 12px;
    }}
    QWidget#Sidebar QTabBar::tab:hover {{
        background: {c['hover']};
        border-radius: 6px 6px 0 0;
    }}
    QWidget#Sidebar QTabBar::tab:selected {{
        color: {c['accent']};
        font-weight: 600;
        border-bottom: 2px solid {c['accent']};
    }}
    QWidget#Sidebar QTabBar::tab:disabled {{
        color: {c['disabled']};
    }}
    QWidget#Sidebar QLineEdit {{
        background-color: {c['card']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 4px 6px;
    }}
    QWidget#Sidebar QLineEdit:focus {{ border-color: {c['accent']}; }}
    QWidget#Sidebar QPushButton {{
        background-color: {c['card']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 5px 8px;
    }}
    QWidget#Sidebar QPushButton:hover {{ background-color: {c['hover']}; }}
    QWidget#Sidebar QPushButton:pressed {{ background-color: {c['press']}; }}
    QWidget#Sidebar QPushButton:disabled {{ color: {c['disabled']}; }}
    QWidget#Sidebar QTreeWidget {{
        background-color: transparent;
        border: 1px solid {c['border']};
        border-radius: 8px;
        color: {c['text']};
    }}
    QWidget#Sidebar QTreeWidget::item {{
        padding: 4px 6px;
        border-radius: 5px;
    }}
    QWidget#Sidebar QTreeWidget::item:hover {{ background-color: {c['hover']}; }}
    QWidget#Sidebar QTreeWidget::item:selected {{
        background-color: {c['highlight']};
        color: {c['text']};
    }}
    QWidget#Sidebar QCheckBox {{ color: {c['text_secondary']}; background: transparent; }}

    /* ---------- 状态栏 ---------- */
    QStatusBar {{
        background-color: {c['card']};
        color: {c['text_secondary']};
        border-top: 1px solid {c['border']};
        font-size: 12px;
    }}
    QStatusBar::item {{ border: none; }}

    /* ---------- 通用控件 ---------- */
    QMenuBar {{
        background-color: {c['card']};
        border-bottom: 1px solid {c['border']};
    }}
    QMenuBar::item:selected {{ background-color: {c['hover']}; }}
    QMenu {{
        background-color: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 8px;
    }}
    QMenu::item:selected {{ background-color: {c['highlight']}; }}
    QMessageBox {{ background-color: {c['card']}; }}
    QPushButton#ColorButton {{
        border: 1px solid {c['border']};
        border-radius: 6px;
    }}
    QPushButton#ColorButton:hover {{ border-color: {c['accent']}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border']};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {c['text_secondary']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """


# ---------------------------------------------------------------------- #
# 内嵌 SVG 图标（不依赖外部资源）
# ---------------------------------------------------------------------- #
_SVGS = {
    "pen": '<svg viewBox="0 0 24 24" fill="{{c}}"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>',
    "eraser": '<svg viewBox="0 0 24 24" fill="{{c}}"><path d="M4 14l6-6 8 8-4 4H8z" opacity="0.92"/><path d="M13 5l6 6-2 2-6-6z"/></svg>',
    "highlighter": '<svg viewBox="0 0 24 24" fill="{{c}}"><path d="M15.5 2.6 21.4 8.5 11 18.9 5.1 13z" opacity="0.38"/><path d="M16.9 1.2 22.8 7.1 20.5 9.4 14.6 3.5z"/><path d="M3 20.4h8v1.8H3z"/></svg>',
    "read_single": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="1.7" stroke-linecap="round"><rect x="4.5" y="2.8" width="15" height="18.4" rx="1.6"/><path d="M8 7h8M8 10.6h8M8 14.2h5"/></svg>',
    "read_continuous": '<svg viewBox="0 0 24 24" fill="{{c}}"><rect x="4" y="2.4" width="16" height="6.6" rx="1.2"/><rect x="4" y="10.6" width="16" height="6.6" rx="1.2" opacity="0.5"/><rect x="4" y="18.8" width="16" height="2.8" rx="1.2" opacity="0.32"/></svg>',
    "sun": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.2" fill="{{c}}"/><line x1="12" y1="3" x2="12" y2="5.5"/><line x1="12" y1="18.5" x2="12" y2="21"/><line x1="3" y1="12" x2="5.5" y2="12"/><line x1="18.5" y1="12" x2="21" y2="12"/><line x1="5.2" y1="5.2" x2="6.9" y2="6.9"/><line x1="17.1" y1="17.1" x2="18.8" y2="18.8"/><line x1="18.8" y1="5.2" x2="17.1" y2="6.9"/><line x1="6.9" y1="17.1" x2="5.2" y2="18.8"/></svg>',
    "moon": '<svg viewBox="0 0 24 24" fill="{{c}}"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
    "menu": '<svg viewBox="0 0 24 24" fill="{{c}}"><path d="M3 6h18v2H3zM3 11h18v2H3zM3 16h18v2H3z"/></svg>',
    "fit": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9V5a1 1 0 0 1 1-1h4"/><path d="M20 9V5a1 1 0 0 0-1-1h-4"/><path d="M4 15v4a1 1 0 0 0 1 1h4"/><path d="M20 15v4a1 1 0 0 1-1 1h-4"/></svg>',
    "zoomin": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="6.5"/><line x1="15.4" y1="15.4" x2="21" y2="21"/><line x1="10.5" y1="7.5" x2="10.5" y2="13.5"/><line x1="7.5" y1="10.5" x2="13.5" y2="10.5"/></svg>',
    "zoomout": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="6.5"/><line x1="15.4" y1="15.4" x2="21" y2="21"/><line x1="7.5" y1="10.5" x2="13.5" y2="10.5"/></svg>',
    "search": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="6.5"/><line x1="15.4" y1="15.4" x2="21" y2="21"/></svg>',
    "rotleft": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5A9 9 0 1 1 3.6 15"/><path d="M3 4v5h5"/></svg>',
    "rotright": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.5 8.5A9 9 0 1 0 20.4 15"/><path d="M21 4v5h-5"/></svg>',
    "fitwidth": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5v14"/><path d="M20 5v14"/><line x1="8" y1="12" x2="16" y2="12"/><path d="M10.5 9.5 8 12l2.5 2.5"/><path d="M13.5 9.5 16 12l-2.5 2.5"/></svg>',
    "info": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16.5"/><line x1="12" y1="7.6" x2="12" y2="7.6"/></svg>',
    "toc": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2" stroke-linecap="round"><line x1="4" y1="6.5" x2="20" y2="6.5"/><line x1="8" y1="12" x2="20" y2="12"/><line x1="8" y1="17.5" x2="20" y2="17.5"/></svg>',
    "copy": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 5.5A1.5 1.5 0 0 0 13.5 4H5.5A1.5 1.5 0 0 0 4 5.5v8A1.5 1.5 0 0 0 5.5 15"/></svg>',
    # 标签关闭按钮（×）：QSS 的 QTabBar::close-button 只能设 image: url(磁盘文件)，
    # 而本项目坚持零外部资源，所以 × 由 QIcon 贴到关闭按钮上（见 main_window._style_tab_close_buttons）。
    "close": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="2.4" stroke-linecap="round"><line x1="6.5" y1="6.5" x2="17.5" y2="17.5"/><line x1="17.5" y1="6.5" x2="6.5" y2="17.5"/></svg>',
    # 标签页图标：一眼区分「笔记」与「PDF」
    "tab_note": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4.5h9.5L19 9v10.5H5z"/><path d="M14 4.5V9h5"/><path d="M8 13h8"/><path d="M8 16.5h5"/></svg>',
    "tab_pdf": '<svg viewBox="0 0 24 24" fill="none" stroke="{{c}}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4.5h9.5L19 9v10.5H5z"/><path d="M14 4.5V9h5"/><path d="M8.5 17v-4.2h1.6a1.4 1.4 0 0 1 0 2.8H8.5"/><path d="M14.2 17v-4.2h1.5a2.1 2.1 0 0 1 0 4.2z"/></svg>',
}


class ThemeManager:
    """管理当前主题并提供配色、QSS 与图标。"""

    def __init__(self, theme: str = "light"):
        self.theme = theme if theme in THEMES else "light"

    def colors(self) -> Dict[str, str]:
        return _palette(self.theme)

    def qss(self) -> str:
        return build_qss(self.theme)

    def canvas_bg(self) -> str:
        return self.colors()["canvas_bg"]

    # ----- 图标 -----
    def svg_icon(self, name: str, size: int = 20) -> QIcon:
        svg = _SVGS.get(name)
        if not svg:
            return QIcon()
        color = self.colors()["icon"]
        svg = svg.replace("{{c}}", color)
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        if not renderer.isValid():
            return QIcon()
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        renderer.render(painter)
        painter.end()
        return QIcon(pm)

    @staticmethod
    def std_icon(sp: QStyle.StandardPixmap) -> QIcon:
        return QApplication.style().standardIcon(sp)

    def icon(self, kind: str, size: int = 20) -> QIcon:
        """按动作类型返回图标。kind 见 main_window 中的使用。"""
        std_map = {
            "new": QStyle.StandardPixmap.SP_FileIcon,
            "open": QStyle.StandardPixmap.SP_DirOpenIcon,
            "save": QStyle.StandardPixmap.SP_DialogSaveButton,
            "export": QStyle.StandardPixmap.SP_DialogSaveButton,
            "undo": QStyle.StandardPixmap.SP_ArrowBack,
            "redo": QStyle.StandardPixmap.SP_ArrowForward,
            "prev": QStyle.StandardPixmap.SP_ArrowUp,
            "next": QStyle.StandardPixmap.SP_ArrowDown,
        }
        if kind in std_map:
            return self.std_icon(std_map[kind])
        # 内嵌 SVG 图标（不依赖外部资源）
        if kind in _SVGS:
            return self.svg_icon(kind, size)
        return QIcon()

    def toggle(self) -> str:
        self.theme = "dark" if self.theme == "light" else "light"
        return self.theme


#: 应用图标（打包 exe 的图标、任务栏/Alt-Tab、`make_icon.py` 生成 .ico 都用这一份）
APP_ICON_SVG = (
    '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">'
    '<rect x="3" y="3" width="58" height="58" rx="14" fill="#10a37f"/>'
    '<path d="M14 20h22M14 28h14" stroke="#ffffff" stroke-width="4.2" '
    'stroke-linecap="round" opacity="0.9"/>'
    '<path d="M18 46c7-1 8-13 14-19 3-3 7-3 8 0 1 4-3 7-9 8-7 1-13 4-13 4" '
    'fill="none" stroke="#ffffff" stroke-width="4.6" stroke-linecap="round" '
    'stroke-linejoin="round"/>'
    '</svg>'
)


def app_icon(size: int = 256) -> QIcon:
    """应用图标（内嵌 SVG 渲染，不依赖外部资源，任何主题下都是同一枚）。"""
    renderer = QSvgRenderer(QByteArray(APP_ICON_SVG.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return QIcon(pm)

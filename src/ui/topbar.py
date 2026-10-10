"""响应式顶栏：窗口变窄时，次要控件自动收进「»」溢出菜单。

为什么用 QToolBar
----------------
Qt 的 `QToolBar` 自带「放不下就进溢出菜单」的机制，而且它会把**原控件**搬进弹出菜单、
关闭后再搬回原位——滑块、下拉框、页码框这类复合控件不会失效，也不需要我们写代理控件。
这正是浏览器式工具栏的心智（Chrome 的 ⋯ / Firefox 的 »）。

旧实现是一条 `QHBoxLayout` 硬排 36 个控件：布局 minimumSize 达 **1625px**，加上 248px
侧边栏，窗口最小宽度被顶到 **1873px** —— 在 150% 缩放的 1920×1080 屏上（逻辑可用宽度
只有 1280）窗口比屏幕还宽，右侧按钮必然被切掉。

约定
----
* `add_core(widget)`：核心控件，永远留在栏上 —— 窗口的最小宽度由它们决定；
* `add_optional(widget)`：次要控件，**越晚加入越先被收进溢出菜单**（顺序即优先级）；
* `separator()`：分组竖线（跟随主题，也会随所在组一起被收起）。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QToolBar, QWidget

#: 控件之间的水平间距（与 QSS 的 QToolBar#TopBar spacing 保持一致）
SPACING = 3
#: 工具栏左右内边距（与 QSS 的 padding 保持一致）
PADDING = 16


class TopBar(QToolBar):
    """带溢出菜单的响应式顶栏。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setMovable(False)
        self.setFloatable(False)
        self.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
        # 图标 18px：150% 缩放下依然清晰，又比 20px 省出约 2px/按钮的横向空间
        self.setIconSize(QSize(18, 18))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._core: List[QWidget] = []
        self._optional: List[QWidget] = []

    # ------------------------------------------------------------------ #
    # 组装
    # ------------------------------------------------------------------ #
    def add_core(self, widget: QWidget) -> QWidget:
        """核心控件：窄窗口下也不会被收起。"""
        self._core.append(widget)
        self.addWidget(widget)
        return widget

    def add_optional(self, widget: QWidget) -> QWidget:
        """次要控件：**越晚加入越先被收进溢出菜单**。"""
        self._optional.append(widget)
        self.addWidget(widget)
        return widget

    def separator(self) -> None:
        """分组竖线（颜色由 QSS 的 QToolBar::separator 提供，跟随主题）。"""
        self.addSeparator()

    # ------------------------------------------------------------------ #
    # 查询（供窗口最小宽度与测试使用）
    # ------------------------------------------------------------------ #
    def core_widgets(self) -> List[QWidget]:
        return list(self._core)

    def optional_widgets(self) -> List[QWidget]:
        return list(self._optional)

    def core_minimum_width(self) -> int:
        """核心控件全部显示所需的最小宽度（用来推算窗口最小宽度）。"""
        total = 0
        for w in self._core:
            total += max(w.minimumSizeHint().width(), w.sizeHint().width())
        total += SPACING * max(0, len(self._core) - 1)
        return total + PADDING

    def suggested_minimum_width(self) -> int:
        """建议的最小窗口宽度 = 核心控件 + 一点画布余量。"""
        return self.core_minimum_width() + 200

    def hidden_widgets(self) -> List[QWidget]:
        """当前被收进溢出菜单的控件（Qt 会把它们的 visible 置 False）。"""
        return [w for w in self._optional if not w.isVisible()]

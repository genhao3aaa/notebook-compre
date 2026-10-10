"""浏览器式标签栏。

只负责几件 Qt 默认不提供的事，其余行为交给 QTabWidget：

* 中键点击标签 → 关闭（浏览器的肌肉记忆）
* 双击标签栏空白处 → 新建标签
* **把标签拖出标签栏 → 请求开一个独立窗口**（浏览器式「撕下标签」，
  由 `MainWindow.detach_tab()` 落地；拖动时有个跟随鼠标的小预览）
* 标签增删 → 发出 `tabs_changed`（Qt 把 `tabInserted/tabRemoved` 做成虚函数而不是信号，
  外面 connect 不到，所以在这里转成信号）

事件里统一用 `_pos()` 取坐标：PySide6 的 `QMouseEvent.position()` 返回 QPointF，
而 `QTabBar.tabAt()` 需要 QPoint；同时保留 `pos()` 兜底，避免不同 Qt 版本差异。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QLabel, QTabBar

#: 拖出判定：鼠标离开标签栏边缘多少像素才算「撕下来」
#: （留一点余量，避免手抖或按下时轻微移动就弹出新窗口）
DETACH_MARGIN = 24


class _TabGhost(QLabel):
    """拖动时跟着鼠标走的小预览：Chrome 里「手里拎着一个标签」的那种感觉。

    用 `Qt.ToolTip` 窗口类型：无边框、不抢焦点、始终在最上层，不会进入任务栏。
    """

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "QLabel { background-color: rgba(32, 33, 36, 225); color: #ffffff;"
            " border: 1px solid rgba(255, 255, 255, 60); border-radius: 7px;"
            " padding: 4px 12px; font-size: 12px; }")

    def show_for(self, text: str, global_pos: QPoint) -> None:
        self.setText(text)
        self.adjustSize()
        self.move(global_pos + QPoint(14, 16))
        self.show()


class DocTabBar(QTabBar):
    """支持「中键关闭 / 双击空白新建 / 拖出成独立窗口」的标签栏。"""

    #: 中键点击了第 index 个标签
    middle_clicked = Signal(int)
    #: 双击了标签栏右侧的空白区域
    empty_double_clicked = Signal()
    #: 标签增删（Qt 把 tabInserted/tabRemoved 做成虚函数，这里转成信号）
    tabs_changed = Signal()
    #: 在某个标签上按下了左键（主窗口据此记住「被拖走的是哪个文档」）
    tab_pressed = Signal(int)
    #: 标签被拖到了标签栏外面 → 请求撕成独立窗口
    detach_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setElideMode(Qt.ElideRight)
        self._press_index = -1
        self._detaching = False
        self._ghost: Optional[_TabGhost] = None

    # ------------------------------------------------------------------ #
    # Qt 虚函数 → 信号
    # ------------------------------------------------------------------ #
    def tabInserted(self, index: int) -> None:
        super().tabInserted(index)
        self.tabs_changed.emit()

    def tabRemoved(self, index: int) -> None:
        super().tabRemoved(index)
        self.tabs_changed.emit()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _pos(e) -> QPoint:
        try:
            return e.position().toPoint()
        except AttributeError:  # pragma: no cover - 老版本 Qt 兜底
            return e.pos()

    def _outside(self, pos: QPoint) -> bool:
        """鼠标是否已经离开标签栏（含余量）。"""
        return (pos.y() < -DETACH_MARGIN or pos.y() > self.height() + DETACH_MARGIN
                or pos.x() < -DETACH_MARGIN or pos.x() > self.width() + DETACH_MARGIN)

    def _show_ghost(self, index: int, pos: QPoint) -> None:
        if self._ghost is None:
            self._ghost = _TabGhost()
        text = self.tabText(index).strip() or "标签"
        if len(text) > 24:
            text = text[:24] + "…"
        self._ghost.show_for(text, self.mapToGlobal(pos))

    def _hide_ghost(self) -> None:
        if self._ghost is not None:
            self._ghost.hide()

    # ------------------------------------------------------------------ #
    # 鼠标：拖出成独立窗口
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_index = self.tabAt(self._pos(e))
            self._detaching = False
            if self._press_index >= 0:
                self.tab_pressed.emit(self._press_index)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        # 先交给 Qt：标签栏内部的拖拽重排仍然由它负责
        super().mouseMoveEvent(e)
        if self._press_index < 0 or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        pos = self._pos(e)
        if self._outside(pos):
            self._detaching = True
            self._show_ghost(self._press_index, pos)
        elif self._detaching:
            # 又拖回标签栏里了：取消「撕下」，恢复成普通重排
            self._detaching = False
            self._hide_ghost()

    def mouseReleaseEvent(self, e) -> None:
        if (e.button() == Qt.MouseButton.MiddleButton
                and self.tabAt(self._pos(e)) >= 0):
            self.middle_clicked.emit(self.tabAt(self._pos(e)))
            e.accept()
            return
        detach = self._detaching and e.button() == Qt.MouseButton.LeftButton
        self._press_index = -1
        self._detaching = False
        self._hide_ghost()
        super().mouseReleaseEvent(e)
        if detach:
            # 放在 super() 之后发：此时 Qt 的内部拖拽已经收尾，标签顺序也定了
            self.detach_requested.emit()

    def mouseDoubleClickEvent(self, e) -> None:
        if self.tabAt(self._pos(e)) < 0:
            self.empty_double_clicked.emit()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

"""多窗口与会话（A7 拆分自 main_window.py）：撕标签成独立窗口、合并回窗口、退出恢复。

关键点：
- 窗口登记用**弱引用**，`showEvent` 登记、`closeEvent` 注销；
- 搬标签是「整份文档搬走」而不是复制（画布、撤销栈、PDF 句柄、阅读进度一起走）；
- 会话只在最后一个窗口关闭时写一次，否则多窗口互相覆盖。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCursor,
                           QGuiApplication, QKeySequence, QShortcut)
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QFileDialog,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSlider, QSpinBox, QSplitter, QStatusBar,
                               QTabBar, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)
from src.core.document_tab import DocumentTab, NOTE, PDF, same_path
from src.ui import win_feedback
from src.ui.canvas import PageCanvas
from src.ui.layout_constants import APP_TITLE, COORD_LABEL_MIN_W, IDLE_CACHE_PAGES, MAX_RESTORED_TABS, SIDEBAR_AUTO_HIDE_W, SIDEBAR_MAX_W, SIDEBAR_MIN_W, WINDOW_MIN_H, WINDOW_MIN_W
from src.ui.window_registry import OPEN_WINDOWS as _OPEN_WINDOWS
from src.ui.window_registry import live_windows as _live_windows



if TYPE_CHECKING:      # 只给类型检查器看；运行时惰性导入，避免循环导入
    from src.ui.main_window import MainWindow


class WindowOpsMixin:
    def _on_detach_requested(self) -> None:
        tab, self._dragged_tab = self._dragged_tab, None
        if tab is not None:
            self.detach_tab(tab, QCursor.pos())
    def detach_tab(self, tab: Optional[DocumentTab],
                   global_pos: Optional[QPoint] = None) -> Optional["MainWindow"]:
        """把一个标签「撕」成独立窗口；若松手时在别的窗口标签栏上，则并进那个窗口。

        文档不会被复制：画布、撤销栈、阅读进度、搜索状态整份搬过去，
        拖出来之后接着写、接着读，和原来完全一样。
        """
        from src.ui.main_window import MainWindow  # 循环导入：运行时才需要
        if tab is None or tab.canvas is None or tab not in self._tabs:
            return None
        if len(self._tabs) <= 1:
            self._status("只剩一个标签时不能拖出窗口（用 Ctrl+T 先新建一个）", 3000)
            return None
        pos = global_pos or QCursor.pos()

        target = self._window_at(pos)
        if target is not None and target is not self:
            self._move_tab_to_window(tab, target)
            target.raise_()
            target.activateWindow()
            self._status(f"已移动到另一个窗口：{tab.display_name()}", 2500)
            return target

        win = MainWindow(settings=self.settings, theme_name=self.theme.theme,
                         restore_session=False, restore_geometry=False)
        win.resize(self.width(), self.height())
        win.move(max(0, pos.x() - 80), max(0, pos.y() - 18))
        win.show()
        self._move_tab_to_window(tab, win)
        win.raise_()
        win.activateWindow()
        self._status(f"已拖出为独立窗口：{tab.display_name()}", 2500)
        return win
    def _move_tab_to_window(self, tab: DocumentTab, target: "MainWindow") -> None:
        """把标签连同文档状态搬到另一个窗口（**不释放**文档资源）。"""
        index = self.tab_widget.indexOf(tab.canvas) if tab.canvas is not None else -1
        was_active = tab is self._active
        if index >= 0:
            self.tab_widget.blockSignals(True)
            self.tab_widget.removeTab(index)
            self.tab_widget.blockSignals(False)
        if tab in self._tabs:
            self._tabs.remove(tab)
        if was_active:
            self._active = None

        # 源窗口不能空着（浏览器也不会留一个没有标签的窗口）
        if not self._tabs:
            self.new_tab_note()
        elif was_active:
            self._switch_to_tab(self._tabs[min(max(index, 0), len(self._tabs) - 1)])
        else:
            self._update_window_title()

        # 画布信号与回调原来挂在源窗口上：先摘掉，交给目标窗口重接
        self.unbind_canvas_from_window(tab.canvas)
        target.adopt_tab(tab)
    def adopt_tab(self, tab: DocumentTab, activate: bool = True) -> None:
        """接管一个标签：丢掉本窗口开局的空白占位标签，挂上去并重新接线。"""
        self._discard_placeholder()
        if tab.canvas is not None:
            self._connect_canvas(tab.canvas)       # 信号接到本窗口
            self._apply_tool_settings_to(tab.canvas)   # 工具样式跟人走
            self.bind_canvas_to_window(tab)        # 连续阅读回调也指向本窗口
        self._add_tab(tab, activate=activate)
        self._refresh_tab_title(tab)
        if activate and self.canvas is not None:
            self.canvas.setFocus()
            self._update_window_title()
    def _discard_placeholder(self) -> None:
        """丢掉「开局占位」的空白标签（只有它一个、无路径、无内容、未改过）。"""
        if len(self._tabs) != 1:
            return
        tab = self._tabs[0]
        if tab.path or tab.dirty or tab.kind != NOTE or tab.note_doc is None:
            return
        if any(page.strokes for page in tab.note_doc.pages):
            return
        canvas = tab.canvas
        index = self.tab_widget.indexOf(canvas) if canvas is not None else -1
        if index >= 0:
            self.tab_widget.blockSignals(True)
            self.tab_widget.removeTab(index)
            self.tab_widget.blockSignals(False)
        self._tabs.remove(tab)
        self._active = None
        tab.release()
        if canvas is not None:
            canvas.setParent(None)
            canvas.deleteLater()
    @staticmethod
    def _window_at(global_pos: QPoint) -> Optional["MainWindow"]:
        """鼠标位置落在哪个窗口的标签栏上（用于「拖到别的窗口合并」）。

        窗口可能互相重叠，这时按 Z 序取「鼠标下真正看得见的那一个」；
        `widgetAt` 在离屏测试里拿不到结果时，退化为取最后创建（通常在最上层）的那个。
        """
        from src.ui.main_window import MainWindow  # 循环导入：运行时才需要
        hits = []
        for win in _live_windows():
            if not win.isVisible():
                continue
            bar = win.tab_widget.tabBar()
            top_left = bar.mapToGlobal(QPoint(0, 0))
            rect = QRect(top_left, QSize(bar.width(), max(bar.height(), 30)))
            if rect.contains(global_pos):
                hits.append(win)
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]
        widget = QApplication.widgetAt(global_pos)
        while widget is not None:
            if isinstance(widget, MainWindow) and widget in hits:
                return widget
            widget = widget.parentWidget()
        return hits[-1]
    def bind_canvas_to_window(self, tab: DocumentTab) -> None:
        """把标签的画布接回**本窗口**：信号 + 连续阅读的两个回调。

        为什么单独成函数：把标签拖到别的窗口时，画布会换主人。若不重接，
        它会继续把「切到第 N 页」的请求发给旧窗口（旧窗口可能已经关了），
        而新窗口的撤销按钮 / 状态栏也收不到画布事件。
        """
        canvas = tab.canvas
        if canvas is None:
            return
        canvas.stroke_provider = lambda idx, t=tab: self._pdf_strokes_for(t, idx)
        canvas.on_page_request = lambda idx, t=tab: self._on_canvas_page_request(t, idx)
    def unbind_canvas_from_window(self, canvas: PageCanvas) -> None:
        """把画布与本窗口断开（只断信号，画布自身状态不动）。"""
        if canvas is None:
            return
        for name in self.CANVAS_SIGNALS:
            sig = getattr(canvas, name, None)
            if sig is None:
                continue
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
    def _save_session(self) -> None:
        """把**所有窗口**的标签记下来（多窗口时只记自己这一扇会丢标签）。

        只在最后一个窗口关闭时写盘，否则多个窗口会互相覆盖。
        """
        windows = _live_windows(exclude=self)
        windows.append(self)   # 自己可能已经不在表里（最后一个窗口正在关）
        items = []
        for w in windows:
            for t in w._tabs:
                if t.path:
                    items.append({"kind": t.kind, "path": t.path})
        try:
            self.settings.set_open_tabs(items[:MAX_RESTORED_TABS])
        except Exception:
            pass
    def _restore_session(self) -> bool:
        """恢复上次退出时打开的标签。返回是否至少恢复了一个。"""
        # 开局的空白标签只是「占位」（没有它工具栏建不起来），恢复成功就丢掉
        placeholder = self._active if len(self._tabs) == 1 else None
        try:
            items = self.settings.open_tabs() or []
        except Exception:
            return False
        opened = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not path or not os.path.exists(path):
                continue
            try:
                if path.lower().endswith(".pdf"):
                    tab = self.open_pdf(path, activate=False)
                else:
                    tab = self.open_note(path, activate=False)
            except Exception:
                tab = None
            if tab is not None:
                opened += 1
            if opened >= MAX_RESTORED_TABS:
                break
        if not opened:
            return False
        if (placeholder is not None and placeholder in self._tabs
                and not placeholder.dirty and len(self._tabs) > 1):
            self.close_tab(placeholder, ask_save=False)
        else:
            first = self._tabs[0] if self._tabs else None
            if first is not None:
                self._switch_to_tab(first)
        self._status(f"已恢复上次打开的 {opened} 个标签", 3000)
        return True
    def _confirm_save(self, tab: Optional[DocumentTab]) -> bool:
        """关闭标签/退出前询问是否保存。返回 False 表示用户取消。"""
        if tab is None or not tab.dirty:
            return True
        reply = QMessageBox.question(
            self, "未保存的更改",
            f"「{tab.display_name()}」有未保存的更改，是否保存？",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Yes:
            # save() 作用于当前标签，询问前已确保 tab 被激活
            if self._active is not tab:
                self._switch_to_tab(tab)
            self.save()
        return True
    def showEvent(self, event) -> None:
        """窗口真正显示时才登记进多窗口表。

        为什么不在 `__init__` 里登记：`close()` 对「从未显示过」的窗口**不会**产生
        closeEvent，于是这类窗口会永久留在表里 —— 多窗口会话汇总时会把它们的标签
        也算进来（真机上不会出现，但测试与将来的脚本会踩到）。
        """
        super().showEvent(event)
        _OPEN_WINDOWS.add(self)
    def event(self, e) -> None:
        """窗口每次显示 / 原生句柄重建时重新下发反馈策略（Qt 会重建 HWND）。"""
        if e.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange):
            policy = getattr(self, "_touch_feedback", None)
            if policy:
                win_feedback.apply_policy(self, policy)
        return super().event(e)
    def changeEvent(self, event) -> None:
        """窗口失去焦点时把阅读进度落盘。

        为什么不等关闭：用户往往是「开着书，切去别的事，过几天再回来」，后台窗口
        不该在关闭时用旧页码覆盖别的窗口，但**它自己的最新位置必须先存下来**。
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            for tab in list(getattr(self, "_tabs", [])):
                self._save_read_state(tab)
    def closeEvent(self, event) -> None:
        # A4：先把后台保存写完，再问「是否保存」（否则可能丢掉最后一次保存）
        if not self._saver.wait_for_idle(5000):
            self._status("仍有保存在进行中，等待超时后继续关闭", 4000)
        # 逐个确认未保存的标签（不是只问当前这一个）
        for tab in list(self._tabs):
            if tab.dirty:
                self._switch_to_tab(tab)
                if not self._confirm_save(tab):
                    event.ignore()
                    return
        is_last = not _live_windows(exclude=self)
        for tab in list(self._tabs):
            # 写盘时按「这个位置是什么时候看到的」比较新旧：拿着旧页码的后台窗口
            # 不会把别人刚读到的位置盖掉（见 _save_read_state / set_pdf_read_state）
            self._save_read_state(tab)
        is_primary = _OPEN_WINDOWS.primary() is self
        if is_last:
            # 会话只在最后一个窗口关闭时写：多窗口各写一次会互相覆盖
            self._save_session()
        if is_primary or is_last:
            self.settings.set_window_geometry(self.saveGeometry())
        self.settings.sync()
        # 释放所有标签：停搜索线程 → 画布脱离 PDF → 关闭 fitz 文档
        for tab in list(self._tabs):
            canvas = tab.canvas
            tab.release()
            if canvas is not None:
                canvas.setParent(None)
                canvas.deleteLater()
        self._tabs.clear()
        self._active = None
        _OPEN_WINDOWS.remove(self)         # 从多窗口登记表里摘掉自己
        self.thumbnails.shutdown()   # 停掉缩略图线程池
        self._saver.shutdown()       # 停掉后台保存线程（A4）
        event.accept()

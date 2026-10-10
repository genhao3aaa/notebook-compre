"""主题、响应式布局与侧边栏（A7 拆分自 main_window.py）。

历史教训：这些逻辑是 v5.2 为了「窗口窄了按钮被切掉 / 最小宽度 1873px」而加的，
`_apply_responsive_layout` 与 `_clamp_to_screen` 里的阈值都有实测依据，改动前先看 README 的 v5.2 小节。
"""

from __future__ import annotations

from PySide6.QtGui import (QAction, QActionGroup, QColor, QCursor,
                           QGuiApplication, QKeySequence, QShortcut)
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QFileDialog,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSlider, QSpinBox, QSplitter, QStatusBar,
                               QTabBar, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)
from src.core.colors import (DEFAULT_HIGHLIGHTER_COLOR, DEFAULT_PEN_COLOR,
                             HIGHLIGHTER_ALPHA, HIGHLIGHTER_COLOR_PRESETS,
                             PEN_COLOR_PRESETS, argb_hex, blend_over,
                             normalize_hex, parse_rgba, with_alpha)
from src.core.document_tab import DocumentTab, NOTE, PDF, same_path
from src.core.note_document import (BACKGROUND_TYPES, BG_BLANK, BG_COLOR_PRESETS,
                                    BG_LABELS, NoteDocument)
from src.ui.layout_constants import APP_TITLE, COORD_LABEL_MIN_W, IDLE_CACHE_PAGES, MAX_RESTORED_TABS, SIDEBAR_AUTO_HIDE_W, SIDEBAR_MAX_W, SIDEBAR_MIN_W, WINDOW_MIN_H, WINDOW_MIN_W




class UiThemeMixin:
    def _apply_initial_geometry(self) -> None:
        """首次启动（没有历史几何）时按屏幕可用区决定窗口大小。

        不再硬编码 1280×860：150% 缩放的 1920×1080 屏，逻辑可用宽度只有 1280，
        硬编码尺寸会直接顶到屏幕边缘、甚至被裁掉一截。这里取可用区的 86% 并居中偏上。
        """
        if self._geo_restored:
            return
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:                       # pragma: no cover - 无屏幕的极端环境
            self.resize(WINDOW_MIN_W + 360, WINDOW_MIN_H + 280)
            return
        avail = screen.availableGeometry()
        w = max(WINDOW_MIN_W, min(1360, int(avail.width() * 0.86)))
        h = max(WINDOW_MIN_H, min(920, int(avail.height() * 0.86)))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2,
                  avail.y() + max(0, (avail.height() - h) // 3))
    def _clamp_to_screen(self) -> None:
        """把窗口限制在当前屏幕的可用区内（宽度/高度都不超出）。"""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:                       # pragma: no cover
            return
        avail = screen.availableGeometry()
        w = max(WINDOW_MIN_W, min(self.width(), avail.width()))
        h = max(WINDOW_MIN_H, min(self.height(), avail.height()))
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)
    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """记住用户拖出来的侧边栏宽度（下次启动沿用）。"""
        w = self.sidebar.width()
        if SIDEBAR_MIN_W <= w <= SIDEBAR_MAX_W:
            self.settings.set_sidebar_width(w)
    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._apply_responsive_layout()
    def _apply_responsive_layout(self) -> None:
        """窗口越窄，界面越精简（浏览器式降级）。

        * 侧边栏：跨过阈值收窄时自动收起，重新变宽再恢复 —— 只在「跨越阈值」时动作，
          所以用户手动展开/折叠过的状态不会被反复覆盖；
        * 状态栏坐标：窄窗口下没什么用，先让位给页码与缩放。

        顶栏的降级由 QToolBar 自己完成（放不下的控件进 « 溢出菜单），这里不用管。
        """
        narrow = self.width() < SIDEBAR_AUTO_HIDE_W
        if narrow != self._was_narrow:
            self._was_narrow = narrow
            if narrow and self.sidebar.isVisible():
                self.sidebar.setVisible(False)
                self._sidebar_auto_hidden = True
            elif not narrow and self._sidebar_auto_hidden:
                self.sidebar.setVisible(True)
                self._sidebar_auto_hidden = False
        if getattr(self, "lbl_coord", None) is not None:
            self.lbl_coord.setVisible(self.width() >= COORD_LABEL_MIN_W)
    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(self.theme.qss())
        self._refresh_icons()
        # 主题作用于「所有」标签的画布，而不只是当前那一个
        for t in self._tabs:
            if t.canvas is not None:
                t.canvas.set_canvas_bg(self.theme.canvas_bg())
                t.canvas.update()
        self._refresh_tab_icons()
        self.settings.set_theme(self.theme.theme)
    def _refresh_icons(self) -> None:
        for btn, kind in self._icon_buttons:
            if not kind:
                continue
            icon = self.theme.icon(kind)
            if not icon.isNull():
                btn.setIcon(icon)
        self._sync_read_mode_ui()
    def _toggle_theme(self) -> None:
        self.theme.toggle()
        self._apply_theme()
        self._status(f"已切换到{'深色' if self.theme.theme == 'dark' else '浅色'}主题", 2500)
    def _toggle_sidebar(self) -> None:
        """折叠/展开侧边栏（用户手动操作）。"""
        show = not self.sidebar.isVisible()
        self.sidebar.setVisible(show)
        if show:
            # 用户自己要看的：别在下次 resize 时又被自动收起
            self._sidebar_auto_hidden = False
            self._sidebar_user_hidden = False
        else:
            self._sidebar_user_hidden = True
    def _show_bg_color_menu(self) -> None:
        if self.mode != NOTE:
            self._status("背景色仅适用于手写笔记本（PDF 模式下请导出后修改）", 3500)
            return
        menu = QMenu(self)
        for hexc, name in BG_COLOR_PRESETS:
            act = QAction(f"■  {name}", self)
            act.setData(hexc)
            act.triggered.connect(lambda checked=False, c=hexc: self._apply_bg_color(c))
            menu.addAction(act)
        menu.addSeparator()
        custom = QAction("自定义颜色…", self)
        custom.triggered.connect(self._choose_bg_color)
        menu.addAction(custom)
        menu.exec(self.btn_bg_color.mapToGlobal(self.btn_bg_color.rect().bottomLeft()))
    def _apply_bg_color(self, color: str) -> None:
        color = normalize_hex(color)
        if self.note_doc is None:
            return
        self.note_doc.set_background_color(color)
        self.canvas.set_background(self.note_doc.background, color)
        self.settings.set_note_bg_color(color)
        self._set_bg_swatch(color)
        self.thumbnails.set_note_doc(self.note_doc)
        self.thumbnails.set_current(self.current_page)
        self.mark_dirty()
    def _choose_bg_color(self) -> None:
        if self.note_doc is None:
            return
        color = QColorDialog.getColor(QColor(self.note_doc.background_color), self,
                                      "选择背景色")
        if color.isValid():
            self._apply_bg_color(color.name())
    def _set_bg_swatch(self, color: str) -> None:
        self.btn_bg_color.setStyleSheet(
            f"background-color: {color}; border: 1px solid #888; border-radius: 6px;")

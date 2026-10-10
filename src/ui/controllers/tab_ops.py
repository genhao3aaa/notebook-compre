"""标签生命周期（A7 拆分自 main_window.py）：新建/切换/关闭/重开/标题与图标。

关键点：每个标签独占画布与撤销栈；界面只在「当前标签」的信号上进行刷新，
后台标签的迟到回调必须被丢弃（否则状态栏/页码会被写乱）。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QFileDialog,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSlider, QSpinBox, QSplitter, QStatusBar,
                               QTabBar, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)
from src.core.document_tab import DocumentTab, NOTE, PDF, same_path
from src.core.note_document import (BACKGROUND_TYPES, BG_BLANK, BG_COLOR_PRESETS,
                                    BG_LABELS, NoteDocument)
from src.ui.layout_constants import APP_TITLE, COORD_LABEL_MIN_W, IDLE_CACHE_PAGES, MAX_RESTORED_TABS, SIDEBAR_AUTO_HIDE_W, SIDEBAR_MAX_W, SIDEBAR_MIN_W, WINDOW_MIN_H, WINDOW_MIN_W




class TabOpsMixin:
    def _add_tab(self, tab: DocumentTab, activate: bool = True) -> None:
        """把标签挂到标签栏上（数据 + 界面）。"""
        self._tabs.append(tab)
        index = self.tab_widget.addTab(tab.canvas, tab.tab_text())
        self.tab_widget.setTabToolTip(index, tab.tooltip())
        self._refresh_tab_icon(tab)
        if activate or self._active is None:
            self._activate_tab(tab)
    def _activate_tab(self, tab: DocumentTab) -> None:
        self._switch_to_tab(tab)
    def _switch_to_tab(self, tab: Optional[DocumentTab]) -> None:
        """切换当前标签：保存旧的阅读进度 → 换画布 → 同步整个界面。"""
        if tab is None or tab.canvas is None:
            return
        old = self._active
        if old is not None and old is not tab:
            self._save_read_state(old)
            self._trim_idle_cache(old)
        self._active = tab
        if self.tab_widget.currentWidget() is not tab.canvas:
            self.tab_widget.setCurrentWidget(tab.canvas)
        self._apply_tool_settings_to(tab.canvas)
        tab.canvas.setFocus()
        self._sync_ui_from_tab(tab)
    def _sync_ui_from_tab(self, tab: DocumentTab) -> None:
        """把侧边栏 / 工具栏 / 状态栏全部刷新成「这个标签」的样子。"""
        canvas = tab.canvas
        if canvas is None:
            return
        # 工具栏/状态栏还没建好时（首个标签创建的过程中）先不同步，装载完会再来一次
        if getattr(self, "page_spin", None) is None:
            return
        # 文档还没装载完（新建标签的瞬间）就先不刷界面，装载完会再同步一次
        if tab.note_doc is None and tab.pdf_engine is None:
            return
        tab.page = canvas.current_index

        if tab.is_note and tab.note_doc is not None:
            self.side_title.setText("笔记本")
            self.thumbnails.set_note_doc(tab.note_doc)
            self.thumbnails.set_current(tab.page)
            self.side_tabs.setTabEnabled(1, False)
            self.side_tabs.setTabEnabled(2, False)
            self.outline_panel.set_unavailable("未打开 PDF")
            self.search_panel.reset()
            self.btn_search.setEnabled(False)
            self.combo_bg.setEnabled(True)
            self.btn_bg_color.setEnabled(True)
            self.btn_add_page.setEnabled(True)
            self.btn_del_page.setEnabled(True)
            idx = self.combo_bg.findData(tab.note_doc.background)
            if idx >= 0:
                self.combo_bg.blockSignals(True)
                self.combo_bg.setCurrentIndex(idx)
                self.combo_bg.blockSignals(False)
            self._set_bg_swatch(tab.note_doc.background_color)
        else:
            self.side_title.setText("PDF 批注")
            self.combo_bg.setEnabled(False)
            self.btn_bg_color.setEnabled(False)
            self.btn_add_page.setEnabled(False)
            self.btn_del_page.setEnabled(False)
            self.btn_search.setEnabled(True)
            self.side_tabs.setTabEnabled(1, True)
            self.side_tabs.setTabEnabled(2, True)
            if tab.pdf_engine is not None:
                self.thumbnails.set_pdf_engine(tab.pdf_engine, canvas.rotation)
                self.thumbnails.set_current(tab.page)
            if tab.outline:
                self.outline_panel.set_outline(tab.outline)
            else:
                self.outline_panel.set_unavailable(
                    "该 PDF 没有书签目录\n（可用左侧「页面」标签浏览）")
            # 搜索结果：切回来时把该标签自己的结果重新显示出来
            if tab.search_hits:
                self.search_panel.set_results(tab.search_hits, tab.search_index)
            else:
                self.search_panel.reset()
                self.search_panel.set_status(
                    "搜索中…" if tab.search_running else "输入关键字后回车搜索")
            self.search_panel.set_busy(tab.search_running)

        self.outline_panel.set_current_page(tab.page)
        self._refresh_page_nav()
        self._sync_read_mode_ui()
        self._update_undo_buttons()
        self._on_zoom(canvas.scale)
        # 页码/缩略图/目录高亮必须跟着「刚切到的标签」走：打开 PDF、切换标签、
        # 会话恢复都走这条路，缺了这一手界面会一直停在上一个标签的页码上
        self._sync_page_ui(tab)
        self._update_window_title()
    def _trim_idle_cache(self, tab: DocumentTab) -> None:
        """切走的标签只保留少量渲染缓存，避免开很多 PDF 后内存无限增长。

        只清缓存不清状态：切回来时页面会重新渲染一次（几十毫秒），
        但缩放/滚动/撤销栈都还在，用户感知不到。
        """
        canvas = tab.canvas
        if canvas is None:
            return
        cache = getattr(canvas, "_render_cache", None)
        if cache is None:
            return
        try:
            while len(cache) > IDLE_CACHE_PAGES:
                cache.popitem(last=False)
        except Exception:
            pass
    def _tab_of_canvas(self, canvas) -> Optional[DocumentTab]:
        if canvas is None:
            return None
        for t in self._tabs:
            if t.canvas is canvas:
                return t
        return None
    def _tab_by_id(self, tab_id: int) -> Optional[DocumentTab]:
        for t in self._tabs:
            if t.tab_id == tab_id:
                return t
        return None
    def _tab_at(self, index: int) -> Optional[DocumentTab]:
        """按标签栏的当前顺序取第 index 个标签（Ctrl+1..9 用）。"""
        if index < 0:
            return None
        w = self.tab_widget.widget(index)
        return self._tab_of_canvas(w)
    def _find_tab_by_path(self, path: Optional[str]) -> Optional[DocumentTab]:
        """同一份文件是否已经打开了：避免同一 PDF 开两份导致批注互相覆盖。"""
        if not path:
            return None
        for t in self._tabs:
            if t.is_same_source(path):
                return t
        return None
    def _refresh_tab_title(self, tab: DocumentTab) -> None:
        idx = self.tab_widget.indexOf(tab.canvas) if tab.canvas is not None else -1
        if idx >= 0:
            self.tab_widget.setTabText(idx, tab.tab_text())
    def _refresh_tab_icon(self, tab: DocumentTab) -> None:
        idx = self.tab_widget.indexOf(tab.canvas) if tab.canvas is not None else -1
        if idx < 0:
            return
        icon = self.theme.icon("tab_pdf" if tab.is_pdf else "tab_note", 16)
        if not icon.isNull():
            self.tab_widget.setTabIcon(idx, icon)
    def _refresh_tab_icons(self) -> None:
        for t in self._tabs:
            self._refresh_tab_icon(t)
        self._style_tab_close_buttons()
    def _schedule_tab_button_style(self) -> None:
        """标签增删后补一次 × 图标（Qt 是插入之后再装按钮的，同步调用会太早）。"""
        QTimer.singleShot(0, self._style_tab_close_buttons)
    def _style_tab_close_buttons(self) -> None:
        """给每个标签的 × 装上主题图标。

        为什么不用 QSS 的 `QTabBar::close-button {{ image: url(...) }}`：那要求磁盘上有
        图片文件，而本项目坚持零外部资源（图标全是内嵌 SVG + QIcon）。直接给按钮装 QIcon
        既能跟随主题换色，又在 150% / 200% 缩放下保持清晰。
        """
        if getattr(self, "tab_widget", None) is None:
            return
        bar = self.tab_widget.tabBar()
        if bar is None:
            return
        icon = self.theme.icon("close", 12)
        for i in range(bar.count()):
            btn = bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if btn is None:
                continue
            if not icon.isNull():
                btn.setIcon(icon)
            btn.setIconSize(QSize(11, 11))
            btn.setToolTip("关闭标签 (Ctrl+W)")
    def _update_window_title(self) -> None:
        tab = self._active
        if tab is None:
            self.setWindowTitle(APP_TITLE)
            return
        star = " *" if tab.dirty else ""
        self.setWindowTitle(f"{tab.display_name()}{star} - {APP_TITLE}")
    def _on_tab_close_requested(self, index: int) -> None:
        tab = self._tab_at(index)
        if tab is not None:
            self.close_tab(tab)
    def _on_tab_current_changed(self, index: int) -> None:
        tab = self._tab_at(index)
        if tab is None or tab is self._active:
            return
        self._switch_to_tab(tab)
    def _on_tab_moved(self, from_: int, to: int) -> None:
        """拖拽重排后，让内部列表顺序与标签栏保持一致。"""
        if not (0 <= from_ < len(self._tabs)) or not (0 <= to < len(self._tabs)):
            return
        t = self._tabs.pop(from_)
        self._tabs.insert(to, t)
    def _show_tab_menu(self, pos) -> None:
        """标签栏右键菜单（浏览器那一套）。"""
        bar = self.tab_widget.tabBar()
        index = bar.tabAt(pos)
        menu = QMenu(self)
        if index >= 0:
            tab = self._tab_at(index)
            act_close = menu.addAction("关闭")
            act_others = menu.addAction("关闭其他标签")
            act_right = menu.addAction("关闭右侧标签")
            act_left = menu.addAction("关闭左侧标签")
            menu.addSeparator()
            act_reload = menu.addAction("重新打开")
            menu.addSeparator()
            act_copy = menu.addAction("复制文件路径")
            chosen = menu.exec(bar.mapToGlobal(pos))
            if chosen is act_close and tab is not None:
                self.close_tab(tab)
            elif chosen is act_others:
                self.close_other_tabs(tab)
            elif chosen is act_right:
                self.close_tabs_to_right(index)
            elif chosen is act_left:
                self.close_tabs_to_left(index)
            elif chosen is act_reload and tab is not None:
                self._reload_tab(tab)
            elif chosen is act_copy and tab is not None:
                if tab.path:
                    QApplication.clipboard().setText(tab.path)
                    self._status("已复制文件路径", 2500)
            return
        act_new = menu.addAction("新建标签")
        act_reopen = menu.addAction("恢复关闭的标签")
        act_close_all = menu.addAction("关闭全部标签")
        chosen = menu.exec(bar.mapToGlobal(pos))
        if chosen is act_new:
            self.new_tab_note()
        elif chosen is act_reopen:
            self.reopen_closed_tab()
        elif chosen is act_close_all:
            self.close_all_tabs()
    def new_tab_note(self, background: str = BG_BLANK) -> DocumentTab:
        """新建一个空白笔记标签（Ctrl+T / Ctrl+N / + 按钮）。"""
        doc = NoteDocument(background=background,
                           background_color=self.settings.note_bg_color())
        tab = DocumentTab(NOTE, self._make_canvas(), "未命名笔记")
        self._add_tab(tab)
        self._load_note_into_tab(tab, doc)
        self.canvas.setFocus()
        self._status("已新建笔记标签", 2000)
        return tab
    def close_tab(self, tab: Optional[DocumentTab], ask_save: bool = True) -> bool:
        """关闭一个标签：先问保存，再释放资源（PDF 句柄 / 线程 / 缓存）。"""
        if tab is None:
            return False
        if ask_save and tab.dirty and not self._confirm_save(tab):
            return False
        was_active = tab is self._active
        index = self.tab_widget.indexOf(tab.canvas) if tab.canvas is not None else -1

        # 先摘界面（屏蔽信号，避免中间态触发切换），再动数据
        if index >= 0:
            self.tab_widget.blockSignals(True)
            self.tab_widget.removeTab(index)
            self.tab_widget.blockSignals(False)
        if tab in self._tabs:
            self._tabs.remove(tab)

        if tab.path:
            self._closed.append({"kind": tab.kind, "path": tab.path})
            del self._closed[:-20]

        canvas = tab.canvas
        tab.release()          # 停搜索线程 → 画布脱离 PDF → 关闭 fitz 文档
        if canvas is not None:
            canvas.setParent(None)
            canvas.deleteLater()

        if not self._tabs:
            self._active = None
            self.new_tab_note()          # 浏览器不会留一个空白窗口
        elif was_active:
            next_tab = self._tabs[min(max(index, 0), len(self._tabs) - 1)]
            self._switch_to_tab(next_tab)
        else:
            self._update_window_title()
        return True
    def close_current_tab(self) -> None:
        self.close_tab(self._active)
    def close_other_tabs(self, keep: Optional[DocumentTab] = None) -> None:
        keep = keep or self._active
        for tab in list(self._tabs):
            if tab is not keep:
                self.close_tab(tab)
    def close_tabs_to_right(self, index: int) -> None:
        for tab in list(self._tabs):
            idx = self.tab_widget.indexOf(tab.canvas) if tab.canvas is not None else -1
            if idx > index:
                self.close_tab(tab)
    def close_tabs_to_left(self, index: int) -> None:
        for tab in list(self._tabs):
            idx = self.tab_widget.indexOf(tab.canvas) if tab.canvas is not None else -1
            if 0 <= idx < index:
                self.close_tab(tab)
    def close_all_tabs(self) -> None:
        for tab in list(self._tabs):
            if not self.close_tab(tab):
                return   # 用户在保存确认里点了取消：立刻停手，不再继续关
    def _on_tab_pressed(self, index: int) -> None:
        """记住被按住的是哪个文档：拖拽过程中 Qt 会实时重排，index 会变。"""
        self._dragged_tab = self._tab_at(index)
    def cycle_tab(self, delta: int) -> None:
        """Ctrl+Tab / Ctrl+Shift+Tab：循环切换。"""
        n = len(self._tabs)
        if n <= 1:
            return
        cur = self._tabs.index(self._active) if self._active in self._tabs else 0
        self._switch_to_tab(self._tabs[(cur + delta) % n])
    def goto_tab_number(self, number: int) -> None:
        """Ctrl+1..8 直达第 N 个，Ctrl+9 直达最后一个（浏览器习惯）。"""
        n = len(self._tabs)
        if n == 0:
            return
        index = n - 1 if number >= 9 else number - 1
        tab = self._tab_at(index)
        if tab is not None:
            self._switch_to_tab(tab)
    def reopen_closed_tab(self) -> None:
        """Ctrl+Shift+T：恢复最近关闭的标签（只恢复能落盘的那些）。"""
        while self._closed:
            info = self._closed.pop()
            path = info.get("path")
            if not path or not os.path.exists(path):
                continue
            if self._find_tab_by_path(path) is not None:
                continue
            if path.lower().endswith(".pdf"):
                self.open_pdf(path)
            else:
                self.open_note(path)
            self._status(f"已恢复：{os.path.basename(path)}", 2500)
            return
        self._status("没有可恢复的标签", 2500)
    def _reload_tab(self, tab: DocumentTab) -> None:
        """右键「重新打开」：从磁盘重新加载（未保存的改动会先问一句）。

        实现上就是「关掉再开一遍」，而不是就地刷数据——后者要处理几十处状态复位，
        反而更容易漏。代价是位置排到最后，但这符合「重新打开」的直觉。
        """
        if tab.dirty and not self._confirm_save(tab):
            return
        path = tab.path
        if not path:
            self._status("该标签还没有保存到磁盘，无法重新打开", 3000)
            return
        if not os.path.exists(path):
            self._status("文件已不存在（可能被移动或删除）", 3500)
            return
        self.close_tab(tab)
        if path.lower().endswith(".pdf"):
            self.open_pdf(path)
        else:
            self.open_note(path)
        self._status(f"已重新打开：{os.path.basename(path)}", 2500)
    def mark_dirty(self) -> None:
        tab = self._active
        if tab is None:
            return
        if not tab.dirty:
            tab.dirty = True
            self._refresh_tab_title(tab)
        self._update_window_title()

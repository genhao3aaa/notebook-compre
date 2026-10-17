"""阅读与导航（A7 拆分自 main_window.py）：翻页、阅读模式、搜索、画布信号槽。

阅读模式的进度恢复（A4.2）节流 1.2 s 写盘，避免滚动时频繁写设置。
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QFileDialog,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSlider, QSpinBox, QSplitter, QStatusBar,
                               QTabBar, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)
from src.core.document_tab import DocumentTab, NOTE, PDF, same_path
from src.core.hn_log import get_logger, log_debug, log_warning
from src.render.pdf_engine import PdfEngine, PdfSearchWorker, export_note_to_pdf
from src.ui.canvas import PageCanvas

_log = get_logger("ui.reading")




class ReadingOpsMixin:
    def _show_page_note(self, index: int,
                        tab: Optional[DocumentTab] = None) -> None:
        """把笔记翻到第 index 页（`tab` 缺省为当前标签）。

        必须能指定标签：会话恢复 / 切标签时是在**后台标签**上翻页的，
        用 `self.note_doc`（当前标签）会把 A 的页面内容画到 B 的画布上。
        """
        tab = tab or self._active
        if tab is None or tab.canvas is None or tab.note_doc is None:
            return
        doc = tab.note_doc
        index = max(0, min(index, doc.page_count() - 1))
        tab.page = index
        page = doc.pages[index]
        canvas = tab.canvas
        # 先同步页码：`set_note_content()` 会发 page_info，画布里的 current_index
        # 如果还是旧的 0，状态栏 / 页码框 / 缩略图高亮就会被写回第 1 页。
        canvas.set_page_info(index, doc.page_count())
        canvas.set_note_content(doc.page_w, doc.page_h, doc.background,
                                page.strokes, page.undo, doc.background_color)
        if tab is self._active:
            self.thumbnails.set_current(index)
            self._touch_view(tab)
            self._update_undo_buttons()
    def _show_page_pdf(self, index: int, scroll: bool = True,
                       tab: Optional[DocumentTab] = None) -> None:
        tab = tab or self._active
        if tab is None or tab.pdf_engine is None or tab.annot_doc is None:
            return
        canvas = tab.canvas
        # 批注页数可能与 PDF 不同步，先补齐（防御性）
        tab.annot_doc.ensure_page_count(tab.pdf_engine.page_count())
        index = max(0, min(index, tab.pdf_engine.page_count() - 1))
        tab.page = index
        page = tab.annot_doc.pages[index]
        canvas.set_pdf_page(tab.pdf_engine, index, tab.pdf_engine.page_count(),
                            page.strokes, page.undo, scroll=scroll)
        if tab is self._active:
            self._touch_view(tab)          # 用户眼前这一页就是「最新位置」
            self._update_undo_buttons()
    def _on_canvas_page_request(self, tab: Optional[DocumentTab], index: int) -> None:
        """画布在连续模式下滚到了另一页 / 在另一页上落笔：同步绑定与界面状态。

        注意 scroll=False —— 滚动是用户行为，不能反过来把视口拽回去。
        """
        if tab is None or not tab.is_pdf or tab.pdf_engine is None:
            return
        if not (0 <= index < tab.pdf_engine.page_count()):
            return
        if index == tab.page:
            return
        self._show_page_pdf(index, scroll=False, tab=tab)
        if tab is self._active:
            self.thumbnails.set_current(index)
            self._after_page_changed()
            self._schedule_read_state_save()
    def goto_page(self, index: int) -> None:
        tab = self._active
        if tab is None:
            return
        if tab.is_note:
            if tab.note_doc is not None and 0 <= index < tab.note_doc.page_count():
                self._show_page_note(index)
                self.thumbnails.set_current(index)
                self._refresh_page_nav()
        elif tab.pdf_engine is not None and 0 <= index < tab.pdf_engine.page_count():
            self._show_page_pdf(index, tab=tab)
            self.thumbnails.set_current(index)
            self._refresh_page_nav()
        self._after_page_changed()
    def _current_read_mode(self) -> str:
        tab = self._active
        if tab is not None and tab.is_pdf and tab.canvas is not None:
            return tab.canvas.read_mode
        return self.settings.pdf_read_mode()
    def _set_read_mode(self, mode: str) -> None:
        """切换单页 / 连续阅读。无 PDF 标签时只改默认值。"""
        mode = "continuous" if mode == "continuous" else "single"
        tab = self._active
        if tab is not None and tab.is_pdf and tab.canvas is not None:
            tab.canvas.set_read_mode(mode)
            tab.canvas.update()
            self._schedule_read_state_save()
        self.settings.set_pdf_read_mode(mode)
        self._sync_read_mode_ui()
        self._status("阅读模式：" + ("连续阅读" if mode == "continuous" else "单页阅读"), 2500)
    def _toggle_read_mode(self) -> None:
        self._set_read_mode("single" if self._current_read_mode() == "continuous"
                            else "continuous")
    def _sync_read_mode_ui(self) -> None:
        continuous = self._current_read_mode() == "continuous"
        kind = "read_continuous" if continuous else "read_single"
        if hasattr(self, "btn_read_mode"):
            self.btn_read_mode.setChecked(continuous)
            self.btn_read_mode.setIcon(self.theme.icon(kind, 20))
            self.btn_read_mode.setToolTip(
                "连续阅读（点击切到单页） (Ctrl+Shift+L)" if continuous
                else "单页阅读（点击切到连续滚动） (Ctrl+Shift+L)")
            # 主题切换会按 _icon_buttons 里的 kind 重刷图标，这里同步记录
            for i, (btn, _k) in enumerate(self._icon_buttons):
                if btn is self.btn_read_mode:
                    self._icon_buttons[i] = (btn, kind)
                    break
        for act, want in ((getattr(self, "act_read_single", None), False),
                          (getattr(self, "act_read_continuous", None), True)):
            if act is not None:
                act.setChecked(want == continuous)
    def _sync_page_ui(self, tab: Optional[DocumentTab]) -> None:
        """把页码相关的界面全部刷成这个标签的现状。

        为什么必须有这一手：打开 PDF 时 `_load_pdf_into_tab()` 是在**标签还没激活**的
        状态下跑完的（`_add_tab` 在其后），而 `_on_canvas_page_info()` 只认当前标签——
        于是恢复出来的页码（例如 656 页书里的第 219 页）不会写到状态栏/页码框上，
        界面一直停在上一个标签的「页: 1 / 1」，看起来就是「打开后页码不再更新」。
        """
        if tab is None or tab.canvas is None:
            return
        canvas = tab.canvas
        if getattr(self, "page_spin", None) is None:
            return
        total = 1
        if tab.is_pdf and tab.pdf_engine is not None:
            total = max(1, tab.pdf_engine.page_count())
            if tab.outline:
                self.outline_panel.set_current_page(tab.page)
            self._apply_search_highlight()
        elif tab.is_note and tab.note_doc is not None:
            total = max(1, tab.note_doc.page_count())
        current = max(0, min(int(tab.page), total - 1))
        self._on_canvas_page_info(canvas, current, total, canvas.scale)
        if getattr(self, "thumbnails", None) is not None:
            self.thumbnails.set_current(current)
        self._touch_view(tab)          # 切到某个标签 = 看到了它的位置
        self._update_undo_buttons()
    def _schedule_read_state_save(self) -> None:
        """延迟写盘：连续滚动时页码变化非常频繁。"""
        self._read_state_timer.start()
    def _save_current_read_state(self) -> None:
        self._save_read_state(self._active)
    def _save_read_state(self, tab: Optional[DocumentTab]) -> None:
        """记下这份文档的阅读进度（PDF：页码 / 模式 / 滚动比例；笔记：页码）。

        三条口径（都是踩过的坑）：
        - 带上 `tab.view_ts`（这个位置是什么时候看到的）：多窗口 / 多实例开同一本书时，
          手里拿着旧页码的窗口在退出时不会把别人刚读到的位置覆盖掉——所以写入用的
          时间戳必须是「视图被看到的时刻」，不是「写盘的时刻」；
        - 笔记也要记（多页 .note 以前一关就从第 1 页重来，用户看到的是「没有记忆」）；
        - 失败不再静默吞掉，写进日志（`HN_LOG=debug`）便于现场排查。
        """
        if tab is None:
            return
        if tab.is_note:
            if not tab.path:
                return                     # 还没落盘的笔记没有稳定标识
            try:
                self.settings.set_note_read_state(tab.path, tab.page)
            except Exception as e:         # pragma: no cover - 防御性
                log_warning(_log, "note_read_state_save_failed", path=tab.path,
                            error=repr(e))
            return
        if tab.pdf_engine is None or tab.canvas is None:
            return
        stamp = float(getattr(tab, "view_ts", 0.0)) or time.time()
        try:
            written = self.settings.set_pdf_read_state(
                tab.pdf_engine.path, tab.page, tab.canvas.read_mode,
                tab.canvas.scroll_offset_frac(), tab.pdf_engine.file_size,
                tab.pdf_engine.mtime, ts=stamp,
                pages=tab.pdf_engine.page_count())
        except Exception as e:                      # pragma: no cover - 防御性
            log_warning(_log, "read_state_save_failed", path=tab.pdf_engine.path,
                        error=repr(e))
            return
        if not written:
            log_debug(_log, "read_state_skip_newer", path=tab.pdf_engine.path)
    def _touch_view(self, tab: Optional[DocumentTab]) -> None:
        """标记「这个标签的位置刚刚被看到」（用于跨窗口的进度新旧比较）。"""
        if tab is not None:
            tab.view_ts = time.time()
    def _after_page_changed(self) -> None:
        """翻页后的联动：目录高亮 + 搜索命中高亮 + 记住进度。"""
        tab = self._active
        if tab is None:
            return
        self.outline_panel.set_current_page(tab.page)
        if tab.is_pdf:
            self._apply_search_highlight()
        self._schedule_read_state_save()      # 笔记也要记住翻到第几页
    def next_page(self) -> None:
        self.goto_page(self.current_page + 1)
    def prev_page(self) -> None:
        self.goto_page(self.current_page - 1)
    def first_page(self) -> None:
        self.goto_page(0)
    def last_page(self) -> None:
        tab = self._active
        total = tab.page_count() if tab is not None else 1
        self.goto_page(total - 1)
    def _goto_dialog(self) -> None:
        tab = self._active
        total = tab.page_count() if tab is not None else 1
        page, ok = QInputDialog.getInt(self, "跳转到页", f"页码（1 – {total}）",
                                       self.current_page + 1, 1, total, 1)
        if ok:
            self.goto_page(page - 1)
    def _add_page(self) -> None:
        doc = self.note_doc
        if doc is None:
            return
        doc.add_page(self.current_page + 1)
        self.thumbnails.set_note_doc(doc)
        self.goto_page(self.current_page + 1)
        self.mark_dirty()
        self._status("已添加一页", 2000)
    def _delete_page(self) -> None:
        doc = self.note_doc
        if doc is None:
            return
        if doc.page_count() <= 1:
            QMessageBox.information(self, "提示", "至少保留一页，无法删除。")
            return
        doc.delete_page(self.current_page)
        self.thumbnails.set_note_doc(doc)
        self.goto_page(min(self.current_page, doc.page_count() - 1))
        self.mark_dirty()
        self._status("已删除当前页", 2000)
    def _fit_view(self) -> None:
        c = self.canvas
        if c is None:
            return
        c.reset_zoom_policy()
        c.fit_to_view()
        c.rebuild_pixmap()
        c.update()
    def _fit_width(self) -> None:
        c = self.canvas
        if c is None:
            return
        c.fit_width()
        c.rebuild_pixmap()
        c.update()
    def _actual_size(self) -> None:
        c = self.canvas
        if c is not None:
            c.actual_size()
    def _rotate(self, delta: int) -> None:
        tab = self._active
        if tab is None:
            return
        if not tab.is_pdf:
            self._status("旋转仅用于 PDF 页面（笔记页无需旋转）", 2500)
            return
        tab.canvas.rotate_by(delta)
    def _on_canvas_rotation(self, canvas: PageCanvas, rotation: int) -> None:
        tab = self._tab_of_canvas(canvas)
        if tab is None:
            return
        if tab.is_pdf and tab.pdf_engine is not None and tab is self._active:
            self.thumbnails.set_pdf_engine(tab.pdf_engine, rotation)
            self.thumbnails.set_current(tab.page)
        if tab is self._active:
            self._status(f"页面已旋转 {rotation}°", 2000)
    def _on_outline_jump(self, page: int, top) -> None:
        self.goto_page(page)
        c = self.canvas
        if c is not None and top is not None:
            try:
                c.focus_page_rect((0.0, float(top) - 40.0, c.page_w_pt, float(top) + 40.0))
            except Exception:
                pass
            c.setFocus()
    def _focus_search(self) -> None:
        tab = self._active
        if tab is None or not tab.is_pdf:
            self._status("搜索功能需要打开 PDF 文档", 3000)
            return
        if not self.sidebar.isVisible():
            self.sidebar.setVisible(True)
        self.side_tabs.setCurrentWidget(self.search_panel)
        self.search_panel.focus_input()
    def _start_search(self, query: str, case_sensitive: bool, whole_word: bool) -> None:
        tab = self._active
        if tab is None or tab.pdf_engine is None:
            return
        tab.stop_search()
        tab.search_token += 1
        tab.last_query = query
        tab.search_running = True
        worker = PdfSearchWorker(tab.pdf_engine.path, query, case_sensitive,
                                 whole_word, tab.search_token)
        # 结果回调带上 tab_id：标签若已被关掉，结果直接丢弃
        worker.finished_hits.connect(
            lambda hits, q, tok, tid=tab.tab_id: self._on_search_done(tid, hits, q, tok))
        tab.search_worker = worker
        self.search_panel.set_busy(True, query)
        worker.start()
    def _stop_search(self) -> None:
        tab = self._active
        if tab is None:
            return
        tab.stop_search()
        self.search_panel.set_busy(False)
    def _on_search_done(self, tab_id: int, hits, query: str, token: int) -> None:
        tab = self._tab_by_id(tab_id)
        if tab is None:          # 标签已关闭：结果作废
            return
        if token != tab.search_token:
            return
        tab.search_running = False
        tab.search_worker = None
        tab.search_hits = list(hits or [])
        tab.search_index = 0 if tab.search_hits else -1
        # 后台标签的搜索结果只存数据，不抢当前标签的界面
        if tab is not self._active:
            return
        self.search_panel.set_busy(False)
        self.search_panel.set_results(tab.search_hits, tab.search_index)
        if tab.search_hits:
            self._goto_hit(0)
        else:
            if self.canvas is not None:
                self.canvas.clear_search()
            self._status(f"「{query}」没有匹配结果", 3500)
    def _goto_hit(self, index: int) -> None:
        tab = self._active
        if tab is None or not tab.search_hits:
            return
        n = len(tab.search_hits)
        index %= n
        tab.search_index = index
        hit = tab.search_hits[index]
        if hit.page != tab.page:
            self.goto_page(hit.page)
        if self.canvas is not None:
            self.canvas.focus_page_rect(hit.rect)
        self._apply_search_highlight()
        self.search_panel.set_current(index)
        self._status(f"第 {index + 1} / {n} 处匹配（第 {hit.page + 1} 页）", 2500)
    def _apply_search_highlight(self) -> None:
        tab = self._active
        if tab is None or self.canvas is None:
            return
        if not tab.search_hits:
            self.canvas.clear_search()
            return
        self.canvas.set_search_results(tab.search_hits, tab.search_index, tab.page)
    def _on_canvas_zoom(self, canvas: PageCanvas, zoom: float) -> None:
        if canvas is not self.canvas:
            return
        self._on_zoom(zoom)
    def _on_canvas_page_info(self, canvas: PageCanvas, current: int, total: int,
                             zoom: float) -> None:
        tab = self._tab_of_canvas(canvas)
        if tab is None:
            return
        tab.page = current
        if tab is not self._active:
            return
        self._touch_view(tab)
        if getattr(self, "lbl_page", None) is None:   # 状态栏还没建好
            return
        self.lbl_page.setText(f"页: {current + 1} / {total}")
        self.lbl_zoom.setText(f"缩放: {int(zoom * 100)}%")
        self.lbl_zoom_tb.setText(f"{int(zoom * 100)}%")
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(current + 1)
        self.page_spin.blockSignals(False)
        self.page_total_label.setText(f"/ {total}")
        self._refresh_page_nav()
        # 滚动在同一页内也会走到这里：阅读进度（含滚动比例）要跟着刷新，
        # 否则一旦异常退出/断电，进度就停在「上一次跨页」的位置。
        # 笔记同样要记（多页笔记以前完全没有位置记忆）。
        self._schedule_read_state_save()
    def _on_canvas_cursor(self, canvas: PageCanvas, x, y) -> None:
        if canvas is not self.canvas:
            return
        self.lbl_coord.setText("坐标: -" if x is None else f"坐标: ({x:.0f}, {y:.0f})")
    def _on_canvas_modified(self, canvas: PageCanvas) -> None:
        tab = self._tab_of_canvas(canvas)
        if tab is None:
            return
        if not tab.dirty:
            tab.dirty = True
            self._refresh_tab_title(tab)
            if tab is self._active:
                self._update_window_title()
        # 缩略图面板是共享的，只刷新「当前标签」那一页
        if tab is self._active and tab.is_note and tab.note_doc is not None:
            self.thumbnails.refresh_note_page(tab.note_doc, tab.page)
    def _on_canvas_undo_state(self, canvas: PageCanvas, can_undo: bool,
                              can_redo: bool) -> None:
        if canvas is not self.canvas or getattr(self, "act_undo", None) is None:
            return
        self.act_undo.setEnabled(can_undo)
        self.act_redo.setEnabled(can_redo)
        self.btn_undo_tb.setEnabled(can_undo)
        self.btn_redo_tb.setEnabled(can_redo)
    def _on_zoom(self, zoom: float) -> None:
        if getattr(self, "lbl_zoom", None) is None:
            return
        self.lbl_zoom.setText(f"缩放: {int(zoom * 100)}%")
        self.lbl_zoom_tb.setText(f"{int(zoom * 100)}%")
    def _update_undo_buttons(self) -> None:
        c = self.canvas
        if c is not None and c.undo_mgr is not None:
            c.undo_state_changed.emit(c.undo_mgr.can_undo(), c.undo_mgr.can_redo())
    def _refresh_page_nav(self) -> None:
        tab = self._active
        total = tab.page_count() if tab is not None else 1
        # 必须屏蔽信号：从 600 页的书切到 20 页的书时，setMaximum() 会把
        # 当前值 401 夹到 20 并**发出 valueChanged**，于是刚切过去的标签
        # 被 `goto_page(19)` 硬拽到最后一页（用户看到的就是「切个标签就跳页」）。
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, total))
        self.page_spin.blockSignals(False)
        self.page_total_label.setText(f"/ {total}")

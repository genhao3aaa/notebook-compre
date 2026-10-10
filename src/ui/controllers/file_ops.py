"""文件操作（A7 拆分自 main_window.py）：打开/保存/导出/最近文件/文档信息。

保存走 A4 的 `AsyncSaver`：小文档同步写、大文档后台写；失败不弹模态框（只标回脏 + 状态栏 + 非模态提示）。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCursor,
                           QGuiApplication, QKeySequence, QShortcut)
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QFileDialog,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSlider, QSpinBox, QSplitter, QStatusBar,
                               QTabBar, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)
from src.core.annot_document import AnnotDocument
from src.core import annot_store
from src.core import serializer
from src.core.document_tab import DocumentTab, NOTE, PDF, same_path
from src.core.hn_log import get_logger, log_debug, log_warning
from src.core.note_document import (BACKGROUND_TYPES, BG_BLANK, BG_COLOR_PRESETS,
                                    BG_LABELS, NoteDocument)
from src.render.pdf_engine import PdfEngine, PdfSearchWorker, export_note_to_pdf

_log = get_logger("ui.file_ops")




class FileOpsMixin:
    def new_note(self, background: str = BG_BLANK) -> DocumentTab:
        """旧「新建笔记」入口：多标签下等价于新建一个笔记标签。"""
        return self.new_tab_note(background)
    def open_note(self, path: str, activate: bool = True) -> Optional[DocumentTab]:
        """打开笔记：已打开过就切过去，否则在新标签里打开。"""
        path = os.path.abspath(path)
        existing = self._find_tab_by_path(path)
        if existing is not None:
            if activate:
                self._switch_to_tab(existing)
                self._status(f"已在标签中打开：{existing.display_name()}", 2500)
            return existing
        try:
            doc = NoteDocument.load(path)
        except Exception as e:
            QMessageBox.critical(self, "打开失败", f"无法打开笔记文件：\n{e}")
            return None
        tab = DocumentTab(NOTE, self._make_canvas(), os.path.basename(path))
        self._add_tab(tab, activate=activate)
        self._load_note_into_tab(tab, doc)
        tab.path = path
        self._refresh_tab_title(tab)
        self._add_recent(path)
        self.settings.set_last_dir(os.path.dirname(path))
        if activate:
            self.canvas.setFocus()
            self._update_window_title()
        return tab
    def open_pdf(self, path: str, activate: bool = True) -> Optional[DocumentTab]:
        """打开 PDF：已打开过就切过去，否则在新标签里打开。"""
        path = os.path.abspath(path)
        existing = self._find_tab_by_path(path)
        if existing is not None:
            if activate:
                self._switch_to_tab(existing)
                self._status(f"已在标签中打开：{existing.display_name()}", 2500)
            return existing
        if not os.path.exists(path):
            QMessageBox.critical(self, "打开失败", f"找不到文件：\n{path}")
            return None

        try:
            engine = PdfEngine(path)
        except Exception as e:
            QMessageBox.critical(self, "打开失败", f"无法打开 PDF 文件：\n{e}")
            return None

        tab = DocumentTab(PDF, self._make_canvas(), os.path.basename(path))
        try:
            self._load_pdf_into_tab(tab, engine, path)
        except Exception as e:
            try:
                engine.close()
            except Exception:
                pass
            tab.release()
            if tab.canvas is not None:
                tab.canvas.deleteLater()
            QMessageBox.critical(self, "打开失败", f"打开 PDF 时出错：\n{e}")
            return None

        self._add_tab(tab, activate=activate)
        self._refresh_tab_title(tab)
        self._add_recent(path)
        self.settings.set_last_dir(os.path.dirname(path))
        if activate:
            self.canvas.setFocus()
            extra = f"，{len(tab.outline)} 个书签" if tab.outline else ""
            where = ""
            if tab.annot_path and os.path.normcase(tab.annot_path) != os.path.normcase(
                    annot_store.primary_path(path)):
                where = f"，批注在备用位置（{os.path.basename(tab.annot_path)}）"
            self._status(f"已打开：{os.path.basename(path)}"
                         f"（{engine.page_count()} 页{extra}）{where}", 4000)
        return tab
    def _close_current_pdf(self) -> None:
        """旧「关闭当前 PDF」入口：关闭当前标签并释放其中的 PDF 句柄。"""
        self.close_tab(self._active, ask_save=False)
    def _save_pdf_read_state(self) -> None:
        """旧「保存 PDF 阅读进度」入口：保存当前标签的进度。"""
        self._save_current_read_state()
    def _copy_current_path(self) -> None:
        tab = self._active
        if tab is None or not tab.path:
            self._status("当前标签还没有保存到磁盘", 2500)
            return
        QApplication.clipboard().setText(tab.path)
        self._status(f"已复制路径：{tab.path}", 3000)
    def _load_note_into_tab(self, tab: DocumentTab, doc: NoteDocument) -> None:
        """把一份笔记文档塞进标签的画布，并同步该标签相关的界面。"""
        canvas = tab.canvas
        canvas.read_mode = "single"        # 笔记始终单页
        canvas.stroke_provider = None
        canvas.on_page_request = None
        tab.note_doc = doc
        tab.page = 0
        tab.dirty = False
        tab.kind = NOTE
        canvas.set_note_content(doc.page_w, doc.page_h, doc.background,
                                doc.pages[0].strokes, doc.pages[0].undo,
                                doc.background_color)
        if tab is self._active:
            self._sync_ui_from_tab(tab)
        self._update_undo_buttons()
    def _load_pdf_into_tab(self, tab: DocumentTab, engine: PdfEngine, path: str) -> None:
        """把一份 PDF 塞进标签（含批注恢复、阅读进度恢复、目录解析）。"""
        canvas = tab.canvas
        # 批注优先在 PDF 旁边；PDF 所在目录写不进去时，上次可能存到了兜底目录（annot_store）
        found = annot_store.find_existing(path)
        annot_path = found[0] if found else annot_store.primary_path(path)
        annot: Optional[AnnotDocument] = None
        md5 = ""
        if os.path.exists(annot_path):
            # 只有「需要和旧批注比对」时才计算 MD5：先查 (路径+大小+修改时间) 缓存，
            # 命中就直接用，避免每次重读整份文件。
            md5 = self.settings.cached_pdf_md5(path, engine.file_size, engine.mtime)
            if not md5:
                md5 = engine.md5
                self.settings.set_pdf_md5(path, engine.file_size, engine.mtime, md5)
            try:
                loaded = AnnotDocument.load(annot_path)
                if loaded.pdf_md5 == md5:
                    annot = loaded
                else:
                    QMessageBox.warning(
                        self, "批注不匹配",
                        "找到同名 .annot 但 PDF 内容已变化（MD5 不一致），将忽略旧批注。")
            except Exception:
                annot = None
        if annot is None:
            # MD5 留到「保存批注」时再算，打开过程不做无谓的全文件哈希
            annot = AnnotDocument.from_pdf(path, md5, max(1, engine.page_count()))
        annot.ensure_page_count(max(1, engine.page_count()))

        tab.pdf_engine = engine
        tab.annot_doc = annot
        tab.annot_path = annot_path
        tab.path = path
        tab.kind = PDF
        tab.dirty = False
        tab.page = 0

        canvas.set_rotation(0)          # 新文档重置旋转
        canvas.reset_zoom_policy()      # 新文档重新适应视口

        self.bind_canvas_to_window(tab)

        # 恢复上次阅读进度（文件被改过则忽略，避免跳到错误的页）
        page = 0
        read_mode: Optional[str] = None
        scroll_frac = 0.0
        state = self.settings.pdf_read_state(path)
        # 判据：**文件大小一致**就认（内容没被换成别的文档）；
        # 修改时间只用来提示，不再当作否决条件——同步盘/备份工具只动 mtime 不动内容，
        # 旧口径下这会让人「每次打开都被丢回第 1 页」。
        if state and int(state.get("size", -1)) == engine.file_size:
            if abs(float(state.get("mtime", -1.0)) - engine.mtime) >= 1e-6:
                log_debug(_log, "read_state_mtime_changed",
                          path=path, saved=str(state.get("mtime")))
            page = max(0, min(int(state.get("page", 0)), engine.page_count() - 1))
            read_mode = state.get("mode")
            scroll_frac = float(state.get("scroll", 0.0) or 0.0)

        # 阅读模式：文档自带的阅读状态优先，否则用全局默认
        mode = read_mode if read_mode in ("single", "continuous") else self.settings.pdf_read_mode()
        canvas.read_mode = "single"      # 先归位，保证下面的切换一定生效
        canvas.set_read_mode(mode)
        tab.page = page
        self._show_page_pdf(page, scroll=True, tab=tab)
        if mode == "continuous" and scroll_frac > 0.0:
            canvas.restore_scroll_frac(scroll_frac)

        try:
            tab.outline = list(engine.outline() or [])
        except Exception:
            tab.outline = []
        tab.search_hits = []
        tab.search_index = -1
    def _pdf_strokes_for(self, tab: Optional[DocumentTab], index: int) -> List:
        """连续阅读模式：画布需要拿到任意页的批注笔画（按标签取，不按「当前」取）。"""
        if tab is None or tab.annot_doc is None:
            return []
        if 0 <= index < len(tab.annot_doc.pages):
            return tab.annot_doc.pages[index].strokes
        return []
    def _open_note_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开笔记", self.settings.last_dir(),
                                              "笔记文件 (*.note)")
        if path:
            self.open_note(path)
    def _open_pdf_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开 PDF", self.settings.last_dir(),
                                              "PDF 文件 (*.pdf)")
        if path:
            self.open_pdf(path)
    def _dispatch_save(self, tab, path: str, kind: str, on_ok=None) -> None:
        """调度一次保存（A4）：快照在这里拍，编码 + 写盘可能落在后台线程。"""
        if kind == "note":
            meta, pages, points = serializer.snapshot_note(tab.note_doc)
        else:
            meta, pages, points = serializer.snapshot_annot(tab.annot_doc)
        # 同一路径重复保存时后一次覆盖前一次的回调
        self._pending_saves[path] = (tab, kind, on_ok)
        tab.dirty = False
        self._refresh_tab_title(tab)
        self._update_window_title()
        self._status(f"正在保存：{os.path.basename(path)}…", 2000)
        # 同步写完时会在 save() 内部回调 _on_save_finished
        self._saver.save(path,
                         lambda: serializer.write_snapshot(path, kind, meta, pages),
                         points)
    def _on_save_finished(self, path: str, ok: bool, error: str) -> None:
        """保存结束（同步或后台线程都会走到这里）。"""
        pending = self._pending_saves.pop(path, None)
        if pending is None:
            return
        tab, _kind, on_ok = pending
        if ok:
            self.last_save_error = ""
            self._annot_retry_done = False
            self._annot_fallbacks = set()     # 保存成功后重置「换位置」记账
            if callable(on_ok):
                on_ok()
            self._status(f"已保存：{path}", 3500)
            return
        # 批注第一次失败（目录不可写 / 被占用 / 权限不足）：按候选链换下一个可写位置
        # 再试（每个位置最多一次）。用户最关心的是「批注别丢」，而不是「必须存在 PDF 旁边」
        if _kind == "annot" and not getattr(self, "_annot_retry_done", False):
            self._annot_retry_done = True
            if self._retry_annot_elsewhere(tab, path):
                return
        self._annot_retry_done = False
        if _kind == "annot":
            error = (f"{error}"
                     "\n这个位置和所有备用位置都写不进去。"
                     f"\n{annot_store.platform_hint()}"
                     f"\n候选位置：{annot_store.describe_candidates()}")
        # 失败：把标签标回「脏」并给非模态提示（保存可能发生在后台，
        # 模态框会打断正在书写的人）
        self.last_save_error = error
        log_warning(_log, "save_failed", path=path, kind=str(_kind), error=error,
                    candidates=annot_store.describe_candidates()
                    if _kind == "annot" else "")
        tab.dirty = True
        self._refresh_tab_title(tab)
        self._status(f"保存失败：{error}", 6000)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("保存失败")
        box.setText(f"保存到 {path} 失败：\n{error}")
        box.setModal(False)
        box.show()
        self._last_save_error_box = box
    def _retry_annot_elsewhere(self, tab, failed_path: str) -> bool:
        """这个位置写不进去时，按「PDF 旁边 → 兜底候选链」换**下一个真的可写**的目标。

        返回 True 表示「已经改道重试」（成功/失败都会再回调一次 `_on_save_finished`）。
        每个路径最多尝试一次（`self._annot_fallbacks`），不会来回绕、也不会死循环。
        """
        if tab is None or not tab.is_pdf or tab.pdf_engine is None or tab.annot_doc is None:
            return False
        tried = getattr(self, "_annot_fallbacks", None)
        if tried is None:
            tried = self._annot_fallbacks = set()
        self._annot_retry_done = False          # 这次改道有结果后再决定是否还允许下一次
        tried.add(os.path.normcase(os.path.abspath(failed_path)))
        target = annot_store.next_target(tab.pdf_engine.path, exclude=failed_path)
        while target and os.path.normcase(os.path.abspath(target)) in tried:
            target = annot_store.next_target(tab.pdf_engine.path, exclude=target)
        if not target or os.path.normcase(os.path.abspath(target)) in tried:
            return False                      # 所有候选都试过了：把真实错误交给用户
        tried.add(os.path.normcase(os.path.abspath(target)))
        tab.annot_path = target
        self._status(f"原位置写不进去，改存到备用位置：{target}", 9000)
        self._dispatch_save(tab, target, "annot")
        return True
    def save(self) -> None:
        tab = self._active
        if tab is None:
            return
        try:
            if tab.is_note:
                if tab.note_doc is None:
                    return
                if not tab.path:
                    self.save_as()
                    return
                self._dispatch_save(tab, tab.path, "note")
            else:
                if tab.pdf_engine is None or tab.annot_doc is None:
                    return
                # PDF 旁边能不能写：写不了就直接用兜底位置（省掉一次注定失败的写盘）
                path, fallback = annot_store.resolve_for_save(tab.pdf_engine.path,
                                                              tab.annot_path or "")
                if fallback:
                    tab.annot_path = path
                    self._status(f"PDF 所在目录不可写，批注保存到备用位置：{path}", 8000)
                # 兜底位置每次保存都要按「当前是否真的可写」重新选：目录权限/占用
                # 可能变化，不能在打开文档时就钉死一个写不进去的位置（线上问题）
                annot_store.log_choice("save", tab.pdf_engine.path, path)
                if not tab.annot_doc.pdf_md5:
                    # 打开时为了提速没有算 MD5，这里才补上（同时写进缓存）
                    tab.annot_doc.pdf_md5 = tab.pdf_engine.md5
                    self.settings.set_pdf_md5(tab.pdf_engine.path,
                                              tab.pdf_engine.file_size,
                                              tab.pdf_engine.mtime,
                                              tab.annot_doc.pdf_md5)

                def _after_annot_save(_tab=tab, _path=path) -> None:
                    _tab.annot_path = _path
                    self._refresh_tab_title(_tab)
                    self._update_window_title()

                self._dispatch_save(tab, path, "annot", on_ok=_after_annot_save)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存出错：\n{e}")
    def save_as(self) -> None:
        tab = self._active
        if tab is None or not tab.is_note:
            self._status("PDF 批注默认保存到 PDF 同名的 .annot 文件（Ctrl+S）", 4000)
            return
        path, _ = QFileDialog.getSaveFileName(self, "另存为",
                                              os.path.join(self.settings.last_dir(), "note.note"),
                                              "笔记文件 (*.note)")
        if path:
            if not path.lower().endswith(".note"):
                path += ".note"
            try:
                def _after_save_as(_tab=tab, _path=path) -> None:
                    _tab.path = _path
                    self._refresh_tab_title(_tab)
                    self._update_window_title()
                    self._add_recent(_path)
                    self.settings.set_last_dir(os.path.dirname(_path))

                self._dispatch_save(tab, path, "note", on_ok=_after_save_as)
            except Exception as e:
                QMessageBox.critical(self, "保存失败", f"保存出错：\n{e}")
    def export_pdf(self) -> None:
        tab = self._active
        if tab is None:
            return
        if tab.is_note:
            if tab.note_doc is None:
                return
            path, _ = QFileDialog.getSaveFileName(self, "导出 PDF",
                                                  os.path.join(self.settings.last_dir(), "note.pdf"),
                                                  "PDF 文件 (*.pdf)")
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            try:
                export_note_to_pdf(tab.note_doc, path)
                self._status(f"已导出：{path}", 5000)
            except Exception as e:
                QMessageBox.critical(self, "导出失败", f"导出出错：\n{e}")
        else:
            if tab.pdf_engine is None or tab.annot_doc is None:
                return
            path, _ = QFileDialog.getSaveFileName(self, "导出带批注的 PDF",
                                                  os.path.join(self.settings.last_dir(), "annotated.pdf"),
                                                  "PDF 文件 (*.pdf)")
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            try:
                tab.pdf_engine.export_with_annotations(tab.annot_doc, path)
                self._status(f"已导出带批注的 PDF：{path}", 5000)
            except Exception as e:
                QMessageBox.critical(self, "导出失败", f"导出出错：\n{e}")
    def _show_doc_info(self) -> None:
        tab = self._active
        if tab is None:
            return
        if not tab.is_pdf or tab.pdf_engine is None:
            doc = tab.note_doc
            info = ("当前标签不是 PDF。\n笔记文档：\n"
                    f"页数：{doc.page_count()}\n"
                    f"页面尺寸：{doc.page_w:.0f} × {doc.page_h:.0f} pt\n"
                    f"背景：{BG_LABELS.get(doc.background, doc.background)}"
                    f"（{doc.background_color}）") if doc is not None else "当前没有打开文档。"
            QMessageBox.information(self, "文档信息", info)
            return
        try:
            m = tab.pdf_engine.metadata()
        except Exception as e:
            QMessageBox.warning(self, "文档信息", f"无法读取文档信息：\n{e}")
            return
        size_kb = m.file_size / 1024.0
        size_txt = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.2f} MB"
        QMessageBox.information(
            self, "文档信息",
            f"文件：{os.path.basename(tab.pdf_engine.path)}\n"
            f"标题：{m.title or '（无）'}\n"
            f"作者：{m.author or '（无）'}\n"
            f"主题：{m.subject or '（无）'}\n"
            f"创建程序：{m.creator or '（无）'}\n"
            f"页数：{m.page_count}\n"
            f"加密：{'是' if m.encrypted else '否'}\n"
            f"大小：{size_txt}")
    def _copy_page_text(self) -> None:
        tab = self._active
        if tab is None or not tab.is_pdf or tab.pdf_engine is None:
            self._status("复制页面文本需要打开 PDF", 3000)
            return
        text = tab.pdf_engine.page_text(tab.page)
        if not text.strip():
            self._status("当前页没有可提取的文本（可能是扫描件）", 3500)
            return
        QApplication.clipboard().setText(text)
        self._status(f"已复制第 {tab.page + 1} 页文本（{len(text)} 字）", 3500)
    def _add_recent(self, path: str) -> None:
        self.recent_files = self.settings.add_recent(path)
        self._build_recent_menu()
    def _build_recent_menu(self) -> None:
        self.recent_menu.clear()
        self.recent_list.clear()
        if not self.recent_files:
            act = QAction("(无)", self)
            act.setEnabled(False)
            self.recent_menu.addAction(act)
            return
        for path in self.recent_files:
            act = QAction(os.path.basename(path), self)
            act.setToolTip(path)
            act.triggered.connect(lambda checked=False, p=path: self._open_recent(p))
            self.recent_menu.addAction(act)
            item = QListWidgetItem(os.path.basename(path))
            item.setToolTip(path)
            item.setData(Qt.UserRole, path)
            self.recent_list.addItem(item)
    def _on_recent_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self._open_recent(path)
    def _open_recent(self, path: str) -> None:
        if not os.path.exists(path):
            QMessageBox.warning(self, "文件不存在", f"找不到文件：\n{path}")
            self.recent_files = self.settings.remove_recent(path)
            self._build_recent_menu()
            return
        # 多标签后不再需要「先保存当前文档」：直接开到新标签里
        if path.lower().endswith(".pdf"):
            self.open_pdf(path)
        else:
            self.open_note(path)

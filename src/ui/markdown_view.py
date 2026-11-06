"""Markdown 只读阅读视图（基于 QTextBrowser）。

设计要点
--------
* **零新依赖**：用 PySide6 自带 QTextDocument 的 Markdown 解析器
  （标题 / 列表 / 表格 / 引用 / 代码块 / 链接 / 图片 / 粗斜体）。
* **与 PageCanvas duck-type 兼容**：MainWindow 里大量通用代码会无差别地往
  「当前标签的 canvas」发消息（切标签、主题刷新、工具样式、资源释放、
  多窗口搬运）。MarkdownView 实现同名的最小桩，让这些路径对 Markdown
  标签安全且无副作用；真正的业务分流（保存 / 翻页 / 批注 / 搜索）在
  controllers 里按 `tab.is_md` 显式处理。
* **复用现有侧栏**：标题大纲接「目录」、全文检索接「搜索」，不另起面板。
"""
from __future__ import annotations

import os
from typing import List, Optional, Sequence

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QTextCharFormat,
                           QTextCursor, QTextDocument, QTextOption)
from PySide6.QtWidgets import QFrame, QTextBrowser, QTextEdit

from src.core.markdown_document import (MdHeading, MdSearchHit, is_markdown,
                                        parse_headings)

_ZOOM_MIN = 0.5
_ZOOM_MAX = 3.0


class MarkdownView(QTextBrowser):
    """渲染 Markdown 的只读富文本视图。"""

    #: 点击了指向另一份本地 .md 的链接 → 让主窗口在新标签打开
    open_markdown_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        # 自己处理链接：避免 QTextBrowser 把相对链接 setSource 成乱码盖掉当前文档
        self.setOpenLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setUndoRedoEnabled(False)
        self.setTabStopDistance(28.0)
        self.anchorClicked.connect(self._on_anchor)
        self._base_font = QFont(self.font())

        self._text: str = ""
        self._path: str = ""
        self._headings: List[MdHeading] = []
        self._hits: List[MdSearchHit] = []
        self._hit_index: int = -1
        self._mode: str = "render"          # render / source
        self._zoom: float = 1.0
        self._colors: dict = {}
        self._highlight_fg = QColor("#10a37f")
        self._highlight_bg = QColor("#d7f0e8")

        # ---- PageCanvas 兼容桩（见模块说明） ----
        self.scale = 1.0
        self.current_index = 0
        self.total_pages = 1
        self.read_mode = "single"
        self.rotation = 0
        self.undo_mgr = None
        self.tool = None
        # 工具栏状态栏会读当前标签的笔样式；Markdown 只读，给个无害默认值即可
        self.pen_color = "#000000"
        self.highlighter_color = "#ffff00"
        self.stroke_provider = None
        self.on_page_request = None
        self._render_cache = None
        self.canvas_bg = ""

        self._apply_stylesheet({})

    # ------------------------------------------------------------------ #
    # 文档装载 / 模式
    # ------------------------------------------------------------------ #
    def set_markdown(self, text: str, path: str = "") -> None:
        self._text = text or ""
        self._path = path or ""
        self._headings = parse_headings(self._text)
        self._hits = []
        self._hit_index = -1
        if self._path:
            folder = os.path.dirname(os.path.abspath(self._path))
            self.setSearchPaths([folder])
            self.document().setMetaInformation(
                QTextDocument.MetaInformation.DocumentUrl,
                QUrl.fromLocalFile(os.path.abspath(self._path)).toString())
        self._rerender(keep_frac=False)

    def source_text(self) -> str:
        return self._text

    def path(self) -> str:
        return self._path

    def headings(self) -> List[MdHeading]:
        return list(self._headings)

    def view_mode(self) -> str:
        return self._mode

    def set_view_mode(self, mode: str) -> None:
        mode = "source" if mode == "source" else "render"
        if mode != self._mode:
            self._mode = mode
            self._rerender()

    def _rerender(self, keep_frac: bool = True) -> None:
        frac = self.scroll_offset_frac() if keep_frac else 0.0
        self._rerender_content()
        if frac > 0.0:
            QTimer.singleShot(0, lambda f=frac: self.restore_scroll_frac(f))

    def _rerender_content(self) -> None:
        if self._mode == "source":
            self.setFont(self._base_font)
            self.setPlainText(self._text)
            cur = QTextCursor(self.document())
            cur.select(QTextCursor.SelectionType.Document)
            fmt = QTextCharFormat()
            fmt.setFontFamilies(["Cascadia Mono", "Consolas",
                                 "Microsoft YaHei Mono", "Courier New", "monospace"])
            fmt.setFontPointSize(10.0 + (self._zoom - 1.0) * 10.0)
            cur.mergeCharFormat(fmt)
        else:
            self.setFont(self._base_font)
            self.setMarkdown(self._text)
            self._apply_zoom_step()
        self.setExtraSelections([])
        self._apply_hits_highlight()

    # ------------------------------------------------------------------ #
    # 主题 / 缩放
    # ------------------------------------------------------------------ #
    def apply_theme(self, colors: dict) -> None:
        """由主窗口下发 ThemeManager.colors()；换主题后重渲染并保留滚动位置。"""
        self._apply_stylesheet(colors or {})
        if self._text:
            self._rerender()

    def set_canvas_bg(self, color: str) -> None:
        """`_apply_theme` 对所有标签统一调用；Markdown 的配色走 apply_theme。"""
        self.canvas_bg = color

    def _apply_stylesheet(self, colors: dict) -> None:
        c = colors or {}
        bg = c.get("card") or "#ffffff"
        fg = c.get("text") or "#1f2328"
        muted = c.get("text_secondary") or "#6b7280"
        border = c.get("border") or "#e5e5e5"
        hover = c.get("hover") or "#eceef0"
        accent = c.get("accent") or "#10a37f"
        highlight = c.get("highlight") or "#d7f0e8"
        self._colors = dict(c)
        self._highlight_fg = QColor(accent)
        self._highlight_bg = QColor(highlight)
        self.setStyleSheet(
            f"QTextBrowser {{ background-color: {bg}; color: {fg}; border: none; "
            f"padding: 20px 28px; selection-background-color: {highlight}; }}")
        self.document().setDefaultStyleSheet(
            f"body {{ color: {fg}; }} "
            f"a {{ color: {accent}; }} "
            f"h1, h2, h3, h4, h5, h6 {{ color: {fg}; }} "
            f"code {{ background-color: {hover}; color: {fg}; }} "
            f"pre {{ background-color: {hover}; color: {fg}; padding: 8px; }} "
            f"blockquote {{ color: {muted}; border-left: 3px solid {border}; "
            f"margin-left: 0; padding-left: 12px; }} "
            f"table, th, td {{ border: 1px solid {border}; border-collapse: collapse; }} "
            f"th, td {{ padding: 4px 8px; }} "
            f"hr {{ border: none; border-top: 1px solid {border}; }}")

    def _apply_zoom_step(self) -> None:
        """setMarkdown 会重建文档，把累计缩放重新补回去（保留标题字号层级）。"""
        step = int(round((self._zoom - 1.0) * 10.0))
        if step > 0:
            self.zoomIn(step)
        elif step < 0:
            self.zoomOut(-step)

    def zoom_to(self, factor: float) -> None:
        new_zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, self._zoom * float(factor)))
        if abs(new_zoom - self._zoom) < 1e-6:
            return
        delta = (new_zoom - self._zoom) * 10.0
        step = int(round(abs(delta)))
        if step:
            if delta > 0:
                self.zoomIn(step)
            else:
                self.zoomOut(step)
        self._zoom = new_zoom
        self.scale = new_zoom

    def actual_size(self) -> None:
        self.zoom_to(1.0 / self._zoom if self._zoom else 1.0)

    # ------------------------------------------------------------------ #
    # 滚动位置（阅读进度）
    # ------------------------------------------------------------------ #
    def scroll_offset_frac(self) -> float:
        sb = self.verticalScrollBar()
        rng = sb.maximum() - sb.minimum()
        if rng <= 0:
            return 0.0
        return max(0.0, min(1.0, (sb.value() - sb.minimum()) / float(rng)))

    def restore_scroll_frac(self, frac: float) -> None:
        def _do() -> None:
            sb = self.verticalScrollBar()
            rng = sb.maximum() - sb.minimum()
            if rng <= 0:
                return
            sb.setValue(int(sb.minimum() + max(0.0, min(1.0, float(frac))) * rng))
        # 文档刚装载时布局还没算完，等一拍再设，否则会被拉回顶部
        QTimer.singleShot(0, _do)

    # ------------------------------------------------------------------ #
    # 目录 / 搜索
    # ------------------------------------------------------------------ #
    def scroll_to_heading(self, index: int) -> None:
        """跳到第 index 个标题（同名标题按顺序取第 n 个）。"""
        if self._mode != "render" or not (0 <= index < len(self._headings)):
            return
        h = self._headings[index]
        nth = sum(1 for x in self._headings[:index] if x.title == h.title)
        doc = self.document()
        pos = 0
        cur = QTextCursor()
        for _ in range(nth + 1):
            cur = doc.find(h.title, pos)
            if cur.isNull():
                break
            pos = cur.selectionEnd()
        if cur.isNull():
            return
        self.setTextCursor(cur)
        self.ensureCursorVisible()
        self.setFocus()

    def find_all(self, query: str, case_sensitive: bool = False,
                 whole_word: bool = False, max_hits: int = 1000) -> List[MdSearchHit]:
        """在渲染后的文档里全文检索（位置准，可直接用于高亮/跳转）。"""
        q = (query or "").strip()
        if not q:
            self.clear_search()
            return []
        flags = QTextDocument.FindFlags()
        if case_sensitive:
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if whole_word:
            flags |= QTextDocument.FindFlag.FindWholeWords
        doc = self.document()
        out: List[MdSearchHit] = []
        pos = 0
        total = doc.characterCount()
        while len(out) < max_hits:
            cur = doc.find(q, pos, flags)
            if cur.isNull():
                break
            start, end = cur.selectionStart(), cur.selectionEnd()
            ctx_cur = QTextCursor(doc)
            ctx_cur.setPosition(max(0, start - 24))
            # setPosition 超出 characterCount()-1 会被 Qt 整条忽略（选中区间变空），
            # 夹到「最后一个字符」的位置，别用 characterCount() 本身。
            ctx_cur.setPosition(min(max(total - 1, 0), end + 24),
                                QTextCursor.MoveMode.KeepAnchor)
            context = ctx_cur.selectedText().replace("\u2029", " ").strip()
            out.append(MdSearchHit(context=context, position=start, length=len(q)))
            pos = end
        return out

    def focus_hit(self, position: int) -> None:
        cur = QTextCursor(self.document())
        cur.setPosition(position)
        self.setTextCursor(cur)
        self.ensureCursorVisible()
        self.setFocus()

    def highlight_hits(self, hits: Sequence[MdSearchHit], current_index: int = -1) -> None:
        self._hits = list(hits or [])
        self._hit_index = current_index
        self._apply_hits_highlight()

    def _apply_hits_highlight(self) -> None:
        if not self._hits:
            self.setExtraSelections([])
            return
        doc = self.document()
        sels = []
        for i, h in enumerate(self._hits):
            cur = QTextCursor(doc)
            cur.setPosition(h.position)
            cur.setPosition(h.position + (h.length or 1),
                            QTextCursor.MoveMode.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            fmt = QTextCharFormat()
            if i == self._hit_index:
                fmt.setBackground(self._highlight_fg)
                fmt.setForeground(QColor("#ffffff"))
            else:
                fmt.setBackground(self._highlight_bg)
            sel.format = fmt
            sels.append(sel)
        self.setExtraSelections(sels)

    def clear_search(self) -> None:
        self._hits = []
        self._hit_index = -1
        self.setExtraSelections([])

    def set_search_results(self, hits, current_index: int = 0,
                           page_index: Optional[int] = None) -> None:
        """兼容 `_apply_search_highlight` 的统一调用。"""
        self.highlight_hits(hits, current_index)

    # ------------------------------------------------------------------ #
    # 链接
    # ------------------------------------------------------------------ #
    def _on_anchor(self, url: QUrl) -> None:
        if url.toString().startswith("#"):
            return  # 文档内锚点：Qt 的 Markdown 不生成对应目标，忽略
        if url.isLocalFile():
            path = url.toLocalFile()
        elif not url.scheme():
            path = url.path()
        else:
            QDesktopServices.openUrl(url)
            return
        path = os.path.abspath(path)
        if is_markdown(path) and os.path.isfile(path):
            self.open_markdown_requested.emit(path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ------------------------------------------------------------------ #
    # PageCanvas 兼容桩：通用代码无差别调用，Markdown 一律无副作用
    # ------------------------------------------------------------------ #
    def detach_pdf(self, *a, **k) -> None:
        pass

    def set_pen_color(self, *a, **k) -> None:
        pass

    def set_pen_width(self, *a, **k) -> None:
        pass

    def set_eraser_width(self, *a, **k) -> None:
        pass

    def set_highlighter_color(self, *a, **k) -> None:
        pass

    def set_highlighter_width(self, *a, **k) -> None:
        pass

    def set_tool(self, *a, **k) -> None:
        pass

    def set_read_mode(self, mode: str) -> None:
        self.read_mode = "single"

    def set_page_info(self, *a, **k) -> None:
        pass

    def set_note_content(self, *a, **k) -> None:
        pass

    def set_pdf_page(self, *a, **k) -> None:
        pass

    def set_rotation(self, *a, **k) -> None:
        pass

    def set_background(self, *a, **k) -> None:
        pass

    def set_canvas_bg_color(self, *a, **k) -> None:
        pass

    def focus_page_rect(self, *a, **k) -> None:
        pass

    def zoom_at(self, *a, **k) -> None:
        pass

    def pan_by(self, *a, **k) -> None:
        pass

    def rotate_by(self, *a, **k) -> None:
        pass

    def clear_pdf_cache(self, *a, **k) -> None:
        pass

    def rebuild_pixmap(self, *a, **k) -> None:
        pass

    def reset_zoom_policy(self, *a, **k) -> None:
        pass

    def fit_to_view(self, *a, **k) -> None:
        pass

    def fit_width(self, *a, **k) -> None:
        pass

    def undo(self, *a, **k) -> None:
        pass

    def redo(self, *a, **k) -> None:
        pass

    def clear_page(self, *a, **k) -> None:
        pass

    def refresh_tools(self, *a, **k) -> None:
        pass

    def available_tools(self):
        return []

    def current_tool_id(self) -> str:
        return "pen"

    def update(self, *a, **k) -> None:
        super().update()

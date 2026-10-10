"""缩略图侧栏：每页一个独立缩略图条目，竖向排列、可滚动。

- 笔记模式：同步渲染各页缩略图（含背景色、背景线与笔画，与画布共用绘制器）。
- PDF 模式：通过有界线程池（默认 4 线程）异步加载，避免大 PDF 创建上百线程；
  页面旋转时缩略图同步旋转、条目尺寸同步换轴。
- 每个缩略图下方显示页码（「第 N 页」），当前页高亮，点击跳转。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from src.core.colors import DEFAULT_BG_COLOR, grid_color_for, line_color_for
from src.core.note_document import BG_DOTS, BG_GRID, BG_LINES, NoteDocument
from src.render.stroke_painter import paint_strokes
from src.render.thumbnail_pool import ThumbnailPool


def _paint_background(p: QPainter, background: str, w: float, h: float, step: float,
                      bg_color: str = DEFAULT_BG_COLOR) -> None:
    """在缩略图上画网格/横线/点阵（线色随底色明暗反转，与画布观感一致）。"""
    if background == BG_GRID:
        p.setPen(QPen(QColor(grid_color_for(bg_color)), 1.0))
        x = step
        while x < w:
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            x += step
        y = step
        while y < h:
            p.drawLine(QPointF(0, y), QPointF(w, y))
            y += step
    elif background == BG_LINES:
        p.setPen(QPen(QColor(line_color_for(bg_color)), 1.0))
        y = step
        while y < h:
            p.drawLine(QPointF(0, y), QPointF(w, y))
            y += step
    elif background == BG_DOTS:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(line_color_for(bg_color)))
        y = step
        while y < h:
            x = step
            while x < w:
                p.drawEllipse(QPointF(x, y), 1.0, 1.0)
                x += step
            y += step
        p.setBrush(Qt.NoBrush)


def render_note_thumb(page, page_w: float, page_h: float, max_w: int = 150,
                      background: str = "blank",
                      bg_color: str = DEFAULT_BG_COLOR) -> QPixmap:
    """离线渲染笔记页缩略图（背景色 + 背景线 + 笔画）。"""
    scale = max_w / page_w
    w = max(1, int(page_w * scale))
    h = max(1, int(page_h * scale))
    pm = QPixmap(w, h)
    pm.fill(QColor(bg_color or DEFAULT_BG_COLOR))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    _paint_background(p, background, w, h, 25.0 * scale, bg_color)
    paint_strokes(p, page.strokes, scale)
    p.end()
    return pm


class ThumbItem(QWidget):
    """单个页面的缩略图条目：上方缩略图、下方页码，可点击跳转。"""

    clicked = Signal(int)

    def __init__(self, index: int, thumb_w: int, thumb_h: int,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.index = index
        self._thumb_w = thumb_w
        self._thumb_h = thumb_h

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignCenter)

        self.img = QLabel()
        self.img.setFixedSize(thumb_w, thumb_h)
        self.img.setStyleSheet("background:#ffffff; border:1px solid #d0d0d0; border-radius:4px;")
        self.img.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.img)

        self.label = QLabel(f"第 {index + 1} 页")
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)

        self.set_selected(False)

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self.img.setPixmap(pixmap.scaled(self._thumb_w, self._thumb_h,
                                         Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.setStyleSheet(
                "ThumbItem{border:2px solid #10a37f; border-radius:8px; background:#d7f0e8;}")
        else:
            self.setStyleSheet(
                "ThumbItem{border:2px solid transparent; border-radius:8px; background:transparent;}")
        self.label.setStyleSheet(
            f"color:{'#0b7a5f' if selected else '#6b7280'}; font-size:11px; "
            f"font-weight:{'600' if selected else '400'};")

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.index)
        super().mousePressEvent(event)


class ThumbnailPanel(QWidget):
    """左侧缩略图列表（竖向、每页独立条目）。

    v4.2 起改为**懒加载**：只为当前页附近的一段（默认 60 页）创建条目并提交渲染，
    滚动/跳页时再向后补。这样 1000 页的 PDF 也能秒开，且不会一次性创建上千个控件。
    """

    page_selected = Signal(int)

    #: 一次创建多少页的条目
    _WINDOW = 60
    #: 每次只为「当前页前后」这么多页提交渲染任务。
    #: 一次把几百页都丢给 4 个线程会抢光 CPU/IO，反而拖慢打开与翻页。
    _RENDER_AHEAD = 12
    _RENDER_BEHIND = 3

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._items: Dict[int, ThumbItem] = {}
        self._current = 0
        self._thumb_w = 150
        self._rotation = 0
        self._engine = None
        self._pdf_path = ""
        self._count = 0
        self._rendered: set = set()
        self._avg_h = 220.0
        self._pool = ThumbnailPool(workers=4, parent=self)
        self._pool.thumbnail_ready.connect(self._on_pdf_thumb)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.vbox = QVBoxLayout(self.content)
        self.vbox.setContentsMargins(6, 6, 6, 6)
        self.vbox.setSpacing(8)
        self.vbox.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # ------------------------------------------------------------------ #
    def _clear(self) -> None:
        self._pool.clear_pending()
        for w in self._items.values():
            self.vbox.removeWidget(w)
            w.deleteLater()
        self._items.clear()
        self._engine = None
        self._pdf_path = ""
        self._count = 0
        self._rendered.clear()

    def _make_item(self, index: int, page_w: float, page_h: float) -> ThumbItem:
        if self._rotation % 180:
            page_w, page_h = page_h, page_w
        thumb_h = max(80, int(self._thumb_w * (page_h / page_w)))
        item = ThumbItem(index, self._thumb_w, thumb_h)
        item.clicked.connect(self.page_selected.emit)
        # 懒加载时条目会不连续地补进来，必须按页序插入
        pos = sum(1 for i in self._items if i < index)
        self.vbox.insertWidget(pos, item)
        self._items[index] = item
        self._avg_h = float(thumb_h + 46)
        return item

    # ------------------------------------------------------------------ #
    # 懒加载
    # ------------------------------------------------------------------ #
    def _ensure_range(self, lo: int, hi: int) -> None:
        """确保 [lo, hi] 范围内的条目存在，并为新建的条目提交渲染任务。"""
        if self._engine is None or self._count <= 0:
            return
        lo = max(0, int(lo))
        hi = min(self._count - 1, int(hi))
        if hi < lo:
            return
        for i in range(lo, hi + 1):
            if i not in self._items:
                w, h = self._engine.page_size(i)
                self._make_item(i, w, h)

    def _submit_around(self, index: int) -> None:
        """只渲染当前页附近的一段：既能立刻看到缩略图，又不抢走打开/翻页的 CPU。"""
        if self._engine is None or self._count <= 0:
            return
        lo = max(0, int(index) - self._RENDER_BEHIND)
        hi = min(self._count - 1, int(index) + self._RENDER_AHEAD)
        self._submit(range(lo, hi + 1))

    def _submit(self, indices) -> None:
        todo = [i for i in indices if i not in self._rendered]
        if not todo:
            return
        self._rendered.update(todo)
        # reset=False：追加到当前批次，不打断已在跑的任务
        self._pool.submit(self._pdf_path, todo, self._thumb_w, self._rotation,
                          reset=False)

    def _on_scroll(self, value: int) -> None:
        """滚动时按平均条目高度估算可见位置，前后各扩一段（懒加载 + 预取）。"""
        if self._engine is None or not self._items:
            return
        first = int(value / max(1.0, self._avg_h))
        self._ensure_range(first - self._WINDOW, first + 2 * self._WINDOW)
        self._submit_around(first)

    # ------------------------------------------------------------------ #
    def set_note_doc(self, doc: NoteDocument) -> None:
        self._rotation = 0
        self._clear()
        bg = getattr(doc, "background", "blank")
        bgc = getattr(doc, "background_color", DEFAULT_BG_COLOR) or DEFAULT_BG_COLOR
        for i, page in enumerate(doc.pages):
            item = self._make_item(i, doc.page_w, doc.page_h)
            item.set_thumbnail(render_note_thumb(page, doc.page_w, doc.page_h,
                                                 self._thumb_w, bg, bgc))
        self._refresh_current()

    def refresh_note_page(self, doc: NoteDocument, index: int) -> None:
        item = self._items.get(index)
        if item is not None:
            bg = getattr(doc, "background", "blank")
            bgc = getattr(doc, "background_color", DEFAULT_BG_COLOR) or DEFAULT_BG_COLOR
            item.set_thumbnail(
                render_note_thumb(doc.pages[index], doc.page_w, doc.page_h,
                                  self._thumb_w, bg, bgc))

    def set_pdf_engine(self, engine, rotation: int = 0) -> None:
        self._rotation = int(rotation or 0) % 360
        self._clear()
        self._engine = engine
        self._pdf_path = getattr(engine, "path", "") or ""
        self._count = int(engine.page_count())
        # 只为开头一段建条目：页数再多也不影响打开速度
        self._ensure_range(0, min(self._count - 1, self._WINDOW))
        self._submit_around(self._current)
        self._refresh_current()

    def _on_pdf_thumb(self, index: int, img: QImage) -> None:
        item = self._items.get(index)
        if item is not None:
            item.set_thumbnail(QPixmap.fromImage(img))

    # ------------------------------------------------------------------ #
    def set_current(self, index: int) -> None:
        self._current = index
        self._ensure_range(index - self._WINDOW // 2, index + self._WINDOW)
        self._submit_around(index)
        self._refresh_current()
        self._scroll_to(index)

    def _scroll_to(self, index: int) -> None:
        item = self._items.get(index)
        if item is not None:
            self.scroll.ensureWidgetVisible(item, 24, 60)

    def _refresh_current(self) -> None:
        for idx, item in self._items.items():
            item.set_selected(idx == self._current)

    def shutdown(self) -> None:
        self._pool.shutdown()

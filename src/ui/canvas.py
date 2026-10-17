"""手写画布组件。

职责边界
--------
* 只负责「视图 + 输入路由 + 渲染调度」，具体绘制/编辑语义全部委托给 `src.tools` 中的工具；
* 坐标与连续布局见 `canvas_layout.CanvasLayoutMixin`（A7 拆分）；
* 渲染、缓存、异步 PDF 见 `canvas_render.CanvasRenderMixin`（A7 拆分）。

信号定义必须留在本类（Qt 不支持在普通 mixin 里声明 Signal）。
"""

from __future__ import annotations

from collections import OrderedDict, deque
from typing import Dict, List, Optional, Tuple
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QTabletEvent, QTransform)
from PySide6.QtWidgets import QWidget

from src.ui.canvas_input import CanvasInputMixin
from src.ui.canvas_layout import CanvasLayoutMixin
from src.ui.canvas_render import CanvasRenderMixin
from src.ui.page_scrollbar import PageScrollBar, scrollbar_palette
from src.core.colors import DEFAULT_BG_COLOR, grid_color_for, line_color_for
from src.core.hn_log import get_logger, log_debug, log_warning
from src.core.stroke import (HIGHLIGHTER_COLOR, HIGHLIGHTER_WIDTH, ClearCmd,
                             Stroke, UndoManager)
from src.render.pdf_engine import PdfEngine, PdfRenderWorker
from src.tools import Tool, ToolContext, get_registry

BG_BLANK = "blank"
BG_GRID = "grid"
BG_LINES = "lines"
BG_DOTS = "dots"
_TABLET_NAME_BY_VALUE = {0: "UnknownPointer", 1: "Pen", 2: "Cursor",
                         3: "Eraser", 4: "Mouse"}
_log = get_logger("ui.canvas")
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
def _tablet_pointer_name(e: QTabletEvent) -> str:
    pt = e.pointerType()
    if hasattr(pt, "name"):
        return pt.name
    try:
        return _TABLET_NAME_BY_VALUE.get(int(pt), "Unknown")
    except Exception:
        return "Unknown"
def _event_local_pos(e) -> QPointF:
    """兼容 Qt6 各版本：优先 position()，回退 posF()/pos()。"""
    if hasattr(e, "position"):
        return e.position()
    if hasattr(e, "posF"):
        return e.posF()
    return QPointF(e.pos())



class PageCanvas(CanvasRenderMixin, CanvasLayoutMixin, CanvasInputMixin, QWidget):
    """手写/批注画布（工具无关）。"""
    zoom_changed = Signal(float)
    page_info_changed = Signal(int, int, float)
    cursor_pos_changed = Signal(object, object)
    content_modified = Signal()
    undo_state_changed = Signal(bool, bool)
    tool_changed = Signal(str)
    rotation_changed = Signal(int)
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_TabletTracking, True)
        self.setFocusPolicy(Qt.StrongFocus)

        # 视图状态
        self.scale: float = 1.0
        self.offset: QPointF = QPointF(0, 0)
        self._user_zoomed: bool = False
        self.rotation: int = 0            # 显示旋转：0 / 90 / 180 / 270（顺时针）

        # 页面 / 内容
        self.mode: str = "note"
        self.page_w_pt: float = 595.28
        self.page_h_pt: float = 841.89
        self.background: str = BG_BLANK
        self.bg_color: str = DEFAULT_BG_COLOR
        self.page_strokes: List[Stroke] = []
        self.undo_mgr: Optional[UndoManager] = None

        # PDF 相关
        self.pdf_engine: Optional[PdfEngine] = None
        self.pdf_index: int = 0
        self.total_pages: int = 1
        self.current_index: int = 0

        # 画笔样式（转发给具体工具）
        self.pen_color: str = "#000000"
        self.pen_width: float = 3.0
        self.eraser_width: float = 20.0
        self.highlighter_color: str = HIGHLIGHTER_COLOR
        self.highlighter_width: float = HIGHLIGHTER_WIDTH

        # 绘制状态
        self.page_pixmap: QPixmap = QPixmap()
        self.drawing: bool = False
        self._using_tablet: bool = False
        self._panning: bool = False
        self._pan_last: QPointF = QPointF()
        self.space_pressed: bool = False
        self._hover_css: QPointF = QPointF()
        self._hover: bool = False
        self._active_device: Optional[str] = None
        self._eraser_override: bool = False
        self._pre_override_tool: str = "pen"
        #: 最近一次手写笔事件的时间戳（monotonic）。用于识别「笔通道僵死」：
        #: 笔的 Release 丢了以后 `_active_device` 会永久卡在 "tablet"，鼠标随之失效。
        self._tablet_last_ts: float = 0.0

        self.canvas_bg: str = "#e9eaee"

        # 搜索高亮（页面坐标矩形）
        self.search_rects: List[Tuple[float, float, float, float]] = []
        self.search_index: int = -1

        # 工具系统（扩展点）
        self.ctx = ToolContext()
        self._tools: Dict[str, Tool] = {}
        self._current_tool_id: str = "pen"
        self.tool: Optional[Tool] = None
        self._wire_context()
        self.refresh_tools()

        # PDF 渲染缓存（LRU）与单线程异步渲染
        # 缓存键包含「文档标识 + 旋转」：否则打开第二个同尺寸 PDF 时会命中上一个文档的缓存，
        # 导致显示上一个 PDF 的页面（曾经的真实 bug）。
        self._render_cache: "OrderedDict[tuple, QImage]" = OrderedDict()
        self._doc_key: Optional[str] = None
        self._render_token: int = 0
        self._render_thread: Optional[PdfRenderWorker] = None
        # 渲染请求队列（连续阅读会同时需要多页；单页模式用它做「下一页预取」）
        self._pending_reqs: "deque[tuple]" = deque()
        self._requested: set = set()

        # 阅读模式：single（单页）/ continuous（连续滚动）
        self.read_mode: str = "single"
        self.page_gap: float = 18.0        # 连续模式下相邻页的间距（CSS px）
        #: callable(index) -> List[Stroke]，连续模式绘制其它页批注时使用
        self.stroke_provider = None
        #: callable(index) -> None，由主窗口绑定：请求把「当前页」切换到 index
        self.on_page_request = None
        # 连续模式的纵向布局（CSS px，文档坐标）
        self._layout_tops: List[float] = []
        self._layout_heights: List[float] = []
        self._layout_width: float = 0.0
        self._layout_scale: float = -1.0
        self._layout_rot: int = -1
        self._layout_total: int = -1
        #: 「还要按这个比例恢复一次滚动位置」。装载文档时画布往往还没有真实尺寸，
        #: 那时算出来的 offset 是不可靠的；等第一次真正排版（显示 / resize）后再补一次，
        #: 用户重新打开书才会落在原来那一段文字上，而不是页首。
        self._restore_frac: Optional[float] = None

        # 右侧 / 下侧的网页式滑动条（自绘）。它们只是「显示 + 输入」的末端：
        # 位置、显隐、比例全部由 `canvas_layout` 里的 _sync_scrollbars 推过来，
        # 滑块拖出来的比例又回到 set_scroll_fraction —— 双向都走同一套夹紧边界。
        self._scroll_palette = scrollbar_palette(self.canvas_bg)
        self._vbar = PageScrollBar(Qt.Orientation.Vertical, self)
        self._hbar = PageScrollBar(Qt.Orientation.Horizontal, self)
        self._vbar.value_changed.connect(lambda v: self.set_scroll_fraction(True, v))
        self._hbar.value_changed.connect(lambda v: self.set_scroll_fraction(False, v))
        self._vbar.wheel_scrolled.connect(lambda d: self._scroll_by_wheel(True, d))
        self._hbar.wheel_scrolled.connect(lambda d: self._scroll_by_wheel(False, d))
        for bar in (self._vbar, self._hbar):
            bar.apply_palette(self._scroll_palette)
    def _apply_style_to_tools(self) -> None:
        pen = self._tools.get("pen")
        if pen is not None and hasattr(pen, "set_style"):
            pen.set_style(color=self.pen_color, width=self.pen_width)
        er = self._tools.get("eraser")
        if er is not None and hasattr(er, "set_style"):
            er.set_style(width=self.eraser_width)
        hl = self._tools.get("highlighter")
        if hl is not None and hasattr(hl, "set_style"):
            hl.set_style(color=self.highlighter_color, width=self.highlighter_width)
    def _bind_page(self, strokes: List[Stroke], undo: UndoManager) -> None:
        self.page_strokes = strokes
        self.undo_mgr = undo
        self.drawing = False
        self._active_device = None
        self.ctx.strokes = strokes
        self.ctx.undo = undo
    def _clamp_offset(self) -> None:
        """保证内容始终留在视口内，避免「滚出去找不回来」。

        边界只在 `_clamp_bounds()` 里写一处（min > max = 该轴装得下 → 居中），
        滑动条读的是同一份边界，所以「滑块到底」与「页面到边」永远是同一件事。
        视口尺寸扣掉了滑动条占位，内容不会被条压住。
        """
        if self.read_mode == "continuous" and self.mode == "pdf":
            self._ensure_layout()
            if not self._layout_tops:
                self._sync_scrollbars()          # 空文档：条也一并收起
                return
        min_x, max_x, min_y, max_y = self._clamp_bounds()
        vw, vh = self._viewport_size()
        cw, ch = self._content_size_css()
        self.offset.setX(_clamp(self.offset.x(), min_x, max_x) if min_x <= max_x
                         else (vw - cw) / 2.0)
        self.offset.setY(_clamp(self.offset.y(), min_y, max_y) if min_y <= max_y
                         else (vh - ch) / 2.0)
        self._sync_scrollbars()
    def _emit_cursor(self, x: float, y: float) -> None:
        css = self.page_to_css(x, y)
        if self._inside_page(css):
            self.cursor_pos_changed.emit(round(x, 1), round(y, 1))
        else:
            self.cursor_pos_changed.emit(None, None)
    def _emit_modified(self) -> None:
        self.content_modified.emit()
    def _emit_page_info(self) -> None:
        self.page_info_changed.emit(self.current_index, self.total_pages, self.scale)
    def _emit_undo_state(self) -> None:
        self.undo_state_changed.emit(bool(self.undo_mgr and self.undo_mgr.can_undo()),
                                     bool(self.undo_mgr and self.undo_mgr.can_redo()))
    def _fit_to(self, mode: str = "page") -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        # 重新排版前先记下「现在看到哪儿」：缩放/fit 之后内容总高会变，
        # 只按比例还原会把画面挪到别的页；按「页 + 页首在屏幕上的位置」摆回去才对得上。
        anchor = self.view_anchor()
        margin = 24.0
        dw, dh = self.display_size_pt()
        vw = max(1.0, self.width() - margin * 2)
        vh = max(1.0, self.height() - margin * 2)
        if mode == "width" and self.read_mode == "continuous" and self.mode == "pdf":
            self._layout_scale = -1.0
            self._ensure_layout()
            dw = max(1.0, self._layout_width / max(self.scale, 1e-6))
        if mode == "width":
            self.scale = _clamp(vw / dw, 0.05, 10.0)
        else:
            self.scale = _clamp(min(vw / dw, vh / dh), 0.05, 10.0)
        if self.read_mode == "continuous" and self.mode == "pdf":
            self._layout_scale = -1.0
            self._ensure_layout()
            self.offset = QPointF((self.width() - self._content_width_css()) / 2.0, 12.0)
            self._scroll_to_page(self.pdf_index, align="top")
        else:
            css_w, css_h = dw * self.scale, dh * self.scale
            self.offset = QPointF((self.width() - css_w) / 2.0,
                                  12.0 if mode == "width" and css_h > vh
                                  else (self.height() - css_h) / 2.0)
            self._clamp_offset()
        self.apply_view_anchor(anchor)
        self._apply_restore_frac()      # 装载时的「上次读到哪个比例」现在才真正可用
        self.zoom_changed.emit(self.scale)
    def _scroll_to_page(self, index: int, align: str = "top") -> None:
        """把某页滚到视口顶部/居中（仅连续模式生效）。"""
        if self.mode != "pdf" or self.read_mode != "continuous":
            return
        self._ensure_layout()
        if not (0 <= index < len(self._layout_tops)):
            return
        top = self._layout_tops[index]
        if align == "center":
            self.offset.setY(self.height() / 2.0 - top - self._layout_heights[index] / 2.0)
        else:
            self.offset.setY(12.0 - top)
        self._clamp_offset()
        self.update()
    def _sync_search_rects(self) -> None:
        """翻页后由外部调用（MainWindow）重新过滤命中；这里仅清空等待外部刷新。"""
        self.search_rects = []
        self.search_index = -1
    def _update_cursor(self) -> None:
        if self.space_pressed or self._panning:
            self.setCursor(QCursor(Qt.OpenHandCursor))
        elif self.tool is not None:
            self.setCursor(QCursor(self.tool.cursor))
        else:
            self.setCursor(QCursor(Qt.ArrowCursor))
    def _wire_context(self) -> None:
        self.ctx.on_rebuild = self.rebuild_pixmap
        self.ctx.on_update = self.update
        self.ctx.on_modified = self._emit_modified
        self.ctx.on_undo_state = self._emit_undo_state
    def actual_size(self) -> None:
        """实际大小（100%，1 point = 1 CSS px，即 72dpi）。"""
        if self.width() <= 0 or self.height() <= 0:
            return
        self.scale = 1.0
        dw, dh = self.display_size_pt()
        self.offset = QPointF((self.width() - dw) / 2.0, 12.0)
        self._user_zoomed = True
        self._clamp_offset()
        self.zoom_changed.emit(self.scale)
        self.rebuild_pixmap()
        self._emit_page_info()
        self.update()
    def available_tools(self) -> List[Tuple[str, str]]:
        """[(id, 名称)]，供 UI 动态生成工具按钮。"""
        return [(tid, getattr(t, "name", tid)) for tid, t in self._tools.items()]
    def clear_page(self) -> None:
        if self.undo_mgr is None:
            return
        self.undo_mgr.push(ClearCmd(self.page_strokes))
        self.rebuild_pixmap()
        self._emit_modified()
        self._emit_undo_state()
        self.update()
    def clear_pdf_cache(self) -> None:
        self._render_cache.clear()
        self._cancel_pending()
    def clear_search(self) -> None:
        self.search_rects = []
        self.search_index = -1
        self.update()
    def current_tool_id(self) -> str:
        return self._current_tool_id
    def fit_to_view(self) -> None:
        """适应页面（整页可见）。"""
        self._fit_to(mode="page")
    def fit_width(self) -> None:
        """适应宽度（阅读器常用：宽度铺满，纵向滚动）。"""
        self._fit_to(mode="width")
    def focus_page_rect(self, rect: Tuple[float, float, float, float]) -> None:
        """把页面坐标矩形滚动到视口中央。"""
        self._restore_frac = None      # 跳到搜索命中/书签：以这次跳转为准
        x0, y0, x1, y1 = rect
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        css = self.page_to_css(cx, cy)
        if self.read_mode == "continuous" and self.mode == "pdf":
            self.offset.setY(self.offset.y() + (self.height() / 2.0 - css.y()))
        else:
            self.offset.setX(self.offset.x() + (self.width() / 2.0 - css.x()))
            self.offset.setY(self.offset.y() + (self.height() / 2.0 - css.y()))
        self._clamp_offset()
        self.update()
    def pan_by(self, dx: float, dy: float) -> None:
        self._restore_frac = None      # 用户自己滚过了：不要再被「恢复位置」拽回去
        self.offset.setX(self.offset.x() + dx)
        self.offset.setY(self.offset.y() + dy)
        self._clamp_offset()
        if dy:
            self._sync_current_page_from_view()
        if dx or dy:
            self._flash_scrollbars()
        self.update()
    def redo(self) -> None:
        if self.undo_mgr and self.undo_mgr.redo():
            self._repaint_after_edit(self.undo_mgr.last_changed)
            self._emit_modified()
            self._emit_undo_state()
    def refresh_tools(self) -> None:
        """（重新）从注册表同步工具实例；已存在的工具保留其样式状态。"""
        reg = get_registry()
        for tid in reg.ids():
            if tid not in self._tools:
                try:
                    self._tools[tid] = reg.create(tid)
                except Exception as e:
                    log_warning(_log, "tool_create_failed", tool=tid, error=e)
        for tid in list(self._tools):
            if not reg.contains(tid):
                self._tools.pop(tid, None)
        if self._current_tool_id not in self._tools:
            self._current_tool_id = "pen" if "pen" in self._tools else next(iter(self._tools), "")
        self.tool = self._tools.get(self._current_tool_id)
        self._apply_style_to_tools()
        self._update_cursor()
    def reset_zoom_policy(self) -> None:
        """恢复「自适应缩放」策略（供菜单「适应页面」调用）。"""
        self._user_zoomed = False
    def restore_scroll_frac(self, frac: float) -> None:
        """按比例恢复滚动位置（连续模式）。

        同时把比例留在 `_restore_frac` 里：装载文档时画布可能还没有真实尺寸
        （标签还没显示，尺寸是默认值），算出来的 offset 不可靠 —— 等第一次真正
        排版完成后再按同一比例补一次，恢复出来的位置才对得上。
        """
        if self.read_mode != "continuous" or self.mode != "pdf":
            return
        self._restore_frac = max(0.0, min(1.0, float(frac)))
        self._apply_restore_frac()
    def _apply_restore_frac(self) -> None:
        """把待恢复的滚动比例真正落到 offset 上（排版完成 / 显示后调用）。"""
        frac = self._restore_frac
        if frac is None:
            return
        if self.read_mode != "continuous" or self.mode != "pdf":
            self._restore_frac = None
            return
        self._ensure_layout()
        if not self._layout_tops:
            return                    # 布局还没建好：留着下次再补
        self._restore_frac = None
        content_h = self._layout_tops[-1] + self._layout_heights[-1]
        if content_h <= 0:
            return
        self.offset.setY(-float(frac) * content_h)
        self._clamp_offset()
        # 这里**不**重算“视口中线所在页”：恢复的页号是用户关掉时看到的那个，
        # 视口高度略有不同就会差一页；重新打开时页码应当还是原来那个数字，
        # 之后用户自己滚动时再由 `_sync_current_page_from_view()` 接管。
        self.update()
    def rotate_by(self, delta: int) -> None:
        """delta 为 ±90，顺时针为正。"""
        self.set_rotation((self.rotation + delta) % 360)
    def scroll_offset_frac(self) -> float:
        """当前滚动位置占全文的比例（0~1），用于恢复上次阅读位置。"""
        if self.read_mode != "continuous" or self.mode != "pdf":
            return 0.0
        self._ensure_layout()
        if not self._layout_tops:
            return 0.0
        content_h = self._layout_tops[-1] + self._layout_heights[-1]
        if content_h <= 0:
            return 0.0
        top_doc = -self.offset.y()
        return max(0.0, min(1.0, top_doc / content_h))
    def set_background(self, background: str, bg_color: Optional[str] = None) -> None:
        """切换笔记背景（类型 + 底色）后重绘。"""
        self.background = background
        if bg_color:
            self.bg_color = bg_color
        self.rebuild_pixmap()
        self.update()
    def set_canvas_bg(self, color: str) -> None:
        """设置画布底色，并顺手刷新滑动条配色（底色亮度决定滑块用深色还是浅色）。"""
        self.canvas_bg = color
        self._scroll_palette = scrollbar_palette(color)
        for bar in (getattr(self, "_vbar", None), getattr(self, "_hbar", None)):
            if bar is not None:
                bar.apply_palette(self._scroll_palette)
    def set_eraser_width(self, width: float) -> None:
        self.eraser_width = width
        self._apply_style_to_tools()
    def set_highlighter_color(self, color: str) -> None:
        self.highlighter_color = color
        self._apply_style_to_tools()
    def set_highlighter_width(self, width: float) -> None:
        self.highlighter_width = width
        self._apply_style_to_tools()
    def set_note_content(self, page_w: float, page_h: float, background: str,
                         strokes: List[Stroke], undo: UndoManager,
                         background_color: str = DEFAULT_BG_COLOR) -> None:
        self.mode = "note"
        self.page_w_pt = page_w
        self.page_h_pt = page_h
        self.background = background
        self.bg_color = background_color or DEFAULT_BG_COLOR
        self.rotation = 0
        self.search_rects = []
        self.search_index = -1
        self.pdf_engine = None
        self._bind_page(strokes, undo)
        self._user_zoomed = False
        self.fit_to_view()
        self.rebuild_pixmap()
        self._emit_page_info()
        self.update()
    def set_page_info(self, current: int, total: int) -> None:
        self.current_index = current
        self.total_pages = total
        self._sync_scrollbars()                  # 页数变了 → 连续模式的纵向长度也变了
        self._emit_page_info()
    def set_pdf_page(self, engine: PdfEngine, index: int, total: int,
                     strokes: List[Stroke], undo: UndoManager,
                     scroll: bool = True) -> None:
        # 文档切换 → 缓存与缩放策略一并失效（否则会显示上一个 PDF 的页面）
        doc_key = getattr(engine, "path", None) or str(id(engine))
        doc_changed = doc_key != self._doc_key
        if doc_changed:
            self._render_cache.clear()
            self._cancel_pending()
            self._doc_key = doc_key
            self._user_zoomed = False   # 新文档重新适应视口
            self._layout_scale = -1.0   # 连续模式布局重算
        self.mode = "pdf"
        self.pdf_engine = engine
        self.pdf_index = index
        self.total_pages = total
        self.current_index = index
        self.page_w_pt, self.page_h_pt = engine.page_size(index)
        self._bind_page(strokes, undo)
        self._sync_search_rects()
        if self.read_mode == "continuous":
            # 连续模式：滚动位置由用户控制；只有「主动跳页」才滚动过去
            self._ensure_layout()
            if scroll:
                self._scroll_to_page(index, align="top")
            self._sync_scrollbars()              # 换文档/换页后滚动总量可能变了
            self._emit_page_info()
            self.update()
            return
        if not self._user_zoomed:
            self.fit_to_view()
        self.rebuild_pixmap()
        self._emit_page_info()
        self.update()
    def set_pen_color(self, color: str) -> None:
        self.pen_color = color
        self._apply_style_to_tools()
    def set_pen_width(self, width: float) -> None:
        self.pen_width = width
        self._apply_style_to_tools()
    def set_read_mode(self, mode: str) -> None:
        """切换「单页」与「连续滚动」两种阅读模式。"""
        mode = "continuous" if mode == "continuous" else "single"
        if mode == self.read_mode:
            return
        keep = self.pdf_index
        self.read_mode = mode
        self._layout_scale = -1.0
        if self.mode == "pdf":
            if mode == "continuous":
                self._ensure_layout()
                self._scroll_to_page(keep, align="top")
            else:
                self.fit_to_view()
                self.rebuild_pixmap()
        self.update()
    def set_rotation(self, rotation: int) -> None:
        rotation = int(rotation) % 360
        if rotation % 90:
            rotation = 0
        if rotation == self.rotation:
            return
        self.rotation = rotation
        self._render_cache.clear()
        self._cancel_pending()
        self._layout_scale = -1.0
        if not self._user_zoomed:
            self.fit_to_view()
        else:
            self._clamp_offset()
        self.rebuild_pixmap()
        self.rotation_changed.emit(self.rotation)
        self._emit_page_info()
        self.update()
    def set_search_results(self, hits, current_index: int, page_index: Optional[int] = None):
        """设置搜索命中（hits 为 SearchHit 列表），仅高亮当前页的命中。"""
        target = page_index if page_index is not None else self.pdf_index
        rects: List[Tuple[float, float, float, float]] = []
        cur = -1
        for i, h in enumerate(hits or []):
            if getattr(h, "page", None) == target:
                if i == current_index:
                    cur = len(rects)
                rects.append(tuple(h.rect))
        self.search_rects = rects
        self.search_index = cur
        self.update()
    def set_tool(self, tool_id: str) -> None:
        if tool_id not in self._tools:
            log_warning(_log, "unknown_tool", tool=tool_id)
            return
        self._current_tool_id = tool_id
        self.tool = self._tools[tool_id]
        self._update_cursor()
        self.tool_changed.emit(tool_id)
        self.update()
    def undo(self) -> None:
        if self.undo_mgr and self.undo_mgr.undo():
            self._repaint_after_edit(self.undo_mgr.last_changed)
            self._emit_modified()
            self._emit_undo_state()
    def zoom_at(self, css_pos: QPointF, factor: float) -> None:
        """以 css_pos 为锚点缩放（用真实逆变换，旋转状态下也精确）。

        旧实现直接 `(css - offset) / scale` 求页面点，隐含「无旋转」假设，
        旋转 90° 后锚点会跑偏。这里改为：先用逆变换求出锚点对应的页面坐标，
        再用新变换把它放回同一个屏幕位置。
        """
        old = self.scale
        new = _clamp(old * factor, 0.05, 10.0)
        if new == old:
            return
        if self.read_mode == "continuous" and self.mode == "pdf":
            # 连续模式：页面在文档里的纵向位置会随缩放变化，必须重算布局后
            # 把锚点重新放回光标处，否则缩放会「跳页」。
            self._ensure_layout()
            idx = self._page_at_doc_y(css_pos.y() - self.offset.y())
            inv, ok = self._page_xform(True, idx).inverted()
            anchor = inv.map(css_pos) if ok else QPointF(0.0, 0.0)
            self.scale = new
            self._layout_scale = -1.0
            self._ensure_layout()
            local = self._page_xform(False, idx).map(anchor)
            self.offset.setX(css_pos.x() - local.x())
            self.offset.setY(css_pos.y() - local.y() - self._layout_tops[idx])
            self._user_zoomed = True
            self._clamp_offset()
            self.zoom_changed.emit(new)
            self._emit_page_info()
            self.update()
            return
        inv, ok = self._page_xform(True).inverted()
        anchor = inv.map(css_pos) if ok else QPointF(0.0, 0.0)
        self.scale = new
        q = self._page_xform(False).map(anchor)
        self.offset.setX(css_pos.x() - q.x())
        self.offset.setY(css_pos.y() - q.y())
        self._user_zoomed = True
        self._clamp_offset()
        self.rebuild_pixmap()
        self.zoom_changed.emit(new)
        self._emit_page_info()
        self.update()
    def zoom_to(self, factor: float) -> None:
        self.zoom_at(QPointF(self.width() / 2.0, self.height() / 2.0), factor)

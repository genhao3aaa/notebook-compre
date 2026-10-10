"""画布坐标与布局（A7 拆分自 canvas.py）：页面坐标 ⇄ 显示坐标、连续阅读纵向布局。

关键约束（历史踩坑记录）：
- `QTransform` 组合语义是「后添加的先作用于点」，代码按「外层在前」的顺序写；
- 旋转 90/270 时页面宽高互换，`display_size_pt()` 统一处理；
- 连续模式的布局缓存随 scale / rotation / 页数失效；滚动每帧都要用，不能每次重算。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QTabletEvent, QTransform)
from src.core.hn_log import get_logger, log_debug, log_warning
from src.ui.page_scrollbar import BAR_THICKNESS

#: 连续阅读时内容与视口边缘的留白（CSS px）。与滑动条共用：有它才有「页面浮在背景上」的呼吸感
SCROLLBAR_GAP = 12.0

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



class CanvasLayoutMixin:
    #: 滑动条是否占位（`_clamp_bounds()` 里按「内容装不装得下」刷新）
    _vbar_shown: bool = False
    _hbar_shown: bool = False
    #: 上一次摆好的两条滑动条几何（相同就不重复 setGeometry，见 _place_scrollbars）
    _bar_rects: Optional[Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int]]] = None
    def _page_size_pt(self, index: Optional[int] = None) -> Tuple[float, float]:
        """某一页的原始尺寸（points）。连续模式下各页尺寸可能不同。"""
        if (self.mode == "pdf" and self.pdf_engine is not None
                and self.read_mode == "continuous" and index is not None):
            return self.pdf_engine.page_size(index)
        return self.page_w_pt, self.page_h_pt
    def display_size_pt(self, index: Optional[int] = None) -> Tuple[float, float]:
        """当前显示方向下的页面尺寸（points）：旋转 90/270 时宽高互换。"""
        w, h = self._page_size_pt(index)
        if self.rotation % 180 == 0:
            return w, h
        return h, w
    def _content_width_css(self) -> float:
        """连续模式下所有页面里最宽的一页（CSS px）。

        结果随布局一起缓存：滚动/夹紧每帧都会调用它，不能每次都遍历全部页面。
        """
        if self.mode != "pdf" or self.pdf_engine is None:
            dw, _ = self.display_size_pt()
            return dw * self.scale
        self._ensure_layout()
        if self._layout_width > 0:
            return self._layout_width
        dw, _ = self.display_size_pt()
        return dw * self.scale
    def _ensure_layout(self) -> None:
        """（重新）计算连续模式的纵向布局；缩放/旋转/页数变化时自动失效。"""
        if self.mode != "pdf" or self.pdf_engine is None or self.read_mode != "continuous":
            self._layout_tops = []
            self._layout_heights = []
            return
        if (self._layout_scale == self.scale and self._layout_rot == self.rotation
                and self._layout_total == self.total_pages):
            return
        tops: List[float] = []
        heights: List[float] = []
        y = 0.0
        widest = 0.0
        for i in range(max(0, self.total_pages)):
            dw, dh = self.display_size_pt(i)
            h = dh * self.scale
            tops.append(y)
            heights.append(h)
            y += h + self.page_gap
            widest = max(widest, dw * self.scale)
        self._layout_tops = tops
        self._layout_heights = heights
        self._layout_width = widest
        self._layout_scale = self.scale
        self._layout_rot = self.rotation
        self._layout_total = self.total_pages
    def _page_origin_css(self, index: Optional[int] = None) -> QPointF:
        """某页左上角在控件里的位置（CSS px）。"""
        if (self.read_mode == "continuous" and self.mode == "pdf"
                and index is not None):
            self._ensure_layout()
            if 0 <= index < len(self._layout_tops):
                return QPointF(self.offset.x(),
                               self.offset.y() + self._layout_tops[index])
        return QPointF(self.offset.x(), self.offset.y())
    def _page_at_doc_y(self, y: float) -> int:
        """文档纵坐标 y 落在第几页（连续模式）。"""
        self._ensure_layout()
        n = len(self._layout_tops)
        if n == 0:
            return 0
        if y < 0:
            return 0
        lo, hi = 0, n - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._layout_tops[mid] <= y:
                lo = mid
            else:
                hi = mid - 1
        return lo
    def _page_at_css(self, css: QPointF) -> Optional[int]:
        """控件坐标落在第几页；不在任何页面上时返回 None（连续模式）。"""
        if not (self.read_mode == "continuous" and self.mode == "pdf"):
            return self.pdf_index if self._inside_page(css) else None
        self._ensure_layout()
        if not self._layout_tops:
            return None
        doc_y = css.y() - self.offset.y()
        idx = self._page_at_doc_y(doc_y)
        top = self._layout_tops[idx]
        bottom = top + self._layout_heights[idx]
        if not (top <= doc_y <= bottom):
            return None
        dw, _ = self.display_size_pt(idx)
        if not (self.offset.x() <= css.x() <= self.offset.x() + dw * self.scale):
            return None
        return idx
    def _visible_page_range(self) -> Tuple[int, int]:
        """当前视口覆盖的页范围 [lo, hi]（连续模式，闭区间）。"""
        self._ensure_layout()
        if not self._layout_tops:
            return self.pdf_index, self.pdf_index
        y0 = -self.offset.y()
        y1 = y0 + self.height()
        return (self._page_at_doc_y(y0), self._page_at_doc_y(y1))
    def _page_xform(self, include_offset: bool = True,
                    index: Optional[int] = None) -> QTransform:
        """页面坐标 → 显示坐标 的变换（旋转 → 缩放 → 归位 → 平移）。

        注意 QTransform 的组合语义：**后添加的变换先作用于点**。因此代码按
        「外层在前」的顺序书写：offset → 归位平移 → scale → rotate。
        归位平移发生在缩放之后的坐标系里，所以要乘回 self.scale。
        """
        idx = self.pdf_index if index is None else index
        pw, ph = self._page_size_pt(idx)
        t = QTransform()
        if include_offset:
            org = self._page_origin_css(idx)
            t.translate(org.x(), org.y())
        if self.rotation:
            r = QTransform().rotate(self.rotation).mapRect(QRectF(0, 0, pw, ph))
            # 旋转后包围盒左上角不在原点 → 平移回 (0,0)（CSS 像素单位）
            t.translate(-r.x() * self.scale, -r.y() * self.scale)
        t.scale(self.scale, self.scale)
        if self.rotation:
            t.rotate(self.rotation)
        return t
    def page_to_css(self, x: float, y: float) -> QPointF:
        return self._page_xform(True).map(QPointF(x, y))
    def _to_page_point(self, css: QPointF, pressure: float) -> Tuple[float, float, float]:
        inv, ok = self._page_xform(True).inverted()
        if not ok:
            return (0.0, 0.0, pressure)
        pt = inv.map(css)
        return (pt.x(), pt.y(), pressure)
    def _inside_page(self, css: QPointF) -> bool:
        if self.read_mode == "continuous" and self.mode == "pdf":
            return self._page_at_css(css) is not None
        dw, dh = self.display_size_pt()
        css_w, css_h = dw * self.scale, dh * self.scale
        return (self.offset.x() <= css.x() <= self.offset.x() + css_w and
                self.offset.y() <= css.y() <= self.offset.y() + css_h)

    # ================================================================== #
    # 网页式滑动条：视口 ⇄ 内容尺寸、夹紧边界、比例定位
    # ================================================================== #
    def _content_size_css(self) -> Tuple[float, float]:
        """内容整体尺寸（CSS px）：连续阅读是所有页面的纵向总高。"""
        if self.read_mode == "continuous" and self.mode == "pdf":
            self._ensure_layout()
            if not self._layout_tops:
                return 0.0, 0.0
            height = self._layout_tops[-1] + self._layout_heights[-1]
            width = (self._layout_width if self._layout_width > 0
                     else self.display_size_pt()[0] * self.scale)
            return width, height
        dw, dh = self.display_size_pt()
        return dw * self.scale, dh * self.scale
    def _overflow_axes(self) -> Tuple[bool, bool]:
        """(纵向, 横向) 内容是否装不下 —— 决定滑动条显不显示（网页行为）。

        两条互相占位：横条出现会吃掉 `BAR_THICKNESS` 的高度，极端尺寸下「刚好装下」
        的纵轴可能因此变成装不下，所以判定跑第二遍。

        装得下就不显示：内容居中、旁边杵着一根不能动的条，比没有更让人困惑。
        """
        cw, ch = self._content_size_css()
        w, h = self.width(), self.height()
        gap = (SCROLLBAR_GAP * 2.0
               if (self.read_mode == "continuous" and self.mode == "pdf") else 0.0)
        over_v = ch + gap > h + 0.5
        over_h = cw + gap > w + 0.5
        if not over_v and over_h and ch + gap > h - BAR_THICKNESS + 0.5:
            over_v = True
        if not over_h and over_v and cw + gap > w - BAR_THICKNESS + 0.5:
            over_h = True
        return over_v, over_h
    def _viewport_size(self) -> Tuple[float, float]:
        """扣掉滑动条占位后的可用视口 —— 内容按它排布/夹紧，不会被条压住。

        贴边的批注点不到是文档类应用的老毛病：条一旦显示，它占的那一条就必须
        从内容区里让出来，而不是盖在纸面上。
        """
        return (max(1.0, self.width() - (BAR_THICKNESS if self._hbar_shown else 0.0)),
                max(1.0, self.height() - (BAR_THICKNESS if self._vbar_shown else 0.0)))
    def _clamp_bounds(self) -> Tuple[float, float, float, float]:
        """夹紧边界 (min_x, max_x, min_y, max_y)；`min > max` 表示该轴装得下、应居中。

        全程序只有这一处写边界：`_clamp_offset()` 与滑动条都读它。两边各算一套的话，
        就会出现「滑块拖到底、页面却还在往前滑」这种对不上的手感。
        """
        self._vbar_shown, self._hbar_shown = self._overflow_axes()
        cw, ch = self._content_size_css()
        vw, vh = self._viewport_size()
        if self.read_mode == "continuous" and self.mode == "pdf":
            return (vw - cw - SCROLLBAR_GAP, SCROLLBAR_GAP,
                    vh - ch - SCROLLBAR_GAP, SCROLLBAR_GAP)
        # 单页模式沿用 v4 的平移手感：至少留 35% 页面可见（见 canvas._clamp_offset 注释）
        keep_x = min(cw, vw) * 0.35
        keep_y = min(ch, vh) * 0.35
        return keep_x - cw, vw - keep_x, keep_y - ch, vh - keep_y
    def _place_scrollbars(self) -> None:
        """把两条滑动条贴在视口右侧 / 下侧；右下角让给竖条，两条不重叠。

        几何没变就不调用 setGeometry：连续阅读时每个滚轮 / 拖动事件都会走到这里，
        重复设置几何会白白触发子控件的 layout / paint 调度。
        """
        vbar = getattr(self, "_vbar", None)
        hbar = getattr(self, "_hbar", None)
        if vbar is None or hbar is None:
            return
        w, h = self.width(), self.height()
        v_h = max(0.0, h - (BAR_THICKNESS if self._hbar_shown else 0.0))
        h_w = max(0.0, w - (BAR_THICKNESS if self._vbar_shown else 0.0))
        rects = ((int(round(w - BAR_THICKNESS)), 0,
                  int(round(BAR_THICKNESS)), int(round(v_h))),
                 (0, int(round(h - BAR_THICKNESS)),
                  int(round(h_w)), int(round(BAR_THICKNESS))))
        if rects == self._bar_rects:
            return
        self._bar_rects = rects
        vbar.setGeometry(*rects[0])
        hbar.setGeometry(*rects[1])
    def _sync_scrollbars(self) -> None:
        """把几何与位置推给两条滑动条。

        只在「状态可能变了」的地方调用（夹紧、缩放、翻页、resize、换文档），而不是
        每帧：每帧重设几何会让正在拖动的滑块被自己拽回去。首次绘制前调用是安全的，
        滑动条自己会按「装不装得下」决定显隐。
        """
        vbar = getattr(self, "_vbar", None)
        hbar = getattr(self, "_hbar", None)
        if vbar is None or hbar is None:
            return
        min_x, max_x, min_y, max_y = self._clamp_bounds()
        vw, vh = self._viewport_size()
        self._place_scrollbars()
        span_y = (max_y - min_y) if (self._vbar_shown and max_y > min_y) else 0.0
        span_x = (max_x - min_x) if (self._hbar_shown and max_x > min_x) else 0.0
        vbar.set_range(span_y, vh)
        hbar.set_range(span_x, vw)
        if span_y > 0.0:
            vbar.set_value((max_y - self.offset.y()) / span_y)
        if span_x > 0.0:
            hbar.set_value((max_x - self.offset.x()) / span_x)
    def scrollbar_state(self) -> Tuple[bool, bool, float, float]:
        """(竖条该显示, 横条该显示, 竖条比例, 横条比例)：供状态提示 / 测试 / 排查用。

        报的是「逻辑可见」（内容装不装得下），而不是 `isVisible()`：窗口还没显示时
        子控件一律 `isVisible() == False`，拿它当状态会让离屏测试莫名其妙地红。
        """
        vbar = getattr(self, "_vbar", None)
        hbar = getattr(self, "_hbar", None)
        return (bool(vbar is not None and vbar.is_scrollable()),
                bool(hbar is not None and hbar.is_scrollable()),
                float(vbar.value()) if vbar is not None else 0.0,
                float(hbar.value()) if hbar is not None else 0.0)
    def set_scroll_fraction(self, vertical: bool, frac: float) -> None:
        """按比例设置滚动位置：拖动滑动条走这里（外部想直接定位也用这个入口）。

        比例 0 = 内容顶部（左侧），1 = 底部（右侧），与 `_clamp_bounds()` 的两端一一对应 ——
        所以滑块的位置永远等于「页面现在在哪」，不会和手势平移脱节。
        """
        min_x, max_x, min_y, max_y = self._clamp_bounds()
        frac = _clamp(float(frac), 0.0, 1.0)
        moved = False
        if vertical and max_y > min_y:
            self.offset.setY(max_y - frac * (max_y - min_y))
            moved = True
        elif (not vertical) and max_x > min_x:
            self.offset.setX(max_x - frac * (max_x - min_x))
            moved = True
        self._clamp_offset()
        if vertical:
            self._sync_current_page_from_view()
        if moved:
            self._flash_scrollbars()
        self.update()
    def _flash_scrollbars(self) -> None:
        """滚动发生时让滑动条短暂提亮（网页「滚动时才明显」的手感）。"""
        for bar in (getattr(self, "_vbar", None), getattr(self, "_hbar", None)):
            if bar is not None and bar.isVisible():
                bar.flash()

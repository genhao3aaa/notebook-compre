"""画布渲染层（A7 拆分自 canvas.py）：整页重建、脏矩形增量重绘、PDF 异步渲染。

单独一个模块的理由：这里是「改一行就影响画面与性能」的地方——A1（压感渲染）、
A2（脏矩形）、A4（PDF 异步缓存）全落在这里，和输入路由、视图状态混在一起时
很难单独 review。渲染层只读画布状态，不处理输入事件。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QTabletEvent, QTransform)
from src.core.colors import DEFAULT_BG_COLOR, grid_color_for, line_color_for
from src.core.hn_log import get_logger, log_debug, log_warning
from src.core.stroke import (HIGHLIGHTER_COLOR, HIGHLIGHTER_WIDTH, ClearCmd,
                             Stroke, UndoManager)
from src.render.pdf_engine import PdfEngine, PdfRenderWorker
from src.render.stroke_painter import paint_stroke, paint_strokes

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



class CanvasRenderMixin:
    def rebuild_pixmap(self) -> None:
        """整页重建（旧名字保留：主窗口与测试都在调用它）。"""
        self.rebuild_all()
    def rebuild_all(self) -> None:
        if self.mode == "pdf" and self.read_mode == "continuous":
            return          # 连续模式按页直接绘制，不需要整页离屏缓存
        dpr = self.devicePixelRatio() or 1.0
        dw, dh = self.display_size_pt()
        css_w, css_h = dw * self.scale, dh * self.scale
        if css_w <= 0 or css_h <= 0:
            return
        px = QPixmap(int(css_w * dpr), int(css_h * dpr))
        px.setDevicePixelRatio(dpr)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        if self.mode == "note":
            p.fillRect(QRectF(0, 0, css_w, css_h), QColor(self.bg_color))
            self._draw_background(p)
        else:
            img = self._get_pdf_image(self.pdf_index)
            if img is not None:
                p.drawImage(QRectF(0, 0, css_w, css_h), img)
            else:
                p.fillRect(QRectF(0, 0, css_w, css_h), QColor("#ffffff"))
                self._request_pdf_render(self.pdf_index, dpr)

        xf = self._page_xform(False)
        p.save()
        p.setTransform(xf, False)
        paint_strokes(p, self.page_strokes, 1.0)
        # 手势进行中的笔画也要画出来（例如中途触发了重绘）
        if self.tool is not None and self.tool.paints_live and self.tool.current_stroke:
            paint_stroke(p, self.tool.current_stroke, 1.0)
        p.restore()
        p.end()
        self.page_pixmap = px
    def _dirty_page_rect(self, strokes) -> QRectF:
        """若干笔画在**页面坐标**下的脏矩形（含线宽、圆头与抗锯齿余量）。"""
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")
        for s in strokes:
            if not s.points:
                continue
            sx0, sy0, sx1, sy1 = s.bounding_rect()
            pad = max(1.0, s.width / 2.0 + 2.0)
            x0 = min(x0, sx0 - pad)
            y0 = min(y0, sy0 - pad)
            x1 = max(x1, sx1 + pad)
            y1 = max(y1, sy1 + pad)
        if x0 > x1 or y0 > y1:
            return QRectF()
        return QRectF(x0, y0, x1 - x0, y1 - y0)
    @staticmethod
    def _stroke_touches(s: Stroke, rect: QRectF) -> bool:
        if not s.points:
            return False
        sx0, sy0, sx1, sy1 = s.bounding_rect()
        pad = max(1.0, s.width / 2.0 + 2.0)
        return not (sx1 + pad < rect.left() or sx0 - pad > rect.right()
                    or sy1 + pad < rect.top() or sy0 - pad > rect.bottom())
    def repaint_region(self, rect_pt: QRectF) -> None:
        """只重绘页面坐标 `rect_pt` 范围内的内容（A2 脏矩形增量重绘）。

        - 笔记 / 单页 PDF：在 `page_pixmap` 上局部重画「背景 + 与该区域相交的笔画」，
          再把控件上对应的一小块 `update()`，避免整页重建。
        - 连续阅读：该模式本就不建整页 pixmap（按可见页直接绘制），退化为 `update()`。
        """
        if self.mode == "pdf" and self.read_mode == "continuous":
            self.update()
            return
        if rect_pt.isNull() or rect_pt.isEmpty():
            return
        if self.page_pixmap.isNull():
            self.rebuild_all()
            self.update()
            return

        dw, dh = self.display_size_pt()
        css_w, css_h = dw * self.scale, dh * self.scale
        xf = self._page_xform(False)
        disp = xf.mapRect(rect_pt).intersected(QRectF(0, 0, css_w, css_h))
        if disp.isEmpty():
            return
        # 外扩 1.5 px：抗锯齿边缘会落在几何边界之外，不外扩会留下旧像素
        disp = disp.adjusted(-1.5, -1.5, 1.5, 1.5)
        dpr = self.devicePixelRatio() or 1.0

        # 统一在「页面坐标系」下绘制并裁剪：背景与笔画用同一个变换、同一个 clip。
        # （先按显示坐标裁一次、再换变换裁第二次，会让第二次裁剪把笔画整片裁掉——
        # 结果就是「背景刷好了、笔画没画上」。）
        p = QPainter(self.page_pixmap)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.save()
        p.setTransform(xf, False)
        p.setClipRect(rect_pt)

        if self.mode == "note":
            p.fillRect(QRectF(0, 0, self.page_w_pt, self.page_h_pt), QColor(self.bg_color))
            self._draw_background(p)
        else:
            img = self._get_pdf_image(self.pdf_index)
            if img is not None:
                p.drawImage(QRectF(0, 0, self.page_w_pt, self.page_h_pt), img)
            else:
                p.fillRect(QRectF(0, 0, self.page_w_pt, self.page_h_pt), QColor("#ffffff"))
                self._request_pdf_render(self.pdf_index, dpr)

        hits = [s for s in self.page_strokes if self._stroke_touches(s, rect_pt)]
        paint_strokes(p, hits, 1.0)
        if self.tool is not None and self.tool.paints_live and self.tool.current_stroke:
            paint_stroke(p, self.tool.current_stroke, 1.0)
        p.restore()
        p.end()

        self.update(disp.translated(self.offset.x(), self.offset.y())
                    .toAlignedRect().adjusted(-2, -2, 2, 2))
    def repaint_strokes(self, strokes) -> None:
        """按受影响笔画的包围盒做增量重绘；范围不明时退回整页重建。"""
        rect = self._dirty_page_rect(strokes)
        if rect.isNull():
            self.rebuild_all()
            self.update()
            return
        self.repaint_region(rect)
    def _repaint_after_edit(self, changed) -> None:
        """编辑类操作的统一重绘入口：拿到受影响笔画就局部重绘，否则整页重建。"""
        if not changed:
            self.rebuild_all()
            self.update()
            return
        self.repaint_strokes(changed)
    def _draw_background(self, p: QPainter) -> None:
        """网格 / 横线 / 点阵：在页面坐标系下绘制，随旋转一起转，线色随底色反转。"""
        if self.background == BG_BLANK:
            return
        w, h = self.page_w_pt, self.page_h_pt
        px = max(0.4, 1.0 / self.scale)          # 恒为 1 CSS px 的线宽
        dot = max(0.6, 1.4 / self.scale)
        p.save()
        p.setTransform(self._page_xform(False), False)
        if self.background == BG_GRID:
            step = 25.0
            p.setPen(QPen(QColor(grid_color_for(self.bg_color)), px))
            x = step
            while x < w:
                p.drawLine(QPointF(x, 0), QPointF(x, h))
                x += step
            y = step
            while y < h:
                p.drawLine(QPointF(0, y), QPointF(w, y))
                y += step
        elif self.background == BG_LINES:
            step = 35.0
            p.setPen(QPen(QColor(line_color_for(self.bg_color)), px))
            y = step
            while y < h:
                p.drawLine(QPointF(0, y), QPointF(w, y))
                y += step
        elif self.background == BG_DOTS:
            step = 25.0
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(line_color_for(self.bg_color)))
            y = step
            while y < h:
                x = step
                while x < w:
                    p.drawEllipse(QPointF(x, y), dot, dot)
                    x += step
                y += step
            p.setBrush(Qt.NoBrush)
        p.restore()
    def _draw_live_ink(self, stroke: Stroke) -> None:
        if self.mode == "pdf" and self.read_mode == "continuous":
            self.update()   # 连续模式每帧从缓存重绘，刷新即可
            return
        pts = stroke.points
        n = len(pts)
        if n == 0 or self.page_pixmap.isNull():
            return
        p = QPainter(self.page_pixmap)
        p.setTransform(self._page_xform(False), False)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(Qt.NoBrush)
        color = QColor(stroke.color)

        if n == 1:
            w = max(0.5, stroke.width * pts[0][2])
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(pts[0][0], pts[0][1]), w / 2.0, w / 2.0)
            p.end()
            return

        if n == 2:
            a, b = pts[0], pts[1]
            w = max(0.5, stroke.width * (a[2] + b[2]) / 2.0)
            p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(QPointF(a[0], a[1]), QPointF(b[0], b[1]))
            p.end()
            return

        i = n - 1
        p_prev2, p_prev1, p_new = pts[i - 2], pts[i - 1], pts[i]
        if i == 2:
            sx, sy = pts[0][0], pts[0][1]
        else:
            sx = (p_prev2[0] + p_prev1[0]) / 2.0
            sy = (p_prev2[1] + p_prev1[1]) / 2.0
        ex = (p_prev1[0] + p_new[0]) / 2.0
        ey = (p_prev1[1] + p_new[1]) / 2.0
        w = max(0.5, stroke.width * (p_prev1[2] + p_new[2]) / 2.0)

        path = QPainterPath()
        path.moveTo(sx, sy)
        path.quadTo(p_prev1[0], p_prev1[1], ex, ey)
        p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        p.end()
    def _cache_key(self, index: int) -> tuple:
        """缓存键 = (文档标识, 页序号, 缩放, 旋转)，避免不同文档/方向之间串味。"""
        return (self._doc_key, index, round(self.scale, 3), self.rotation)
    def _get_pdf_image(self, index: int) -> Optional[QImage]:
        key = self._cache_key(index)
        img = self._render_cache.get(key)
        if img is not None:
            self._render_cache.move_to_end(key)  # LRU
        return img
    def _cancel_pending(self) -> None:
        """作废排队中的渲染请求与在途结果（切文档 / 旋转 / 关闭时）。"""
        self._pending_reqs.clear()
        self._requested.clear()
        self._render_token += 1
    def _request_pdf_render(self, index: int, dpr: float,
                            prefetch: bool = False) -> None:
        """请求渲染某页。

        prefetch=True 时排到队尾（后台预取），False 时插到队首（用户正等着的页优先）。
        """
        if self.pdf_engine is None:
            return
        if index < 0 or index >= max(1, self.total_pages):
            return
        if self._cache_key(index) in self._render_cache:
            return
        req = (index, round(self.scale * dpr, 3), self.rotation)
        if req in self._requested:
            return
        self._requested.add(req)
        item = (index, req[1], req[2], self._render_token, dpr)
        if prefetch:
            self._pending_reqs.append(item)
        else:
            self._pending_reqs.appendleft(item)
        self._start_render_thread()
    def _start_render_thread(self) -> None:
        if self._render_thread is not None and self._render_thread.isRunning():
            return
        if not self._pending_reqs:
            return
        index, z, rot, token, dpr = self._pending_reqs.popleft()
        self._requested.discard((index, z, rot))
        worker = PdfRenderWorker(self.pdf_engine, index, z, rot)
        worker.result.connect(
            lambda i, zz, img, t=token, d=dpr: self._on_pdf_rendered(t, i, zz, img, d))
        self._render_thread = worker
        worker.start()
    def _on_pdf_rendered(self, token: int, index: int, zoom: float, img: QImage,
                         dpr: float = 1.0) -> None:
        if token == self._render_token and img is not None and not img.isNull():
            key = self._cache_key(index)
            self._render_cache[key] = img
            self._render_cache.move_to_end(key)
            while len(self._render_cache) > 24:
                self._render_cache.popitem(last=False)
            if self.mode == "pdf":
                if self.read_mode == "single":
                    if index == self.pdf_index:
                        self.rebuild_pixmap()
                        # 预取下一页：翻页时几乎瞬时（只预取一层，不会连锁渲染整本）
                        self._request_pdf_render(index + 1, dpr, prefetch=True)
                else:
                    lo, hi = self._visible_page_range()
                    if index <= hi:
                        self._request_pdf_render(hi + 1, dpr, prefetch=True)
                self.update()
        self._start_render_thread()
    def _strokes_for(self, index: int) -> List[Stroke]:
        """某一页的批注笔画（连续模式要同时绘制多页）。"""
        if index == self.pdf_index:
            return self.page_strokes
        if self.stroke_provider is not None:
            try:
                return self.stroke_provider(index) or []
            except Exception:
                return []
        return []
    def _sync_current_page_from_view(self) -> None:
        """连续模式：视口中线所在的页成为「当前页」（批注绑定 / 页码 / 目录高亮随之更新）。"""
        if self.read_mode != "continuous" or self.mode != "pdf":
            return
        self._ensure_layout()
        if not self._layout_tops:
            return
        center_doc = -self.offset.y() + self.height() / 2.0
        idx = self._page_at_doc_y(center_doc)
        if idx == self.pdf_index:
            return
        if self.on_page_request is not None:
            self.on_page_request(idx)
        else:
            self.pdf_index = idx
            self.current_index = idx
            self._emit_page_info()
    def wait_for_render(self) -> None:
        if self._render_thread is not None and self._render_thread.isRunning():
            self._render_thread.wait()
    def detach_pdf(self) -> None:
        """断开与当前 PDF 的关联（切换/关闭文档前调用）。

        必须先等在途渲染线程结束，否则线程会访问已关闭的 fitz 文档而崩溃；
        再作废在途结果与缓存，避免旧文档图像残留。
        """
        self.wait_for_render()
        self._cancel_pending()
        self._render_cache.clear()
        self._doc_key = None
        self.pdf_engine = None
        self._layout_tops = []
        self._layout_heights = []
        self._layout_scale = -1.0
        self._layout_total = -1
        self._restore_frac = None
        self.stroke_provider = None
        self._sync_scrollbars()      # 文档断开后没有内容可滚，两条都该收起来
    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(self.canvas_bg))

        # 滑动条占的那一条不属于视口：内容裁到视口为止，不从沟槽底下露出来。
        # （轨道本身是半透明的，不裁的话单页模式平移到头时会露出纸的边。）
        vw, vh = self._viewport_size()
        if vw < self.width() or vh < self.height():
            p.setClipRect(QRectF(0.0, 0.0, vw, vh))

        if self.mode == "pdf" and self.read_mode == "continuous":
            self._paint_continuous(p)
            p.end()
            return

        dw, dh = self.display_size_pt()
        css_w, css_h = dw * self.scale, dh * self.scale
        x, y = self.offset.x(), self.offset.y()

        # 纸张投影：让页面看起来「浮」在背景上
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 45))
        p.drawRoundedRect(QRectF(x + 3, y + 4, css_w, css_h), 4, 4)
        p.setBrush(Qt.NoBrush)

        if not self.page_pixmap.isNull():
            p.drawPixmap(x, y, css_w, css_h, self.page_pixmap)

        # 搜索命中高亮（页面坐标 → 显示坐标）
        if self.search_rects:
            p.save()
            p.setTransform(self._page_xform(True), False)
            for i, r in enumerate(self.search_rects):
                if i == self.search_index:
                    p.fillRect(QRectF(*r), QColor(255, 138, 0, 150))
                else:
                    p.fillRect(QRectF(*r), QColor(255, 213, 79, 120))
            p.restore()

        p.setPen(QPen(QColor(0, 0, 0, 40), 1.0))
        p.drawRect(QRectF(x, y, css_w, css_h))

        # 工具光标提示（例如橡皮圈）
        if (self.tool is not None and self._hover and not self.drawing
                and self._inside_page(self._hover_css)):
            self.tool.paint_overlay(p, self.ctx, self._hover_css, self.scale)
        p.end()
    def _paint_continuous(self, p: QPainter) -> None:
        """连续阅读模式：只绘制视口覆盖到的页面（图像 + 该页批注 + 投影边框）。"""
        self._ensure_layout()
        if not self._layout_tops or self.pdf_engine is None:
            return
        dpr = self.devicePixelRatio() or 1.0
        lo, hi = self._visible_page_range()
        vh = self.height()
        for i in range(lo, hi + 1):
            if not (0 <= i < len(self._layout_tops)):
                continue
            top = self.offset.y() + self._layout_tops[i]
            dw, dh = self.display_size_pt(i)
            css_w, css_h = dw * self.scale, dh * self.scale
            x = self.offset.x()
            if top > vh or top + css_h < 0:
                continue

            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 45))
            p.drawRoundedRect(QRectF(x + 3, top + 4, css_w, css_h), 4, 4)
            p.setBrush(Qt.NoBrush)

            img = self._get_pdf_image(i)
            if img is not None:
                p.drawImage(QRectF(x, top, css_w, css_h), img)
            else:
                p.fillRect(QRectF(x, top, css_w, css_h), QColor("#ffffff"))
                self._request_pdf_render(i, dpr)

            strokes = self._strokes_for(i)
            live = (i == self.pdf_index and self.tool is not None
                    and self.tool.paints_live and self.tool.current_stroke is not None)
            if strokes or live or (i == self.pdf_index and self.search_rects):
                p.save()
                p.setTransform(self._page_xform(True, i), False)
                if strokes:
                    paint_strokes(p, strokes, 1.0)
                if live:
                    paint_stroke(p, self.tool.current_stroke, 1.0)
                if i == self.pdf_index and self.search_rects:
                    p.setPen(Qt.NoPen)
                    for k, r in enumerate(self.search_rects):
                        if k == self.search_index:
                            p.fillRect(QRectF(*r), QColor(255, 138, 0, 150))
                        else:
                            p.fillRect(QRectF(*r), QColor(255, 213, 79, 120))
                p.restore()

            p.setPen(QPen(QColor(0, 0, 0, 40), 1.0))
            p.drawRect(QRectF(x, top, css_w, css_h))

        # 工具光标提示（橡皮圈等）画在指针所在的那一页
        if self.tool is not None and self._hover and not self.drawing:
            if self._page_at_css(self._hover_css) is not None:
                self.tool.paint_overlay(p, self.ctx, self._hover_css, self.scale)
    def resizeEvent(self, e) -> None:
        if not self._user_zoomed:
            self.fit_to_view()
            self.rebuild_pixmap()
        else:
            self._clamp_offset()
        self._sync_scrollbars()      # fit/夹紧都可能提前返回，这里再兜一次（尺寸变了条也要跟着走）
        super().resizeEvent(e)
    def showEvent(self, e) -> None:
        """首次显示时再同步一次：隐藏期间的尺寸变化 Qt 不一定会发 resizeEvent。"""
        self._sync_scrollbars()
        super().showEvent(e)

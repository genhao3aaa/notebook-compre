"""画布右侧 / 下侧的网页式滑动条（自绘，不继承 QAbstractScrollArea）。

为什么自己画
------------
* `PageCanvas` 是纯 `QWidget`：连续阅读的滚动是自研的 offset 模型，没有现成的
  `QScrollArea` / `QGraphicsView` 可以挂；
* 系统 `QScrollBar` 的轨道、箭头与尺寸随 Windows 主题变化很大，做不出「细、圆、
  半透明、待机淡出」的观感，摆在这个界面里不和谐；
* 「内容装得下就自动隐藏」是网页行为，用 QScrollBar 反而要再包一层策略。

职责边界
--------
本模块只做「几何 + 鼠标交互 + 画自己」，不知道页面、缩放、PDF 的存在：

* 输入：`set_range(span, viewport)`（可滚距离 / 视口尺寸，单位 CSS px）、`set_value(0..1)`；
* 输出：`value_changed(frac)`（用户拖出来的新位置）、`wheel_scrolled(delta)`（滚轮原样转发）。

把比例翻译成 offset 是画布的事，见 `canvas_layout.CanvasLayoutMixin` 的
`_clamp_bounds()` / `set_scroll_fraction()` —— 两者共用同一套边界，滑块拖到底
就一定是夹紧的极限，不会出现「拖到底了页面还在往前滑」。
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import (QEasingCurve, Property, QPointF, QPropertyAnimation,
                            QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from src.ui.theme import PRIMARY

#: 滑动条厚度（CSS px）：比系统滚动条窄，接近网页的悬浮细条
BAR_THICKNESS = 13.0
#: 轨道内缩（CSS px）：两侧留白，滑块才显得细
TRACK_INSET = 3.0
#: 滑块最小长度（CSS px）：内容再长也要抓得住
MIN_THUMB = 30.0
#: 待机 / 激活时滑块的不透明度（整条一直可见，但滚动时更醒目）
#: 实测口径：待机时滑块在纸面上的实际不透明度 ≈ 0.28（看得见但不抢戏），
#: 悬停 0.46、拖动用主题绿 —— 和 Windows 11 / Chrome 的悬浮条接近。
IDLE_OPACITY = 0.85
ACTIVE_OPACITY = 1.0
#: 滚动一次后保持高亮的时间（毫秒）与淡入淡出时长
FLASH_MS = 900
FADE_MS = 160


def scrollbar_palette(bg: str) -> Dict[str, QColor]:
    """按画布底色给出一套滑动条配色。

    用底色的**亮度**而不是主题名决定明暗：底色是可以被用户改的（笔记背景色、
    深浅主题各一套），亮度才是「这条滑块压在什么上面」的可靠信息。
    """
    color = QColor(bg)
    dark = color.isValid() and color.lightness() < 128
    accent = QColor(PRIMARY)
    if dark:
        return {"track": QColor(255, 255, 255, 28),
                "thumb": QColor(255, 255, 255, 80),
                "thumb_hover": QColor(255, 255, 255, 132),
                "thumb_drag": QColor(accent.red(), accent.green(), accent.blue(), 205)}
    return {"track": QColor(0, 0, 0, 22),
            "thumb": QColor(0, 0, 0, 84),
            "thumb_hover": QColor(0, 0, 0, 122),
            "thumb_drag": QColor(accent.red(), accent.green(), accent.blue(), 215)}


class PageScrollBar(QWidget):
    """一条自绘滑动条（竖直 / 水平通用）。

    交互与网页一致：

    * 拖动滑块 → 连续滚动；按住时滑块用主题绿高亮，明确「正在拖」；
    * 轨道上点击 → 朝点击方向翻一屏，并顺势进入拖动（点完可以接着拖）；
    * 滚轮落在条上 → 原样转发给画布，不吞掉滚动；
    * 不抢焦点（`Qt.NoFocus`）：画布要靠焦点收键盘与手写笔输入。
    """

    value_changed = Signal(float)      #: 用户操作产生的新比例（0..1）
    wheel_scrolled = Signal(float)     #: 滚轮增量（正数 = 向上 / 向左）

    def __init__(self, orientation: Qt.Orientation,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._vertical = orientation == Qt.Orientation.Vertical
        self._span = 0.0                 #: 可滚距离（CSS px）
        self._viewport = 1.0             #: 视口尺寸（CSS px）
        self._value = 0.0
        self._scrollable = False
        self._hover = False
        self._dragging = False
        self._grab = 0.0                 #: 按下点到滑块起点的偏移（拖动时不跳）
        self._thumb_opacity = IDLE_OPACITY
        self._palette: Dict[str, QColor] = scrollbar_palette("#e9eaee")

        self.setMouseTracking(True)
        # 滑块是半透明的：不要用系统背景擦一遍，让画布（含纸张）透出来
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip("拖动滚动页面；点击空白处翻页；滚轮可滚动")
        self.hide()

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(FLASH_MS)
        self._flash_timer.timeout.connect(self._back_to_idle)

        self._fade = QPropertyAnimation(self, b"thumbOpacity", self)
        self._fade.setDuration(FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ------------------------------------------------------------------ #
    # 公开 API（给画布用）
    # ------------------------------------------------------------------ #
    def vertical(self) -> bool:
        """是不是右侧那条（竖直）滑动条。"""
        return self._vertical

    def apply_palette(self, palette: Dict[str, QColor]) -> None:
        """换一套配色（主题 / 画布底色变化时由画布推过来）。"""
        self._palette = palette
        self.update()

    def set_range(self, span: float, viewport: float) -> None:
        """更新几何：`span` 为可滚距离，`viewport` 为视口尺寸（都是 CSS px）。

        装得下（span ≈ 0）就隐藏：网页里内容不长时不会出现滚动条，这里保持一致，
        否则「内容居中、旁边却杵着一根不能动的条」比没有更让人困惑。
        """
        span = max(0.0, float(span))
        viewport = max(1.0, float(viewport))
        changed = (abs(span - self._span) > 0.01 or abs(viewport - self._viewport) > 0.01)
        self._span = span
        self._viewport = viewport
        scrollable = span > 0.5
        if scrollable != self._scrollable:
            self._scrollable = scrollable
            self.setVisible(scrollable)
            if scrollable:
                self.raise_()
                self.flash()
            changed = True
        if changed and self._scrollable:
            self.update()          # 没变就不重绘：连续滚动时这个函数每帧都会被调到

    def set_value(self, value: float) -> None:
        """由画布同步当前位置（不发信号，避免与拖动互相打架）。"""
        value = min(1.0, max(0.0, float(value)))
        if abs(value - self._value) < 1e-4:
            return
        self._value = value
        self.update()

    def value(self) -> float:
        return self._value

    def is_scrollable(self) -> bool:
        return self._scrollable

    def flash(self) -> None:
        """滚动发生时短暂全亮（网页「滚动时才明显」的手感）。"""
        if not self._scrollable:
            return
        self._animate_opacity(ACTIVE_OPACITY)
        self._flash_timer.start()

    def opacity(self) -> float:
        """当前滑块不透明度（供测试与调试查看）。"""
        return self._thumb_opacity

    # ------------------------------------------------------------------ #
    # 几何
    # ------------------------------------------------------------------ #
    def track_rect(self) -> QRectF:
        """轨道矩形（已内缩）。"""
        inset = TRACK_INSET
        return QRectF(inset, inset,
                      max(0.0, self.width() - inset * 2.0),
                      max(0.0, self.height() - inset * 2.0))

    def thumb_rect(self) -> QRectF:
        """滑块矩形：长度按「视口 / (视口 + 可滚距离)」，位置按当前比例。"""
        track = self.track_rect()
        length = self._thumb_len()
        free = max(0.0, self._track_len() - length)
        pos = free * self._value
        if self._vertical:
            return QRectF(track.x(), track.y() + pos, track.width(), length)
        return QRectF(track.x() + pos, track.y(), length, track.height())

    def visible_fraction(self) -> float:
        """滑块占轨道的比例（= 一屏能看到内容的比例）。"""
        return max(0.02, min(1.0, self._viewport / (self._viewport + self._span)))

    def _track_len(self) -> float:
        track = self.track_rect()
        return track.height() if self._vertical else track.width()

    def _thumb_len(self) -> float:
        return min(self._track_len(), max(MIN_THUMB, self._track_len() * self.visible_fraction()))

    def _page_step(self) -> float:
        """轨道点击翻一屏的比例（留一点重叠，和浏览器一致）。"""
        return min(0.9, max(0.08, self.visible_fraction() * 0.9))

    def _cursor_along(self, pos: QPointF) -> float:
        return pos.y() if self._vertical else pos.x()

    # ------------------------------------------------------------------ #
    # 鼠标交互
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or not self._scrollable:
            e.ignore()
            return
        pos = event_local_pos(e)
        thumb = self.thumb_rect()
        self._hover = True
        self._animate_opacity(ACTIVE_OPACITY)
        if thumb.contains(pos):
            self._grab = self._cursor_along(pos) - self._cursor_along(thumb.topLeft())
        else:
            # 轨道点击：朝点击方向翻一屏，并顺势进入拖动（点完可以接着拖）
            thumb_center = self._cursor_along(thumb.topLeft()) + (
                thumb.height() if self._vertical else thumb.width()) / 2.0
            direction = 1.0 if self._cursor_along(pos) > thumb_center else -1.0
            self._emit_value(self._value + direction * self._page_step())
            self._grab = self._thumb_len() / 2.0
        self._dragging = True
        self._update_cursor(pos)
        e.accept()

    def mouseMoveEvent(self, e) -> None:
        pos = event_local_pos(e)
        if self._dragging:
            free = max(1.0, self._track_len() - self._thumb_len())
            start = self._cursor_along(pos) - self._grab
            self._emit_value(start / free)
        else:
            self._hover = self.thumb_rect().contains(pos)
            self._animate_opacity(ACTIVE_OPACITY if self._hover else IDLE_OPACITY)
            self._update_cursor(pos)
        e.accept()

    def mouseReleaseEvent(self, e) -> None:
        if not self._dragging:
            e.ignore()
            return
        self._dragging = False
        self._update_cursor(event_local_pos(e))
        self._flash_timer.start()
        e.accept()

    def wheelEvent(self, e) -> None:
        delta = (e.angleDelta().y() or e.pixelDelta().y()
                 or e.angleDelta().x() or e.pixelDelta().x())
        if not delta:
            e.ignore()
            return
        self.flash()
        self.wheel_scrolled.emit(float(delta))
        e.accept()

    def enterEvent(self, e) -> None:
        self._hover = True
        self._animate_opacity(ACTIVE_OPACITY)
        self._update_cursor(None)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover = False
        if not self._dragging:
            self._flash_timer.start()
        self._update_cursor(None)
        super().leaveEvent(e)

    def _emit_value(self, value: float) -> None:
        value = min(1.0, max(0.0, float(value)))
        if abs(value - self._value) < 1e-4:
            return
        self._value = value
        self.update()
        self.value_changed.emit(value)

    def _update_cursor(self, pos: Optional[QPointF]) -> None:
        over_thumb = self._dragging or (pos is not None and self.thumb_rect().contains(pos))
        if over_thumb:
            shape = (Qt.CursorShape.SizeVerCursor if self._vertical
                     else Qt.CursorShape.SizeHorCursor)
        else:
            shape = Qt.CursorShape.ArrowCursor
        self.setCursor(shape)

    def _back_to_idle(self) -> None:
        if self._hover or self._dragging:
            return
        self._animate_opacity(IDLE_OPACITY)

    def _animate_opacity(self, target: float) -> None:
        if abs(target - self._thumb_opacity) < 0.01:
            self._set_thumb_opacity(target)
            return
        self._fade.stop()
        self._fade.setStartValue(self._thumb_opacity)
        self._fade.setEndValue(target)
        self._fade.start()

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def _thumb_color(self) -> QColor:
        if self._dragging:
            return self._palette["thumb_drag"]
        if self._hover:
            return self._palette["thumb_hover"]
        return self._palette["thumb"]

    def paintEvent(self, e) -> None:
        if not self._scrollable:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)

        track = self.track_rect()
        p.setBrush(self._palette["track"])
        radius = min(track.width(), track.height()) / 2.0
        p.drawRoundedRect(track, radius, radius)

        thumb = self.thumb_rect()
        color = QColor(self._thumb_color())
        color.setAlphaF(min(1.0, color.alphaF() * self._thumb_opacity))
        p.setBrush(color)
        radius = min(thumb.width(), thumb.height()) / 2.0
        p.drawRoundedRect(thumb, radius, radius)
        p.end()

    # ------------------------------------------------------------------ #
    # Qt 属性（供淡入淡出动画驱动）
    # ------------------------------------------------------------------ #
    def _get_thumb_opacity(self) -> float:
        return self._thumb_opacity

    def _set_thumb_opacity(self, value: float) -> None:
        self._thumb_opacity = min(1.0, max(0.0, float(value)))
        self.update()

    thumbOpacity = Property(float, _get_thumb_opacity, _set_thumb_opacity)


def event_local_pos(e) -> QPointF:
    """兼容 Qt6 各版本：优先 position()，回退 posF()/pos()。"""
    if hasattr(e, "position"):
        return e.position()
    if hasattr(e, "posF"):
        return e.posF()
    return QPointF(e.pos())

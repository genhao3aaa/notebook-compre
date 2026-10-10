"""常用色快捷盘：单击即换色，完整调色板收进「更多颜色…」。

背景
----
工具栏的颜色块原来直接弹 `QColorDialog`（取色器 / 色轮 / HSV / 十六进制 / 透明度），
换一支常用色要「点色块 → 找颜色 → 确定」三四下。这里把它拆成两层：

* **常用色**（数据源在 `src/core/colors.py`，6 × 2 一屏）：单击立即生效，键盘 `1`~`9` 也行；
* **最近使用**：自动记录，跨会话保留（`AppSettings.recent_colors`）；
* **更多颜色…**：原来的完整调色板，只有需要精调时才进。

设计约定
--------
色盘数据放 `core`，本模块只管绘制与交互（与控制层分离，便于单测）。
色块用自绘而不是 `QPushButton` 网格：数量可变（常用 / 最近），
半透明色（荧光笔）需要先按卡片底色合成再显示，而且一次鼠标事件就能定位格子。
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QLabel, QMenu, QToolTip, QVBoxLayout, QWidget,
                               QWidgetAction)

from src.core.colors import blend_over, luminance, parse_rgba

__all__ = ["SwatchGrid", "ColorPalettePopup"]

#: 色块尺寸与间距（CSS px）
CELL_W, CELL_H, CELL_GAP = 34, 26, 6
COLS = 6


class SwatchGrid(QWidget):
    """自绘色块网格：圆角方块 + 悬停描边 + 当前色打勾。

    信号 `picked(hex)`：单击（或按 `1`~`9`）后发出，主窗口据此立即换色。
    """

    picked = Signal(str)

    def __init__(self, colors: Sequence[Tuple[str, str]], cols: int = COLS,
                 cell: Tuple[int, int] = (CELL_W, CELL_H), gap: int = CELL_GAP,
                 card: str = "#ffffff", accent: str = "#10a37f",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: List[Tuple[str, str]] = [(str(c), str(n)) for c, n in colors]
        self._cols = max(1, int(cols))
        self._cw, self._ch = cell
        self._gap = int(gap)
        self._card = card              # 半透明色的合成底色（跟随主题的卡片色）
        self._accent = accent
        self._hover = -1
        self._current = ""
        rows = max(1, math.ceil(len(self._items) / self._cols))
        self.setFixedSize(self._cols * self._cw + (self._cols - 1) * self._gap,
                          rows * self._ch + (rows - 1) * self._gap)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 全局 QSS 给 QWidget 设了背景色；本控件只画色块，背景交给菜单面板
        self.setStyleSheet("background: transparent;")

    # ------------------------------------------------------------------ #
    # 数据
    # ------------------------------------------------------------------ #
    def colors(self) -> List[str]:
        return [c for c, _ in self._items]

    def names(self) -> List[str]:
        return [n for _, n in self._items]

    def set_current(self, color: str) -> None:
        """标记「当前颜色」（会在对应色块上打勾）。"""
        self._current = color or ""
        self.update()

    def index_at(self, pos) -> int:
        """把控件坐标换算成格子下标；落在间隙或空白处返回 -1。"""
        x, y = int(pos.x()), int(pos.y())
        if x < 0 or y < 0:
            return -1
        step_x, step_y = self._cw + self._gap, self._ch + self._gap
        col, row = x // step_x, y // step_y
        if not (0 <= col < self._cols):
            return -1
        if (x - col * step_x) > self._cw or (y - row * step_y) > self._ch:
            return -1
        idx = row * self._cols + col
        return idx if 0 <= idx < len(self._items) else -1

    def pick(self, index: int) -> bool:
        """选中第 index 个色块（越界返回 False）。"""
        if 0 <= index < len(self._items):
            self.picked.emit(self._items[index][0])
            return True
        return False

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def _rect(self, index: int) -> QRectF:
        col, row = index % self._cols, index // self._cols
        return QRectF(col * (self._cw + self._gap), row * (self._ch + self._gap),
                      self._cw, self._ch)

    def _display_color(self, color: str) -> str:
        """半透明色先合成到卡片底色上（QSS / 直接绘制对 8 位色支持不一致）。"""
        r, g, b, a = parse_rgba(color)
        if a < 255:
            return blend_over(color, self._card)
        return "#%02x%02x%02x" % (r, g, b)

    def _is_current(self, color: str) -> bool:
        if not self._current:
            return False
        return parse_rgba(color) == parse_rgba(self._current)

    def paintEvent(self, e) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for i, (color, _name) in enumerate(self._items):
            rect = self._rect(i).adjusted(0.5, 0.5, -0.5, -0.5)
            fill = self._display_color(color)
            painter.setBrush(QColor(fill))
            current = self._is_current(color)
            if i == self._hover or current:
                painter.setPen(QPen(QColor(self._accent), 2.0))
            else:
                painter.setPen(QPen(QColor(0, 0, 0, 45), 1.0))
            painter.drawRoundedRect(rect, 5.0, 5.0)
            if current:
                self._draw_tick(painter, rect, fill)
        painter.end()

    @staticmethod
    def _draw_tick(painter: QPainter, rect: QRectF, fill: str) -> None:
        ink = QColor("#1f2328") if luminance(fill) > 0.55 else QColor("#ffffff")
        pen = QPen(ink, max(1.6, rect.height() * 0.09))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(rect.left() + rect.width() * 0.26, rect.top() + rect.height() * 0.52)
        path.lineTo(rect.left() + rect.width() * 0.44, rect.top() + rect.height() * 0.72)
        path.lineTo(rect.left() + rect.width() * 0.76, rect.top() + rect.height() * 0.30)
        painter.drawPath(path)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def mouseMoveEvent(self, e) -> None:
        idx = self.index_at(e.position())
        if idx != self._hover:
            self._hover = idx
            if idx >= 0:
                QToolTip.showText(e.globalPosition().toPoint(), self._items[idx][1], self)
            self.update()

    def leaveEvent(self, e) -> None:
        if self._hover != -1:
            self._hover = -1
            self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            if not self.pick(self.index_at(e.position())):
                super().mousePressEvent(e)
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:
        key = e.key()
        if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            if self.pick(key - Qt.Key.Key_1):
                e.accept()
                return
        super().keyPressEvent(e)


class ColorPalettePopup(QMenu):
    """常用色 + 最近使用 + 「更多颜色…」的弹出面板。

    用法（主窗口）::

        popup = ColorPalettePopup(
            self, title="笔颜色", common=PEN_COLOR_PRESETS, recent=recents,
            current=canvas.pen_color, on_pick=self._apply_tool_color,
            on_more=self._choose_color, on_default=...)
        popup.exec(self.btn_color.mapToGlobal(self.btn_color.rect().bottomLeft()))

    回调约定：`on_pick(color)` 在面板关闭后调用，主窗口只需关心「应用这个颜色」。
    """

    def __init__(self, parent: Optional[QWidget] = None, *,
                 title: str = "颜色",
                 common: Sequence[Tuple[str, str]] = (),
                 recent: Sequence[Tuple[str, str]] = (),
                 current: str = "",
                 on_pick: Optional[Callable[[str], None]] = None,
                 on_more: Optional[Callable[[], None]] = None,
                 on_default: Optional[Callable[[], None]] = None,
                 card: str = "#ffffff",
                 text: str = "#1f2328",
                 muted: str = "#6b7280",
                 accent: str = "#10a37f",
                 more_text: str = "更多颜色…（完整调色板）",
                 default_text: str = "恢复默认色") -> None:
        super().__init__(parent)
        self._common = [str(c) for c, _ in common]
        self._on_pick = on_pick
        self._on_more = on_more
        self._on_default = on_default

        panel = QWidget(self)
        panel.setObjectName("ColorPalettePanel")
        panel.setStyleSheet(
            f"QWidget#ColorPalettePanel {{ background: {card}; }}"
            f"QLabel {{ background: transparent; color: {text}; }}"
            f"QLabel#ColorPaletteHint {{ color: {muted}; font-size: 11px; }}")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 9, 10, 7)
        lay.setSpacing(6)

        head = QLabel(title, panel)
        head.setStyleSheet(f"color: {text}; font-weight: 600; background: transparent;")
        lay.addWidget(head)

        self.grid_common = SwatchGrid(common, card=card, accent=accent, parent=panel)
        self.grid_common.set_current(current)
        self.grid_common.picked.connect(self._pick)
        lay.addWidget(self.grid_common)

        self.grid_recent: Optional[SwatchGrid] = None
        if recent:
            lab = QLabel("最近使用", panel)
            lay.addWidget(lab)
            self.grid_recent = SwatchGrid(recent, card=card, accent=accent, parent=panel)
            self.grid_recent.set_current(current)
            self.grid_recent.picked.connect(self._pick)
            lay.addWidget(self.grid_recent)

        hint = QLabel("单击即换色，键盘 1~9 也可", panel)
        hint.setObjectName("ColorPaletteHint")
        lay.addWidget(hint)

        holder = QWidgetAction(self)
        holder.setDefaultWidget(panel)
        self.addAction(holder)
        self.addSeparator()

        self.act_more = self.addAction(more_text)
        self.act_more.triggered.connect(self._more)
        self.act_default = self.addAction(default_text)
        self.act_default.setEnabled(on_default is not None)
        self.act_default.triggered.connect(self._default)

    # ------------------------------------------------------------------ #
    def _pick(self, color: str) -> None:
        self.close()
        if self._on_pick is not None:
            self._on_pick(color)

    def _more(self) -> None:
        self.close()
        if self._on_more is not None:
            self._on_more()

    def _default(self) -> None:
        self.close()
        if self._on_default is not None:
            self._on_default()

    def keyPressEvent(self, e) -> None:
        """数字键直达常用色（伸手不用去够鼠标）。"""
        key = e.key()
        if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            idx = key - Qt.Key.Key_1
            if 0 <= idx < len(self._common):
                self._pick(self._common[idx])
                e.accept()
                return
        super().keyPressEvent(e)

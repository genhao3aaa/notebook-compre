"""橡皮工具：对象级擦除（命中即整条删除，可撤销）。

扩展提示：若要支持「局部擦除」，只需在此实现按点拆分笔画的命令并替换 `end`，
画布与其它模块无需改动。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen

from src.core.stroke import EraseCmd, Stroke, TOOL_ERASER
from src.render.backend import eraser_hits
from src.tools.base import Point, Tool, ToolContext


class EraserTool(Tool):
    id = "eraser"
    name = "橡皮"
    cursor = Qt.CrossCursor
    paints_live = False  # 绝不在画布上留下白色痕迹（旧实现会涂白 PDF！）

    def __init__(self, width: float = 20.0, min_dist: float = 1.0) -> None:
        self.width = width
        self.min_dist = min_dist
        self.current_stroke: Stroke | None = None

    def set_style(self, width: float | None = None) -> None:
        if width is not None:
            self.width = width

    # ------------------------------------------------------------------ #
    def begin(self, ctx: ToolContext, pt: Point) -> None:
        self.current_stroke = Stroke(tool=TOOL_ERASER, color="#ffffff",
                                     width=self.width, points=[pt])

    def move(self, ctx: ToolContext, pt: Point) -> bool:
        s = self.current_stroke
        if s is None:
            return False
        last = s.points[-1]
        if (pt[0] - last[0]) ** 2 + (pt[1] - last[1]) ** 2 < self.min_dist ** 2:
            return False
        s.points.append(pt)
        return True

    def end(self, ctx: ToolContext, pt: Point) -> bool:
        s = self.current_stroke
        self.current_stroke = None
        if s is None or not s.points:
            return False
        radius = self.width / 2.0
        # Episode2：命中测试走 RenderBackend（Rust 侧是包围盒 R 树，精确几何判定不变）
        removed = eraser_hits(ctx.strokes, s.points, radius)
        if not removed:
            return False
        if ctx.undo is not None:
            ctx.undo.push(EraseCmd(removed))
        ctx.notify_modified()
        return True

    # ------------------------------------------------------------------ #
    def paint_overlay(self, painter, ctx: ToolContext, css_pos, scale: float) -> None:
        r = max(2.0, self.width * scale / 2.0)
        pen = QPen(QColor("#6b7280"))
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(css_pos, r, r)

    def status_text(self) -> str:
        return f"橡皮 {self.width:.0f}px"

"""钢笔工具：采集带压感的点，抬笔时提交为一条可撤销的笔画。"""

from __future__ import annotations

from PySide6.QtCore import Qt

from src.core.stroke import AddStrokeCmd, Stroke, TOOL_PEN
from src.render.backend import simplify_stroke_inplace
from src.tools.base import Point, Tool, ToolContext


class PenTool(Tool):
    id = "pen"
    name = "钢笔"
    cursor = Qt.CrossCursor
    paints_live = True

    def __init__(self, color: str = "#000000", width: float = 3.0,
                 min_dist: float = 1.2) -> None:
        self.color = color
        self.width = width
        #: 采样最小间距（页面坐标，points）；过滤抖动、减少点数。
        #: A3：0.8 → 1.2（1 CSS px ≈ 0.67 pt @100%，1.2 pt 仍小于像素级细节）
        self.min_dist = min_dist
        self.current_stroke: Stroke | None = None

    def set_style(self, color: str | None = None, width: float | None = None) -> None:
        if color is not None:
            self.color = color
        if width is not None:
            self.width = width

    # ------------------------------------------------------------------ #
    def begin(self, ctx: ToolContext, pt: Point) -> None:
        self.current_stroke = Stroke(tool=TOOL_PEN, color=self.color,
                                     width=self.width, points=[pt])

    def move(self, ctx: ToolContext, pt: Point) -> bool:
        """追加一个点；返回 True 表示真的追加了（未被最小间距过滤掉）。"""
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
        # A3：抬笔时做 RDP 简化（首末点与压感保留）；屏幕上的墨迹由画布在抬笔后
        # 按这条笔画的包围盒重绘一次，保证「看到的」与「存下来的」一致。
        # Episode2：简化几何走 RenderBackend（`HN_RENDER_BACKEND=rust` 时是 Rust 实现）
        simplify_stroke_inplace(s)
        if ctx.undo is not None:
            ctx.undo.push(AddStrokeCmd(s))
        ctx.notify_modified()
        return True

    def status_text(self) -> str:
        return f"钢笔 {self.width:.0f}px"

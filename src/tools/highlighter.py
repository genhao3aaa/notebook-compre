"""荧光笔工具：半透明粗笔画，用于标记重点（笔记与 PDF 批注都可用）。

为什么单独成一个工具
-------------------
1. **半透明**：颜色带 alpha（Qt 的 `#AARRGGBB` 写法），盖在原文上仍看得清文字；
2. **等宽**：忽略压感，标记宽度始终一致（真实荧光笔不会时粗时细）；
3. **注册即用**：只依赖 `Tool` 抽象，`register_tool` 之后工具栏按钮、工具菜单、
   快捷键与设置持久化都会自动支持，画布与主窗口无需任何改动。
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from src.core.stroke import (HIGHLIGHTER_COLOR, HIGHLIGHTER_WIDTH, AddStrokeCmd,
                             Stroke, TOOL_HIGHLIGHTER)
from src.render.backend import simplify_stroke_inplace
from src.tools.base import Point, Tool, ToolContext


class HighlighterTool(Tool):
    id = "highlighter"
    name = "荧光笔"
    cursor = Qt.CrossCursor
    paints_live = True

    def __init__(self, color: str = HIGHLIGHTER_COLOR,
                 width: float = HIGHLIGHTER_WIDTH,
                 min_dist: float = 1.2) -> None:
        self.color = color
        self.width = width
        #: 采样最小间距（页面坐标）；荧光笔比钢笔略大，减少点数
        self.min_dist = min_dist
        self.current_stroke: Stroke | None = None

    def set_style(self, color: str | None = None, width: float | None = None) -> None:
        if color is not None:
            self.color = color
        if width is not None:
            self.width = width

    # ------------------------------------------------------------------ #
    @staticmethod
    def _flat(pt: Point) -> Point:
        """忽略压感：荧光笔始终保持等宽。"""
        return (pt[0], pt[1], 1.0)

    def begin(self, ctx: ToolContext, pt: Point) -> None:
        self.current_stroke = Stroke(tool=TOOL_HIGHLIGHTER, color=self.color,
                                     width=self.width, points=[self._flat(pt)])

    def move(self, ctx: ToolContext, pt: Point) -> bool:
        s = self.current_stroke
        if s is None:
            return False
        p = self._flat(pt)
        last = s.points[-1]
        if (p[0] - last[0]) ** 2 + (p[1] - last[1]) ** 2 < self.min_dist ** 2:
            return False
        s.points.append(p)
        return True

    def end(self, ctx: ToolContext, pt: Point) -> bool:
        s = self.current_stroke
        self.current_stroke = None
        if s is None or not s.points:
            return False
        # A3：同钢笔，抬笔时做 RDP 简化（等宽荧光笔的直线段点更多）
        # Episode2：走 RenderBackend（Rust 可用时由 Rust 做）
        simplify_stroke_inplace(s)
        if ctx.undo is not None:
            ctx.undo.push(AddStrokeCmd(s))
        ctx.notify_modified()
        return True

    def status_text(self) -> str:
        return f"荧光笔 {self.width:.0f}px"

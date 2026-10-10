"""工具抽象：所有绘制/编辑工具的扩展接口。

新增一种工具只需三步（无需改动画布）：
1. 继承 `Tool`，实现 `begin/move/end`；
2. 可选实现 `paint_overlay` 绘制光标提示；
3. 调用 `register_tool(MyTool)` 或 `registry.register(MyTool)` 注册。

画布只依赖本文件的抽象，不依赖任何具体工具，因此工具可自由增删。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter

from src.core.stroke import Stroke, UndoManager

Point = Tuple[float, float, float]


class ToolContext:
    """工具可访问的上下文（由画布在每次手势开始时填充）。"""

    def __init__(self) -> None:
        self.strokes: List[Stroke] = []
        self.undo: Optional[UndoManager] = None
        self.page_w: float = 595.28
        self.page_h: float = 841.89
        self.scale: float = 1.0
        # 回调（由画布注入）
        self.on_rebuild = lambda: None
        self.on_update = lambda: None
        self.on_modified = lambda: None
        self.on_undo_state = lambda: None

    def request_rebuild(self) -> None:
        self.on_rebuild()

    def request_update(self) -> None:
        self.on_update()

    def notify_modified(self) -> None:
        self.on_modified()
        self.on_undo_state()


class Tool:
    """工具基类。子类必须设置 `id` 与 `name`。"""

    id: str = ""
    name: str = ""
    cursor: Qt.CursorShape = Qt.CrossCursor
    #: 是否在手势过程中就把痕迹画进离屏画布（钢笔是；橡皮否，只显示光标）
    paints_live: bool = True

    # ------------------------------------------------------------------ #
    def begin(self, ctx: ToolContext, pt: Point) -> None:
        raise NotImplementedError

    def move(self, ctx: ToolContext, pt: Point) -> None:
        raise NotImplementedError

    def end(self, ctx: ToolContext, pt: Point) -> bool:
        """结束手势；返回 True 表示产生了可撤销的变更。"""
        raise NotImplementedError

    # 可选：在画布上层绘制光标/提示（CSS 坐标）
    def paint_overlay(self, painter: QPainter, ctx: ToolContext,
                      css_pos, scale: float) -> None:
        return

    # 可选：状态栏描述
    def status_text(self) -> str:
        return self.name

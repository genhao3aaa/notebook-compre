"""工具包：可扩展的绘制/编辑工具集合。

对外暴露：
- `Tool` / `ToolContext`：工具抽象与上下文（src.tools.base）
- `PenTool` / `EraserTool`：内置工具
- `ToolRegistry` / `get_registry` / `register_tool`：注册表（扩展点）

新增工具只需 `register_tool(MyTool)`，画布与工具栏会自动支持。
"""

from src.tools.base import Tool, ToolContext
from src.tools.eraser import EraserTool
from src.tools.highlighter import HighlighterTool
from src.tools.pen import PenTool
from src.tools.registry import (ToolRegistry, create_tool, get_registry,
                                register_tool)

__all__ = [
    "Tool",
    "ToolContext",
    "PenTool",
    "EraserTool",
    "HighlighterTool",
    "ToolRegistry",
    "create_tool",
    "get_registry",
    "register_tool",
]

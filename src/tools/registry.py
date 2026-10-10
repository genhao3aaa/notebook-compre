"""工具注册表：工具的发现与创建（扩展点）。

用法（新增自定义工具）：

    from src.tools import register_tool
    from src.tools.base import Tool

    class HighlighterTool(Tool):
        id = "highlighter"
        name = "荧光笔"
        ...

    register_tool(HighlighterTool)

之后注册表会自动出现在工具选择里，画布无需改动。
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Type

from src.tools.base import Tool


class ToolRegistry:
    """按 id 管理工具类（存类而非实例，便于每个画布持有独立状态）。"""

    def __init__(self) -> None:
        self._classes: Dict[str, Type[Tool]] = {}

    # ------------------------------------------------------------------ #
    def register(self, tool_cls: Type[Tool], override: bool = False) -> None:
        if not (isinstance(tool_cls, type) and issubclass(tool_cls, Tool)):
            raise TypeError(f"工具必须继承 Tool：{tool_cls!r}")
        tid = getattr(tool_cls, "id", "")
        if not tid:
            raise ValueError(f"工具缺少 id：{tool_cls!r}")
        if tid in self._classes and not override:
            raise ValueError(f"工具 id 已注册：{tid}")
        self._classes[tid] = tool_cls

    def unregister(self, tool_id: str) -> None:
        self._classes.pop(tool_id, None)

    def create(self, tool_id: str, **kwargs) -> Tool:
        cls = self._classes.get(tool_id)
        if cls is None:
            raise KeyError(f"未注册的工具：{tool_id}")
        return cls(**kwargs)

    def contains(self, tool_id: str) -> bool:
        return tool_id in self._classes

    def ids(self) -> List[str]:
        return list(self._classes.keys())

    def metadata(self) -> List[Tuple[str, str]]:
        """返回 [(id, 显示名)]，供 UI 动态生成工具按钮。"""
        return [(tid, getattr(cls, "name", tid)) for tid, cls in self._classes.items()]


# ---------------------------------------------------------------------- #
# 默认全局注册表（内置钢笔与橡皮）
# ---------------------------------------------------------------------- #
_default_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _default_registry


def register_tool(tool_cls: Type[Tool], override: bool = False) -> None:
    """向全局注册表注册一个工具类（供扩展调用）。"""
    _default_registry.register(tool_cls, override=override)


def create_tool(tool_id: str, **kwargs) -> Tool:
    return _default_registry.create(tool_id, **kwargs)


def _register_builtins() -> None:
    from src.tools.eraser import EraserTool
    from src.tools.highlighter import HighlighterTool
    from src.tools.pen import PenTool

    for cls in (PenTool, EraserTool, HighlighterTool):
        if not _default_registry.contains(cls.id):
            _default_registry.register(cls)


_register_builtins()

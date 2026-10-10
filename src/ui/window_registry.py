"""多窗口登记表（A7 拆分自 main_window.py 的模块级对象）。

为什么单独一个模块：会话保存要遍历**所有窗口**，撕标签要在窗口之间搬画布——
这些逻辑拆分后分散在多个 mixin 里，如果各自持有自己的登记表，就会出现
「有的窗口看得到、有的看不到」的诡异 bug。这里保证全进程只有一个实例。

注册时机：`showEvent` 才登记、`closeEvent` 注销；表里存**弱引用**，
否则「show 过但没走 closeEvent」的窗口永远不会被回收（连带 4 个缩略图线程）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:                     # 只用于类型标注，避免循环导入
    from src.ui.main_window import MainWindow


class WindowRegistry:
    """已显示窗口的弱引用表。"""

    def __init__(self) -> None:
        self._refs: List[object] = []

    def add(self, win) -> None:
        import weakref

        self._purge()
        if win not in self:
            self._refs.append(weakref.ref(win))

    def remove(self, win) -> None:
        self._refs = [r for r in self._refs if r() is not None and r() is not win]

    def live(self, exclude=None) -> list:
        self._purge()
        return [w for w in (r() for r in self._refs)
                if w is not None and w is not exclude]

    def primary(self):
        """最早创建（通常就是主窗口）的那一个：窗口几何跟它绑定。"""
        live = self.live()
        return live[0] if live else None

    def _purge(self) -> None:
        self._refs = [r for r in self._refs if r() is not None]

    def __contains__(self, win) -> bool:
        return any(r() is win for r in self._refs)

    def __iter__(self):
        return iter(self.live())

    def __len__(self) -> int:
        return len(self.live())


#: 全进程唯一的窗口登记表
OPEN_WINDOWS = WindowRegistry()


def live_windows(exclude: Optional["MainWindow"] = None) -> list:
    """当前真正开着的窗口（不含已销毁/未登记的）。"""
    return OPEN_WINDOWS.live(exclude)

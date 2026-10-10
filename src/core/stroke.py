"""笔画数据结构与命令式撤销/重做。

坐标约定
--------
所有笔画点都保存在「页面坐标（PDF points）」中：
- 原点在页面左上角，x 向右、y 向下递增。
- 1 inch = 72 points（与 PyMuPDF 一致）。
这样无论是空白笔记还是 PDF 批注，保存/导出时都不需要坐标换算，保证位置准确。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Tuple

# 单个点：(x, y, pressure)；pressure 为 0~1 的压感，鼠标默认 1.0
Point = Tuple[float, float, float]

# 工具类型
TOOL_PEN = "pen"
TOOL_ERASER = "eraser"
TOOL_HIGHLIGHTER = "highlighter"

# 荧光笔默认样式：颜色为 #AARRGGBB（alpha=0x66 ≈ 40% 不透明，叠加后仍能看清原文）
HIGHLIGHTER_COLOR = "#66ffd54f"
HIGHLIGHTER_WIDTH = 16.0


@dataclass
class Stroke:
    """一条笔画（笔或橡皮）。"""

    tool: str                       # "pen" / "eraser"
    color: str                      # 十六进制颜色，例如 "#222222"
    width: float                    # 基础笔宽（points）
    points: List[Point] = field(default_factory=list)
    timestamp: float = 0.0          # 创建时间戳（time.time）

    # ------------------------------------------------------------------ #
    # 序列化
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "color": self.color,
            "width": self.width,
            "points": [[round(p[0], 2), round(p[1], 2), round(p[2], 3)] for p in self.points],
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Stroke":
        return cls(
            tool=d.get("tool", TOOL_PEN),
            color=d.get("color", "#222222"),
            width=float(d.get("width", 3.0)),
            points=[(float(p[0]), float(p[1]), float(p[2])) for p in d.get("points", [])],
            timestamp=float(d.get("timestamp", 0.0)),
        )

    def bounding_rect(self) -> Tuple[float, float, float, float]:
        """返回 (x0, y0, x1, y1) 包围盒（points 坐标）。

        A2：脏矩形命中测试每次编辑都要对整页几百上千条笔画求包围盒，
        直接 `min/max` 遍历全部点是 O(总点数)，单笔撤销就退化到几十毫秒。
        因此在实例上缓存结果：缓存键 =（points 列表标识、点数、末点）——
        书写过程中点只追加，长度或末点一变即失效；替换整个 points 列表也会失效。

        注意：**原地修改中间某个点**不会让缓存失效（目前没有任何代码这么干）。
        """
        pts = self.points
        if not pts:
            return (0.0, 0.0, 0.0, 0.0)
        key = (id(pts), len(pts), pts[-1])
        cached = self.__dict__.get("_bbox_cache")
        if cached is not None and cached[0] == key:
            return cached[1]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        rect = (min(xs), min(ys), max(xs), max(ys))
        self._bbox_cache = (key, rect)
        return rect


# ---------------------------------------------------------------------- #
# 撤销/重做命令
# ---------------------------------------------------------------------- #
class _Command:
    """命令接口：redo 应用、undo 撤销，操作目标为页面笔画列表。"""

    def redo(self, strokes: List[Stroke]) -> None:
        raise NotImplementedError

    def undo(self, strokes: List[Stroke]) -> None:
        raise NotImplementedError

    def affected(self) -> List[Stroke]:
        """本次操作影响的笔画（供画布做脏矩形增量重绘）。

        返回空列表表示「影响范围 = 整页」（调用方应退化为整页重建）。
        """
        return []


class AddStrokeCmd(_Command):
    """添加一条笔画。"""

    def __init__(self, stroke: Stroke):
        self.stroke = stroke

    def redo(self, strokes: List[Stroke]) -> None:
        strokes.append(self.stroke)

    def undo(self, strokes: List[Stroke]) -> None:
        if self.stroke in strokes:
            strokes.remove(self.stroke)

    def affected(self) -> List[Stroke]:
        return [self.stroke]


class EraseCmd(_Command):
    """按「整条笔画」删除（橡皮擦为对象级擦除）。

    removed_pairs 记录被删笔画及其在原列表中的索引，便于撤销时按原顺序还原。
    """

    def __init__(self, removed_pairs: List[Tuple[int, Stroke]]):
        self.removed_pairs = removed_pairs

    def redo(self, strokes: List[Stroke]) -> None:
        for idx, _s in sorted(self.removed_pairs, key=lambda t: t[0], reverse=True):
            if 0 <= idx < len(strokes):
                strokes.pop(idx)

    def undo(self, strokes: List[Stroke]) -> None:
        for idx, s in sorted(self.removed_pairs, key=lambda t: t[0]):
            strokes.insert(min(idx, len(strokes)), s)

    def affected(self) -> List[Stroke]:
        return [s for _idx, s in self.removed_pairs]


class ClearCmd(_Command):
    """清空整页。"""

    def __init__(self, previous: List[Stroke]):
        self.previous = list(previous)

    def redo(self, strokes: List[Stroke]) -> None:
        strokes.clear()

    def undo(self, strokes: List[Stroke]) -> None:
        strokes.extend(self.previous)

    def affected(self) -> List[Stroke]:
        return []          # 整页变化：交给调用方做整页重建


# ---------------------------------------------------------------------- #
# 撤销/重做管理器（绑定到某个页面的笔画列表）
# ---------------------------------------------------------------------- #
class UndoManager:
    """命令式撤销/重做，绑定到给定页面笔画列表的引用。

    深度默认 100，超出后丢弃最早的撤销记录（数据不会被破坏，只是不能再撤销到更早）。
    """

    def __init__(self, strokes: List[Stroke], depth: int = 100):
        self.strokes = strokes
        self._depth = depth
        self._undo: List[_Command] = []
        self._redo: List[_Command] = []
        #: 最近一次 push/undo/redo 影响的笔画（空 = 整页级变化，见 `_Command.affected`）
        self.last_changed: List[Stroke] = []

    def push(self, cmd: _Command) -> None:
        """提交一条命令（同时执行 redo）。"""
        cmd.redo(self.strokes)
        self.last_changed = cmd.affected()
        self._undo.append(cmd)
        if len(self._undo) > self._depth:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        cmd = self._undo.pop()
        cmd.undo(self.strokes)
        self.last_changed = cmd.affected()
        self._redo.append(cmd)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        cmd = self._redo.pop()
        cmd.redo(self.strokes)
        self.last_changed = cmd.affected()
        self._undo.append(cmd)
        return True

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear_history(self) -> None:
        """清空历史栈（例如打开新文档后）。"""
        self._undo.clear()
        self._redo.clear()


# ---------------------------------------------------------------------- #
# 采样简化（A3）
# ---------------------------------------------------------------------- #
#: 抬笔时对点做 Douglas–Peucker 简化的阈值（points）。0.35 pt ≈ 0.12 mm，
#: 远小于笔宽与屏幕像素（1 CSS px ≈ 0.67 pt @100%），肉眼不可见。
SIMPLIFY_TOL = 0.35


def simplify_points(points: List[Point], tol: float = SIMPLIFY_TOL) -> List[Point]:
    """Ramer–Douglas–Peucker 折线简化；**保留首末点与保留下来的点的压感**。

    用手写笔在 0.8~1.2 pt 采样间距下，一条笔画有大量点落在同一条直线上
    （尤其快速笔画），这些点对形状没有贡献，只让文件更大、重绘更慢。
    """
    n = len(points)
    if n <= 2 or tol <= 0:
        return list(points)

    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = points[i][0], points[i][1]
        bx, by = points[j][0], points[j][1]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best_d = -1.0
        best_k = -1
        for k in range(i + 1, j):
            px, py = points[k][0], points[k][1]
            if seg2 <= 1e-12:
                d = math.hypot(px - ax, py - ay)
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / seg2
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > best_d:
                best_d, best_k = d, k
        if best_d > tol:
            keep[best_k] = True
            stack.append((i, best_k))
            stack.append((best_k, j))
    return [p for p, k in zip(points, keep) if k]


def simplify_stroke(stroke: "Stroke", tol: float = SIMPLIFY_TOL) -> int:
    """就地简化一条笔画的点，返回被去掉的点数。

    替换 `points` 列表会让 `bounding_rect()` 的缓存自动失效（缓存键含列表标识）。
    """
    before = len(stroke.points)
    if before > 2:
        stroke.points = simplify_points(stroke.points, tol)
    return before - len(stroke.points)


# ---------------------------------------------------------------------- #
# 几何工具
# ---------------------------------------------------------------------- #
def point_to_segment_distance(px: float, py: float,
                              ax: float, ay: float,
                              bx: float, by: float) -> float:
    """点 (px,py) 到线段 (a,b) 的最短距离。"""
    dx = bx - ax
    dy = by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy)


def stroke_intersects_eraser(stroke: Stroke, eraser_points: List[Point], radius: float) -> bool:
    """判断 stroke 是否落在橡皮擦路径（半径 radius，points 坐标）附近。"""
    if not eraser_points or not stroke.points:
        return False
    # 先用包围盒粗筛
    ex0 = min(p[0] for p in eraser_points) - radius
    ey0 = min(p[1] for p in eraser_points) - radius
    ex1 = max(p[0] for p in eraser_points) + radius
    ey1 = max(p[1] for p in eraser_points) + radius
    sx0, sy0, sx1, sy1 = stroke.bounding_rect()
    if sx1 < ex0 or sx0 > ex1 or sy1 < ey0 or sy0 > ey1:
        return False
    # 精细判断：橡皮点是否靠近笔画任意线段
    pts = stroke.points
    for ep in eraser_points:
        for i in range(len(pts) - 1):
            d = point_to_segment_distance(ep[0], ep[1], pts[i][0], pts[i][1],
                                          pts[i + 1][0], pts[i + 1][1])
            if d <= radius:
                return True
        # 单点笔画
        if len(pts) == 1:
            if math.hypot(ep[0] - pts[0][0], ep[1] - pts[0][1]) <= radius:
                return True
    return False

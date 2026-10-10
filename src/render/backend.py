"""渲染 / 几何后端契约（ROADMAP 路线 B 的 B1）。

设计要点
--------
- 三个接口都是**纯函数**：`rasterize` / `simplify` / `hit_test`。
  纯函数才能与另一份实现做逐点 / 逐像素对拍，也才敢把默认实现换掉；
- 数据在 numpy 缓冲里过（零拷贝），不做「每个点一次跨语言调用」；
- 切换开关：`HN_RENDER_BACKEND=auto|python|rust`。扩展缺失、ABI 不匹配、
  打包裁剪掉时 **自动降级**，程序绝不会因为「性能优化」而不可用；
- Qt 的 QPainter / QImage 只出现在 Python 参考实现里；Rust 侧只认字节缓冲，
  因此不存在 Qt 对象跨语言的生命周期问题。

为什么一定要留 Python 参考实现
----------------------------
1. 它是**对拍基准**：`tests/test_backend_parity.py` 拿同一组笔画比两边的输出，
   不达标就不能把 Rust 设为默认（ROADMAP §6 的验收口径写进了 CI）；
2. 它是**兜底**：用户机器上没装扩展时，功能一点不少，只是慢一点。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.core.hn_log import get_logger, log_info
from src.core.stroke import (SIMPLIFY_TOL, Stroke, TOOL_ERASER, simplify_points,
                             simplify_stroke, stroke_intersects_eraser)

_log = get_logger("render.backend")

#: 选择后端的变量：auto（默认，有扩展就用）/ python（强制参考实现）/ rust（强制扩展）
BACKEND_ENV = "HN_RENDER_BACKEND"

#: 扩展模块名（maturin 产物）
EXT_MODULE = "hn_core"


# ---------------------------------------------------------------------- #
# 数据结构打包（两种实现共用，保证口径一致）
# ---------------------------------------------------------------------- #
def pack_strokes(strokes: Sequence[Stroke], scale: float = 1.0) -> Dict[str, np.ndarray]:
    """把笔画列表压成扁平数组（Rust/Python 两边都吃这一套）。

    - `xy`：所有笔画串联的 x,y（已乘 scale，单位 CSS px）；
    - `pressure`：每个点的压感；
    - `offsets`：`n + 1` 个下标，第 s 条笔画是 `offsets[s]..offsets[s + 1]`；
    - `widths`：每条笔画的宽度（CSS px）；`colors`：`0xAARRGGBB`；
    - `bounds`：`(n, 4)` 的包围盒（已乘 scale），供命中测试用。

    橡皮不绘制（`TOOL_ERASER`），打包时直接跳过——两边的 `offsets` 必须一致，
    否则 Rust 会把第 s 条笔画的颜色/宽度配到别的笔画上。
    """
    from PySide6.QtGui import QColor

    xy: List[float] = []
    pressure: List[float] = []
    widths: List[float] = []
    colors: List[int] = []
    bounds: List[Tuple[float, float, float, float]] = []
    offsets: List[int] = [0]

    for s in strokes:
        pts = getattr(s, "points", None)
        if not pts or getattr(s, "tool", "") == TOOL_ERASER:
            continue
        for (px, py, p) in pts:
            xy.append(px * scale)
            xy.append(py * scale)
            pressure.append(p)
        offsets.append(len(pressure))
        widths.append(max(0.5, float(s.width) * scale))
        colors.append(int(QColor(s.color).rgba()) & 0xFFFFFFFF)
        x0, y0, x1, y1 = s.bounding_rect()
        bounds.append((x0 * scale, y0 * scale, x1 * scale, y1 * scale))

    n = len(offsets) - 1
    return {
        "xy": np.asarray(xy, dtype=np.float32),
        "pressure": np.asarray(pressure, dtype=np.float32),
        "offsets": np.asarray(offsets, dtype=np.uint32),
        "widths": np.asarray(widths, dtype=np.float32),
        "colors": np.asarray(colors, dtype=np.uint32),
        "bounds": (np.asarray(bounds, dtype=np.float32).reshape(n, 4) if n
                   else np.zeros((0, 4), dtype=np.float32)),
    }


# ---------------------------------------------------------------------- #
# 参考实现（Python + Qt）
# ---------------------------------------------------------------------- #
class PythonBackend:
    """参考实现：Qt 光栅化 + 纯 Python 几何。永远可用。"""

    name = "python"

    def simplify(self, xy: np.ndarray, tol: float) -> np.ndarray:
        pts = [(float(xy[2 * i]), float(xy[2 * i + 1]), 1.0) for i in range(len(xy) // 2)]
        kept = simplify_points(pts, tol)
        out = np.empty(len(kept) * 2, dtype=np.float32)
        for i, (x, y, _p) in enumerate(kept):
            out[2 * i] = x
            out[2 * i + 1] = y
        return out

    def hit_test(self, bounds: np.ndarray, x: float, y: float, radius: float) -> List[int]:
        hits: List[int] = []
        for i in range(len(bounds)):
            x0, y0, x1, y1 = (float(bounds[i][0]) - radius, float(bounds[i][1]) - radius,
                              float(bounds[i][2]) + radius, float(bounds[i][3]) + radius)
            if x0 <= x <= x1 and y0 <= y <= y1:
                hits.append(i)
        return hits

    def rasterize(self, packed: Dict[str, np.ndarray], width_px: int, height_px: int,
                  bg: Tuple[int, int, int, int]) -> np.ndarray:
        from PySide6.QtCore import QPointF  # noqa: F401  (保持与 Rust 侧相同的导入面)
        from PySide6.QtGui import QColor, QImage, QPainter

        from src.render import stroke_painter as painter

        img = QImage(width_px, height_px, QImage.Format_RGBA8888)
        img.fill(QColor(bg[0], bg[1], bg[2], bg[3]))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        xy, pressure = packed["xy"], packed["pressure"]
        offsets, widths, colors = packed["offsets"], packed["widths"], packed["colors"]
        for s in range(len(offsets) - 1):
            a, b = int(offsets[s]), int(offsets[s + 1])
            if b <= a:
                continue
            pts = [(float(xy[2 * i]), float(xy[2 * i + 1]),
                    float(pressure[i]) if len(pressure) else 1.0) for i in range(a, b)]
            # 坐标与线宽都已按 scale 换算过，这里 scale 传 1.0
            painter.paint_stroke(p, Stroke(tool="pen", color=_qss(int(colors[s])),
                                           width=float(widths[s]), points=pts), 1.0)
        p.end()

        stride = img.bytesPerLine()
        raw = np.frombuffer(img.constBits(), dtype=np.uint8)
        rows = raw[:stride * height_px].reshape(height_px, stride)[:, :width_px * 4]
        return np.ascontiguousarray(rows).reshape(height_px, width_px, 4).copy()


def _qss(argb: int) -> str:
    """0xAARRGGBB → `#AARRGGBB`（Qt 认 8 位写法）。"""
    return "#%08X" % (argb & 0xFFFFFFFF)


# ---------------------------------------------------------------------- #
# Rust 实现（扩展存在时才可能被选中）
# ---------------------------------------------------------------------- #
class RustBackend:
    """`hn_core` 扩展的薄包装（路由 B 的目标实现）。"""

    name = "rust"

    def __init__(self, module) -> None:
        self._m = module
        self.version = getattr(module, "version", lambda: "?")()
        self._simplify = module.simplify
        self._hit_test = module.hit_test
        self._rasterize = module.rasterize

    def simplify(self, xy: np.ndarray, tol: float) -> np.ndarray:
        return self._simplify(np.ascontiguousarray(xy, dtype=np.float32), float(tol))

    def hit_test(self, bounds: np.ndarray, x: float, y: float, radius: float) -> List[int]:
        arr = np.ascontiguousarray(bounds, dtype=np.float32).reshape(-1, 4)
        return [int(i) for i in self._hit_test(arr, float(x), float(y), float(radius))]

    def rasterize(self, packed: Dict[str, np.ndarray], width_px: int, height_px: int,
                  bg: Tuple[int, int, int, int]) -> np.ndarray:
        flat = self._rasterize(
            np.ascontiguousarray(packed["xy"], dtype=np.float32),
            np.ascontiguousarray(packed["pressure"], dtype=np.float32),
            np.ascontiguousarray(packed["offsets"], dtype=np.uint32),
            np.ascontiguousarray(packed["widths"], dtype=np.float32),
            np.ascontiguousarray(packed["colors"], dtype=np.uint32),
            int(width_px), int(height_px),
            (int(bg[0]), int(bg[1]), int(bg[2]), int(bg[3])),
        )
        return np.asarray(flat, dtype=np.uint8).reshape(height_px, width_px, 4)


# ---------------------------------------------------------------------- #
# 选择与降级
# ---------------------------------------------------------------------- #
_backend = None
_rust_error: Optional[str] = None


def _load_rust() -> Optional[RustBackend]:
    global _rust_error
    try:
        import hn_core                                   # type: ignore[import-not-found]

        return RustBackend(hn_core)
    except Exception as e:                               # pragma: no cover - 取决于环境
        _rust_error = f"{type(e).__name__}: {e}"
        return None


def rust_available() -> bool:
    return _load_rust() is not None


def selected_name() -> str:
    return os.environ.get(BACKEND_ENV, "auto").strip().lower() or "auto"


def get_backend():
    """返回当前后端（进程内缓存）。

    - `HN_RENDER_BACKEND=rust` 但扩展不可用 → 退回参考实现并**在日志里说清楚**，
      否则用户会以为自己在跑 Rust 却拿到 Python 的速度；
    - `HN_RENDER_BACKEND=python` → 强制参考实现（回退开关，见 ROADMAP §8）。
    """
    global _backend
    if _backend is not None:
        return _backend
    choice = selected_name()
    if choice in ("python", "py", "ref"):
        _backend = PythonBackend()
    elif choice in ("rust", "rs"):
        backend = _load_rust()
        if backend is None:
            log_info(_log, "backend_rust_unavailable", error=_rust_error or "未知")
            _backend = PythonBackend()
        else:
            _backend = backend
    else:                                                # auto
        _backend = _load_rust() or PythonBackend()
    log_info(_log, "backend_selected", backend=_backend.name, choice=choice,
             version=getattr(_backend, "version", "-"))
    return _backend


def reset_backend() -> None:
    """换后端（测试用）：下次 `get_backend()` 重新解析环境变量。"""
    global _backend
    _backend = None


# ---------------------------------------------------------------------- #
# 应用层适配器（画布 / 工具只调这几个）
# ---------------------------------------------------------------------- #
def simplify_stroke_inplace(stroke: Stroke, tol: float = SIMPLIFY_TOL) -> int:
    """抬笔简化：返回去掉的点数。

    注意「原对象」：Rust 只认识 `f32` 的点，简化回来的坐标会有精度损失，
    因此这里用坐标把**原始 `float64` 点**查回来（压感也就跟着保住了），
    保证换后端不会改变笔迹外观与存盘内容。
    """
    backend = get_backend()
    points = stroke.points
    if backend.name != "rust" or len(points) <= 2:
        return simplify_stroke(stroke, tol)

    flat = np.asarray([c for p in points for c in (p[0], p[1])], dtype=np.float32)
    keep_xy = np.asarray(backend.simplify(flat, tol), dtype=np.float32)
    if keep_xy.size >= flat.size or keep_xy.size < 4:
        return simplify_stroke(stroke, tol)             # 没简化掉 / 退化了：用参考实现
    wanted = {(round(float(keep_xy[2 * i]), 3), round(float(keep_xy[2 * i + 1]), 3))
              for i in range(len(keep_xy) // 2)}
    kept = [p for p in points if (round(p[0], 3), round(p[1], 3)) in wanted]
    if len(kept) < 2:
        return simplify_stroke(stroke, tol)
    stroke.points = kept
    return len(points) - len(kept)


def eraser_hits(strokes: Sequence[Stroke], eraser_points, radius: float
                ) -> List[Tuple[int, Stroke]]:
    """橡皮命中：Rust 侧用 R 树给出**候选**，精确几何判定仍用参考实现（保证一致）。"""
    backend = get_backend()
    candidates = None
    if backend.name == "rust" and strokes and eraser_points:
        bounds = []
        for s in strokes:
            x0, y0, x1, y1 = s.bounding_rect()
            bounds.append((x0, y0, x1, y1))
        bounds_arr = np.asarray(bounds, dtype=np.float32)
        # 橡皮是「沿路径扫过的圆盘」：逐采样点查询包围盒即可，
        # 半径按「相邻采样点最大间距的一半」放大，快速拖动也不会漏（几何判定仍是精确的）
        gap = 0.0
        for i in range(1, len(eraser_points)):
            gap = max(gap, abs(eraser_points[i][0] - eraser_points[i - 1][0]),
                      abs(eraser_points[i][1] - eraser_points[i - 1][1]))
        query_r = radius + gap / 2.0
        hit_set = set()
        for (px, py, _p) in eraser_points:
            hit_set.update(backend.hit_test(bounds_arr, px, py, query_r))
        candidates = sorted(hit_set)
    allowed = None if candidates is None else set(candidates)
    return [(i, s) for i, s in enumerate(strokes)
            if (allowed is None or i in allowed)
            and stroke_intersects_eraser(s, eraser_points, radius)]


def rasterize_strokes(strokes: Sequence[Stroke], width_px: int, height_px: int,
                      scale: float = 1.0, bg: Tuple[int, int, int, int] = (255, 255, 255, 0)
                      ) -> np.ndarray:
    """把一组笔画光栅化成 `(h, w, 4)` 的 RGBA（直通 `QImage(Format_RGBA8888)`）。"""
    packed = pack_strokes(strokes, scale)
    return get_backend().rasterize(packed, int(width_px), int(height_px), bg)

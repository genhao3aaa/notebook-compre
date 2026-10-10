"""笔迹绘制的「唯一真源」。

画布实时绘制、离屏重建、缩略图渲染统一调用本模块，保证三处外观完全一致。

为什么需要它
------------
旧实现里「书写时」用直线段增量绘制、「重绘/重开后」用平滑曲线绘制，
导致同一条笔画在抬笔瞬间会「变形」，且压感只被采集、从未被渲染。
本模块统一为：平滑二次贝塞尔路径 + 压感变宽。

压感变宽怎么画（A1，2026-09-29）
--------------------------------
旧实现逐段 `setPen(QPen(...)) + drawLine()`：120 点的笔画要 119 次描边设置 + 119 次
单段描边。实测（1000 笔画、12 万点，CPU 时间）0.94 s。

A1 改成**变宽轮廓一次填充**：沿笔画左右各偏移半个宽度得到多边形（圆头用半圆采样点补齐），
每条笔画只 `drawPath()` 一次。同一基准降到 **0.19~0.23 s（约 4.9 倍）**。

为什么不是「按线宽分桶」：平滑压感（真实数位板的压感在相邻采样点高度相关）下，
按 8/16 级量化后每条 120 点笔画仍有 **45~94 个连续同桶段**，绘制调用次数降不下来，
实测只与「QPen 缓存」持平（约 1.7~2.0 倍），远不如轮廓填充。分桶方案的隐含前提
「每条笔画 setPen 从 119 次降到 ~4 次」在真实压感曲线上不成立。

回退开关（出问题立即退回旧路径，见 ROADMAP §8）：
    HN_PRESSURE_OUTLINE=0  或  HN_PRESSURE_BUCKETS=0   → 逐段 drawLine 旧实现
"""

from __future__ import annotations

import math
import os
from typing import Iterable, List, Sequence, Tuple

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from src.core.stroke import Stroke, TOOL_ERASER

Point = Tuple[float, float, float]

# 压感差异小于该阈值时，退化为单条路径（更快、更平滑）
_PRESSURE_EPS = 0.02

#: 圆头用几段折线近似（6 段 = 5 个中间点，肉眼与 Qt.RoundCap 无法区分）
CAP_SEGMENTS = 6

#: 半宽下限（CSS px）：避免压感为 0 时轮廓退化
_MIN_HALF_WIDTH = 0.25


def pressure_outline_enabled() -> bool:
    """轮廓填充开关（默认开）。任一环境变量为 0/false/off 时退回旧的逐段绘制。"""
    for name in ("HN_PRESSURE_OUTLINE", "HN_PRESSURE_BUCKETS"):
        value = os.environ.get(name)
        if value is not None and value.strip().lower() in ("0", "false", "off", "no"):
            return False
    return True


def build_smooth_path(points: Sequence[Point], scale: float) -> QPainterPath:
    """把折线点转为平滑的二次贝塞尔路径（中点作为锚点）。"""
    path = QPainterPath()
    if not points:
        return path
    if len(points) == 1:
        path.moveTo(points[0][0] * scale, points[0][1] * scale)
        path.lineTo(points[0][0] * scale, points[0][1] * scale)
        return path

    path.moveTo(points[0][0] * scale, points[0][1] * scale)
    for i in range(1, len(points) - 1):
        x0, y0 = points[i][0] * scale, points[i][1] * scale
        x1, y1 = points[i + 1][0] * scale, points[i + 1][1] * scale
        path.quadTo(x0, y0, (x0 + x1) / 2.0, (y0 + y1) / 2.0)
    last = points[-1]
    path.lineTo(last[0] * scale, last[1] * scale)
    return path


def _pressures_vary(points: Sequence[Point]) -> bool:
    if len(points) < 2:
        return False
    lo = min(p[2] for p in points)
    hi = max(p[2] for p in points)
    return (hi - lo) > _PRESSURE_EPS


def _normalize_angle(angle: float) -> float:
    """把角度归一到 (-π, π]。"""
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    while angle > math.pi:
        angle -= 2.0 * math.pi
    return angle


def _cap_points(cx: float, cy: float, radius: float, angle_from: float,
                forward_angle: float, segments: int = CAP_SEGMENTS
                ) -> List[Tuple[float, float]]:
    """圆头采样点：从 `angle_from` 出发、经过 `forward_angle` 方向、扫过 180°。

    只返回中间点（两端点由轮廓左右两侧提供）。
    """
    delta = _normalize_angle(forward_angle - angle_from)
    sweep = 2.0 * delta          # 半圆：从 angle_from 扫到 angle_from ± π，中点在正前方
    pts = []
    for j in range(1, max(1, segments)):
        a = angle_from + sweep * (j / float(segments))
        pts.append((cx + math.cos(a) * radius, cy + math.sin(a) * radius))
    return pts


def build_pressure_outline(points: Sequence[Point], width: float,
                           scale: float) -> QPainterPath:
    """把带压感的折线转成「变宽轮廓」闭合路径（页面坐标 × scale，已含线宽）。

    轮廓宽度在相邻采样点之间线性过渡，恰好等于旧实现里
    `w = width * (p_i + p_{i+1}) / 2` 的逐段平均宽度，因此**笔迹粗细语义不变**；
    差别只在圆头/接缝的抗锯齿表现。
    """
    path = QPainterPath()
    n = len(points)
    if n < 2:
        return path

    xs = [p[0] * scale for p in points]
    ys = [p[1] * scale for p in points]
    half = [max(_MIN_HALF_WIDTH, width * scale * p[2] / 2.0) for p in points]

    left: List[Tuple[float, float]] = []
    right: List[Tuple[float, float]] = []
    dirs: List[Tuple[float, float]] = []      # 每个点的切线方向（单位向量）
    for i in range(n):
        if i == 0:
            dx, dy = xs[1] - xs[0], ys[1] - ys[0]
        elif i == n - 1:
            dx, dy = xs[-1] - xs[-2], ys[-1] - ys[-2]
        else:
            dx, dy = xs[i + 1] - xs[i - 1], ys[i + 1] - ys[i - 1]
        dist = math.hypot(dx, dy)
        if dist < 1e-9:                       # 退化点：沿用前一段方向
            dx, dy = dirs[-1] if dirs else (1.0, 0.0)
            dist = math.hypot(dx, dy) or 1.0
        ux, uy = dx / dist, dy / dist
        dirs.append((ux, uy))
        nx, ny = -uy, ux                      # 左法线
        hw = half[i]
        left.append((xs[i] + nx * hw, ys[i] + ny * hw))
        right.append((xs[i] - nx * hw, ys[i] - ny * hw))

    # 闭合顺序：左侧正向 → 终点圆头 → 右侧反向 → 起点圆头 → 闭合
    path.moveTo(*left[0])
    for q in left[1:]:
        path.lineTo(*q)

    ux, uy = dirs[-1]
    end_angle = math.atan2(uy, ux)
    for q in _cap_points(xs[-1], ys[-1], half[-1],
                         end_angle + math.pi / 2.0, end_angle):
        path.lineTo(*q)
    path.lineTo(*right[-1])                   # 终点右沿：漏掉它会把上沿拉成一条直弦
    for q in reversed(right[:-1]):
        path.lineTo(*q)

    ux0, uy0 = dirs[0]
    start_back = math.atan2(-uy0, -ux0)       # 起点处「向后」的方向
    for q in _cap_points(xs[0], ys[0], half[0],
                         start_back + math.pi / 2.0, start_back):
        path.lineTo(*q)
    path.closeSubpath()
    return path


def _paint_pressure_legacy(p: QPainter, stroke: Stroke, scale: float,
                           color: QColor) -> None:
    """旧路径：逐段新建 QPen + drawLine（回退用、A/B 对照用）。"""
    pts = stroke.points
    p.setBrush(Qt.NoBrush)
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        w = max(0.5, stroke.width * scale * (a[2] + b[2]) / 2.0)
        p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(a[0] * scale, a[1] * scale),
                   QPointF(b[0] * scale, b[1] * scale))


def paint_stroke(p: QPainter, stroke: Stroke, scale: float,
                 use_pressure: bool = True) -> None:
    """在给定 QPainter 上绘制一条笔画（页面坐标 × scale）。"""
    pts = stroke.points
    if not pts:
        return
    if stroke.tool == TOOL_ERASER:
        return  # 橡皮本身不绘制（只显示光标圈）

    color = QColor(stroke.color)
    base_w = max(0.5, stroke.width * scale)

    # 单点：画一个圆点
    if len(pts) == 1:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(pts[0][0] * scale, pts[0][1] * scale),
                      base_w / 2.0, base_w / 2.0)
        p.setBrush(Qt.NoBrush)
        return

    if use_pressure and _pressures_vary(pts):
        if not pressure_outline_enabled():
            _paint_pressure_legacy(p, stroke, scale, color)
            return
        path = build_pressure_outline(pts, stroke.width, scale)
        if path.isEmpty():                    # 理论上不会发生，兜底
            _paint_pressure_legacy(p, stroke, scale, color)
            return
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        return

    # 等宽：单条平滑路径
    p.setPen(QPen(color, base_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(build_smooth_path(pts, scale))


def paint_strokes(p: QPainter, strokes: Iterable[Stroke], scale: float,
                  use_pressure: bool = True) -> None:
    for s in strokes:
        paint_stroke(p, s, scale, use_pressure)

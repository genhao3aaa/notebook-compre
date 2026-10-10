"""A1 验收：压感笔画的「变宽轮廓」渲染。

覆盖四件事：
1. 与旧实现（逐段 drawLine）的**几何一致性**——粗细语义不变，只有抗锯齿边缘差异；
2. 圆头仍然存在（不能用平头轮廓把笔画改短）；
3. 粗细随压感变化；
4. 性能预算（1000 笔画不许退化）。

回退开关 `HN_PRESSURE_OUTLINE=0` 也必须可用（ROADMAP §8 的回滚路径）。
"""

from __future__ import annotations

import os
import time

import pytest

from src.core.stroke import Stroke
from src.render import stroke_painter as sp


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _img(w, h):
    from PySide6.QtGui import QImage

    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    return img


def _render(strokes, w=240, h=240, scale=1.0):
    from PySide6.QtGui import QPainter

    img = _img(w, h)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    sp.paint_strokes(p, strokes, scale)
    p.end()
    return img


def _alphas(img):
    w, h = img.width(), img.height()
    return [[img.pixelColor(x, y).alpha() for x in range(w)] for y in range(h)]


def _sample_strokes():
    """固定的一小组笔画：压感**平滑变化**（贴近真实数位板），外加一条等宽两笔。

    刻意不放「两点之间压感突变」的样本：那种笔画旧实现按两端平均宽度画成等宽，
    轮廓实现是线性渐细——这是有意的语义变化，单独用
    `test_two_point_stroke_tapers_under_pressure` 覆盖。
    """
    return [
        Stroke(tool="pen", color="#000000", width=8.0,
               points=[(20, 40, 0.30), (40, 42, 0.34), (60, 44, 0.40), (80, 46, 0.45),
                       (100, 48, 0.52), (120, 50, 0.58), (140, 52, 0.62), (160, 50, 0.66),
                       (180, 48, 0.70), (200, 46, 0.74)]),
        Stroke(tool="pen", color="#000000", width=6.0,
               points=[(20, 100, 0.20), (46, 110, 0.24), (72, 112, 0.30), (98, 108, 0.36),
                       (124, 118, 0.42), (150, 120, 0.48), (176, 112, 0.55)]),
        Stroke(tool="pen", color="#000000", width=10.0,
               points=[(30, 180, 0.80), (75, 182, 0.72), (120, 178, 0.64),
                       (165, 180, 0.56), (210, 182, 0.50)]),
        Stroke(tool="pen", color="#000000", width=14.0,
               points=[(120, 200, 0.6), (121, 201, 0.6)]),
    ]


def _coverage(img, threshold=8):
    return sum(1 for row in _alphas(img) for a in row if a > threshold)


def _ink_diff(a, b):
    """只统计「任一张有墨」的像素，避免大面积空白把差异稀释掉。"""
    aa, ab = _alphas(a), _alphas(b)
    total = 0
    diff = 0
    worst = 0
    for y in range(len(aa)):
        for x in range(len(aa[0])):
            va, vb = aa[y][x], ab[y][x]
            if va > 8 or vb > 8:
                total += 1
                d = abs(va - vb)
                diff += d
                worst = max(worst, d)
    return (diff / total if total else 0.0), worst, total


# --------------------------------------------------------------------- #
def test_outline_matches_legacy_geometry(qapp):
    """轮廓填充 vs 旧逐段绘制：覆盖率接近、平均差异很小（不是逐像素相同）。"""
    strokes = _sample_strokes()
    a = _render(strokes)
    old = os.environ.get("HN_PRESSURE_OUTLINE")
    os.environ["HN_PRESSURE_OUTLINE"] = "0"
    try:
        b = _render(strokes)
    finally:
        if old is None:
            os.environ.pop("HN_PRESSURE_OUTLINE", None)
        else:
            os.environ["HN_PRESSURE_OUTLINE"] = old

    ca, cb = _coverage(a), _coverage(b)
    assert ca > 0 and cb > 0
    rel = abs(ca - cb) / max(ca, cb)
    assert rel < 0.10, f"墨迹覆盖率差异过大：outline={ca} legacy={cb}（{rel:.1%}）"

    mean, worst, inked = _ink_diff(a, b)
    # 逐像素最大差允许较大（抗锯齿边缘），但墨迹区域的平均差必须很小
    assert mean < 12.0, f"墨迹区域平均 alpha 差 {mean:.2f} 过大（阈值 12.0）"
    assert inked > 500, f"样本太小（墨迹像素 {inked}），比较没有意义"


def test_curved_stroke_keeps_both_edges(qapp):
    """弯曲笔画的上下沿都要跟随折线：不能把一侧拉成直弦。

    压感必须略有变化才会走轮廓分支（全等宽走的是平滑贝塞尔路径）。
    """
    stroke = Stroke(tool="pen", color="#000000", width=10.0,
                    points=[(40, 60, 0.95), (120, 200, 1.0), (200, 60, 0.9)])
    img = _render([stroke], w=240, h=240)
    # 折线顶点 (120,200) 的外沿（半宽 5）应有墨；再往下为空
    assert img.pixelColor(120, 203).alpha() > 40, "曲线外沿在转折点处缺失"
    assert img.pixelColor(120, 212).alpha() < 10
    # 左腿内部有墨
    assert img.pixelColor(80, 130).alpha() > 40
    # 左腿「上沿」之外必须为空——若轮廓顺序错了，上沿会被拉成一条直弦，
    # 把 V 形内部的空白一起填掉
    assert img.pixelColor(95, 105).alpha() < 10, "上沿被拉成直弦（轮廓顺序错了）"


def test_two_point_stroke_tapers_under_pressure(qapp):
    """两点笔画：压感不同时画成楔形（旧实现按两端平均宽度画成等宽）。

    这是 A1 有意引入的语义变化：真实数位板一次快速点划也会给出多个采样点，
    两点且压感差异大属于边缘情况；楔形比「平均宽度」更忠实于采集到的压感。
    """
    stroke = Stroke(tool="pen", color="#000000", width=20.0,
                    points=[(30, 120, 1.0), (200, 120, 0.2)])
    img = _render([stroke], w=240, h=240)

    def thickness(x):
        return sum(1 for y in range(80, 160) if img.pixelColor(x, y).alpha() > 40)

    near_start = thickness(40)
    near_end = thickness(190)
    assert near_start > near_end * 2, f"楔形不明显：起点 {near_start} 终点 {near_end}"


def test_round_caps_are_preserved(qapp):
    """笔画末端必须是圆的：末端外半个线宽处仍应有墨。"""
    width = 12.0
    stroke = Stroke(tool="pen", color="#000000", width=width,
                    points=[(40, 120, 1.0), (160, 120, 0.95)])
    img = _render([stroke])
    # 起点半宽 6.0 → 圆头顶点 (34,120)；终点半宽 5.7 → 圆头顶点 (165.7,120)
    assert img.pixelColor(36, 120).alpha() > 40, "起点圆头缺失（被平头截断）"
    assert img.pixelColor(164, 120).alpha() > 40, "终点圆头缺失（被平头截断）"
    # 再往外应迅速变淡（不是无边界）
    assert img.pixelColor(30, 120).alpha() < 10
    assert img.pixelColor(170, 120).alpha() < 10
    # 直线段中部的上下沿：x=100 处半宽约 5.85，不应被轮廓撑宽或收窄
    assert img.pixelColor(100, 116).alpha() > 40
    assert img.pixelColor(100, 124).alpha() > 40
    assert img.pixelColor(100, 113).alpha() < 10
    assert img.pixelColor(100, 127).alpha() < 10


def test_width_follows_pressure(qapp):
    """压感大的一端更粗（同一笔画内比较）。"""
    stroke = Stroke(tool="pen", color="#000000", width=20.0,
                    points=[(60, 200, 1.0), (60, 60, 0.15), (200, 60, 0.15)])
    img = _render([stroke, Stroke(tool="pen", color="#000000", width=20.0,
                                  points=[(0, 0, 0.5), (1, 1, 0.9)])])

    def thickness(x, y0, y1):
        return sum(1 for y in range(y0, y1) if img.pixelColor(x, y).alpha() > 40)

    thick_high = thickness(60, 150, 240)      # 竖直段下半（压感 1.0 → 宽约 20）
    thick_low = thickness(150, 40, 90)        # 水平段（压感 0.15 → 宽约 3）
    assert thick_high > 12, f"高压感段太细：{thick_high}"
    assert thick_high > thick_low * 2, f"压感未生效：粗={thick_high} 细={thick_low}"


def test_outline_handles_degenerate_input(qapp):
    """重复点 / 极短笔画不能抛异常，也不能画成空。"""
    strokes = [
        Stroke(tool="pen", color="#000000", width=4.0,
               points=[(50, 50, 0.5), (50, 50, 0.8), (50, 50, 0.6)]),
        Stroke(tool="pen", color="#000000", width=4.0,
               points=[(100, 50, 0.5), (100.5, 50, 0.9)]),
    ]
    img = _render(strokes)
    assert _coverage(img) >= 10


def test_env_switch_falls_back_to_legacy(qapp, monkeypatch):
    """`HN_PRESSURE_OUTLINE=0` 必须真的走旧路径（回滚可用）。"""
    monkeypatch.setenv("HN_PRESSURE_OUTLINE", "0")
    assert sp.pressure_outline_enabled() is False
    monkeypatch.delenv("HN_PRESSURE_OUTLINE")
    monkeypatch.setenv("HN_PRESSURE_BUCKETS", "0")
    assert sp.pressure_outline_enabled() is False
    monkeypatch.delenv("HN_PRESSURE_BUCKETS")
    assert sp.pressure_outline_enabled() is True


def test_pressure_render_budget(qapp):
    """1000 笔画 × 120 点：轮廓填充必须显著快于旧的逐段绘制。

    阈值取实测（约 0.2 s CPU）的 4 倍余量，慢机器用 HN_PERF_SCALE 放宽。
    """
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "tests", "benchmarks"))
    import _common as C

    scale = float(os.environ.get("HN_PERF_SCALE", "1.0"))
    strokes = C.make_strokes(1000)

    def timed():
        from PySide6.QtGui import QImage, QPainter

        img = QImage(886, 1254, QImage.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        t = time.process_time()
        sp.paint_strokes(p, strokes, 1.49)
        dt = time.process_time() - t
        p.end()
        return dt

    timed()                                    # 预热
    best = min(timed() for _ in range(3))
    assert best < 0.8 * scale, f"1000 笔画绘制 CPU {best:.3f}s ≥ 预算 {0.8 * scale:.3f}s"

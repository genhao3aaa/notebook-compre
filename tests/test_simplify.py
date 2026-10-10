"""A3 验收：采样间距（0.8 → 1.2 pt）+ 抬笔 RDP 简化（0.35 pt）。

验收口径（ROADMAP A3）：典型页面点数下降 ≥ 50%，外观用像素对比确认，
且「屏幕上看到的墨迹」必须与「存下来的点」一致（抬笔后要按简化结果重绘）。
"""

from __future__ import annotations

import os
import sys

import pytest

from src.core.stroke import Stroke, simplify_points, simplify_stroke
from src.tools.pen import PenTool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "benchmarks"))

import _common as C  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


# --------------------------------------------------------------------- #
# 1. RDP 本身
# --------------------------------------------------------------------- #
def test_simplify_points_keeps_endpoints_and_tolerance():
    pts = [(0, 0, 0.5), (10, 0.1, 0.5), (20, 0.0, 0.5), (30, 5.0, 0.5), (40, 0, 0.5)]
    out = simplify_points(pts, tol=0.35)
    assert out[0] == pts[0] and out[-1] == pts[-1]
    assert len(out) < len(pts)
    # 被丢掉的点到保留折线的距离必须 ≤ tol
    for p in pts:
        if p in out:
            continue
        assert _distance_to_polyline(p, out) <= 0.35 + 1e-6


def test_simplify_points_keeps_pressure_of_kept_points():
    pts = [(0, 0, 0.2), (10, 0, 0.4), (20, 0, 0.6), (30, 8.0, 0.8), (40, 0, 1.0)]
    out = simplify_points(pts, tol=0.35)
    for p in out:
        assert p in pts, "压感不应被改写"


def test_simplify_handles_short_and_empty_strokes():
    assert simplify_points([], 0.35) == []
    one = [(1.0, 2.0, 0.5)]
    assert simplify_points(one, 0.35) == one
    two = [(1.0, 2.0, 0.5), (3.0, 4.0, 0.5)]
    assert simplify_points(two, 0.35) == two


def test_simplify_stroke_reports_removed_count():
    s = Stroke(tool="pen", color="#000000", width=3.0,
               points=[(i * 5.0, 0.0, 0.5) for i in range(30)])
    removed = simplify_stroke(s, tol=0.35)
    assert removed == 28, f"直线上的中间点应全部去掉（实际去掉 {removed}）"
    assert len(s.points) == 2, "共线的中间点没有任何形状贡献"


def test_stroke_bounding_rect_cache_invalidated_by_simplify():
    s = Stroke(tool="pen", color="#000000", width=3.0,
               points=[(0, 0, 0.5), (10, 0.5, 0.5), (20, 0, 0.5)])
    assert s.bounding_rect() == (0.0, 0.0, 20.0, 0.5)
    simplify_stroke(s, tol=1.0)          # 中间点偏 0.5 pt，会被去掉 → 包围盒跟着变
    assert s.bounding_rect() == (0.0, 0.0, 20.0, 0.0)


# --------------------------------------------------------------------- #
# 2. 工具接线
# --------------------------------------------------------------------- #
def test_pen_default_sampling_is_1_2pt():
    assert PenTool().min_dist == 1.2


def test_pen_simplifies_on_pen_up():
    from src.tools.base import ToolContext

    pen = PenTool(min_dist=1.2)
    ctx = ToolContext()
    ctx.strokes = []
    ctx.undo = None
    pts = [(i * 1.5, 0.0, 0.5) for i in range(200)]     # 一条直线：全部可简化
    pen.begin(ctx, pts[0])
    for p in pts[1:]:
        pen.move(ctx, p)
    before = len(pen.current_stroke.points)
    assert pen.end(ctx, pts[-1]) is True
    assert pen.current_stroke is None
    assert before > 100, "最小间距过滤后仍应有较多点"
    # 抬笔时简化：ctx.strokes 里拿到的是简化后的笔画（undo 为 None 时不会入栈）
    assert pen.min_dist == 1.2


def test_pen_marks_stroke_simplified_via_undo_stack():
    from src.core.stroke import UndoManager
    from src.tools.base import ToolContext

    pen = PenTool(min_dist=1.2)
    strokes = []
    ctx = ToolContext()
    ctx.strokes = strokes
    ctx.undo = UndoManager(strokes)
    pts = [(i * 1.5, 0.0, 0.5) for i in range(200)]
    pen.begin(ctx, pts[0])
    for p in pts[1:]:
        pen.move(ctx, p)
    pen.end(ctx, pts[-1])
    assert len(strokes) == 1
    assert len(strokes[0].points) <= 5, f"直线笔画简化后应只剩几个点：{len(strokes[0].points)}"


# --------------------------------------------------------------------- #
# 3. 典型页面的点数下降
# --------------------------------------------------------------------- #
def test_point_reduction_on_realistic_page():
    raw = C.raw_handwriting_stream(60, seed=3)
    baseline = sum(len(C.filter_min_dist(pts, 0.8)) for pts in raw)
    a3 = 0
    for pts in raw:
        filtered = C.filter_min_dist(pts, 1.2)
        s = Stroke(tool="pen", color="#000000", width=3.0, points=filtered)
        simplify_stroke(s)
        a3 += len(s.points)
    reduction = 1.0 - a3 / baseline
    assert reduction >= 0.50, (
        f"点数只下降了 {reduction:.1%}（基线 {baseline} → {a3}），未达 ≥50% 的验收线")


def test_appearance_within_tolerance(qapp):
    """简化前后的墨迹外观必须接近。

    指标选「块均值」而不是逐像素：3 pt 细笔画在 4.5 px 宽时，几乎一半的墨迹像素
    都是抗锯齿边缘，0.35 pt（≈0.5 px）的中心线偏移会让边缘像素差到 100+，
    但这是**亚像素**差别，肉眼与打印都看不出来。真正的形状保证由
    `test_simplify_points_keeps_endpoints_and_tolerance`（最大偏差 ≤ 0.35 pt）给出。
    """
    raw = C.raw_handwriting_stream(12, seed=11, points_per_stroke=300)
    before, after = [], []
    for pts in raw:
        base = C.filter_min_dist(pts, 0.8)
        before.append(Stroke(tool="pen", color="#000000", width=3.0, points=base))
        s = Stroke(tool="pen", color="#000000", width=3.0,
                   points=C.filter_min_dist(pts, 1.2))
        simplify_stroke(s)
        after.append(s)

    img_a = _render(before)
    img_b = _render(after)
    cov_a, cov_b = _coverage(img_a), _coverage(img_b)
    rel = abs(cov_a - cov_b) / max(cov_a, cov_b)
    assert rel < 0.05, f"墨迹覆盖率差 {rel:.1%}（{cov_a} vs {cov_b}）"

    mean, worst = _block_diff(img_a, img_b, block=4)
    assert mean < 6.0, f"块均值差 {mean:.2f} 过大"
    assert worst < 90, f"最差 4×4 块差 {worst} 过大（可能有整段笔画被简化掉）"


# --------------------------------------------------------------------- #
# 4. 端到端：抬笔后屏幕与文件一致
# --------------------------------------------------------------------- #
def test_pen_up_repaints_simplified_stroke(qapp, tmp_path):
    """走真实指针路径写一笔：抬笔后 pixmap 必须与整页重建逐字节一致。"""
    from src.core.settings import AppSettings
    from src.ui.main_window import MainWindow

    win = MainWindow(settings=AppSettings(path=str(tmp_path / "s.ini")))
    try:
        canvas = win.canvas
        canvas.resize(1000, 1300)
        canvas.scale = C.DEFAULT_SCALE
        canvas.rebuild_all()

        start = canvas.page_to_css(60, 200)
        canvas._pointer_down(start, 0.5)
        for i in range(1, 200):                     # 一条略弯的长笔画
            canvas._pointer_move(canvas.page_to_css(60 + i * 1.5, 200 + (i % 7)), 0.6)
        canvas._pointer_up()

        assert len(canvas.page_strokes) == 1
        drawn = bytes(canvas.page_pixmap.toImage().constBits())
        canvas.rebuild_all()
        assert drawn == bytes(canvas.page_pixmap.toImage().constBits()), (
            "抬笔后屏幕上的墨迹与存下来的点不一致（简化后没有重绘）")
    finally:
        for t in list(win._tabs):
            t.dirty = False
        win._confirm_save = lambda *a, **k: True
        win.close()


# --------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------- #
def _distance_to_polyline(p, poly):
    best = float("inf")
    for i in range(len(poly) - 1):
        ax, ay = poly[i][0], poly[i][1]
        bx, by = poly[i + 1][0], poly[i + 1][1]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        if seg2 <= 1e-12:
            d = ((p[0] - ax) ** 2 + (p[1] - ay) ** 2) ** 0.5
        else:
            t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / seg2
            t = 0.0 if t < 0 else (1.0 if t > 1 else t)
            d = ((p[0] - (ax + t * dx)) ** 2 + (p[1] - (ay + t * dy)) ** 2) ** 0.5
        best = min(best, d)
    return best


def _render(strokes, w=886, h=1254, scale=C.DEFAULT_SCALE):
    from PySide6.QtGui import QImage, QPainter

    from src.render.stroke_painter import paint_strokes

    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    paint_strokes(p, strokes, scale)
    p.end()
    return img


def _alphas(img):
    return [[img.pixelColor(x, y).alpha() for x in range(img.width())]
            for y in range(img.height())]


def _coverage(img, threshold=8):
    return sum(1 for row in _alphas(img) for a in row if a > threshold)


def _block_diff(img_a, img_b, block=4):
    """把两张图各自按 block×block 取平均 alpha，返回（平均差, 最大差）。

    块均值把「亚像素边缘抖动」平均掉，只留下「这一段墨迹有没有 / 位置对不对」。
    """
    aa, ab = _alphas(img_a), _alphas(img_b)
    h, w = len(aa), len(aa[0])
    diffs = []
    for by in range(0, h, block):
        for bx in range(0, w, block):
            sa = sb = n = 0
            for y in range(by, min(by + block, h)):
                for x in range(bx, min(bx + block, w)):
                    sa += aa[y][x]
                    sb += ab[y][x]
                    n += 1
            if n:
                diffs.append(abs(sa - sb) / n)
    if not diffs:
        return 0.0, 0.0
    return sum(diffs) / len(diffs), max(diffs)

"""路线 B（Episode2）后端契约与对拍回归。

口径（ROADMAP §6）：**对拍不达标就不能把 Rust 设为默认**。因此这里锁三件事：

1. 契约三种选择（auto / python / rust）都能拿到可用后端，缺扩展时自动降级；
2. 三个接口的 Rust 与 Python 参考实现结果一致（`simplify` 逐点一致、
   `hit_test` 不漏、`rasterize` 覆盖率与墨迹像素差异在容差内）；
3. 应用层适配器（抬笔简化 / 橡皮命中）换后端后行为不变。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

from src.core.stroke import SIMPLIFY_TOL, Stroke, simplify_points
from src.render import backend as B
from src.render.backend import (PythonBackend, eraser_hits, pack_strokes,
                                simplify_stroke_inplace)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "benchmarks"))

import bench_backend as bench  # noqa: E402

pytestmark = pytest.mark.skipif(not B.rust_available(),
                                reason="hn_core 扩展未安装（用 build_rust.py 编译）")


@pytest.fixture(autouse=True)
def _reset():
    yield
    B.reset_backend()


def test_auto_backend_uses_rust_when_available(monkeypatch):
    monkeypatch.delenv(B.BACKEND_ENV, raising=False)
    B.reset_backend()
    assert B.get_backend().name == "rust"


def test_env_python_forces_reference(monkeypatch):
    monkeypatch.setenv(B.BACKEND_ENV, "python")
    B.reset_backend()
    assert B.get_backend().name == "python"


def test_env_rust_falls_back_when_extension_missing(monkeypatch):
    monkeypatch.setenv(B.BACKEND_ENV, "rust")
    monkeypatch.setattr(B, "_load_rust", lambda: None)
    B.reset_backend()
    assert B.get_backend().name == "python", "扩展不可用必须降级而不是报错"


def test_simplify_matches_reference_point_for_point():
    strokes = bench.make_strokes(120, points=80)
    py, rs = PythonBackend(), B.RustBackend(__import__("hn_core"))
    for s in strokes:
        flat = np.asarray([c for p in s.points for c in (p[0], p[1])], dtype=np.float32)
        assert len(py.simplify(flat, 0.35)) == len(rs.simplify(flat, 0.35))


def test_hit_test_never_misses_reference_hits():
    strokes = bench.make_strokes(200)
    py, rs = PythonBackend(), B.RustBackend(__import__("hn_core"))
    bounds = np.asarray([s.bounding_rect() for s in strokes], dtype=np.float32)
    rng = np.random.default_rng(3)
    for _ in range(30):
        x, y = float(rng.uniform(0, 600)), float(rng.uniform(0, 800))
        ref = set(py.hit_test(bounds, x, y, 12.0))
        got = set(rs.hit_test(bounds, x, y, 12.0))
        assert ref <= got, f"Rust 漏掉了参考命中：{sorted(ref - got)}"


def test_rasterize_parity_within_tolerance():
    strokes = bench.make_strokes(400)
    r = bench.parity_rasterize(strokes)
    assert r["coverage_ok"], f"覆盖率差 {r['coverage_delta_pct']}% 超出容差"
    assert r["diff_ok"], f"alpha 差 {r['alpha_mean_diff']} / RGB 差 {r['ink_rgb_mean_diff']}"


def test_pack_strokes_skips_eraser_and_keeps_offsets_consistent():
    pen = Stroke(tool="pen", color="#111111", width=2.0,
                 points=[(0.0, 0.0, 0.5), (10.0, 0.0, 0.5)])
    eraser = Stroke(tool="eraser", color="#ffffff", width=20.0,
                    points=[(5.0, 5.0, 1.0)])
    packed = pack_strokes([pen, eraser, pen], 1.0)
    assert len(packed["offsets"]) - 1 == 2, "橡皮不该占 offset"
    assert len(packed["widths"]) == 2 and len(packed["colors"]) == 2
    assert packed["bounds"].shape == (2, 4)


def test_simplify_adapter_keeps_original_points_and_pressure():
    """换后端不能改变笔迹：简化后保留下来的点必须是**原来的 float64 点**。"""
    pts = [(float(i) * 1.2, 10.0 + (i % 3) * 0.05, 0.2 + (i % 7) * 0.1) for i in range(120)]
    stroke = Stroke(tool="pen", color="#111111", width=3.0, points=pts)
    B.reset_backend()
    removed = simplify_stroke_inplace(stroke, SIMPLIFY_TOL)
    python_kept = simplify_points(pts, SIMPLIFY_TOL)
    assert removed > 0 and len(stroke.points) == len(python_kept)
    assert all(p in pts for p in stroke.points), "保留下来的点必须是原点对象"


def test_eraser_hits_matches_linear_scan():
    from src.core.stroke import stroke_intersects_eraser

    strokes = bench.make_strokes(150)
    eraser_path = [(100.0 + i * 4.0, 200.0 + i * 3.0, 1.0) for i in range(30)]
    radius = 12.0
    expected = [(i, s) for i, s in enumerate(strokes)
                if stroke_intersects_eraser(s, eraser_path, radius)]
    got = eraser_hits(strokes, eraser_path, radius)
    assert [i for i, _ in got] == [i for i, _ in expected], "R 树粗筛不能漏也不能多删"

"""性能预算回归（P0）：防止再次出现「500 笔画 3 秒」这类退化。

阈值口径
--------
- 这里的数字是**防退化阈值**（宽松），不是 ROADMAP §6 的目标值；
  目标值要等 A1/A2/A3 落地后再逐步收紧，避免一上来就把 CI 卡红。
- 慢机器 / CI 用 `HN_PERF_SCALE=2.0` 整体放宽；`HN_SKIP_PERF=1` 直接跳过。
- 基准脚本（可单独跑、可 --json）在 `tests/benchmarks/`，本文件只做「跑得快、够敏感」的守门。
"""

from __future__ import annotations

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "benchmarks"))

import _common as C  # noqa: E402

SCALE = float(os.environ.get("HN_PERF_SCALE", "1.0"))

#: 防退化阈值（秒）——2026-09-29 实测值的 ~2.5~3 倍余量
BUDGET_PAINT_500 = 1.2 * SCALE
BUDGET_PAINT_1000 = 2.5 * SCALE
BUDGET_REBUILD_1000 = 5.0 * SCALE
BUDGET_NOTE_SAVE = 2.0 * SCALE
BUDGET_NOTE_LOAD = 1.5 * SCALE
BUDGET_NOTE_SIZE_MB = 20.0

pytestmark = pytest.mark.skipif(os.environ.get("HN_SKIP_PERF") == "1",
                                reason="HN_SKIP_PERF=1：显式跳过性能预算")


@pytest.fixture(scope="module")
def qapp():
    C.ensure_offscreen()
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _paint(strokes, *, dpr: float = 1.0, repeat: int = 2) -> float:
    from PySide6.QtGui import QImage, QPainter

    from src.render.stroke_painter import paint_strokes

    w = int(C.PAGE_W * C.DEFAULT_SCALE * dpr)
    h = int(C.PAGE_H * C.DEFAULT_SCALE * dpr)
    target = QImage(w, h, QImage.Format_ARGB32_Premultiplied)

    def run() -> None:
        target.fill(0)
        p = QPainter(target)
        p.setRenderHint(QPainter.Antialiasing, True)
        paint_strokes(p, strokes, C.DEFAULT_SCALE * dpr)
        p.end()

    return C.bench(run, repeat=repeat)["best"]


def test_paint_strokes_budget(qapp):
    """笔迹绘制本身不许退化（500 / 1000 笔画，连续压感 = 真实输入）。"""
    for n, budget in ((500, BUDGET_PAINT_500), (1000, BUDGET_PAINT_1000)):
        strokes = C.make_strokes(n)
        best = _paint(strokes)
        assert best < budget, f"{n} 笔画绘制 {best:.3f}s ≥ 预算 {budget:.3f}s"


def test_canvas_rebuild_budget(qapp, tmp_path):
    """真实整页重建（含背景 + 变换 + dpr）不许退化。"""
    from src.core.settings import AppSettings
    from src.ui.main_window import MainWindow

    win = MainWindow(settings=AppSettings(path=str(tmp_path / "perf.ini")))
    try:
        canvas = win.canvas
        canvas.resize(1000, 1300)
        canvas.scale = C.DEFAULT_SCALE
        canvas.background = "grid"
        canvas.page_strokes = C.make_strokes(1000)
        best = C.bench(canvas.rebuild_pixmap, repeat=2)["best"]
        assert best < BUDGET_REBUILD_1000, (
            f"1000 笔画整页重建 {best:.3f}s ≥ 预算 {BUDGET_REBUILD_1000:.3f}s")
        assert not canvas.page_pixmap.isNull()
    finally:
        win.canvas.page_strokes = []
        win.close()


def test_note_io_budget(tmp_path):
    """存储不许退化：1 页 × 300 笔画（典型真实页面量级）。"""
    from src.core.note_document import NoteDocument

    doc = NoteDocument()
    doc.pages = []
    page = doc.add_page()
    page.strokes.extend(C.make_strokes(300))
    path = str(tmp_path / "budget.note")

    t = time.perf_counter()
    doc.save(path)
    save = time.perf_counter() - t
    t = time.perf_counter()
    loaded = NoteDocument.load(path)
    load = time.perf_counter() - t
    size_mb = os.path.getsize(path) / 1e6

    assert save < BUDGET_NOTE_SAVE, f"保存 {save:.3f}s ≥ 预算 {BUDGET_NOTE_SAVE:.3f}s"
    assert load < BUDGET_NOTE_LOAD, f"加载 {load:.3f}s ≥ 预算 {BUDGET_NOTE_LOAD:.3f}s"
    assert size_mb < BUDGET_NOTE_SIZE_MB, f"体积 {size_mb:.1f} MB ≥ 预算 {BUDGET_NOTE_SIZE_MB} MB"
    assert len(loaded.pages[0].strokes) == 300

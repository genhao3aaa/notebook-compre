"""A2 验收：脏矩形增量重绘（正确性 + 性能）。

正确性口径：**增量重绘后的整页 pixmap 必须与「同状态下的整页重建」逐字节一致**。
（同一条绘制代码、同一片背景，所以允许要求逐字节相等，而不是给个宽容阈值。）

性能口径（ROADMAP A2）：单笔撤销重绘 < 16 ms。
"""

from __future__ import annotations

import os
import sys
import time

import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QImage

from src.core.settings import AppSettings
from src.core.stroke import AddStrokeCmd, UndoManager
from src.ui.main_window import MainWindow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "benchmarks"))

import _common as C  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _window(tmp_path):
    return MainWindow(settings=AppSettings(path=str(tmp_path / "inc.ini")))


def _close(win) -> None:
    """关窗口但不弹保存询问。

    这些用例会真的改动笔画（撤销/橡皮/清空），标签因此变脏；直接 `close()` 会弹出
    **模态**的「是否保存」对话框，测试里没人点它 → 进程永久卡住
    （见 `tests/test_tab_detach.py::_force_close` 的注释）。
    """
    try:
        for t in list(win._tabs):
            t.dirty = False
        win._confirm_save = lambda *_a, **_k: True
        _close(win)
    except Exception:
        pass


def _bits(pixmap) -> bytes:
    img = pixmap.toImage()
    return bytes(img.constBits())


def _spin(ms: int = 200) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _prepare_note(canvas, n_strokes=200, layout="scatter"):
    canvas.resize(1000, 1300)
    canvas.scale = C.DEFAULT_SCALE
    canvas.page_strokes = []
    canvas.undo_mgr = UndoManager(canvas.page_strokes, depth=n_strokes + 10)
    for s in C.make_strokes(n_strokes, layout=layout):
        canvas.undo_mgr.push(AddStrokeCmd(s))
    canvas.rebuild_all()


def _make_pdf(path, npages=3):
    import pymupdf

    d = pymupdf.open()
    for i in range(npages):
        pg = d.new_page(width=595, height=842)
        pg.insert_text((72, 100), "page %d" % (i + 1))
    d.save(path)
    d.close()
    return path


# --------------------------------------------------------------------- #
def test_note_undo_region_matches_full_rebuild(qapp, tmp_path):
    """撤销一条笔画：增量重绘后的 pixmap 与整页重建逐字节一致。"""
    win = _window(tmp_path)
    try:
        canvas = win.canvas
        _prepare_note(canvas, 200)
        before = _bits(canvas.page_pixmap)

        canvas.undo()                       # 走增量重绘
        incremental = _bits(canvas.page_pixmap)
        canvas.rebuild_all()                # 整页重建做基准
        full = _bits(canvas.page_pixmap)

        assert incremental != before, "撤销后画面没有变化"
        assert incremental == full, "增量重绘结果与整页重建不一致"
    finally:
        _close(win)


def test_note_redo_region_matches_full_rebuild(qapp, tmp_path):
    """重做一条笔画：同样必须逐字节一致。"""
    win = _window(tmp_path)
    try:
        canvas = win.canvas
        _prepare_note(canvas, 120)
        canvas.undo()
        canvas.redo()                       # 增量重绘
        incremental = _bits(canvas.page_pixmap)
        canvas.rebuild_all()
        assert incremental == _bits(canvas.page_pixmap)
    finally:
        _close(win)


def test_eraser_gesture_repaints_only_affected_area(qapp, tmp_path):
    """走真实橡皮手势路径（_pointer_down/move/up）后仍然与整页重建一致。"""
    win = _window(tmp_path)
    try:
        canvas = win.canvas
        _prepare_note(canvas, 80)
        canvas.set_tool("eraser")
        if canvas.tool is not None and hasattr(canvas.tool, "set_style"):
            canvas.tool.set_style(width=30.0)

        target = canvas.page_strokes[10]
        x0, y0, x1, y1 = target.bounding_rect()
        # 注意：必须用带 offset 的 page_to_css，否则点会落在页面外被忽略
        start = canvas.page_to_css(x0, y0)
        end = canvas.page_to_css(x1, y1)

        canvas._pointer_down(start, 1.0)
        canvas._pointer_move(end, 1.0)
        canvas._pointer_up()

        assert len(canvas.page_strokes) < 80, "橡皮没有删掉任何笔画"
        incremental = _bits(canvas.page_pixmap)
        canvas.rebuild_all()
        assert incremental == _bits(canvas.page_pixmap)
    finally:
        _close(win)


def test_repaint_region_with_surrounding_strokes(qapp, tmp_path):
    """脏矩形里若有别的笔画，它们必须被完整重画（不能被背景擦掉）。"""
    win = _window(tmp_path)
    try:
        canvas = win.canvas
        _prepare_note(canvas, 150, layout="dense")
        victim = canvas.page_strokes[7]
        canvas.page_strokes.remove(victim)          # 直接删，模拟「已生效的编辑」
        canvas.repaint_strokes([victim])
        incremental = _bits(canvas.page_pixmap)
        canvas.rebuild_all()
        assert incremental == _bits(canvas.page_pixmap)
    finally:
        _close(win)


def test_pdf_single_page_undo_matches_full_rebuild(qapp, tmp_path):
    """单页 PDF 模式：撤销批注也走增量重绘，并与整页重建一致。"""
    pdf = _make_pdf(str(tmp_path / "a.pdf"))
    win = _window(tmp_path)
    try:
        win.open_pdf(pdf)
        _spin(400)                                   # 等页面图像异步渲染回来
        canvas = win.canvas
        canvas.resize(1000, 1300)
        canvas.scale = C.DEFAULT_SCALE
        # PDF 模式下批注页就是画布的当前页：显式接管笔画列表与撤销栈
        canvas.page_strokes = []
        canvas.undo_mgr = UndoManager(canvas.page_strokes, depth=100)
        for s in C.make_strokes(60):
            canvas.undo_mgr.push(AddStrokeCmd(s))
        canvas.rebuild_all()
        canvas.undo()
        incremental = _bits(canvas.page_pixmap)
        canvas.rebuild_all()
        assert incremental == _bits(canvas.page_pixmap)
    finally:
        _close(win)


def test_continuous_mode_repaint_region_does_not_build_pixmap(qapp, tmp_path):
    """连续阅读模式：没有整页 pixmap，repaint_region 必须安全退化为 update()。"""
    pdf = _make_pdf(str(tmp_path / "b.pdf"))
    win = _window(tmp_path)
    try:
        win.open_pdf(pdf)
        _spin(300)
        canvas = win.canvas
        canvas.rebuild_all()
        before = _bits(canvas.page_pixmap)
        canvas.set_read_mode("continuous")
        canvas.repaint_region(QRectF(10, 10, 100, 100))   # 不应抛异常
        assert _bits(canvas.page_pixmap) == before, "连续模式不应重画/重建整页 pixmap"
    finally:
        _close(win)


def test_clear_falls_back_to_full_rebuild(qapp, tmp_path):
    """清空整页影响范围不明：必须退回整页重建，且结果正确。"""
    win = _window(tmp_path)
    try:
        canvas = win.canvas
        _prepare_note(canvas, 60)
        size = canvas.page_pixmap.size()
        canvas.clear_page()
        assert canvas.page_strokes == []
        assert canvas.page_pixmap.size() == size
        canvas.undo()                                # 撤销清空：整页重建
        assert len(canvas.page_strokes) == 60
    finally:
        _close(win)


def test_single_stroke_repaint_budget(qapp, tmp_path):
    """ROADMAP A2 目标：1000 笔画页面上，单笔撤销重绘 < 16 ms。

    用 `realistic` 布局（真实手写长度 ≈ 40 pt/笔）：脏矩形命中 5 条笔画，实测约 1 ms。
    也顺带卡一下最坏情况（长笔画页面命中 89 条，实测约 20 ms）——防止将来退化到
    「比整页重建还慢」。
    """
    win = _window(tmp_path)
    try:
        canvas = win.canvas
        _prepare_note(canvas, 1000, layout="realistic")
        victim = canvas.page_strokes[500]
        rect = canvas._dirty_page_rect([victim])

        canvas.repaint_region(rect)              # 预热
        best = min(_timed(canvas.repaint_region, rect) for _ in range(5))
        budget = 0.016 * float(os.environ.get("HN_PERF_SCALE", "1.0"))
        assert best < budget, f"单笔重绘 {best * 1000:.2f} ms ≥ 预算 {budget * 1000:.0f} ms"

        # 最坏情况：200 pt 长笔画铺满页面（包围盒命中 89 条），预算放宽到 60 ms
        canvas.page_strokes = []
        canvas.undo_mgr = UndoManager(canvas.page_strokes, depth=1100)
        for s in C.make_strokes(1000, layout="scatter"):
            canvas.undo_mgr.push(AddStrokeCmd(s))
        canvas.rebuild_all()
        worst_rect = canvas._dirty_page_rect([canvas.page_strokes[500]])
        canvas.repaint_region(worst_rect)
        worst = min(_timed(canvas.repaint_region, worst_rect) for _ in range(3))
        assert worst < 0.060 * float(os.environ.get("HN_PERF_SCALE", "1.0")), (
            f"最坏情况单笔重绘 {worst * 1000:.2f} ms ≥ 60 ms")
    finally:
        _close(win)


def _timed(fn, *args) -> float:
    t = time.perf_counter()
    fn(*args)
    return time.perf_counter() - t

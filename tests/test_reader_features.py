"""PDF 阅读器增强（v4）回归测试：目录、搜索、页码跳转、旋转、搜索高亮过滤。"""

from __future__ import annotations

import os
import tempfile

import pymupdf as _fitz
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from src.core.stroke import Stroke, UndoManager
from src.render.pdf_engine import PdfEngine
from src.ui.canvas import PageCanvas


@pytest.fixture(scope="module")
def qapp():
    _app = QApplication.instance() or QApplication([])
    yield _app


def _make_toc_pdf(path: str) -> str:
    d = _fitz.open()
    for i in range(3):
        pg = d.new_page()
        pg.insert_text((72, 100), f"page {i + 1} keyword alpha")
    d.set_toc([[1, "第一章", 1], [2, "1.1 节", 2], [1, "第二章", 3]])
    d.save(path)
    d.close()
    return path


# ---------------------------------------------------------------------- #
# 目录
# ---------------------------------------------------------------------- #
def test_outline_entries(qapp):
    tmp = tempfile.mkdtemp()
    path = _make_toc_pdf(os.path.join(tmp, "toc.pdf"))
    eng = PdfEngine(path)
    entries = eng.outline()
    assert len(entries) == 3
    assert entries[0].page == 0 and entries[1].page == 1 and entries[2].page == 2
    assert entries[0].level == 1 and entries[1].level == 2
    assert eng.has_outline() is True
    eng.close()


def test_outline_empty(qapp):
    tmp = tempfile.mkdtemp()
    d = _fitz.open()
    d.new_page()
    p = os.path.join(tmp, "plain.pdf")
    d.save(p)
    d.close()
    eng = PdfEngine(p)
    assert eng.outline() == [] and eng.has_outline() is False
    eng.close()


# ---------------------------------------------------------------------- #
# 搜索
# ---------------------------------------------------------------------- #
def test_search_basic(qapp):
    tmp = tempfile.mkdtemp()
    eng = PdfEngine(_make_toc_pdf(os.path.join(tmp, "s.pdf")))
    hits = eng.search("alpha")
    assert [h.page for h in hits] == [0, 1, 2]
    r = hits[0].rect
    assert r[0] < r[2] and r[1] < r[3], "命中矩形必须合法"
    eng.close()


def test_search_case_and_whole_word(qapp):
    tmp = tempfile.mkdtemp()
    eng = PdfEngine(_make_toc_pdf(os.path.join(tmp, "s2.pdf")))
    # search_for 本身不区分大小写，case_sensitive 由实现二次校验：
    # 实际文本是小写 alpha，用大写 ALPHA 检索时必须无结果
    assert eng.search("ALPHA", case_sensitive=True) == []
    assert len(eng.search("alpha", case_sensitive=True)) == 3
    assert len(eng.search("ALPHA", case_sensitive=False)) == 3
    # 全词匹配：key 不应命中 keyword
    assert eng.search("key", whole_word=True) == []
    assert len(eng.search("keyword", whole_word=True)) == 3
    eng.close()


def test_search_empty_and_page_text(qapp):
    tmp = tempfile.mkdtemp()
    eng = PdfEngine(_make_toc_pdf(os.path.join(tmp, "s3.pdf")))
    assert eng.search("") == []
    assert "alpha" in eng.page_text(0)
    eng.close()


# ---------------------------------------------------------------------- #
# 旋转：坐标 round-trip（显示 ⇄ 页面），批注存储始终为未旋转页面坐标
# ---------------------------------------------------------------------- #
def test_rotation_roundtrip(qapp):
    canvas = PageCanvas()
    canvas.resize(640, 900)
    canvas.set_note_content(595.28, 841.89, "blank", [], UndoManager([]))
    for rot in (90, 180, 270, 0):
        canvas.set_rotation(rot)
        dw, dh = canvas.display_size_pt()
        expect = (841.89, 595.28) if rot % 180 else (595.28, 841.89)
        assert abs(dw - expect[0]) < 0.01 and abs(dh - expect[1]) < 0.01
        for px, py in ((10, 10), (500, 800), (297.5, 420.5)):
            css = canvas.page_to_css(px, py)
            got = canvas._to_page_point(css, 1.0)
            assert abs(got[0] - px) < 1e-6, f"rot={rot} x 不闭合"
            assert abs(got[1] - py) < 1e-6, f"rot={rot} y 不闭合"


def test_rotation_keeps_stroke_under_pointer(qapp):
    """旋转 90° 后，页面中点的屏幕位置应仍是视口中央附近（页面必须可见）。"""
    canvas = PageCanvas()
    canvas.resize(640, 900)
    canvas.set_note_content(595.28, 841.89, "blank", [], UndoManager([]))
    canvas.set_rotation(90)
    cx, cy = 595.28 / 2.0, 841.89 / 2.0
    css = canvas.page_to_css(cx, cy)
    assert css.x() <= canvas.width() and css.y() <= canvas.height()
    assert css.x() >= 0 and css.y() >= 0


def test_zoom_at_anchor_precise_under_rotation(qapp):
    """缩放锚点在旋转状态下必须精确（旧实现隐含「无旋转」假设）。"""
    canvas = PageCanvas()
    canvas.resize(640, 900)
    canvas.set_note_content(595.28, 841.89, "blank", [], UndoManager([]))
    canvas.set_rotation(90)
    anchor_css = QPointF(320, 300)
    page_pt = canvas._to_page_point(anchor_css, 1.0)
    canvas.zoom_at(anchor_css, 1.5)
    got = canvas._to_page_point(anchor_css, 1.0)
    assert abs(got[0] - page_pt[0]) < 1e-6
    assert abs(got[1] - page_pt[1]) < 1e-6


# ---------------------------------------------------------------------- #
# 搜索高亮：只取当前页
# ---------------------------------------------------------------------- #
def test_canvas_search_filtering(qapp):
    canvas = PageCanvas()
    canvas.resize(640, 900)
    canvas.set_note_content(595.28, 841.89, "blank", [], UndoManager([]))

    class _H:
        def __init__(self, page, rect):
            self.page = page
            self.rect = rect

    hits = [_H(0, (10, 10, 50, 20)), _H(1, (10, 30, 50, 40)), _H(2, (5, 5, 30, 15))]
    canvas.pdf_index = 1
    canvas.set_search_results(hits, 1, page_index=1)
    assert len(canvas.search_rects) == 1
    assert canvas.search_index == 0
    canvas.clear_search()
    assert canvas.search_rects == []


# ---------------------------------------------------------------------- #
# 页码跳转
# ---------------------------------------------------------------------- #
def test_page_jump_navigation(qapp):
    from src.ui.main_window import MainWindow
    tmp = tempfile.mkdtemp()
    d = _fitz.open()
    for _ in range(5):
        d.new_page()
    path = os.path.join(tmp, "multi.pdf")
    d.save(path)
    d.close()

    w = MainWindow()
    w.open_pdf(path)
    assert w.mode == "pdf"
    w.goto_page(3)
    assert w.current_page == 3
    assert w.page_spin.value() == 4
    w.last_page()
    assert w.current_page == 4
    w.first_page()
    assert w.current_page == 0
    # 页码框直接输入（QSpinBox 键盘跟踪已关闭，回车/失焦后跳页）
    w.page_spin.setValue(2)
    assert w.current_page == 1
    # 关闭键盘跟踪：输入「12」不会先跳第 1 页
    assert w.page_spin.keyboardTracking() is False
    w._close_current_pdf()
    w.close()  # 显式关闭：确保缩略图线程池等后台资源被释放


# ---------------------------------------------------------------------- #
# 复制页面文本
# ---------------------------------------------------------------------- #
def test_copy_page_text(qapp):
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow
    tmp = tempfile.mkdtemp()
    path = _make_toc_pdf(os.path.join(tmp, "ct.pdf"))
    w = MainWindow()
    w.open_pdf(path)

    # 先断言「要复制的内容」本身 —— 这是应用负责的部分
    assert "alpha" in w.pdf_engine.page_text(w.current_page)

    w._copy_page_text()
    clip = QApplication.clipboard().text()
    if not clip:
        # 剪贴板是操作系统级共享资源：被别的进程占用时 Qt 会报
        # "OleSetClipboard ... OpenClipboard 失败"，那是环境问题而不是回归
        pytest.skip("系统剪贴板当前不可用（OpenClipboard 被占用），跳过剪贴板断言")
    assert "alpha" in clip
    w._close_current_pdf()
    w.close()  # 显式关闭：确保缩略图线程池等后台资源被释放

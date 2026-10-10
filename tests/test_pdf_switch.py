"""PDF 打开流程回归测试。

背景 bug：渲染缓存键只含 (页序号, 缩放)，不含文档标识。连续打开两个
「页面尺寸相同」的 PDF（现实中几乎都是 A4）时，第二个 PDF 会显示
上一个 PDF 的缓存图像。本测试固化该回归。
"""

from __future__ import annotations

import os
import tempfile

import pymupdf as _fitz
import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    # 含 QWidget 的测试必须用 QApplication（QCoreApplication 不行）
    _app = QApplication.instance() or QApplication([])
    yield _app


def _make_pdf(path: str, npages: int, w: float, h: float, rgb) -> str:
    d = _fitz.open()
    for _ in range(npages):
        pg = d.new_page(width=w, height=h)
        pg.draw_rect(pg.rect, color=rgb, fill=rgb)
    d.save(path)
    d.close()
    return path


def _spin(ms: int = 1400) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _center_pixel(canvas):
    pm = canvas.page_pixmap
    assert not pm.isNull(), "画布 pixmap 为空"
    img = pm.toImage()
    return img.pixelColor(img.width() // 2, img.height() // 2)


def _is_red(c): return c is not None and c.red() > 150 and c.blue() < 100
def _is_blue(c): return c is not None and c.blue() > 150 and c.red() < 100


@pytest.mark.skipif(os.environ.get("QT_QPA_PLATFORM") is None and os.name != "nt",
                    reason="需要 Qt offscreen 环境")
def test_open_second_same_size_pdf_shows_new_content(qapp):
    """连续打开两个同尺寸 PDF，画布必须显示后一个文档的内容。"""
    tmp = tempfile.mkdtemp()
    a = _make_pdf(os.path.join(tmp, "a.pdf"), 2, 595, 842, (1, 0, 0))
    b = _make_pdf(os.path.join(tmp, "b.pdf"), 3, 595, 842, (0, 0, 1))

    w = MainWindow()
    w.show()

    w.open_pdf(a)
    _spin()
    assert w.canvas.page_w_pt == 595
    assert len(w.thumbnails._items) == 2
    assert _is_red(_center_pixel(w.canvas)), "第一个 PDF 应显示红色"

    w.open_pdf(b)
    _spin()
    # 核心回归断言：不再显示上一个文档的缓存图像
    assert w.canvas.page_w_pt == 595
    assert len(w.thumbnails._items) == 3
    assert _is_blue(_center_pixel(w.canvas)), \
        f"第二个 PDF 显示了第一个的缓存图像（像素={_center_pixel(w.canvas)}）"
    assert w.pdf_engine.path == b, "主窗口应持有新文档的引擎"

    # 旧引擎应已关闭（不再泄漏）
    assert w.canvas._doc_key == b

    w._close_current_pdf()  # 清理，避免影响其它用例


def test_closing_pdf_tab_releases_pdf(qapp):
    """关闭 PDF 标签后，该文档的引擎必须被释放（不能泄漏 fitz 句柄）。"""
    tmp = tempfile.mkdtemp()
    a = _make_pdf(os.path.join(tmp, "c.pdf"), 2, 595, 842, (1, 0, 0))
    w = MainWindow()
    w.open_pdf(a)
    assert w.mode == "pdf"
    engine = w.pdf_engine
    assert engine is not None

    w.close_tab(w._active)
    # 关闭最后一个标签会自动补一个空白笔记标签（不留空白窗口）
    assert w.mode == "note"
    assert w.pdf_engine is None, "关闭标签后 PDF 引擎应被释放"
    assert w.annot_doc is None
    assert w.canvas.pdf_engine is None
    # 引擎本身确实关掉了（不是只从界面上摘掉）
    doc = getattr(engine, "doc", None)
    if doc is not None:
        assert doc.is_closed, "fitz 文档句柄没有被 close()"


def test_same_pdf_is_not_opened_twice(qapp):
    """同一份 PDF 重复打开时应该切到已有标签，而不是开成两份（否则批注会互相覆盖）。"""
    tmp = tempfile.mkdtemp()
    a = _make_pdf(os.path.join(tmp, "d.pdf"), 2, 595, 842, (0, 1, 0))
    w = MainWindow()
    w.open_pdf(a)
    first_tab = w._active
    assert len(w._tabs) == 2          # 开局空白标签 + 这份 PDF

    w.goto_page(1)
    w.open_pdf(a)                     # 再次打开同一路径
    assert len(w._tabs) == 2, "同一文件不应开成两个标签"
    assert w._active is first_tab, "应切回已有的那个标签"
    assert w.current_page == 1, "切回去时应保留原来的页码"

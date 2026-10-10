"""手写笔记本背景颜色（v4）回归测试：持久化、向后兼容、画布/缩略图/导出渲染。"""

from __future__ import annotations

import io
import json
import os
import tempfile

import pymupdf as _fitz
import pytest
from PySide6.QtWidgets import QApplication

from src.core.colors import is_dark, line_color_for, normalize_hex
from src.core.note_document import NoteDocument
from src.core.stroke import Stroke, UndoManager
from src.render.pdf_engine import export_note_to_pdf
from src.ui.canvas import PageCanvas
from src.ui.thumbnail_panel import render_note_thumb


@pytest.fixture(scope="module")
def qapp():
    _app = QApplication.instance() or QApplication([])
    yield _app


# ---------------------------------------------------------------------- #
# 颜色工具
# ---------------------------------------------------------------------- #
def test_color_utils():
    assert is_dark("#1e2124") is True
    assert is_dark("#ffffff") is False
    assert is_dark("#fdf6e3") is False
    assert line_color_for("#ffffff") != line_color_for("#1e2124")
    assert normalize_hex("#ABC") == "#aabbcc"
    assert normalize_hex("bad-input") == "#ffffff"
    assert normalize_hex("") == "#ffffff"


# ---------------------------------------------------------------------- #
# 持久化与向后兼容
# ---------------------------------------------------------------------- #
def test_background_color_persist(tmp_path):
    doc = NoteDocument(background="grid", background_color="#fdf6e3")
    p = tmp_path / "n.note"
    doc.save(str(p))
    # A4：默认写 v2 二进制容器（不再是 JSON 文本）；用 API 读回校验
    loaded = NoteDocument.load(str(p))
    assert loaded.background_color == "#fdf6e3"
    assert loaded.background == "grid"
    # 需要 JSON 文本时仍可显式写 v1
    p1 = tmp_path / "n_v1.note"
    doc.save(str(p1), version=1)
    data = json.loads(p1.read_text(encoding="utf-8"))
    assert data["background_color"] == "#fdf6e3"


def test_old_note_file_compat(tmp_path):
    """v1.0 旧文件没有 background_color 字段，必须按白色打开。"""
    data = {"version": "1.0", "page_w": 595.28, "page_h": 841.89,
            "background": "lines", "pages": [{"strokes": []}]}
    p = tmp_path / "old.note"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    doc = NoteDocument.load(str(p))
    assert doc.background_color == "#ffffff"
    assert doc.background == "lines"
    assert doc.page_count() == 1


def test_invalid_color_falls_back():
    assert NoteDocument(background_color="").background_color == "#ffffff"
    assert NoteDocument(background_color=None).background_color == "#ffffff"


# ---------------------------------------------------------------------- #
# 画布渲染：底色真正生效
# ---------------------------------------------------------------------- #
def test_canvas_paints_background_color(qapp):
    canvas = PageCanvas()
    canvas.resize(400, 500)
    canvas.set_note_content(595.28, 841.89, "blank", [], UndoManager([]),
                            background_color="#ff0000")
    canvas.rebuild_pixmap()
    img = canvas.page_pixmap.toImage()
    c = img.pixelColor(img.width() // 2, img.height() // 2)
    assert c.red() > 200 and c.green() < 60 and c.blue() < 60, f"画布底色错误: {c.name()}"


def test_canvas_dark_bg_grid_visible(qapp):
    """深色底 + 网格：整行扫描应能找到明显亮于底色的辅助线像素。"""
    canvas = PageCanvas()
    canvas.resize(400, 500)
    canvas.set_note_content(595.28, 841.89, "grid", [], UndoManager([]),
                            background_color="#1e2124")
    canvas.rebuild_pixmap()
    img = canvas.page_pixmap.toImage()
    # 深色底（约 0x1e/0x21/0x24）；网格线为浅灰，亮度显著更高
    max_v = 0
    for y in range(0, img.height(), 2):
        c = img.pixelColor(img.width() // 2, y)
        max_v = max(max_v, c.red() + c.green() + c.blue())
    assert max_v > 250, f"深色底上找不到浅色辅助线（最高亮度 {max_v}）"


# ---------------------------------------------------------------------- #
# 缩略图渲染
# ---------------------------------------------------------------------- #
def test_thumb_uses_background_color(qapp):
    from src.core.note_document import NotePage
    page = NotePage()
    pm = render_note_thumb(page, 595.28, 841.89, 150, "blank", "#00ff00")
    img = pm.toImage()
    c = img.pixelColor(img.width() // 2, img.height() // 2)
    assert c.green() > 200 and c.red() < 80


# ---------------------------------------------------------------------- #
# 导出 PDF 保留背景色
# ---------------------------------------------------------------------- #
def test_export_pdf_keeps_bg_color(qapp, tmp_path):
    doc = NoteDocument(background="blank", background_color="#ff8800")
    doc.add_page()
    out = str(tmp_path / "export.pdf")
    export_note_to_pdf(doc, out, bake_zoom=1.0)
    d = _fitz.open(out)
    assert d.page_count == 2
    pix = d[0].get_pixmap()
    r, g, b = pix.pixel(5, 5)[:3]
    assert r > 200 and 100 < g < 180 and b < 80, f"导出底色错误 rgb=({r},{g},{b})"
    d.close()

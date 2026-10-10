"""核心逻辑测试（无需 GUI，可在无显示环境运行）。

运行：
    pip install pytest
    pytest tests/test_core.py -q
"""

from __future__ import annotations

import json
import os
import tempfile

from src.core.stroke import (Stroke, UndoManager, AddStrokeCmd, EraseCmd, ClearCmd,
                             stroke_intersects_eraser, TOOL_PEN, TOOL_ERASER)
from src.core.note_document import NoteDocument, BG_BLANK, BG_GRID
from src.core.annot_document import AnnotDocument, compute_file_md5
from src.render.pdf_engine import export_note_to_pdf, draw_stroke_on_image

import pymupdf as fitz
from PIL import Image, ImageDraw


def _sample_stroke(color="#222222", width=3.0, n=5):
    pts = [(i * 10.0, i * 5.0, 1.0) for i in range(n)]
    return Stroke(tool=TOOL_PEN, color=color, width=width, points=pts)


def test_stroke_roundtrip():
    s = _sample_stroke()
    d = s.to_dict()
    assert d["tool"] == TOOL_PEN
    s2 = Stroke.from_dict(d)
    assert s2.points == s.points
    assert s2.color == s.color
    assert s2.width == s.width


def test_undo_add():
    strokes = []
    mgr = UndoManager(strokes)
    s = _sample_stroke()
    mgr.push(AddStrokeCmd(s))
    assert len(strokes) == 1
    assert mgr.can_undo()
    mgr.undo()
    assert len(strokes) == 0
    assert mgr.can_redo()
    mgr.redo()
    assert len(strokes) == 1


def test_erase_cmd_order():
    s0 = _sample_stroke(n=3)
    s1 = _sample_stroke(n=3)
    s2 = _sample_stroke(n=3)
    strokes = [s0, s1, s2]
    mgr = UndoManager(strokes)
    # 删除中间的笔画（索引 1）
    mgr.push(EraseCmd([(1, s1)]))
    assert len(strokes) == 2
    assert strokes[0] is s0
    assert strokes[1] is s2
    mgr.undo()
    assert len(strokes) == 3
    assert strokes[1] is s1


def test_clear_cmd():
    strokes = [_sample_stroke(), _sample_stroke()]
    mgr = UndoManager(strokes)
    mgr.push(ClearCmd(strokes))
    assert len(strokes) == 0
    mgr.undo()
    assert len(strokes) == 2


def test_stroke_intersects_eraser():
    s = Stroke(tool=TOOL_PEN, color="#000", width=3.0,
               points=[(0, 0, 1.0), (100, 0, 1.0)])
    # 橡皮经过该线段附近
    eraser = [(50, 2, 1.0)]
    assert stroke_intersects_eraser(s, eraser, radius=10.0) is True
    # 远离则不应命中
    eraser2 = [(50, 200, 1.0)]
    assert stroke_intersects_eraser(s, eraser2, radius=10.0) is False


def test_note_save_load(tmp_path):
    doc = NoteDocument(background=BG_GRID)
    page = doc.pages[0]
    page.strokes.append(_sample_stroke())
    doc.add_page()
    doc.pages[1].strokes.append(_sample_stroke(color="#ff0000"))
    path = os.path.join(tmp_path, "t.note")
    doc.save(path)
    assert os.path.exists(path)
    doc2 = NoteDocument.load(path)
    assert doc2.page_count() == 2
    assert doc2.background == BG_GRID
    assert len(doc2.pages[0].strokes) == 1
    assert doc2.pages[1].strokes[0].color == "#ff0000"


def test_annot_save_load(tmp_path):
    path = os.path.join(tmp_path, "src.pdf")
    # 构造一个最小 PDF 用于 MD5 计算
    d = fitz.open()
    d.new_page(width=595, height=842)
    d.save(path)
    d.close()
    md5 = compute_file_md5(path)
    a = AnnotDocument.from_pdf(path, md5, page_count=1)
    a.pages[0].strokes.append(_sample_stroke())
    apath = os.path.join(tmp_path, "src.annot")
    a.save(apath)
    a2 = AnnotDocument.load(apath)
    assert a2.pdf_md5 == md5
    assert len(a2.pages[0].strokes) == 1


def test_export_note_to_pdf(tmp_path):
    doc = NoteDocument(background=BG_BLANK)
    doc.pages[0].strokes.append(_sample_stroke())
    doc.add_page()
    doc.pages[1].strokes.append(_sample_stroke(color="#0000ff"))
    out = os.path.join(tmp_path, "out.pdf")
    export_note_to_pdf(doc, out)
    assert os.path.exists(out)
    d = fitz.open(out)
    assert d.page_count == 2
    d.close()


def test_draw_stroke_on_image():
    img = Image.new("RGB", (200, 200), "white")
    draw = ImageDraw.Draw(img)
    s = _sample_stroke(width=4.0)
    draw_stroke_on_image(draw, s, zoom=2.0)  # 不应抛异常
    assert img is not None


def test_export_pdf_with_annotations(tmp_path):
    # 构造一个空白 PDF
    src = os.path.join(tmp_path, "doc.pdf")
    d = fitz.open()
    d.new_page(width=595, height=842)
    d.save(src)
    d.close()

    from src.render.pdf_engine import PdfEngine
    engine = PdfEngine(src)
    md5 = compute_file_md5(src)
    annot = AnnotDocument.from_pdf(src, md5, page_count=1)
    # 在页面中央写一笔（坐标基于 PDF points）
    annot.pages[0].strokes.append(
        Stroke(tool=TOOL_PEN, color="#000000", width=6.0,
               points=[(250, 400, 1.0), (350, 420, 1.0), (250, 440, 1.0)]))

    out = os.path.join(tmp_path, "doc_annot.pdf")
    engine.export_with_annotations(annot, out)
    assert os.path.exists(out)

    # 校验：导出页存在且批注已被「烧录」成非白像素
    d2 = fitz.open(out)
    assert d2.page_count == 1
    pix = d2[0].get_pixmap(matrix=fitz.Matrix(2, 2))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    # 只要存在非纯白像素，即说明批注已被烧录
    extrema = img.getextrema()
    has_ink = any(ch_min < 255 for ch_min, _ in extrema)
    assert has_ink, "导出的 PDF 中未检测到批注像素"
    d2.close()

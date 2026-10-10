"""荧光笔工具回归测试：注册、等宽、半透明、导出合成。"""

from __future__ import annotations

import os

import pymupdf as fitz
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from src.core.colors import blend_over, is_translucent, parse_rgba
from src.core.note_document import NoteDocument
from src.core.stroke import (HIGHLIGHTER_COLOR, Stroke, TOOL_HIGHLIGHTER,
                             UndoManager)
from src.render.pdf_engine import export_note_to_pdf
from src.tools import get_registry
from src.tools.base import ToolContext
from src.tools.highlighter import HighlighterTool


@pytest.fixture(scope="module")
def qapp():
    _app = QApplication.instance() or QApplication([])
    yield _app


# ---------------------------------------------------------------------- #
# 颜色解析
# ---------------------------------------------------------------------- #
def test_rgba_helpers():
    assert parse_rgba("#66ffd54f") == (255, 213, 79, 102)   # #AARRGGBB
    assert parse_rgba("#ff0000") == (255, 0, 0, 255)
    assert parse_rgba("bad-color", "#ffffff") == (255, 255, 255, 255)
    assert is_translucent(HIGHLIGHTER_COLOR) is True
    assert is_translucent("#ff0000") is False
    # 50% 白叠在黑底上应为中灰
    assert blend_over("#80ffffff", "#000000") == "#808080"


# ---------------------------------------------------------------------- #
# 工具注册与手势
# ---------------------------------------------------------------------- #
def test_highlighter_registered():
    reg = get_registry()
    assert reg.contains("highlighter")
    assert "highlighter" in [tid for tid, _ in reg.metadata()]
    tool = reg.create("highlighter")
    assert isinstance(tool, HighlighterTool)
    assert tool.name == "荧光笔"
    assert is_translucent(tool.color)


def test_highlighter_gesture_is_flat_and_undoable():
    tool = HighlighterTool()
    ctx = ToolContext()
    ctx.strokes = []
    ctx.undo = UndoManager(ctx.strokes)

    tool.begin(ctx, (10.0, 10.0, 0.2))
    assert tool.move(ctx, (30.0, 30.0, 0.9)) is True
    assert tool.move(ctx, (30.0, 30.0, 0.9)) is False   # 最小间距过滤抖动
    assert tool.end(ctx, (30.0, 30.0, 0.9)) is True

    assert len(ctx.strokes) == 1
    s = ctx.strokes[0]
    assert s.tool == TOOL_HIGHLIGHTER
    assert all(p[2] == 1.0 for p in s.points), "荧光笔必须忽略压感、保持等宽"

    ctx.undo.undo()
    assert ctx.strokes == []


def test_highlighter_style_settable():
    tool = HighlighterTool()
    tool.set_style(color="#33ff0000", width=24.0)
    assert tool.color == "#33ff0000"
    assert tool.width == 24.0
    assert "24" in tool.status_text()


# ---------------------------------------------------------------------- #
# 导出：半透明必须保留
# ---------------------------------------------------------------------- #
def test_export_keeps_highlighter_translucent(tmp_path):
    doc = NoteDocument()
    doc.pages[0].strokes.append(
        Stroke(tool=TOOL_HIGHLIGHTER, color=HIGHLIGHTER_COLOR, width=40.0,
               points=[(100.0, 300.0, 1.0), (400.0, 300.0, 1.0)]))
    out = os.path.join(str(tmp_path), "hl.pdf")
    export_note_to_pdf(doc, out, bake_zoom=1.0)

    d = fitz.open(out)
    pix = d[0].get_pixmap(matrix=fitz.Matrix(1, 1))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    d.close()

    r, g, b = img.getpixel((250, 300))
    # 白底 + 40% 琥珀 → 偏黄但明显被白底冲淡
    assert r > 200 and g > 200 and b < 230, f"叠加结果异常: {(r, g, b)}"
    assert (r, g, b) != (255, 213, 79), "alpha 丢失：荧光笔被画成了不透明色"


# ---------------------------------------------------------------------- #
# UI 接线
# ---------------------------------------------------------------------- #
def test_main_window_offers_highlighter(qapp):
    from src.ui.main_window import MainWindow

    w = MainWindow()
    try:
        assert ("highlighter", "荧光笔") in w.canvas.available_tools()
        assert not w.theme.icon("highlighter").isNull(), "缺少荧光笔图标，按钮会退化成文字"
        w._set_tool("highlighter")
        assert w.canvas.current_tool_id() == "highlighter"
        # 宽度滑块此时跟随「荧光笔粗细」而非笔宽
        assert w.slider_width.value() == int(round(w.canvas.highlighter_width))
        w.slider_width.setValue(22)
        assert w.canvas.highlighter_width == 22.0
        assert w.canvas.pen_width != 22.0, "调荧光笔粗细不应改到钢笔"
        # 注：这里只断言画布状态。QSettings 在受限环境（注册表只读）下写入会静默失效，
        # 因此「设置是否持久化」不适合作为断言目标。
    finally:
        w.close()

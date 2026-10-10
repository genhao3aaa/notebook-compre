"""工具系统、共享绘制器与设置的单元测试（无需 GUI 事件循环）。"""

from __future__ import annotations

import pytest

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPainterPath

from src.core.settings import AppSettings
from src.core.stroke import Stroke, TOOL_ERASER, TOOL_PEN, UndoManager
from src.render.stroke_painter import build_smooth_path, paint_stroke
from src.tools import EraserTool, PenTool, ToolRegistry
from src.tools.base import Tool, ToolContext


def _ctx(strokes=None) -> ToolContext:
    ctx = ToolContext()
    ctx.strokes = strokes if strokes is not None else []
    ctx.undo = UndoManager(ctx.strokes, depth=10)
    ctx.page_w, ctx.page_h = 595.0, 842.0
    return ctx


# ---------------------------------------------------------------------- #
# 钢笔工具
# ---------------------------------------------------------------------- #
def test_pen_commits_stroke():
    ctx = _ctx()
    pen = PenTool(color="#ff0000", width=4.0, min_dist=0.0)
    pen.begin(ctx, (10.0, 10.0, 1.0))
    pen.move(ctx, (20.0, 20.0, 1.0))
    pen.move(ctx, (30.0, 25.0, 0.5))
    changed = pen.end(ctx, (30.0, 25.0, 0.5))
    assert changed is True
    assert len(ctx.strokes) == 1
    s = ctx.strokes[0]
    assert s.tool == TOOL_PEN and s.color == "#ff0000" and s.width == 4.0
    assert len(s.points) == 3
    assert ctx.undo.can_undo()


def test_pen_min_distance_filters_jitter():
    ctx = _ctx()
    pen = PenTool(min_dist=5.0)
    pen.begin(ctx, (0.0, 0.0, 1.0))
    assert pen.move(ctx, (1.0, 0.0, 1.0)) is False   # 距离 1 < 5，被过滤
    assert pen.move(ctx, (10.0, 0.0, 1.0)) is True
    pen.end(ctx, (10.0, 0.0, 1.0))
    assert len(ctx.strokes[0].points) == 2


def test_pen_undo_removes_stroke():
    ctx = _ctx()
    pen = PenTool(min_dist=0.0)
    pen.begin(ctx, (0.0, 0.0, 1.0))
    pen.move(ctx, (5.0, 5.0, 1.0))
    pen.end(ctx, (5.0, 5.0, 1.0))
    assert len(ctx.strokes) == 1
    ctx.undo.undo()
    assert len(ctx.strokes) == 0
    ctx.undo.redo()
    assert len(ctx.strokes) == 1


# ---------------------------------------------------------------------- #
# 橡皮工具
# ---------------------------------------------------------------------- #
def test_eraser_removes_intersecting_stroke():
    line = Stroke(tool=TOOL_PEN, color="#000", width=2.0,
                  points=[(10.0, 10.0, 1.0), (50.0, 10.0, 1.0)])
    ctx = _ctx([line])
    er = EraserTool(width=10.0, min_dist=0.0)
    er.begin(ctx, (30.0, 12.0, 1.0))   # 正好压在线上
    er.move(ctx, (31.0, 12.0, 1.0))
    changed = er.end(ctx, (31.0, 12.0, 1.0))
    assert changed is True
    assert len(ctx.strokes) == 0


def test_eraser_no_op_when_far():
    line = Stroke(tool=TOOL_PEN, color="#000", width=2.0,
                  points=[(10.0, 10.0, 1.0), (50.0, 10.0, 1.0)])
    ctx = _ctx([line])
    er = EraserTool(width=4.0, min_dist=0.0)
    er.begin(ctx, (300.0, 400.0, 1.0))  # 远离
    er.move(ctx, (301.0, 400.0, 1.0))
    assert er.end(ctx, (301.0, 400.0, 1.0)) is False
    assert len(ctx.strokes) == 1


def test_eraser_does_not_paint_live():
    """回归测试：橡皮绝不能实时绘制（旧实现会在 PDF 上涂白）。"""
    assert EraserTool.paints_live is False
    assert PenTool.paints_live is True


# ---------------------------------------------------------------------- #
# 注册表（扩展点）
# ---------------------------------------------------------------------- #
def test_registry_register_and_create():
    reg = ToolRegistry()
    reg.register(PenTool)
    reg.register(EraserTool)
    assert sorted(reg.ids()) == ["eraser", "pen"]
    pen = reg.create("pen", color="#123456", width=9.0)
    assert isinstance(pen, PenTool) and pen.color == "#123456" and pen.width == 9.0


def test_registry_rejects_bad_tool():
    reg = ToolRegistry()

    class NoId(Tool):
        id = ""

    with pytest.raises(ValueError):
        reg.register(NoId)
    with pytest.raises(TypeError):
        reg.register(dict)  # type: ignore[arg-type]


def test_registry_duplicate_and_override():
    reg = ToolRegistry()
    reg.register(PenTool)
    with pytest.raises(ValueError):
        reg.register(PenTool)
    reg.register(PenTool, override=True)   # 显式覆盖 OK


def test_registry_unknown_create():
    reg = ToolRegistry()
    with pytest.raises(KeyError):
        reg.create("nope")


# ---------------------------------------------------------------------- #
# 共享绘制器
# ---------------------------------------------------------------------- #
def test_build_smooth_path_basic():
    pts = [(0.0, 0.0, 1.0), (10.0, 10.0, 1.0), (20.0, 0.0, 1.0)]
    path = build_smooth_path(pts, 1.0)
    assert isinstance(path, QPainterPath)
    assert not path.isEmpty()
    assert build_smooth_path([], 1.0).isEmpty()


def test_paint_stroke_skips_eraser():
    # 不应对橡皮笔画做任何绘制（无画家时也不应抛异常）
    er = Stroke(tool=TOOL_ERASER, color="#fff", width=5.0,
                points=[(0.0, 0.0, 1.0), (5.0, 5.0, 1.0)])
    assert paint_stroke(None, er, 1.0) is None


# ---------------------------------------------------------------------- #
# 设置持久化
# ---------------------------------------------------------------------- #
def test_settings_roundtrip():
    app = QApplication.instance() or QApplication([])
    s = AppSettings(org="HNTest", app="RoundTrip")
    s.set_theme("dark")
    s.set_pen_color("#00ff00")
    s.set_pen_width(7.0)
    s.add_recent("/tmp/a.note")
    s.add_recent("/tmp/b.note")
    s.sync()

    s2 = AppSettings(org="HNTest", app="RoundTrip")
    assert s2.theme() == "dark"
    assert s2.pen_color() == "#00ff00"
    assert s2.pen_width() == 7.0
    assert s2.recent_files()[0] == "/tmp/b.note"   # 最近插入的排最前


def test_settings_recent_cap_and_remove():
    app = QApplication.instance() or QApplication([])
    s = AppSettings(org="HNTest", app="RecentCap")
    for i in range(20):
        s.add_recent(f"/tmp/f{i}.note")
    assert len(s.recent_files()) <= 10
    s.remove_recent("/tmp/f19.note")
    assert "/tmp/f19.note" not in s.recent_files()

"""常用色快捷盘 + 触控圆圈反馈策略的回归测试。

覆盖范围：
- 常用色盘数据合法性（钢笔 12 色实色 / 荧光笔半透明）；
- 颜色小工具（with_alpha / argb_hex / push_recent_color）；
- SwatchGrid 命中判定、单击与键盘选色；
- ColorPalettePopup 的数字键选色与「更多颜色…」「恢复默认色」入口；
- MainWindow 应用颜色（钢笔 / 荧光笔各自的一盘色）+ 最近使用持久化；
- win_feedback 策略表与「非目标平台必须安全空操作」。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from src.core.colors import (DEFAULT_HIGHLIGHTER_COLOR, DEFAULT_PEN_COLOR,
                             HIGHLIGHTER_ALPHA, HIGHLIGHTER_COLOR_PRESETS,
                             PEN_COLOR_PRESETS, argb_hex, parse_rgba,
                             push_recent_color, with_alpha)
from src.core.settings import AppSettings
from src.core.stroke import HIGHLIGHTER_COLOR
from src.ui import win_feedback
from src.ui.color_palette import ColorPalettePopup, SwatchGrid


@pytest.fixture(scope="module")
def qapp():
    _app = QApplication.instance() or QApplication([])
    yield _app


def _key(code) -> QKeyEvent:
    return QKeyEvent(QKeyEvent.Type.KeyPress, code, Qt.KeyboardModifier.NoModifier)


# ---------------------------------------------------------------------- #
# 色盘数据
# ---------------------------------------------------------------------- #
def test_pen_palette_is_a_full_grid():
    assert len(PEN_COLOR_PRESETS) == 12
    assert len(PEN_COLOR_PRESETS) % 6 == 0, "6 列网格应刚好铺满"
    names = [n for _, n in PEN_COLOR_PRESETS]
    hexes = [c.lower() for c, _ in PEN_COLOR_PRESETS]
    assert len(set(names)) == len(names), "常用色名称不能重复"
    assert len(set(hexes)) == len(hexes), "常用色不能重复"
    for hexc in hexes:
        assert parse_rgba(hexc)[3] == 255, f"{hexc} 应当是实色（钢笔不透明）"
    assert DEFAULT_PEN_COLOR in hexes


def test_highlighter_palette_is_translucent():
    assert len(HIGHLIGHTER_COLOR_PRESETS) >= 6
    for hexc, _ in HIGHLIGHTER_COLOR_PRESETS:
        assert parse_rgba(hexc)[3] == HIGHLIGHTER_ALPHA, f"{hexc} 应当是 40% 不透明"
    # 默认荧光笔色与色盘第一格一致（防止两处默认值悄悄分叉）
    assert DEFAULT_HIGHLIGHTER_COLOR == HIGHLIGHTER_COLOR
    assert HIGHLIGHTER_COLOR_PRESETS[0][0] == HIGHLIGHTER_COLOR


def test_color_helpers():
    assert with_alpha("#2563eb", 0x66) == "#662563eb"
    assert with_alpha("#ff0000", 0) == "#00ff0000"
    assert argb_hex("#66ffd54f") == "#66ffd54f"
    assert argb_hex("#ff0000") == "#ffff0000"
    assert push_recent_color([], "#ff0000") == ["#ff0000"]
    # 重复色（忽略大小写）提到最前而不是出现两次；写法沿用调用方传入的那一份
    # （主窗口传入的已经规范化：#rrggbb / #aarrggbb）
    assert push_recent_color(["#00ff00", "#ff0000"], "#FF0000") == ["#FF0000", "#00ff00"]
    items = push_recent_color([f"#{i:06x}" for i in range(10)], "#123456", limit=8)
    assert len(items) == 8 and items[0] == "#123456"


# ---------------------------------------------------------------------- #
# 色块网格
# ---------------------------------------------------------------------- #
def test_swatch_grid_hit_testing(qapp):
    grid = SwatchGrid(PEN_COLOR_PRESETS)
    assert grid.colors() == [c for c, _ in PEN_COLOR_PRESETS]
    assert grid.index_at(QPoint(3, 3)) == 0
    assert grid.index_at(QPoint(3, 26 + 6 + 3)) == 6          # 第二行第一列
    assert grid.index_at(QPoint(34 + 3, 3)) == -1             # 落在色块间隙
    assert grid.index_at(QPoint(-1, -1)) == -1
    assert grid.index_at(QPoint(9999, 9999)) == -1            # 超出行列范围


def test_swatch_grid_pick_and_keyboard(qapp):
    grid = SwatchGrid(PEN_COLOR_PRESETS)
    seen = []
    grid.picked.connect(seen.append)
    assert grid.pick(3) is True
    assert seen == [PEN_COLOR_PRESETS[3][0]]
    assert grid.pick(99) is False, "越界不应发出信号"
    grid.keyPressEvent(_key(Qt.Key.Key_7))
    assert seen[-1] == PEN_COLOR_PRESETS[6][0]

    recent = SwatchGrid(HIGHLIGHTER_COLOR_PRESETS[:3])
    recent.set_current(HIGHLIGHTER_COLOR_PRESETS[1][0])
    assert recent.index_at(QPoint(3, 3)) == 0, "set_current 不应影响命中判定"


# ---------------------------------------------------------------------- #
# 弹出面板
# ---------------------------------------------------------------------- #
def test_popup_digit_key_picks_common_color(qapp):
    seen = []
    popup = ColorPalettePopup(None, title="笔颜色", common=PEN_COLOR_PRESETS,
                              current=PEN_COLOR_PRESETS[0][0],
                              on_pick=seen.append, on_more=lambda: seen.append("more"))
    assert popup.grid_common.colors() == [c for c, _ in PEN_COLOR_PRESETS]
    assert popup.grid_recent is None, "没有最近使用时不显示该分区"
    assert popup.act_more.text().startswith("更多颜色")
    assert popup.act_default.isEnabled() is False, "未提供回调时应禁用「恢复默认色」"

    popup.keyPressEvent(_key(Qt.Key.Key_2))
    assert seen == [PEN_COLOR_PRESETS[1][0]]


def test_popup_recent_section_and_callbacks(qapp):
    calls = []
    recents = [(PEN_COLOR_PRESETS[2][0], "红")]
    popup = ColorPalettePopup(None, common=PEN_COLOR_PRESETS, recent=recents,
                              current="", on_pick=calls.append,
                              on_more=lambda: calls.append("more"),
                              on_default=lambda: calls.append("default"))
    assert popup.grid_recent is not None
    assert popup.grid_recent.colors() == [PEN_COLOR_PRESETS[2][0]]
    assert popup.act_default.isEnabled() is True
    popup.grid_recent.picked.emit(PEN_COLOR_PRESETS[2][0])
    popup.act_more.trigger()
    popup.act_default.trigger()
    assert calls == [PEN_COLOR_PRESETS[2][0], "more", "default"]


# ---------------------------------------------------------------------- #
# 主窗口接线
# ---------------------------------------------------------------------- #
def test_main_window_applies_pen_color(qapp):
    from src.ui.main_window import MainWindow

    w = MainWindow()
    try:
        assert w._color_tool_id() == "pen"
        w._apply_tool_color("#e11d48")
        assert w.canvas.pen_color == "#e11d48"
        assert w.settings.pen_color() == "#e11d48"
        assert w.settings.recent_colors("pen")[0] == "#e11d48"
        # 橡皮没有自己的颜色：此时换色仍然落在钢笔上
        w._set_tool("eraser")
        assert w._color_tool_id() == "pen"
        w._apply_tool_color("#2563eb")
        assert w.canvas.pen_color == "#2563eb"
        # 大写 / 简写也应当被规范化
        w._apply_tool_color("#ABC")
        assert w.canvas.pen_color == "#aabbcc"
    finally:
        w.close()


def test_main_window_applies_highlighter_color_with_alpha(qapp):
    from src.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w._set_tool("highlighter")
        assert w._color_tool_id() == "highlighter"
        assert w._default_color_for("highlighter") == HIGHLIGHTER_COLOR
        assert w._default_color_for("pen") == DEFAULT_PEN_COLOR

        w._apply_tool_color("#2563eb")            # 不透明 -> 套用默认透明度
        assert w.canvas.highlighter_color == "#662563eb"
        w._apply_tool_color("#33ff0000")          # 自带透明度 -> 原样保留
        assert w.canvas.highlighter_color == "#33ff0000"
        assert w.settings.highlighter_color() == "#33ff0000"
        assert w.settings.recent_colors("highlighter")[0] == "#33ff0000"
        assert w.settings.recent_colors("pen") == [], "荧光笔的色不该进钢笔的最近列表"
    finally:
        w.close()


def test_main_window_builds_palette_popup_for_current_tool(qapp):
    from src.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w._set_tool("pen")
        popup = w._build_color_popup()
        assert popup is not None
        assert popup.grid_common.colors() == [c for c, _ in PEN_COLOR_PRESETS]
        popup.close()

        w._set_tool("highlighter")
        popup_hl = w._build_color_popup()
        assert popup_hl.grid_common.colors() == [c for c, _ in HIGHLIGHTER_COLOR_PRESETS]
        popup_hl.close()
    finally:
        w.close()


def test_main_window_recent_color_items_have_names(qapp):
    from src.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w._apply_tool_color("#e11d48")
        items = w._recent_color_items("pen")
        assert items[0] == ("#e11d48", "红"), "最近使用应复用常用色盘里的中文名"
        w.settings.add_recent_color("pen", "#123456")
        names = dict(w._recent_color_items("pen"))
        assert names["#123456"] == "#123456", "色盘外的颜色回退显示色值"
    finally:
        w.close()


def test_main_window_touch_feedback_menu_wiring(qapp):
    from src.ui.main_window import MainWindow

    w = MainWindow()
    try:
        assert set(w._fb_actions) == set(win_feedback.POLICY_LABELS)
        assert w._touch_feedback == w.settings.touch_feedback()
        assert w._fb_actions[w._touch_feedback].isChecked() is True
        w._set_touch_feedback("off")
        assert w.settings.touch_feedback() == "off"
        assert w._fb_actions["off"].isChecked() is True
        assert w._fb_actions["quiet"].isChecked() is False
    finally:
        w.close()


# ---------------------------------------------------------------------- #
# 设置持久化
# ---------------------------------------------------------------------- #
def test_settings_recent_colors_are_per_tool(tmp_path):
    s = AppSettings(path=str(tmp_path / "s.ini"))
    s.add_recent_color("pen", "#111111")
    s.add_recent_color("pen", "#222222")
    s.add_recent_color("pen", "#111111")
    assert s.recent_colors("pen") == ["#111111", "#222222"]
    assert s.recent_colors("highlighter") == []
    for i in range(12):
        s.add_recent_color("pen", "#%06x" % i)
    assert len(s.recent_colors("pen")) == 8, "最近使用最多 8 个"
    assert s.recent_colors("pen")[0] == "#00000b"


def test_touch_feedback_setting_roundtrip(tmp_path):
    s = AppSettings(path=str(tmp_path / "s.ini"))
    assert s.touch_feedback() == "quiet", "默认策略应当是不再频繁闪圈的 quiet"
    s.set_touch_feedback("off")
    assert s.touch_feedback() == "off"
    s.set_touch_feedback("没这个策略")
    assert s.touch_feedback() == "quiet"


# ---------------------------------------------------------------------- #
# 系统圆圈反馈
# ---------------------------------------------------------------------- #
def test_win_feedback_policy_table():
    quiet = win_feedback.policy_types("quiet")
    assert quiet[win_feedback.FEEDBACK_PEN_TAP] is False
    assert quiet[win_feedback.FEEDBACK_TOUCH_CONTACTVISUALIZATION] is False
    # 「保留」= 不下发设置，长按这类刻意操作的系统反馈照旧
    assert win_feedback.FEEDBACK_PEN_PRESSANDHOLD not in quiet

    off = win_feedback.policy_types("off")
    assert set(off) == set(win_feedback.ALL_TYPES)
    assert all(v is False for v in off.values())

    system = win_feedback.policy_types("system")
    assert set(system) == set(win_feedback.ALL_TYPES)
    assert all(v is None for v in system.values()), "None = 清除设置，回到系统默认"

    assert win_feedback.policy_types("乱写的策略") == quiet


def test_win_feedback_apply_is_safe(qapp):
    assert win_feedback.apply_policy(None, "quiet") == 0, "没有窗口时必须是空操作"
    holder = QWidget()
    try:
        win_feedback.apply_policy(holder, "quiet")   # 不抛异常即可（不支持则返回 0）
        win_feedback.restore(holder)
    finally:
        holder.deleteLater()

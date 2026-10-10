"""右侧 / 下侧网页式滑动条回归测试。

覆盖四件事（都是这次新增的行为，坏了用户会立刻感知）：

1. **显示策略**：内容装得下就隐藏（网页行为），装不下才出现，两条互不重叠、
   也不压在纸面上；
2. **手感一致**：滑块比例与 `_clamp_bounds()` 的两端严格对应 —— 拖到底就是夹紧的
   极限，手势平移之后再抓滑块也不会跳；
3. **输入不丢**：条上的滚轮原样转发、方向键 / PageUp / Home / End 能滚、
   按在条上不会画出笔画；
4. **观感可切换**：深浅底色各一套滑块配色，主题切换时跟着走。

事件全部直接调用处理函数（与 test_canvas_mouse_input 的做法一致），离屏可跑。
"""

from __future__ import annotations

import pymupdf as _fitz
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from src.core.settings import AppSettings
from src.ui.main_window import MainWindow
from src.ui.page_scrollbar import (BAR_THICKNESS, MIN_THUMB, PageScrollBar,
                                   scrollbar_palette)


@pytest.fixture(scope="module")
def qapp():
    _app = QApplication.instance() or QApplication([])
    yield _app


def _settings(tmp_path) -> AppSettings:
    return AppSettings(path=str(tmp_path / "settings.ini"))


def _make_pdf(path: str, npages: int = 20) -> str:
    d = _fitz.open()
    for i in range(npages):
        pg = d.new_page(width=595, height=842)
        pg.insert_text((72, 100), "page %d" % (i + 1))
    d.save(path)
    d.close()
    return path


def _resize(canvas, w: int, h: int) -> None:
    """显式派发 resizeEvent。

    隐藏控件上 Qt 会把 resize 事件压到 show 之后再发，离屏测试拿不到 —— 直接按
    Qt 的做法补一次；走的仍是同一条代码路径（resizeEvent → 夹紧 → 同步滑动条）。
    """
    size = QSize(w, h)
    canvas.resize(size)
    canvas.resizeEvent(QResizeEvent(size, size))


def _mouse(widget, etype, pos: QPointF) -> None:
    buttons = (Qt.MouseButton.LeftButton
               if etype != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton)
    ev = QMouseEvent(etype, pos, QPointF(widget.mapToGlobal(pos.toPoint())),
                     Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
    if etype == QEvent.Type.MouseButtonPress:
        widget.mousePressEvent(ev)
    elif etype == QEvent.Type.MouseMove:
        widget.mouseMoveEvent(ev)
    else:
        widget.mouseReleaseEvent(ev)


def _drag_bar(bar, along: float) -> None:
    """抓住滑块，拖到轨道的 `along` 位置（竖直= y，水平= x）。"""
    center = bar.thumb_rect().center()
    end = (QPointF(center.x(), along) if bar.vertical()
           else QPointF(along, center.y()))
    _mouse(bar, QEvent.Type.MouseButtonPress, QPointF(center))
    _mouse(bar, QEvent.Type.MouseMove, end)
    _mouse(bar, QEvent.Type.MouseButtonRelease, end)


def _wheel(widget, dy: int = 120) -> None:
    ev = QWheelEvent(QPointF(6, 200), QPointF(6, 200), QPoint(0, 0), QPoint(0, dy),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.ScrollUpdate, False)
    widget.wheelEvent(ev)


def _key(canvas, key) -> None:
    canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key,
                                   Qt.KeyboardModifier.NoModifier))


def _close(w) -> None:
    """关窗时不弹「是否保存」（批注是脏的），否则会卡在模态框上。"""
    try:
        for t in list(w._tabs):
            t.dirty = False
        w._confirm_save = lambda *a, **k: True
        w._close_current_pdf()
        w.close()
    except Exception:
        pass


@pytest.fixture
def note_win(qapp, tmp_path):
    """一张空白手写笔记：滚动条行为靠缩放置造（不需要 PDF）。"""
    w = MainWindow(settings=_settings(tmp_path), restore_session=False,
                   restore_geometry=False)
    _resize(w.canvas, 900, 700)
    yield w
    _close(w)


@pytest.fixture
def pdf_win(qapp, tmp_path):
    """20 页 PDF：连续阅读下纵向一定会出现滑动条。"""
    pdf = _make_pdf(str(tmp_path / "s.pdf"), 20)
    w = MainWindow(settings=_settings(tmp_path), restore_session=False,
                   restore_geometry=False)
    _resize(w.canvas, 700, 600)
    w.open_pdf(pdf)
    yield w
    _close(w)


# ===================================================================== #
# 一、滑动条自身：几何 / 交互 / 配色
# ===================================================================== #
def test_thumb_geometry_follows_range(qapp):
    bar = PageScrollBar(Qt.Orientation.Vertical)
    bar.resize(int(BAR_THICKNESS), 400)
    bar.set_range(0.0, 400.0)                       # 内容装得下
    assert bar.is_scrollable() is False
    assert bar.visible_fraction() == pytest.approx(1.0)
    assert bar.thumb_rect().height() == pytest.approx(bar.track_rect().height())

    bar.set_range(1200.0, 400.0)                    # 内容三倍于视口
    assert bar.is_scrollable() is True
    assert bar.visible_fraction() == pytest.approx(0.25)
    assert bar.thumb_rect().height() == pytest.approx(bar.track_rect().height() * 0.25)
    bar.set_value(1.0)
    assert bar.thumb_rect().bottom() == pytest.approx(bar.track_rect().bottom())
    bar.set_value(0.0)
    assert bar.thumb_rect().top() == pytest.approx(bar.track_rect().top())


def test_thumb_never_shrinks_below_minimum(qapp):
    bar = PageScrollBar(Qt.Orientation.Horizontal)
    bar.resize(400, int(BAR_THICKNESS))
    bar.set_range(1e6, 400.0)
    assert bar.thumb_rect().width() == pytest.approx(MIN_THUMB)
    assert bar.thumb_rect().height() <= BAR_THICKNESS


def test_drag_to_both_ends(qapp):
    bar = PageScrollBar(Qt.Orientation.Vertical)
    bar.resize(int(BAR_THICKNESS), 400)
    bar.set_range(1200.0, 400.0)
    seen = []
    bar.value_changed.connect(seen.append)
    _drag_bar(bar, float(bar.height()))
    assert bar.value() == pytest.approx(1.0)
    assert seen and seen[-1] == pytest.approx(1.0)
    _drag_bar(bar, 0.0)
    assert bar.value() == pytest.approx(0.0)
    assert seen[-1] == pytest.approx(0.0)


def test_track_click_steps_one_screen_then_follows_cursor(qapp):
    bar = PageScrollBar(Qt.Orientation.Vertical)
    bar.resize(int(BAR_THICKNESS), 400)
    bar.set_range(1200.0, 400.0)
    bar.set_value(0.5)
    before = bar.value()
    step = min(0.9, max(0.08, bar.visible_fraction() * 0.9))
    thumb_x = bar.thumb_rect().center().x()
    _mouse(bar, QEvent.Type.MouseButtonPress,
           QPointF(thumb_x, bar.thumb_rect().top() - 20.0))
    assert bar.value() == pytest.approx(before - step, abs=1e-6)
    # 点完轨道直接进入拖动：继续移动要跟手，而不是「点一下翻一屏就没了」
    _mouse(bar, QEvent.Type.MouseMove, QPointF(thumb_x, float(bar.height())))
    assert bar.value() == pytest.approx(1.0)
    _mouse(bar, QEvent.Type.MouseButtonRelease, QPointF(thumb_x, float(bar.height())))


def test_wheel_over_bar_is_forwarded(qapp):
    bar = PageScrollBar(Qt.Orientation.Horizontal)
    bar.resize(400, int(BAR_THICKNESS))
    bar.set_range(800.0, 400.0)
    got = []
    bar.wheel_scrolled.connect(got.append)
    _wheel(bar, 120)
    assert got == [120.0], "滚动条不能把滚轮吃掉"


def test_palette_uses_canvas_background_lightness(qapp):
    light = scrollbar_palette("#f7f7f8")
    dark = scrollbar_palette("#161616")
    assert light["thumb"].lightness() < dark["thumb"].lightness()
    for palette in (light, dark):
        assert palette["thumb"].alpha() > 0
        assert palette["track"].alpha() < palette["thumb"].alpha(), "轨道比滑块更淡"


# ===================================================================== #
# 二、笔记本（单页）画布上的滑动条
# ===================================================================== #
def test_page_fits_hides_bars_and_zoom_shows_them(note_win):
    c = note_win.canvas
    assert c.scrollbar_state()[:2] == (False, False), "整页可见时不该出现滑动条"

    c.zoom_to(3.0)
    assert c.scrollbar_state()[:2] == (True, True), "放大后两个方向都装不下"
    vbar, hbar = c._vbar, c._hbar
    assert vbar.thumb_rect().height() < vbar.track_rect().height() - 1.0
    assert hbar.thumb_rect().width() < hbar.track_rect().width() - 1.0
    assert vbar.geometry().right() == c.width() - 1, "竖条贴在右侧"
    assert hbar.geometry().bottom() == c.height() - 1, "横条贴在下侧"
    assert vbar.geometry().bottom() < hbar.geometry().top() + 1, "右下角两条不能重叠"

    c.fit_to_view()
    assert c.scrollbar_state()[:2] == (False, False), "缩回整页可见后要收起来"


def test_bar_reserves_space_so_page_is_not_covered(note_win):
    c = note_win.canvas
    c.zoom_to(3.0)
    vw, vh = c._viewport_size()
    assert c._vbar.geometry().left() >= vw - 1
    assert c._hbar.geometry().top() >= vh - 1
    assert vw <= c.width() - BAR_THICKNESS + 1
    assert vh <= c.height() - BAR_THICKNESS + 1


def test_drag_bars_scroll_page_and_never_draw(note_win):
    c = note_win.canvas
    c.zoom_to(3.0)
    min_x, max_x, min_y, max_y = c._clamp_bounds()

    _drag_bar(c._vbar, float(c._vbar.height()))
    assert c.offset.y() == pytest.approx(min_y, abs=0.5), "拖到底 = 夹紧的下界"
    assert c._vbar.value() == pytest.approx(1.0)
    _drag_bar(c._vbar, 0.0)
    assert c.offset.y() == pytest.approx(max_y, abs=0.5)
    assert c._vbar.value() == pytest.approx(0.0)

    _drag_bar(c._hbar, float(c._hbar.width()))
    assert c.offset.x() == pytest.approx(min_x, abs=0.5)
    _drag_bar(c._hbar, 0.0)
    assert c.offset.x() == pytest.approx(max_x, abs=0.5)

    assert c.page_strokes == [], "点在滑动条上不能画出笔画"
    assert c.drawing is False


def test_grab_after_pan_does_not_jump(note_win):
    """先手势平移到底，再抓滑块：位置必须连续（两边共用同一套夹紧边界）。"""
    c = note_win.canvas
    c.zoom_to(3.0)
    c.pan_by(0.0, -99999.0)
    assert c.offset.y() == pytest.approx(c._clamp_bounds()[2], abs=0.5)
    assert c._vbar.value() == pytest.approx(1.0)

    before = c.offset.y()
    _mouse(c._vbar, QEvent.Type.MouseButtonPress,
           QPointF(c._vbar.thumb_rect().center()))
    assert c.offset.y() == pytest.approx(before, abs=0.5), "抓住滑块的一瞬间不该跳"
    _mouse(c._vbar, QEvent.Type.MouseButtonRelease,
           QPointF(c._vbar.thumb_rect().center()))


def test_wheel_over_bar_and_arrow_keys_scroll_canvas(note_win):
    c = note_win.canvas
    c.zoom_to(3.0)
    c.set_scroll_fraction(True, 0.5)
    mid = c.offset.y()

    _wheel(c._vbar, 120)
    assert c.offset.y() > mid, "向上滚 = 看上方内容"

    c.set_scroll_fraction(True, 0.5)
    _key(c, Qt.Key.Key_Down)
    assert c.offset.y() < mid, "↓ = 看到下方内容"
    assert c.offset.y() == pytest.approx(mid - 48.0, abs=0.5), "↑/↓ 是一次 48px 的微调"
    _key(c, Qt.Key.Key_Up)
    assert c.offset.y() == pytest.approx(mid, abs=0.5)
    assert c.page_strokes == [], "键盘滚动不能顺手画一笔"


# ===================================================================== #
# 三、连续阅读（PDF）上的滑动条
# ===================================================================== #
def test_continuous_scrollbar_tracks_bounds_and_current_page(pdf_win):
    w = pdf_win
    c = w.canvas
    w._set_read_mode("continuous")
    assert c.scrollbar_state()[0] is True, "20 页连续阅读一定装不下"
    assert c._vbar.value() == pytest.approx(0.0), "初始在文档顶部"
    assert c.pdf_index == 0

    c.set_scroll_fraction(True, 1.0)
    min_x, max_x, min_y, max_y = c._clamp_bounds()
    assert c.offset.y() == pytest.approx(min_y, abs=0.5)
    assert c._vbar.value() == pytest.approx(1.0)
    assert c.pdf_index == c.total_pages - 1, "滚到底应看到最后一页"

    c.set_scroll_fraction(True, 0.5)
    assert c.offset.y() == pytest.approx(max_y - 0.5 * (max_y - min_y), abs=0.5)

    c.pan_by(0.0, -400.0)
    expected = (max_y - c.offset.y()) / (max_y - min_y)
    assert c._vbar.value() == pytest.approx(expected, abs=1e-3), "手势平移后滑块要跟上"


def test_page_jump_keeps_scrollbar_in_sync(pdf_win):
    """PgDn / Home / End 那套键盘翻页由主窗口的 QShortcut 负责（优先级高于画布）。

    翻页是「外部改 offset」的典型路径：跳完之后滑块必须立刻跟上，不能落在原地 ——
    否则用户会看到「明明翻到最后一页了，滑块还在上面」。
    """
    w = pdf_win
    c = w.canvas
    w._set_read_mode("continuous")

    w.last_page()
    assert c._vbar.value() > 0.9, "跳到最后一页后滑块应该在底部附近"
    w.first_page()
    assert c._vbar.value() == pytest.approx(0.0)
    w.next_page()
    w.next_page()
    min_x, max_x, min_y, max_y = c._clamp_bounds()
    assert c._vbar.value() == pytest.approx((max_y - c.offset.y()) / (max_y - min_y), abs=1e-3)
    assert c.pdf_index == 2


def test_fit_width_leaves_room_for_vertical_bar(pdf_win):
    w = pdf_win
    c = w.canvas
    w._set_read_mode("continuous")
    c.fit_width()
    cw = c.display_size_pt(0)[0] * c.scale
    assert c.scrollbar_state()[1] is False, "适应宽度后横向不该有滑动条"
    assert c._vbar.is_scrollable() is True
    vw, _ = c._viewport_size()
    assert cw <= vw, "页面宽度不能压到滑动条底下"


def test_each_tab_has_own_bars_and_theme_switch_repaints_them(pdf_win):
    w = pdf_win
    w.new_tab_note()
    canvases = [t.canvas for t in w._tabs if t.canvas is not None]
    assert len(canvases) >= 2
    assert canvases[0]._vbar is not canvases[1]._vbar, "滑动条跟着标签的画布走"
    before = canvases[0]._scroll_palette["thumb"].getRgb()
    w._toggle_theme()
    after = canvases[0]._scroll_palette["thumb"].getRgb()
    assert before != after, "换主题（= 换画布底色）后滑块配色要跟着变"
    for c in canvases:
        assert c._vbar._palette is c._scroll_palette
        assert c._hbar._palette is c._scroll_palette

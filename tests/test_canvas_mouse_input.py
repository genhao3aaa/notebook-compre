"""画布输入回归（v5.1 鼠标批注失效 + 相关的输入设备兼容性问题）。

bug 背景
--------
v5.1 之后「鼠标在 PDF 上完全没法批注，手写笔正常」。根因是画布拆分时把调试日志
改成结构化日志，顺手写了 `log_debug(..., button=int(e.button()))`：PySide6 6.11 起
`Qt.MouseButton` 是 pybind11 的 flag（不是 int 子类），`int()` 直接抛 `TypeError`，
而它抛在 `mousePressEvent` 里面 —— 鼠标按下永远走不完，笔走另一条通道所以没事。

同一轮排查还发现三处「只影响鼠标 / 只影响 PDF」的缺陷，一并锁在这里：
* 鼠标悬停（不按键）不刷新 → 橡皮圈提示与坐标显示失效；
* 连续阅读在**非当前页**起笔会先切页，`_bind_page()` 把设备标记清掉 → 只提交一个点；
* 手写笔 Release 丢失时 `_active_device` 卡在 "tablet" → 鼠标永久失效。

测试全部直接调用事件处理函数（不发真实系统事件），离屏也能跑。
"""

from __future__ import annotations

import time

import pymupdf as _fitz
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (QFocusEvent, QInputDevice, QMouseEvent, QPointingDevice,
                           QTabletEvent, QWheelEvent)
from PySide6.QtWidgets import QApplication

from src.core.settings import AppSettings
from src.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    _app = QApplication.instance() or QApplication([])
    yield _app


def _settings(tmp_path) -> AppSettings:
    return AppSettings(path=str(tmp_path / "settings.ini"))


def _make_pdf(path: str, npages: int = 6) -> str:
    d = _fitz.open()
    for i in range(npages):
        pg = d.new_page(width=595, height=842)
        pg.insert_text((72, 100), "page %d" % (i + 1))
    d.save(path)
    d.close()
    return path


@pytest.fixture
def win(qapp, tmp_path):
    pdf = _make_pdf(str(tmp_path / "m.pdf"))
    w = MainWindow(settings=_settings(tmp_path), restore_session=False,
                   restore_geometry=False)
    w.resize(1000, 800)
    w.canvas.resize(900, 700)
    w.open_pdf(pdf)
    qapp.processEvents()
    yield w
    # 与其它用例一致：关窗时不弹「是否保存」（批注是脏的），否则会卡在模态框上
    try:
        for t in list(w._tabs):
            t.dirty = False
        w._confirm_save = lambda *a, **k: True
        w._close_current_pdf()
        w.close()
    except Exception:
        pass


# --------------------------------------------------------------------- #
# 事件注入工具
# --------------------------------------------------------------------- #
def _send(canvas, etype, pos: QPointF) -> None:
    buttons = Qt.LeftButton if etype != QEvent.Type.MouseButtonRelease else Qt.NoButton
    ev = QMouseEvent(etype, pos, QPointF(canvas.mapToGlobal(pos.toPoint())),
                     Qt.LeftButton, buttons, Qt.NoModifier)
    if etype == QEvent.Type.MouseButtonPress:
        canvas.mousePressEvent(ev)
    elif etype == QEvent.Type.MouseMove:
        canvas.mouseMoveEvent(ev)
    else:
        canvas.mouseReleaseEvent(ev)


def _drag(canvas, x: float, y: float, n: int = 10, step: float = 6.0) -> None:
    _send(canvas, QEvent.Type.MouseButtonPress, QPointF(x, y))
    for i in range(1, n + 1):
        _send(canvas, QEvent.Type.MouseMove, QPointF(x + i * step, y + i * step))
    _send(canvas, QEvent.Type.MouseButtonRelease, QPointF(x + n * step, y + n * step))


def _page_center(canvas) -> tuple[float, float]:
    dw, dh = canvas.display_size_pt()
    return (canvas.offset.x() + dw * canvas.scale / 2.0,
            canvas.offset.y() + dh * canvas.scale / 2.0)


def _send_tablet(canvas, etype, pos: QPointF, pressure: float = 0.6,
                 ptype=None) -> None:
    """合成一个 QTabletEvent（离屏也能跑，用来锁住「手写笔书写不能退化」）。"""
    ptype = ptype or QPointingDevice.PointerType.Pen
    dev = QPointingDevice("stylus", 1, QInputDevice.DeviceType.Stylus, ptype,
                          QInputDevice.Capabilities(), 1, 1)
    ev = QTabletEvent(etype, dev, pos,
                      QPointF(canvas.mapToGlobal(pos.toPoint())), pressure,
                      0.0, 0.0, 0.0, 0.0, 0.0, Qt.NoModifier,
                      Qt.LeftButton, Qt.LeftButton)
    canvas.tabletEvent(ev)


def _pen_drag(canvas, x: float, y: float, n: int = 10, step: float = 6.0,
              pressure: float = 0.6, ptype=None) -> None:
    _send_tablet(canvas, QEvent.Type.TabletPress, QPointF(x, y), pressure, ptype)
    for i in range(1, n + 1):
        _send_tablet(canvas, QEvent.Type.TabletMove, QPointF(x + i * step, y + i * step),
                     pressure, ptype)
    _send_tablet(canvas, QEvent.Type.TabletRelease, QPointF(x + n * step, y + n * step),
                 0.0, ptype)


# ===================================================================== #
# 一、鼠标批注（回归主目标）
# ===================================================================== #
def test_mouse_can_annotate_pdf(win):
    """鼠标按下-移动-抬起必须产生一条完整笔画。"""
    c = win.canvas
    _drag(c, *_page_center(c))
    assert len(c.page_strokes) == 1
    assert len(c.page_strokes[0].points) >= 2      # 不是只有一个点
    assert c.undo_mgr.can_undo()


def test_mouse_annotate_lands_on_current_annot_page(win):
    """批注要写进「当前标签 + 当前页」的笔画表（而不是别的副本）。"""
    c = win.canvas
    _drag(c, *_page_center(c))
    assert c.page_strokes is win.annot_doc.pages[c.pdf_index].strokes
    win.next_page()
    assert len(c.page_strokes) == 0                # 第二页干净
    _drag(c, *_page_center(c))
    assert len(win.annot_doc.pages[1].strokes) == 1


def test_mouse_can_annotate_note(win):
    """笔记页同样要能用鼠标写（同一套输入路由）。"""
    win.new_note()
    c = win.canvas
    _drag(c, *_page_center(c))
    assert len(win.note_doc.pages[0].strokes) == 1


def test_mouse_works_after_rotation(win, qapp):
    """旋转 90° 后命中判定与坐标变换仍要正确。"""
    c = win.canvas
    c.set_rotation(90)
    qapp.processEvents()
    _drag(c, *_page_center(c))
    assert c.rotation == 90
    assert len(c.page_strokes) == 1
    assert len(c.page_strokes[0].points) >= 2


# ===================================================================== #
# 二、悬停与滚轮（输入设备兼容性）
# ===================================================================== #
def test_mouse_hover_updates_without_button(win):
    """不按键的移动也要更新悬停状态（橡皮圈提示 / 状态栏坐标靠它）。"""
    c = win.canvas
    c._hover = False
    x, y = _page_center(c)
    _send(c, QEvent.Type.MouseMove, QPointF(x, y))
    assert c._hover is True
    assert c._hover_css == QPointF(x, y)


def test_trackpad_pixel_delta_pans(win):
    """高精度触摸板只给 pixelDelta（angleDelta 为 0）时也要能滚动。"""
    c = win.canvas
    before = QPointF(c.offset)
    ev = QWheelEvent(QPointF(400, 400), QPointF(400, 400), QPoint(0, 40),
                     QPoint(0, 0), Qt.NoButton, Qt.NoModifier,
                     Qt.ScrollPhase.ScrollUpdate, False)
    c.wheelEvent(ev)
    assert c.offset.y() != before.y()


# ===================================================================== #
# 三、连续阅读：在「非当前页」上起笔
# ===================================================================== #
def test_continuous_stroke_on_other_page_is_not_a_dot(win, qapp):
    """在别的页上落笔会先切页；切页不能把设备标记清掉（否则只剩一个点）。"""
    c = win.canvas
    win._set_read_mode("continuous")
    c.resize(900, 700)
    qapp.processEvents()
    c._ensure_layout()
    assert c.pdf_index == 0

    x = c.offset.x() + 150
    y = c.offset.y() + c._layout_tops[1] + 150
    assert c._inside_page(QPointF(x, y)) is True
    _drag(c, x, y)

    qapp.processEvents()
    assert c.pdf_index == 1                        # 已切到第 2 页
    page1 = win.annot_doc.pages[1]
    assert len(page1.strokes) == 1
    assert len(page1.strokes[0].points) >= 2, "移动过程被丢掉（只提交了一个点）"


# ===================================================================== #
# 四、双通道互斥与自愈
# ===================================================================== #
def test_tablet_channel_busy_ignores_synthesized_mouse(win):
    """笔正在书写时，系统补发的鼠标事件必须被忽略（否则一笔画成两半）。"""
    c = win.canvas
    c._active_device = "tablet"
    c._using_tablet = True
    c.drawing = True
    c._tablet_last_ts = time.monotonic()
    n = len(c.page_strokes)
    _drag(c, *_page_center(c))
    assert len(c.page_strokes) == n
    assert c._active_device == "tablet", "笔通道不能被鼠标事件抢走"
    assert c.drawing is True, "正在写的笔画不能被鼠标松开截断"


def test_stale_tablet_channel_self_heals(win, qapp):
    """笔的 Release 丢了（切窗口 / 笔离开感应区）不能让鼠标永久失效。"""
    c = win.canvas
    c._active_device = "tablet"
    c._using_tablet = True
    c.drawing = True
    c._tablet_last_ts = time.monotonic() - 60.0     # 一分钟没有笔事件 = 僵死
    _drag(c, *_page_center(c))
    assert len(c.page_strokes) == 1, "鼠标应能自愈并继续批注"
    assert c._active_device is None
    assert c.drawing is False


def test_stale_tablet_channel_self_heals_on_release(win):
    """僵死状态下收到鼠标松开也要能收尾（不然 `_active_device` 一直卡着）。"""
    c = win.canvas
    c._active_device = "tablet"
    c._using_tablet = True
    c.drawing = True
    c._tablet_last_ts = time.monotonic() - 60.0
    _send(c, QEvent.Type.MouseButtonRelease, QPointF(*_page_center(c)))
    assert c._active_device is None
    assert c._using_tablet is False
    assert c.drawing is False


def test_tablet_pointer_types_are_classified(win):
    """指针类型判定：真实笔进 tablet 通道，鼠标/未知指针回退鼠标通道。"""
    from src.ui.canvas_input import (_NON_PEN_POINTERS, _mouse_event_from_pen,
                                     _tablet_pointer_name)

    assert "Mouse" in _NON_PEN_POINTERS
    assert "Pen" not in _NON_PEN_POINTERS
    assert "Eraser" not in _NON_PEN_POINTERS

    class _FakeTablet:
        def __init__(self, pt):
            self._pt = pt

        def pointerType(self):
            return self._pt

    from PySide6.QtGui import QPointingDevice

    assert _tablet_pointer_name(_FakeTablet(QPointingDevice.PointerType.Pen)) == "Pen"
    assert (_tablet_pointer_name(_FakeTablet(QPointingDevice.PointerType.Eraser))
            == "Eraser")

    class _FakeMouse:
        def source(self):
            return Qt.MouseEventSource.MouseEventSynthesizedByQt

    assert _mouse_event_from_pen(_FakeMouse()) is True

    real = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(0, 0), QPointF(0, 0),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    assert _mouse_event_from_pen(real) is False


def test_non_pen_tablet_event_does_not_keep_channel_alive(win):
    """被忽略的「鼠标类」tablet 事件不能刷新笔通道心跳。

    否则笔的 Release 丢了之后，这类事件会不停给僵死的笔通道「续命」，
    鼠标就永远拿不回控制权。
    """
    c = win.canvas
    c._active_device = "tablet"
    c._using_tablet = True
    c.drawing = True
    stale = time.monotonic() - 60.0
    c._tablet_last_ts = stale

    class _Ptr:
        name = "Mouse"

    class _FakeTablet:
        def pointerType(self):
            return _Ptr()

        def type(self):
            return QEvent.Type.TabletMove

        def ignore(self):
            return None

        def accept(self):
            return None

    c.tabletEvent(_FakeTablet())
    assert c._tablet_last_ts == stale, "非笔事件不该续命"

    _drag(c, *_page_center(c))
    assert len(c.page_strokes) == 1, "鼠标应能自愈"


def test_focus_loss_does_not_leak_pan_state(win):
    """失焦后 `_panning` 不能卡住（卡住会让每次落笔都被当成平移而失效）。"""
    c = win.canvas
    c._panning = True
    c.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert c._panning is False
    n = len(c.page_strokes)
    _drag(c, *_page_center(c))
    assert len(c.page_strokes) == n + 1


# ===================================================================== #
# 五、工具切换
# ===================================================================== #
def test_tool_switch_keeps_mouse_usable(win, qapp):
    """钢笔 → 橡皮 → 荧光笔 来回切换后，鼠标仍要能正常落笔/擦除。"""
    c = win.canvas
    _drag(c, *_page_center(c))
    assert len(c.page_strokes) == 1

    c.set_tool("eraser")
    qapp.processEvents()
    x, y = _page_center(c)
    _drag(c, x, y, n=6)
    assert len(c.page_strokes) == 0, "橡皮应擦掉刚画的那一条"
    assert c.undo_mgr.can_undo()

    c.set_tool("highlighter")
    qapp.processEvents()
    _drag(c, x + 40, y + 40)
    assert len(c.page_strokes) == 1
    assert c.page_strokes[0].points

    c.set_tool("pen")
    qapp.processEvents()
    _drag(c, x - 40, y + 80)
    assert len(c.page_strokes) == 2


# ===================================================================== #
# 六、手写笔通道不能因为上面的修复而退化
# ===================================================================== #
def test_pen_still_writes_with_pressure(win):
    """手写笔照旧能写，并且压感不丢。"""
    c = win.canvas
    _pen_drag(c, *_page_center(c), pressure=0.6)
    assert len(c.page_strokes) == 1
    stroke = c.page_strokes[0]
    assert len(stroke.points) >= 2
    assert stroke.points[0][2] == pytest.approx(0.6, abs=0.01)
    assert c._active_device is None            # 抬笔后交还控制权
    assert c.drawing is False


def test_pen_and_mouse_alternate(win):
    """笔写一笔、鼠标写一笔，两条笔画都要落在同一页且互不干扰。"""
    c = win.canvas
    x, y = _page_center(c)
    _pen_drag(c, x - 60, y - 60)
    assert len(c.page_strokes) == 1
    _drag(c, x + 60, y + 60)
    assert len(c.page_strokes) == 2
    _pen_drag(c, x - 120, y + 40)
    assert len(c.page_strokes) == 3
    assert c._active_device is None


def test_pen_tail_eraser_overrides_tool_then_restores(win):
    """笔尾橡皮：临时切到橡皮，抬笔后还原原来的工具。"""
    c = win.canvas
    c.set_tool("pen")
    x, y = _page_center(c)
    _drag(c, x, y)                             # 先用鼠标画一条，待会儿擦掉
    assert len(c.page_strokes) == 1

    _pen_drag(c, x, y, n=6, ptype=QPointingDevice.PointerType.Eraser)
    assert c.current_tool_id() == "pen", "笔尾橡皮结束后必须还原工具"
    assert len(c.page_strokes) == 0, "笔尾橡皮应擦掉那条笔画"

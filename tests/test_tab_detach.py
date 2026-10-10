"""浏览器式「把标签拖出来变成独立窗口」的回归测试。

覆盖：拖出后文档不复制（同一份画布/笔画/撤销栈搬过去）、源窗口不会空掉、
最后一个标签不许拖出、拖到另一个窗口的标签栏上会合并过去、搬标签不弹保存询问、
会话要记录所有窗口的标签、多窗口下快捷键必须是 WindowShortcut。
"""

from __future__ import annotations

import pytest
import pymupdf as fitz
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from src.core.settings import AppSettings
from src.ui import main_window as mw


@pytest.fixture(scope="module")
def qapp():
    _app = QApplication.instance() or QApplication([])
    yield _app


@pytest.fixture
def opened(qapp, tmp_path):
    """创建窗口的工厂，测试结束统一关闭 —— 包括测试中途由 detach_tab 新建的窗口。"""
    made = []
    preexisting = list(mw._OPEN_WINDOWS)
    ini = str(tmp_path / "s.ini")

    def factory(**kw):
        w = mw.MainWindow(settings=AppSettings(path=ini), **kw)
        w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        w.show()
        QApplication.processEvents()
        made.append(w)
        return w

    yield factory
    # 注意：窗口里可能留着「画过一笔」的脏标签，直接 close() 会弹出模态的
    # 「是否保存」对话框 —— 测试里没人点它，整个测试进程就卡死在这里
    # （曾经真的卡了十几分钟）。测试收尾统一走「不询问」路径。
    for w in list(mw._OPEN_WINDOWS):
        if w not in preexisting:
            _force_close(w)
    QApplication.processEvents()
    for w in list(mw._OPEN_WINDOWS):
        if w not in preexisting:
            mw._OPEN_WINDOWS.remove(w)


def _force_close(win) -> None:
    """关窗口但不弹保存询问（测试专用）。"""
    try:
        for t in list(win._tabs):
            t.dirty = False
        win._confirm_save = lambda *_a, **_k: True
        win.close()
    except Exception:
        pass


def make_pdf(tmp_path, name="demo.pdf", pages=3) -> str:
    path = str(tmp_path / name)
    d = fitz.open()
    for i in range(pages):
        pg = d.new_page()
        pg.insert_text((72, 100), f"Chapter {i + 1} sample text")
    d.save(path)
    d.close()
    return path


def draw(canvas, pts) -> None:
    css = [canvas.page_to_css(x, y) for x, y in pts]
    canvas._pointer_down(QPointF(css[0].x(), css[0].y()), 1.0)
    for p in css[1:]:
        canvas._pointer_move(QPointF(p.x(), p.y()), 1.0)
    canvas._pointer_up()


def tab_bar_rect_center(win) -> QPoint:
    bar = win.tab_widget.tabBar()
    return bar.mapToGlobal(QPoint(max(10, bar.width() // 2), bar.height() // 2))


def separate(win, x: int = 40, y: int = 40) -> None:
    """把窗口挪到互不重叠的位置：否则「鼠标下面是哪个窗口」本身就有歧义。"""
    win.move(x, y)
    QApplication.processEvents()


# ---------------------------------------------------------------------- #
# 拖出成新窗口
# ---------------------------------------------------------------------- #
def test_detach_moves_the_very_same_document(opened, tmp_path):
    win = opened()
    pdf_tab = win.open_pdf(make_pdf(tmp_path))
    win.new_tab_note()
    QApplication.processEvents()
    draw(pdf_tab.canvas, [(100, 100), (180, 160), (260, 200)])
    strokes = len(pdf_tab.canvas.page_strokes)
    assert strokes >= 1

    win2 = win.detach_tab(pdf_tab, QPoint(500, 60))
    QApplication.processEvents()

    assert win2 is not None and win2 is not win
    assert pdf_tab in win2._tabs, "被拖走的标签应当出现在新窗口里"
    assert pdf_tab not in win._tabs, "源窗口不应再持有它"
    assert win2.tab_widget.widget(0) is pdf_tab.canvas, "画布要真的搬到新窗口（会重新挂父）"
    assert len(pdf_tab.canvas.page_strokes) == strokes, "笔画必须原样保留（不是重新打开文件）"
    assert len(win2._tabs) == 1, "新窗口开局的空白占位标签应当被丢掉"
    assert len(win._tabs) >= 1, "源窗口不能空着"
    assert win2 in mw._OPEN_WINDOWS and win in mw._OPEN_WINDOWS


def test_last_tab_cannot_be_detached(opened):
    win = opened()
    only = win.tab
    assert len(win._tabs) == 1
    before = len(mw._OPEN_WINDOWS)
    assert win.detach_tab(only, QPoint(500, 60)) is None
    assert win._tabs == [only]
    assert len(mw._OPEN_WINDOWS) == before, "不该凭空多出一个窗口"


def test_detach_does_not_prompt_to_save(opened, tmp_path, monkeypatch):
    win = opened()
    tab = win.open_pdf(make_pdf(tmp_path))
    win.new_tab_note()
    win._switch_to_tab(tab)
    win.mark_dirty()
    assert tab.dirty is True

    def boom(*_a, **_k):
        raise AssertionError("搬标签不该问「是否保存」——它不是关闭")

    monkeypatch.setattr(win, "_confirm_save", boom)
    win2 = win.detach_tab(tab, QPoint(500, 60))
    assert win2 is not None and tab in win2._tabs
    assert tab.dirty is True, "搬过去之后仍然是未保存状态"


def test_moved_tab_talks_to_its_new_window(opened, tmp_path):
    """画布的信号与「切页」回调必须改指向新窗口，否则会去叫已经搬空的旧窗口。"""
    a = opened()
    tab = a.open_pdf(make_pdf(tmp_path, pages=4))
    a.new_tab_note()
    QApplication.processEvents()

    b = a.detach_tab(tab, QPoint(700, 80))
    QApplication.processEvents()

    calls = []
    b._on_canvas_page_request = lambda t, idx: calls.append(("new", idx))
    a._on_canvas_page_request = lambda t, idx: calls.append(("old", idx))
    assert tab.canvas.on_page_request is not None
    tab.canvas.on_page_request(2)
    assert calls == [("new", 2)], "回调必须指向新窗口"

    zoomed = []
    b._on_canvas_zoom = lambda c, v: zoomed.append(v)
    tab.canvas.zoom_changed.emit(1.5)
    assert zoomed == [1.5], "画布信号应当接到新窗口（旧的已断开）"


def test_source_window_keeps_its_other_tabs(opened, tmp_path):
    win = opened()
    keep = win.tab
    a = win.new_tab_note()
    b = win.new_tab_note()
    assert len(win._tabs) == 3
    win.detach_tab(a, QPoint(500, 60))
    QApplication.processEvents()
    assert keep in win._tabs and b in win._tabs
    assert a not in win._tabs
    assert len(win._tabs) == 2, "不该额外补一个空白标签"


# ---------------------------------------------------------------------- #
# 拖到另一个窗口 → 合并
# ---------------------------------------------------------------------- #
def test_drop_on_another_window_merges_instead_of_new_window(opened, tmp_path):
    a = opened()
    b = opened()
    separate(a, 40, 40)
    separate(b, 900, 300)
    tab = a.open_pdf(make_pdf(tmp_path))
    a.new_tab_note()
    QApplication.processEvents()

    before = len(mw._OPEN_WINDOWS)
    pos = tab_bar_rect_center(b)
    target = a.detach_tab(tab, pos)
    QApplication.processEvents()

    assert target is b, "松手落在另一个窗口的标签栏上，应当并进那个窗口"
    assert tab in b._tabs and tab not in a._tabs
    assert len(mw._OPEN_WINDOWS) == before, "不该再新建窗口"


def test_window_at_finds_tab_bar(opened):
    a = opened()
    b = opened()
    separate(a, 40, 40)
    separate(b, 900, 300)
    inside = tab_bar_rect_center(b)
    assert mw.MainWindow._window_at(inside) is b
    assert mw.MainWindow._window_at(QPoint(-5000, -5000)) is None


# ---------------------------------------------------------------------- #
# 拖动手势本身
# ---------------------------------------------------------------------- #
def _mouse(kind, pos: QPoint, button=Qt.MouseButton.LeftButton,
           buttons=Qt.MouseButton.LeftButton) -> QMouseEvent:
    return QMouseEvent(kind, QPointF(pos), QPointF(pos), button, buttons,
                       Qt.KeyboardModifier.NoModifier)


def test_dragging_a_tab_outside_emits_detach(opened, tmp_path):
    win = opened()
    win.open_pdf(make_pdf(tmp_path))
    win.new_tab_note()
    QApplication.processEvents()
    bar = win.tab_widget.tabBar()
    fired = []
    bar.detach_requested.connect(lambda: fired.append(True))

    start = QPoint(bar.tabRect(0).center().x(), bar.tabRect(0).center().y())
    outside = QPoint(start.x(), bar.height() + 60)
    bar.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, start))
    bar.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, outside))
    bar.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, outside,
                                 buttons=Qt.MouseButton.NoButton))
    assert fired, "拖到标签栏外面松手应当请求撕成独立窗口"


def test_dragging_inside_does_not_detach(opened, tmp_path):
    win = opened()
    win.open_pdf(make_pdf(tmp_path))
    win.new_tab_note()
    QApplication.processEvents()
    bar = win.tab_widget.tabBar()
    fired = []
    bar.detach_requested.connect(lambda: fired.append(True))

    r0, r1 = bar.tabRect(0).center(), bar.tabRect(1).center()
    bar.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPoint(r0.x(), r0.y())))
    bar.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPoint(r1.x(), r1.y())))
    bar.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, QPoint(r1.x(), r1.y()),
                                 buttons=Qt.MouseButton.NoButton))
    assert not fired, "在标签栏内部拖动只是重排顺序，不该撕出新窗口"


# ---------------------------------------------------------------------- #
# 多窗口的其它配套
# ---------------------------------------------------------------------- #
def test_shortcuts_are_window_scoped(opened):
    """多窗口下必须用 WindowShortcut：ApplicationShortcut 会被判定为冲突而全部失效。"""
    win = opened()
    assert win._shortcuts
    for sc in win._shortcuts:
        assert sc.context() == Qt.ShortcutContext.WindowShortcut


def test_session_records_tabs_from_every_window(opened, tmp_path):
    a = opened()
    b = opened()
    pdf1 = make_pdf(tmp_path, "one.pdf")
    pdf2 = make_pdf(tmp_path, "two.pdf")
    a.open_pdf(pdf1)
    b.open_pdf(pdf2)
    a._save_session()
    paths = {t["path"] for t in AppSettings(path=str(tmp_path / "s.ini")).open_tabs()}
    assert {pdf1, pdf2} <= paths, "会话必须涵盖所有窗口，否则重启会丢标签"


def test_detached_window_skips_session_and_geometry(opened, tmp_path):
    ini = str(tmp_path / "s.ini")
    AppSettings(path=ini).set_open_tabs([{"kind": "pdf", "path": make_pdf(tmp_path)}])
    AppSettings(path=ini).set_window_geometry(b"")   # 没有历史几何
    w = mw.MainWindow(settings=AppSettings(path=ini), restore_session=False,
                      restore_geometry=False)
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.show()
    try:
        assert len(w._tabs) == 1, "拖出来的窗口不该再去恢复会话里的标签"
        assert w._geo_restored is False
    finally:
        w.close()
        if w in mw._OPEN_WINDOWS:
            mw._OPEN_WINDOWS.remove(w)

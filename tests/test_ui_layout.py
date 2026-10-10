"""界面缩放 / 标签关闭按钮的回归测试（v5.2 前端改造）。

对应两个真实缺陷：

* **窗口最小宽度 1873px**：顶栏把 36 个控件硬排成一行（布局 minimumSize 1625px），
  再加固定 248px 侧边栏 —— 在 150% 缩放的 1920×1080 屏上（逻辑宽度只有 1280）
  窗口比屏幕还宽，右侧按钮必然被切掉；
* **每个标签都没有 ×**：`setTabsClosable(True)` 写在了 `setTabBar()` 之前，
  QTabWidget 把它记在「当时的」标签栏上，新装的标签栏拿不到关闭按钮。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTabBar, QToolButton

from src.core.settings import AppSettings
from src.ui.main_window import SIDEBAR_AUTO_HIDE_W, WINDOW_MIN_W
from src.ui.topbar import TopBar


@pytest.fixture(scope="module")
def qapp():
    _app = QApplication.instance() or QApplication([])
    yield _app


@pytest.fixture
def win(qapp, tmp_path):
    from src.ui.main_window import MainWindow

    w = MainWindow(settings=AppSettings(path=str(tmp_path / "s.ini")))
    # 用真实平台插件离屏显示：拿得到真实字体/图标，又不会弹到屏幕上
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.show()
    QApplication.processEvents()
    yield w
    # 关窗前清掉「未保存」标记：否则会弹出模态的保存询问，测试里没人点它就卡死
    for t in list(w._tabs):
        t.dirty = False
    w._confirm_save = lambda *_a, **_k: True
    w.close()


def resize(win, width: int, height: int = 700) -> None:
    win.resize(width, height)
    QApplication.processEvents()


# ---------------------------------------------------------------------- #
# 窗口尺寸
# ---------------------------------------------------------------------- #
def test_window_has_sane_minimum_size(win):
    assert win.minimumWidth() == WINDOW_MIN_W
    assert WINDOW_MIN_W <= 800, "最小宽度必须小到能放进 1080p@150% 的屏幕（逻辑宽 1280）"
    # 布局本身还能更窄 —— 说明最小宽度是我们主动设的，而不是被顶栏撑出来的
    assert win.minimumSizeHint().width() < WINDOW_MIN_W


def test_topbar_core_fits_inside_minimum_window(win):
    assert isinstance(win.topbar, TopBar)
    assert win.topbar.core_minimum_width() + 60 <= WINDOW_MIN_W, \
        "核心控件在最小窗口下也必须放得下"


def test_clamp_to_screen_never_exceeds_available(win):
    screen = win.screen() or QApplication.primaryScreen()
    avail = screen.availableGeometry()
    win.resize(avail.width() + 400, avail.height() + 400)
    win._clamp_to_screen()
    assert win.width() <= avail.width()
    assert win.height() <= avail.height()


# ---------------------------------------------------------------------- #
# 响应式顶栏
# ---------------------------------------------------------------------- #
def test_core_widgets_are_never_hidden(win):
    """核心组（文件 / 撤销重做 / 工具 / 颜色 / 笔宽）任何宽度下都要在。"""
    for width in (1600, 1280, 1100, 980, 900, 820, 760, WINDOW_MIN_W):
        resize(win, width)
        hidden = [x for x in win.topbar.core_widgets() if not x.isVisible()]
        assert not hidden, f"宽度 {width} 时核心控件被收起来了: {hidden}"


def test_wide_window_keeps_everything(win):
    resize(win, 1600)
    assert win.topbar.hidden_widgets() == [], "窗口够宽时不应该收起任何控件"


def test_overflow_takes_the_tail_first(win):
    """溢出必须从优先级最低的一端开始（尾部），否则常用按钮会先消失。"""
    resize(win, WINDOW_MIN_W)
    hidden = win.topbar.hidden_widgets()
    optional = win.topbar.optional_widgets()
    assert hidden, "最窄时应至少收起一部分次要控件"
    hidden_set = {id(x) for x in hidden}
    tail = optional[len(optional) - len(hidden):]
    assert {id(x) for x in tail} == hidden_set, "被收起的应当正好是优先级最低的那一段"


def test_clusters_overflow_atomically(win):
    """成组的控件必须同进同退（不能出现「有上一页、没下一页」）。"""
    for width in (1600, 1280, 1100, 980, 900):
        resize(win, width)
        for cluster in win.topbar.optional_widgets():
            buttons = cluster.findChildren(QToolButton)
            if not buttons:
                continue
            shown = [b.isVisible() for b in buttons]
            assert all(shown) or not any(shown) or not cluster.isVisible(), \
                f"宽度 {width} 时成组控件被拆开了: {[b.toolTip() for b in buttons]}"


# ---------------------------------------------------------------------- #
# 标签关闭按钮
# ---------------------------------------------------------------------- #
def test_tabs_are_closable_with_visible_buttons(win):
    win.new_tab_note()
    win.new_tab_note()
    QApplication.processEvents()
    bar = win.tab_widget.tabBar()
    assert win.tab_widget.tabsClosable() is True, "setTabsClosable 必须在 setTabBar 之后"
    assert bar.count() >= 3
    for i in range(bar.count()):
        btn = bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
        assert btn is not None, f"第 {i} 个标签没有关闭按钮"
        assert not btn.icon().isNull(), f"第 {i} 个标签的 × 没有图标（QSS 会把它画成空白）"


def test_close_button_click_closes_the_tab(win):
    first = win.tab
    win.new_tab_note()
    QApplication.processEvents()
    before = len(win._tabs)
    bar = win.tab_widget.tabBar()
    btn = bar.tabButton(0, QTabBar.ButtonPosition.RightSide)
    assert btn is not None
    btn.click()
    QApplication.processEvents()
    assert len(win._tabs) == before - 1, "点 × 应当关掉一个标签"


def test_close_buttons_shrink_when_window_is_narrow(win):
    """窄窗口下标签会变短，但 × 不能被挤没。"""
    for _ in range(6):
        win.new_tab_note()
    QApplication.processEvents()
    resize(win, WINDOW_MIN_W)
    bar = win.tab_widget.tabBar()
    for i in range(bar.count()):
        btn = bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
        assert btn is not None and not btn.icon().isNull()


def test_close_buttons_follow_theme(win):
    win.new_tab_note()
    QApplication.processEvents()
    win._toggle_theme()
    QApplication.processEvents()
    bar = win.tab_widget.tabBar()
    for i in range(bar.count()):
        btn = bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
        assert btn is not None and not btn.icon().isNull(), "切主题后 × 图标不能丢"


# ---------------------------------------------------------------------- #
# 侧边栏
# ---------------------------------------------------------------------- #
def test_sidebar_auto_collapses_when_narrow(win):
    resize(win, SIDEBAR_AUTO_HIDE_W + 60)
    assert win.sidebar.isVisible(), "宽窗口下侧边栏应当可见"
    resize(win, SIDEBAR_AUTO_HIDE_W - 60)
    assert not win.sidebar.isVisible(), "窄窗口下侧边栏应当自动收起"
    resize(win, SIDEBAR_AUTO_HIDE_W + 60)
    assert win.sidebar.isVisible(), "窗口重新变宽后应当恢复"


def test_manual_sidebar_toggle_wins(win):
    resize(win, 1400)
    win._toggle_sidebar()                     # 用户手动收起
    assert not win.sidebar.isVisible()
    resize(win, 800)
    resize(win, 1400)
    assert not win.sidebar.isVisible(), "用户手动收起后，窗口变宽不该自动弹回来"


def test_sidebar_width_is_draggable_and_persisted(qapp, tmp_path):
    from src.ui.main_window import MainWindow

    ini = str(tmp_path / "s.ini")
    AppSettings(path=ini).set_sidebar_width(360)
    w = MainWindow(settings=AppSettings(path=ini))
    try:
        w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        w.show()
        QApplication.processEvents()
        assert w.sidebar.width() == pytest.approx(360, abs=2), "启动时应沿用记住的宽度"
        w._on_splitter_moved(0, 0)            # 拖拽后写入设置
        assert AppSettings(path=ini).sidebar_width() == w.sidebar.width()
    finally:
        w.close()


def test_sidebar_width_setting_is_clamped(tmp_path):
    s = AppSettings(path=str(tmp_path / "s.ini"))
    s.set_sidebar_width(10)
    assert s.sidebar_width() == 180
    s.set_sidebar_width(9999)
    assert s.sidebar_width() == 460


def test_statusbar_coord_hides_when_narrow(win):
    resize(win, 760)
    assert not win.lbl_coord.isVisible()
    resize(win, 1400)
    assert win.lbl_coord.isVisible()

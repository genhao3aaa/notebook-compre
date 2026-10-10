"""打开 PDF 后的页码 / 阅读进度回归。

两个线上缺陷（都已修）：

1. **页码不更新**：`_load_pdf_into_tab()` 是在标签**激活之前**跑完的，而
   `_on_canvas_page_info()` 只认当前标签 —— 恢复出来的页码（例如 656 页书里的第 219 页）
   写不到状态栏 / 页码框上，界面一直停在上一个标签的「页: 1 / 1」。
2. **进度被旧窗口覆盖**：进度原先按「写盘时刻」存。多窗口 / 多实例开着同一本书时，
   手里拿着旧页码的窗口一退出就把别人刚读到的位置盖掉 —— 表现就是「每次打开都回到那一页」。
   现在按「这个位置是什么时候看到的」(`view_ts`) 比较新旧，旧位置不再覆盖新位置。
"""

from __future__ import annotations

import os

import pymupdf as _fitz
import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from src.core.settings import AppSettings
from src.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _spin(ms: int = 250) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _make_pdf(path: str, npages: int = 12) -> str:
    d = _fitz.open()
    for i in range(npages):
        pg = d.new_page(width=595, height=842)
        pg.insert_text((72, 100), "page %d" % (i + 1))
    d.set_toc([[1, "第 %d 章" % (i + 1), i + 1] for i in range(npages)])
    d.save(path)
    d.close()
    return path


def _close(win) -> None:
    try:
        for t in list(win._tabs):
            t.dirty = False
        win._confirm_save = lambda *a, **k: True
        win.close()
    except Exception:
        pass


def _settings(tmp_path, name: str = "s.ini") -> AppSettings:
    return AppSettings(path=str(tmp_path / name))


# --------------------------------------------------------------------- #
# 1. 打开后页码界面必须跟着恢复出来的页走
# --------------------------------------------------------------------- #
def test_page_ui_follows_restored_page(qapp, tmp_path):
    pdf = _make_pdf(str(tmp_path / "book.pdf"))
    s = _settings(tmp_path)
    size, mtime = os.path.getsize(pdf), os.path.getmtime(pdf)
    assert s.set_pdf_read_state(pdf, 6, "single", 0.0, size, mtime) is True

    win = MainWindow(settings=s, restore_session=False)
    try:
        win.open_pdf(pdf)
        _spin(400)
        assert win._active.page == 6, "前提：进度恢复成功"
        assert win.lbl_page.text() == "页: 7 / 12", "状态栏必须显示恢复后的页码"
        assert win.page_spin.value() == 7, "页码框必须跟到第 7 页"
        assert win.page_spin.maximum() == 12
        assert win.page_total_label.text() == "/ 12"
    finally:
        _close(win)


def test_page_ui_follows_restored_page_in_continuous_mode(qapp, tmp_path):
    pdf = _make_pdf(str(tmp_path / "book2.pdf"))
    s = _settings(tmp_path)
    size, mtime = os.path.getsize(pdf), os.path.getmtime(pdf)
    assert s.set_pdf_read_state(pdf, 9, "continuous", 0.9, size, mtime) is True

    win = MainWindow(settings=s, restore_session=False)
    try:
        win.open_pdf(pdf)
        _spin(600)
        assert win.canvas.read_mode == "continuous"
        assert win._active.page == 9
        assert win.lbl_page.text() == "页: 10 / 12"
        assert win.canvas.scroll_offset_frac() > 0.5
    finally:
        _close(win)


def test_switching_tabs_updates_page_ui(qapp, tmp_path):
    """切标签也要刷新页码：否则 A 书的页码会留在 B 书上。"""
    pdf_a = _make_pdf(str(tmp_path / "a.pdf"))
    pdf_b = _make_pdf(str(tmp_path / "b.pdf"))
    win = MainWindow(settings=_settings(tmp_path), restore_session=False)
    try:
        tab_a = win.open_pdf(pdf_a)
        win.goto_page(4)
        _spin(200)
        tab_b = win.open_pdf(pdf_b)
        win.goto_page(8)
        _spin(200)
        assert win.lbl_page.text() == "页: 9 / 12"

        win._switch_to_tab(tab_a)
        _spin(200)
        assert win.lbl_page.text() == "页: 5 / 12", "切回 A 书应显示 A 书自己的页码"
        assert win.page_spin.value() == 5
        win._switch_to_tab(tab_b)
        _spin(200)
        assert win.lbl_page.text() == "页: 9 / 12"
    finally:
        _close(win)


# --------------------------------------------------------------------- #
# 2. 阅读进度的新旧比较（多窗口不再互相覆盖）
# --------------------------------------------------------------------- #
def test_newer_view_wins_over_stale_window(tmp_path):
    s = _settings(tmp_path)
    pdf = str(tmp_path / "x.pdf")
    old_ts, new_ts = 1_000.0, 2_000.0

    assert s.set_pdf_read_state(pdf, 3, "continuous", 0.1, 111, old_ts, ts=old_ts) is True
    assert s.set_pdf_read_state(pdf, 218, "continuous", 0.33, 111, old_ts, ts=new_ts) is True
    # 拿着旧页码的窗口（view_ts 还是老的）退出：不允许覆盖
    assert s.set_pdf_read_state(pdf, 3, "continuous", 0.1, 111, old_ts, ts=old_ts) is False
    assert s.pdf_read_state(pdf)["page"] == 218, "新位置不能被旧窗口覆盖"

    # 同一时刻重复写（同一个窗口连按两次保存）允许通过
    assert s.set_pdf_read_state(pdf, 219, "continuous", 0.34, 111, old_ts, ts=new_ts) is True
    assert s.pdf_read_state(pdf)["page"] == 219


def test_progress_survives_mtime_change(qapp, tmp_path):
    """文件只被动过 mtime（同步盘/备份工具）时，仍应恢复上次读到的页。"""
    pdf = _make_pdf(str(tmp_path / "touched.pdf"))
    s = _settings(tmp_path)
    s.set_pdf_read_state(pdf, 7, "single", 0.0, os.path.getsize(pdf),
                         os.path.getmtime(pdf) - 3600.0)
    win = MainWindow(settings=s, restore_session=False)
    try:
        win.open_pdf(pdf)
        _spin(400)
        assert win._active.page == 7, "mtime 变了不该把用户丢回第 1 页"
        assert win.lbl_page.text() == "页: 8 / 12"
    finally:
        _close(win)


def test_scrolling_refreshes_saved_progress(qapp, tmp_path):
    """滚动也要落盘（原先只在下一次跨页时才写），否则异常退出就丢位置。"""
    pdf = _make_pdf(str(tmp_path / "scroll.pdf"), npages=30)
    s = _settings(tmp_path)
    win = MainWindow(settings=s, restore_session=False)
    try:
        win.open_pdf(pdf)
        _spin(300)
        win._set_read_mode("continuous")
        _spin(200)
        for _ in range(20):
            win.canvas.pan_by(0.0, -700.0)
        _spin(1800)                                   # 等节流写盘
        state = s.pdf_read_state(pdf)
        assert state, "滚动后必须留下阅读进度"
        assert state["page"] > 3, f"进度应跟到第 {state['page']} 页附近"
        assert state["scroll"] > 0.0
    finally:
        _close(win)

# --------------------------------------------------------------------- #
# 3. 设置后端：注册表写不进去时必须自动落到 INI 文件（否则阅读进度白存）
# --------------------------------------------------------------------- #
def test_settings_backend_falls_back_to_ini_when_registry_blocked(tmp_path, monkeypatch):
    """注册表写失败（QSettings → AccessError，静默）时要自动改用 INI 文件。"""
    from src.core import settings as settings_mod

    monkeypatch.setattr(settings_mod, "_registry_writable", lambda org, app: False)
    monkeypatch.setattr(settings_mod, "check_registry_flagged", lambda: False)
    monkeypatch.delenv(settings_mod.ENV_FORCE_INI, raising=False)
    monkeypatch.delenv(settings_mod.ENV_SETTINGS_FILE, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(settings_mod, "mark_registry_unavailable", lambda: None)
    try:
        assert settings_mod.detect_settings_backend(refresh=True) == "ini"
        chosen = settings_mod.choose_settings_file()
        assert chosen, "必须能选出一个可写的 INI 设置文件"
        assert os.path.normcase(os.path.dirname(chosen)).startswith(
            os.path.normcase(str(tmp_path))), "选出来的文件要在本次测试的临时目录里"
        # 正常构造函数此时也应当落到这条链上（conftest 的隔离壳用的就是 path=…）
        s = settings_mod.AppSettings(path=chosen)
        assert s.storage()["backend"] == "ini"
        assert os.path.normcase(s.storage()["path"]) == os.path.normcase(chosen)
        s.set_pdf_read_state(str(tmp_path / "b.pdf"), 3, "single", 0.0, 1, 1.0)
        s.sync()
        assert os.path.isfile(chosen), "设置必须真的落到 INI 文件里"
    finally:
        settings_mod._BACKEND_CACHE = None


def test_settings_ini_round_trip_across_instances(tmp_path):
    """换成 INI 后端后，重启（新建实例）仍读得回阅读进度与最近文件。"""
    ini = str(tmp_path / "settings.ini")
    s = AppSettings(path=ini)
    assert s.storage()["backend"] == "ini"
    assert s.set_pdf_read_state(str(tmp_path / "book.pdf"), 42, "continuous",
                               0.25, 1234, 5.0, ts=7.0) is True
    s.set_open_tabs([{"kind": "pdf", "path": str(tmp_path / "book.pdf")}])
    s.sync()

    again = AppSettings(path=ini)
    state = again.pdf_read_state(str(tmp_path / "book.pdf"))
    assert state["page"] == 42 and state["mode"] == "continuous"
    assert state["ts"] == 7.0, "新旧比较用的时间戳必须落盘"
    assert [t["path"] for t in again.open_tabs()] == [str(tmp_path / "book.pdf")]


def test_page_restore_under_ini_backend(qapp, tmp_path):
    """端到端：设置走 INI 文件时，「重新打开同一份 PDF 回到上次读到的页」仍然成立。"""
    pdf = _make_pdf(str(tmp_path / "ini-book.pdf"), npages=20)
    ini = str(tmp_path / "app.ini")
    s = AppSettings(path=ini)
    win = MainWindow(settings=s, restore_session=False)
    try:
        win.open_pdf(pdf)
        _spin(400)
        win._set_read_mode("continuous")
        _spin(200)
        win.goto_page(11)
        _spin(600)
        assert win.lbl_page.text() == "页: 12 / 20"
        win._confirm_save = lambda *a, **k: True
        for t in win._tabs:
            t.dirty = False
        win.close()
        _spin(300)

        assert AppSettings(path=ini).pdf_read_state(pdf)["page"] == 11, \
            "关闭时必须把页码写进 INI 后端（而不是丢在写不进去的注册表里）"
        win2 = MainWindow(settings=AppSettings(path=ini), restore_session=False)
        try:
            win2.open_pdf(pdf)
            _spin(600)
            assert win2.lbl_page.text() == "页: 12 / 20", "重开必须回到上次那一页"
            assert win2.page_spin.value() == 12
        finally:
            win2._confirm_save = lambda *a, **k: True
            for t in win2._tabs:
                t.dirty = False
            win2.close()
            _spin(150)
    finally:
        win._confirm_save = lambda *a, **k: True
        for t in win._tabs:
            t.dirty = False
        win.close()


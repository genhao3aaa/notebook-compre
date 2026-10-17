"""视图记忆回归：切标签 / 关标签 / 重新排版后，「看到哪儿」必须还在。

线上缺陷（2026-10，用户报「打开文件后总是在一个页面打开」）：

1. **多页笔记完全没有位置记忆**：`_save_read_state()` 一开头就 `if not tab.is_pdf: return`，
   于是笔记关掉再开永远回到第 1 页；更糟的是 `_sync_ui_from_tab()` 里
   `tab.page = canvas.current_index`，而笔记的画布从来不更新 `current_index`——
   切一次标签页码就被清成 1（「翻到第 5 页 → 看别的文件 → 切回来又在开头」），
   连「加页 / 删页 / 下一页」都会作用在错误的页上。
2. **页码框的上限夹紧会把刚切到的标签拽走**：从 600 页的书切到 20 页的书时
   `setMaximum(20)` 把值 401 夹到 20 并发出 `valueChanged`，`goto_page(19)`
   当场把新标签翻到最后一页。
3. **重新排版丢滚动位置**：标签在后台被 resize 过，`fit_to_view()` 会把连续阅读
   算回页首；现在按「页 + 页首在屏幕上的位置」这个锚点还原。
4. **关标签不落盘**：释放文档之后才写的进度写不进去，重开只能回到上一次节流写盘的位置。

本文件的用例都走真实的 `QTabWidget.setCurrentIndex`（用户点标签栏的路径），
不走 `_switch_to_tab` 直调——两者在 Qt 里的隐藏/显示顺序不同，旧用例正是
因为绕过了前者，才没抓到「切回来位置丢了」。
"""

from __future__ import annotations

import os

import pymupdf as _fitz
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from src.core.note_document import NoteDocument
from src.core.settings import AppSettings
from src.ui.main_window import MainWindow


def _spin(ms: int = 250) -> None:
    QApplication.instance() or QApplication([])
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _make_pdf(path: str, npages: int = 60) -> str:
    d = _fitz.open()
    for i in range(npages):
        pg = d.new_page(width=595, height=842)
        pg.insert_text((72, 100), "page %d" % (i + 1))
    d.save(path)
    d.close()
    return path


def _make_note(path: str, npages: int = 10) -> str:
    doc = NoteDocument()
    while doc.page_count() < npages:
        doc.add_page(doc.page_count())
    doc.save(path)
    return path


def _window(tmp_path, name: str = "s.ini") -> MainWindow:
    win = MainWindow(settings=AppSettings(path=str(tmp_path / name)),
                     restore_session=False)
    win.show()
    _spin(300)
    return win


def _close(win: MainWindow) -> None:
    try:
        win._confirm_save = lambda *a, **k: True
        for t in list(win._tabs):
            t.dirty = False
        win.close()
    except Exception:
        pass
    _spin(200)


def _click_tab(win: MainWindow, tab) -> None:
    """用户点标签栏：真实走 currentChanged → _switch_to_tab。"""
    win.tab_widget.setCurrentIndex(win.tab_widget.indexOf(tab.canvas))
    _spin(300)


# --------------------------------------------------------------------- #
# 1. PDF：切走再切回来，页与滚动位置都要原样
# --------------------------------------------------------------------- #
def test_continuous_position_survives_tab_switch(tmp_path):
    pdf_a = _make_pdf(str(tmp_path / "a.pdf"))
    pdf_b = _make_pdf(str(tmp_path / "b.pdf"), 40)
    win = _window(tmp_path)
    try:
        tab_a = win.open_pdf(pdf_a)
        _spin(300)
        win._set_read_mode("continuous")
        _spin(200)
        win.goto_page(42)
        _spin(300)
        page, frac = tab_a.page, tab_a.canvas.scroll_offset_frac()
        assert page == 42

        win.open_pdf(pdf_b)
        _spin(300)
        _click_tab(win, tab_a)
        assert tab_a.canvas.current_index == page, "切回来应停在同一页"
        assert abs(tab_a.canvas.scroll_offset_frac() - frac) < 0.01, "滚动位置也要还原"
    finally:
        _close(win)


def test_single_page_position_survives_tab_switch(tmp_path):
    pdf_a = _make_pdf(str(tmp_path / "c.pdf"))
    pdf_b = _make_pdf(str(tmp_path / "d.pdf"), 40)
    win = _window(tmp_path, "s2.ini")
    try:
        tab_a = win.open_pdf(pdf_a)
        _spin(300)
        win.goto_page(21)
        _spin(200)
        win.open_pdf(pdf_b)
        _spin(300)
        _click_tab(win, tab_a)
        assert tab_a.page == 21
        assert tab_a.canvas.current_index == 21
        assert win.page_spin.value() == 22
    finally:
        _close(win)


def test_switching_to_short_pdf_does_not_jump_to_last_page(tmp_path):
    """页码框上限夹紧不得触发跳页（旧实现会把 20 页的书直接翻到第 20 页）。"""
    long_pdf = _make_pdf(str(tmp_path / "long.pdf"), 60)
    short_pdf = _make_pdf(str(tmp_path / "short.pdf"), 8)
    win = _window(tmp_path, "s3.ini")
    try:
        win.open_pdf(long_pdf)
        _spin(300)
        win.goto_page(55)
        _spin(200)
        assert win.page_spin.value() == 56

        tab_s = win.open_pdf(short_pdf)
        _spin(400)
        assert tab_s.page == 0, f"新标签被页码框夹紧拽到了第 {tab_s.page + 1} 页"
        assert tab_s.canvas.current_index == 0
        assert win.page_spin.value() == 1
    finally:
        _close(win)


# --------------------------------------------------------------------- #
# 2. 多页笔记：页码要记得住（切换 + 关闭重开）
# --------------------------------------------------------------------- #
def test_note_page_survives_tab_switch(tmp_path):
    note = _make_note(str(tmp_path / "n.note"), 10)
    pdf = _make_pdf(str(tmp_path / "p.pdf"), 20)
    win = _window(tmp_path, "s4.ini")
    try:
        tab_n = win.open_note(note)
        _spin(300)
        win.goto_page(5)
        _spin(300)
        assert win.page_spin.value() == 6

        win.open_pdf(pdf)
        _spin(300)
        _click_tab(win, tab_n)
        assert tab_n.page == 5, "切回笔记不能把页码清成第 1 页"
        assert win.canvas.current_index == 5
        assert win.canvas.page_strokes is tab_n.note_doc.pages[5].strokes
        assert win.page_spin.value() == 6
    finally:
        _close(win)


def test_note_page_survives_close_and_reopen(tmp_path):
    note = _make_note(str(tmp_path / "m.note"), 12)
    win = _window(tmp_path, "s5.ini")
    try:
        tab = win.open_note(note)
        _spin(300)
        win.goto_page(7)
        _spin(1600)                     # 等节流写盘
        win.close_tab(tab, ask_save=False)
        _spin(300)
        tab2 = win.open_note(note)
        _spin(300)
        assert tab2.page == 7, "重新打开笔记应回到上次那一页"
        assert win.canvas.current_index == 7
        assert win.canvas.page_strokes is tab2.note_doc.pages[7].strokes
    finally:
        _close(win)


def test_note_add_and_delete_page_use_current_page(tmp_path):
    """加页/删页必须作用在「用户正在看的那一页」上（页码被清成 0 时会插错位置）。"""
    note = _make_note(str(tmp_path / "g.note"), 6)
    win = _window(tmp_path, "s8.ini")
    try:
        tab = win.open_note(note)
        _spin(300)
        win.goto_page(4)
        _spin(200)
        before = tab.note_doc.page_count()

        win._add_page()
        assert tab.note_doc.page_count() == before + 1
        assert tab.page == 5, "新页应插在当前页之后，并跳到新页"

        win._delete_page()
        assert tab.note_doc.page_count() == before, "删掉的应该是当前页"
        assert 0 <= tab.page < tab.note_doc.page_count()
        assert win.page_spin.value() == tab.page + 1
    finally:
        _close(win)


# --------------------------------------------------------------------- #
# 3. 重新排版（窗口 resize）不把画面拽回页首
# --------------------------------------------------------------------- #
def test_resize_keeps_continuous_view(tmp_path):
    pdf = _make_pdf(str(tmp_path / "r.pdf"), 60)
    win = _window(tmp_path, "s6.ini")
    try:
        tab = win.open_pdf(pdf)
        _spin(300)
        win._set_read_mode("continuous")
        _spin(200)
        win.goto_page(30)
        _spin(400)
        anchor_before = tab.canvas.view_anchor()
        assert anchor_before is not None
        win.resize(win.width() - 220, win.height() - 120)
        _spin(500)
        anchor_after = tab.canvas.view_anchor()
        assert anchor_after is not None
        assert anchor_after[0] == anchor_before[0], "resize 后画面应停在原页"
        assert abs(anchor_after[1] - anchor_before[1]) < 12.0, "页首在屏幕上的位置也不该跳"
        assert tab.canvas.current_index == 30
    finally:
        _close(win)


def test_detach_to_new_window_keeps_position(tmp_path):
    """「把文件拖成另一个窗口并排看」之后，位置不能回到开头。"""
    from PySide6.QtCore import QPoint

    pdf_a = _make_pdf(str(tmp_path / "w.pdf"), 60)
    win = _window(tmp_path, "s7.ini")
    other = None
    try:
        tab_a = win.open_pdf(pdf_a)
        _spin(300)
        win._set_read_mode("continuous")
        _spin(200)
        win.goto_page(42)
        _spin(400)
        frac = tab_a.canvas.scroll_offset_frac()
        win.new_tab_note()                 # 源窗口不能空
        _spin(200)

        other = win.detach_tab(tab_a, QPoint(600, 80))
        _spin(400)
        assert other is not None and tab_a in other._tabs
        assert tab_a.page == 42, "搬窗口后页面位置不能丢"
        assert tab_a.canvas.read_mode == "continuous"
        assert abs(tab_a.canvas.scroll_offset_frac() - frac) < 0.05
    finally:
        _close(win)
        if other is not None:
            _close(other)


# --------------------------------------------------------------------- #
# 4. 设置层：笔记页码往返 / 时间戳明显来自未来时不能让进度永久卡死
# --------------------------------------------------------------------- #
def test_reopen_restores_exact_scroll_fraction(tmp_path):
    """重新打开时画布还没有真实尺寸，滚动比例必须在真正排版后再补一次。"""
    pdf = _make_pdf(str(tmp_path / "exact.pdf"), 60)
    win = _window(tmp_path, "s9.ini")
    try:
        tab = win.open_pdf(pdf)
        _spin(300)
        win._set_read_mode("continuous")
        _spin(200)
        win.goto_page(36)
        _spin(400)
        saved = tab.canvas.scroll_offset_frac()
        assert saved > 0.3
        win._save_pdf_read_state()

        win.close_tab(tab, ask_save=False)
        _spin(300)
        tab2 = win.open_pdf(pdf)
        _spin(600)
        assert tab2.page == 36
        assert abs(tab2.canvas.scroll_offset_frac() - saved) < 0.02, \
            f"滚动位置应恢复到 {saved:.3f}，实际 {tab2.canvas.scroll_offset_frac():.3f}"
    finally:
        _close(win)


def test_note_read_state_round_trip(tmp_path):
    ini = str(tmp_path / "app.ini")
    s = AppSettings(path=ini)
    assert s.note_read_state(str(tmp_path / "x.note")) == 0
    s.set_note_read_state(str(tmp_path / "x.note"), 9)
    s.sync()
    assert AppSettings(path=ini).note_read_state(str(tmp_path / "x.note")) == 9


def test_future_timestamp_does_not_freeze_pdf_progress(tmp_path):
    s = AppSettings(path=str(tmp_path / "app2.ini"))
    pdf = str(tmp_path / "book.pdf")
    # 脏数据：时间戳来自十年后（时钟回拨 / 别的机器同步过来）
    s.set_pdf_read_state(pdf, 3, "single", 0.0, 100, 1.0, ts=10 ** 10)
    assert s.set_pdf_read_state(pdf, 50, "continuous", 0.2, 100, 1.0,
                                ts=1_000.0) is True, "来自未来的旧时间戳必须被作废"
    assert s.pdf_read_state(pdf)["page"] == 50


def test_page_count_identifies_document_when_size_drifted(tmp_path):
    """只改了封面/元数据（大小变了、页数没变）时仍应恢复到上次那一页。"""
    s = AppSettings(path=str(tmp_path / "app3.ini"))
    pdf = _make_pdf(str(tmp_path / "drift.pdf"), 12)
    s.set_pdf_read_state(pdf, 8, "continuous", 0.0, os.path.getsize(pdf),
                         os.path.getmtime(pdf), pages=12)
    win = MainWindow(settings=s, restore_session=False)
    try:
        win.settings.set_pdf_read_state(pdf, 8, "continuous", 0.0,
                                        os.path.getsize(pdf) + 4096,
                                        os.path.getmtime(pdf), pages=12)
        win.open_pdf(pdf)
        _spin(400)
        assert win._active.page == 8, "大小变了但页数没变，不该把用户丢回第 1 页"
    finally:
        _close(win)

"""多标签系统测试（v5）。

重点不是「能不能开多个」，而是这三条最容易出错的性质：

1. **状态隔离**：A 标签的页码/缩放/笔画/dirty/背景色，绝不能因为切到 B 而串味；
2. **切换无损**：切回来还是刚才那一页、那个缩放比例（每个标签独占画布的直接好处）；
3. **异步不串台**：后台标签迟到的回调（搜索结果等）不能写到当前标签上。
"""

from __future__ import annotations

import os
import tempfile

import pymupdf as fitz
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from src.core.settings import AppSettings
from src.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    _app = QApplication.instance() or QApplication([])
    yield _app


def _settings(tmp_path, name: str = "settings.ini") -> AppSettings:
    return AppSettings(path=str(tmp_path / name))


def _win(tmp_path, name: str = "settings.ini") -> MainWindow:
    """独立设置文件，避免用例之间（以及和真实用户配置）互相干扰。"""
    return MainWindow(settings=_settings(tmp_path, name))


def _make_pdf(path: str, npages: int = 3, text: str = "alpha") -> str:
    d = fitz.open()
    for i in range(npages):
        pg = d.new_page()
        pg.insert_text((72, 72), f"{text} page {i}")
    d.save(path)
    d.close()
    return path


def _make_note(path: str, pages: int = 2) -> str:
    from src.core.note_document import NoteDocument
    doc = NoteDocument()
    for _ in range(pages - 1):
        doc.add_page(doc.page_count())
    doc.save(path)
    return path


def _hit(page: int = 0):
    """一个最小可用的搜索命中项（界面会读它的 page / context）。"""
    from src.render.pdf_engine import SearchHit
    return SearchHit(page=page, rect=(0.0, 0.0, 40.0, 12.0), context="alpha")


def _draw(canvas, points) -> None:
    """在画布上画一笔：页面坐标 -> CSS 坐标，保证落在页面内。"""
    pts = [canvas.page_to_css(x, y) for x, y in points]
    canvas._pointer_down(QPointF(pts[0].x(), pts[0].y()), 1.0)
    for p in pts[1:]:
        canvas._pointer_move(QPointF(p.x(), p.y()), 1.0)
    canvas._pointer_up()


# ================================================================== #
# 标签的创建与独立性
# ================================================================== #
def test_new_tab_has_its_own_canvas(qapp, tmp_path):
    w = _win(tmp_path)
    before = len(w._tabs)
    t1 = w.new_tab_note()
    t2 = w.new_tab_note()
    assert len(w._tabs) == before + 2
    assert t1.canvas is not t2.canvas, "每个标签必须独占一个画布"
    assert w.tab is t2
    assert w.canvas is t2.canvas

    w._switch_to_tab(t1)
    assert w.canvas is t1.canvas
    assert w.tab is t1


def test_switch_preserves_page_and_zoom(qapp, tmp_path):
    """切走再切回：页码和缩放都还在（浏览器标签的手感）。"""
    pdf = _make_pdf(str(tmp_path / "a.pdf"))
    w = _win(tmp_path)
    pdf_tab = w.open_pdf(pdf)
    w.goto_page(2)
    w.canvas.zoom_to(2.0)
    scale = w.canvas.scale

    note_tab = w.new_tab_note()
    assert w.canvas is note_tab.canvas
    assert w.mode == "note"

    w._switch_to_tab(pdf_tab)
    assert w.current_page == 2, "切回来应停在原来那一页"
    assert abs(w.canvas.scale - scale) < 1e-6, "切回来应保留原来的缩放"
    assert w.mode == "pdf"


def test_mixed_note_and_pdf_tabs(qapp, tmp_path):
    """笔记 / PDF / 笔记 混开，切换时模式与文档对象跟着换。"""
    pdf = _make_pdf(str(tmp_path / "b.pdf"))
    note = _make_note(str(tmp_path / "n.note"))
    w = _win(tmp_path)

    pdf_tab = w.open_pdf(pdf)
    assert w.mode == "pdf" and w.pdf_engine is not None

    note_tab = w.open_note(note)
    assert w.mode == "note" and w.note_doc is not None
    assert w.pdf_engine is None, "当前是笔记标签，不应看到 PDF 引擎"

    w._switch_to_tab(pdf_tab)
    assert w.mode == "pdf" and w.note_doc is None

    w._switch_to_tab(note_tab)
    assert w.mode == "note"
    assert len(w._tabs) == 3   # 开局空白 + PDF + 笔记


def test_stroke_drawn_on_one_tab_does_not_leak(qapp, tmp_path):
    """在一个标签上画的东西，不会跑到另一个标签上。"""
    w = _win(tmp_path)
    a = w.new_tab_note()
    b = w.new_tab_note()

    w._switch_to_tab(a)
    _draw(a.canvas, [(100, 100), (200, 200), (300, 260)])
    n_a = len(a.note_doc.pages[0].strokes)
    assert n_a >= 1, "这一笔应该画在当前标签的页面上"

    w._switch_to_tab(b)
    assert len(b.note_doc.pages[0].strokes) == 0, "笔画泄漏到别的标签了"

    w._switch_to_tab(a)
    assert len(a.note_doc.pages[0].strokes) == n_a, "切回来笔画应原样还在"


def test_dirty_flag_is_per_tab(qapp, tmp_path):
    w = _win(tmp_path)
    a = w.new_tab_note()
    b = w.new_tab_note()

    w._switch_to_tab(a)
    w.mark_dirty()
    assert a.dirty is True and b.dirty is False
    assert "*" in w.tab_widget.tabText(w.tab_widget.indexOf(a.canvas))
    assert "*" not in w.tab_widget.tabText(w.tab_widget.indexOf(b.canvas))

    w._switch_to_tab(b)
    assert w.dirty is False, "切到干净的标签后，脏标记不应跟过来"

    w._switch_to_tab(a)
    assert w.dirty is True


def test_background_color_is_per_tab(qapp, tmp_path):
    """改 A 的背景色，已经打开的 B 不能被带着变。"""
    w = _win(tmp_path)
    a = w.new_tab_note()
    b = w.new_tab_note()

    w._switch_to_tab(a)
    w._apply_bg_color("#3b3f45")
    assert a.note_doc.background_color == "#3b3f45"
    assert b.note_doc.background_color != "#3b3f45", "背景色串到别的标签了"

    w._switch_to_tab(b)
    assert w.note_doc.background_color != "#3b3f45"
    w._switch_to_tab(a)
    assert w.note_doc.background_color == "#3b3f45"


# ================================================================== #
# 关闭 / 资源释放
# ================================================================== #
def test_close_tab_releases_pdf(qapp, tmp_path):
    pdf = _make_pdf(str(tmp_path / "c.pdf"))
    w = _win(tmp_path)
    tab = w.open_pdf(pdf)
    engine = tab.pdf_engine
    canvas = tab.canvas

    assert w.close_tab(tab) is True
    assert tab not in w._tabs
    assert tab.pdf_engine is None and tab.canvas is None
    doc = getattr(engine, "doc", None)
    if doc is not None:
        assert doc.is_closed, "关闭标签必须 close() 掉 fitz 文档"
    assert w.tab_widget.indexOf(canvas) < 0, "画布应从标签栏移除"


def test_closing_last_tab_reopens_blank_note(qapp, tmp_path):
    """浏览器不会留一个空白窗口：关掉最后一个标签会自动补一个空白笔记。"""
    w = _win(tmp_path)
    for tab in list(w._tabs):
        w.close_tab(tab)
    assert len(w._tabs) == 1
    assert w.mode == "note"
    assert w.canvas is not None


def test_close_other_tabs_keeps_current(qapp, tmp_path):
    w = _win(tmp_path)
    keep = w.new_tab_note()
    w.new_tab_note()
    w.new_tab_note()
    w._switch_to_tab(keep)

    w.close_other_tabs(keep)
    assert len(w._tabs) == 1
    assert w.tab is keep


def test_closed_tab_can_be_reopened(qapp, tmp_path):
    pdf = _make_pdf(str(tmp_path / "d.pdf"))
    w = _win(tmp_path)
    w.open_pdf(pdf)
    w.close_tab(w._active)
    assert len(w._closed) >= 1

    before = len(w._tabs)
    w.reopen_closed_tab()
    assert len(w._tabs) == before + 1
    assert w.mode == "pdf"


# ================================================================== #
# 切换快捷键
# ================================================================== #
def test_cycle_tab_wraps_around(qapp, tmp_path):
    w = _win(tmp_path)
    first = w.tab                                  # 开局的空白标签
    t1, t2, t3 = w.new_tab_note(), w.new_tab_note(), w.new_tab_note()
    w._switch_to_tab(t1)

    w.cycle_tab(1)
    assert w.tab is t2
    w.cycle_tab(1)
    assert w.tab is t3
    w.cycle_tab(1)          # 末尾回到开头（循环）
    assert w.tab is first
    w.cycle_tab(-1)         # 反向
    assert w.tab is t3


def test_goto_tab_number(qapp, tmp_path):
    w = _win(tmp_path)
    for _ in range(3):
        w.new_tab_note()
    tabs = list(w._tabs)
    w.goto_tab_number(1)
    assert w.tab is tabs[0]
    w.goto_tab_number(3)
    assert w.tab is tabs[2]
    w.goto_tab_number(9)    # Ctrl+9 = 最后一个
    assert w.tab is tabs[-1]
    w.goto_tab_number(99)   # 越界：停在最后一个，不能崩
    assert w.tab is tabs[-1]


def test_tab_moved_syncs_internal_order(qapp, tmp_path):
    """拖拽重排后，内部列表顺序要和标签栏一致（否则 Ctrl+1..9 会跳错）。"""
    w = _win(tmp_path)
    t1, t2, t3 = w.new_tab_note(), w.new_tab_note(), w.new_tab_note()
    i1, _, i3 = (w._tabs.index(t) for t in (t1, t2, t3))

    # 用真实的标签栏移动（会发出 tabMoved），而不是直接调回调
    w.tab_widget.tabBar().moveTab(i3, i1)

    assert w._tabs.index(t3) == i1, "内部列表顺序应跟上标签栏"
    assert w._tab_at(i1) is t3, "按位置反查标签应得到移动后的那个"
    w.goto_tab_number(i1 + 1)
    assert w.tab is t3


# ================================================================== #
# 异步结果不串台
# ================================================================== #
def test_search_result_of_closed_tab_is_dropped(qapp, tmp_path):
    """搜索结果回来时标签已关闭：静默丢弃，不能崩也不能写到当前标签。"""
    w = _win(tmp_path)
    w._on_search_done(99999, [object()], "query", 0)
    assert w._search_hits == []


def test_search_result_of_background_tab_does_not_touch_ui(qapp, tmp_path):
    """后台标签的搜索结果只落到它自己身上，不抢当前标签的界面。"""
    pdf = _make_pdf(str(tmp_path / "e.pdf"))
    w = _win(tmp_path)
    pdf_tab = w.open_pdf(pdf)
    w.new_tab_note()                      # 当前变成笔记标签

    w._on_search_done(pdf_tab.tab_id, [_hit(0), _hit(1)], "alpha",
                      pdf_tab.search_token)
    assert len(pdf_tab.search_hits) == 2, "结果应存到发起搜索的那个标签"
    assert w._search_hits == [], "当前标签的搜索结果不应被污染"

    w._switch_to_tab(pdf_tab)
    assert len(w._search_hits) == 2, "切回去时应看到该标签自己的搜索结果"


def test_stale_search_token_is_ignored(qapp, tmp_path):
    """用户在结果回来前改了关键字：旧批次必须作废。"""
    pdf = _make_pdf(str(tmp_path / "f.pdf"))
    w = _win(tmp_path)
    tab = w.open_pdf(pdf)
    tab.search_token = 5
    w._on_search_done(tab.tab_id, [object()], "old", 3)
    assert tab.search_hits == []


# ================================================================== #
# 会话持久化
# ================================================================== #
def test_session_saves_and_restores_open_tabs(qapp, tmp_path):
    pdf = _make_pdf(str(tmp_path / "g.pdf"))
    note = _make_note(str(tmp_path / "h.note"))
    s = _settings(tmp_path, "s1.ini")

    w = MainWindow(settings=s)
    w.open_pdf(pdf)
    w.open_note(note)
    w._save_session()
    s.sync()
    assert len(s.open_tabs()) == 2

    w2 = MainWindow(settings=_settings(tmp_path, "s1.ini"))   # 重新读同一份设置
    paths = {os.path.normcase(t.path or "") for t in w2._tabs if t.path}
    assert os.path.normcase(pdf) in paths
    assert os.path.normcase(note) in paths
    w2.close()


def test_restore_skips_missing_files(qapp, tmp_path):
    """上次的文件被删了：跳过它，不能让启动崩掉。"""
    s = _settings(tmp_path, "s2.ini")
    s.set_open_tabs([{"kind": "pdf", "path": str(tmp_path / "gone.pdf")},
                     {"kind": "note", "path": str(tmp_path / "gone.note")}])
    s.sync()

    w = MainWindow(settings=_settings(tmp_path, "s2.ini"))
    assert len(w._tabs) == 1, "打不开的文件应被跳过，只留空白标签"
    w.close()


def test_settings_rejects_malformed_session(qapp, tmp_path):
    """设置里是脏数据（不是列表 / 缺 path）时也不能崩。"""
    s = _settings(tmp_path, "s3.ini")
    s._qs.setValue("open_tabs", '{"not": "a list"}')
    assert s.open_tabs() == []
    s._qs.setValue("open_tabs", "not json at all")
    assert s.open_tabs() == []

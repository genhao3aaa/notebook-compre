"""Markdown 阅读格式：文档模型 / 渲染视图 / 主窗口集成。

这些用例覆盖「阅读器支持格式扩展」的新增路径；PDF 与手写笔记的既有行为
由原来的 reader/reading/multi_tab 等用例守护。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from src.core.markdown_document import (MD_SUFFIXES, document_title, is_markdown,
                                        parse_headings, read_markdown,
                                        search_markdown)
from src.core.settings import AppSettings
from src.ui.markdown_view import MarkdownView


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _window(tmp_path):
    from src.ui.main_window import MainWindow

    return MainWindow(settings=AppSettings(path=str(tmp_path / "s.ini")),
                      restore_session=False, restore_geometry=False)


# ---------------------------------------------------------------------- #
# 文档模型（纯逻辑）
# ---------------------------------------------------------------------- #
def test_is_markdown_suffixes():
    for suffix in MD_SUFFIXES:
        assert is_markdown("doc" + suffix)
        assert is_markdown("DOC" + suffix.upper())
    assert not is_markdown("doc.pdf")
    assert not is_markdown("doc.note")
    assert not is_markdown("")


def test_read_markdown_encodings(tmp_path):
    p = tmp_path / "u.md"
    p.write_text("# 中文\n\n内容", encoding="utf-8")
    assert "中文" in read_markdown(str(p))

    bom = tmp_path / "b.md"
    bom.write_bytes(b"\xef\xbb\xbf# BOM\n")
    assert read_markdown(str(bom)) == "# BOM\n"

    gbk = tmp_path / "g.md"
    gbk.write_bytes("# GBK\n中文".encode("gbk"))
    assert "GBK" in read_markdown(str(gbk))


def test_read_markdown_size_guard(tmp_path, monkeypatch):
    import src.core.markdown_document as m

    monkeypatch.setattr(m, "MAX_MD_BYTES", 8)
    p = tmp_path / "big.md"
    p.write_text("0123456789", encoding="utf-8")
    with pytest.raises(ValueError):
        read_markdown(str(p))


def test_parse_headings_atx_setext_and_fence():
    text = ("# 一级\n\n正文\n\n```\n# 假的标题\n```\n\n"
            "## 二级\n\n### 三级\n\nSetext 一\n===\n\nSetext 二\n---\n")
    got = [(h.level, h.title) for h in parse_headings(text)]
    assert got == [(1, "一级"), (2, "二级"), (3, "三级"),
                   (1, "Setext 一"), (2, "Setext 二")]


def test_parse_headings_strips_inline():
    assert parse_headings("# **粗体** 与 `代码` [链接](url)\n")[0].title == \
        "粗体 与 代码 链接"


def test_search_markdown_flags():
    text = "alpha beta\nAlpha\nkeyword key\nalpha"
    assert len(search_markdown(text, "alpha")) == 3
    assert len(search_markdown(text, "alpha", case_sensitive=True)) == 2
    assert search_markdown(text, "ALPHA", case_sensitive=True) == []
    assert len(search_markdown(text, "key", whole_word=True)) == 1
    assert search_markdown(text, "") == []
    hits = search_markdown(text, "alpha", max_hits=1)
    assert len(hits) == 1 and "alpha" in hits[0].context


def test_document_title():
    assert document_title("## 小标题\n\n# 正标题\n", "f.md") == "正标题"
    assert document_title("无标题", "f.md") == "f.md"


# ---------------------------------------------------------------------- #
# 渲染视图
# ---------------------------------------------------------------------- #
def test_markdown_view_render_and_find(qapp):
    v = MarkdownView()
    v.set_markdown("# 标题\n\n正文 alpha\n\n## 小节\n\n- alpha\n", "")
    assert len(v.headings()) == 2
    assert "alpha" in v.toPlainText()
    hits = v.find_all("alpha")
    assert len(hits) == 2
    assert all(h.context for h in hits), "命中必须带上下文"
    assert v.find_all("ALPHA", case_sensitive=True) == []
    v.highlight_hits(hits, 0)
    v.focus_hit(hits[1].position)
    v.clear_search()
    assert v.find_all("") == []


def test_markdown_view_source_mode_zoom_theme(qapp):
    v = MarkdownView()
    v.apply_theme({"card": "#ffffff", "text": "#111111", "accent": "#0a7",
                   "highlight": "#dfd", "hover": "#eee", "border": "#ccc",
                   "text_secondary": "#666"})
    v.set_markdown("# T\n\nalpha\n", "")
    v.zoom_to(1.2)
    assert v.scale > 1.0
    v.actual_size()
    assert abs(v.scale - 1.0) < 1e-6
    v.set_view_mode("source")
    assert v.view_mode() == "source" and "alpha" in v.toPlainText()
    v.set_view_mode("render")
    assert v.view_mode() == "render"


# ---------------------------------------------------------------------- #
# 主窗口集成
# ---------------------------------------------------------------------- #
def test_open_markdown_tab(qapp, tmp_path):
    w = _window(tmp_path)
    p = tmp_path / "doc.md"
    p.write_text("# 标题\n\n正文 **粗体**\n\n## 小节\n", encoding="utf-8")
    try:
        tab = w.open_markdown(str(p))
        assert tab is not None and tab.kind == "md" and w.mode == "md"
        assert tab.md_text.startswith("# 标题")
        assert len(tab.md_headings) == 2
        assert w.side_title.text() == "Markdown 阅读"
        assert w.outline_panel.count_label.text() == "共 2 个标题"
        # 同一份文件重复打开复用已有标签
        assert w.open_markdown(str(p)) is tab
        assert str(p) in w.settings.recent_files()
    finally:
        w.close()


def test_open_path_dispatches_all_formats(qapp, tmp_path):
    import pymupdf as fitz

    w = _window(tmp_path)
    md = tmp_path / "a.md"
    md.write_text("# A\n", encoding="utf-8")
    pdf = tmp_path / "c.pdf"
    d = fitz.open()
    d.new_page()
    d.save(str(pdf))
    d.close()
    try:
        assert w.open_path(str(md)).kind == "md"
        assert w.open_path(str(pdf)).kind == "pdf"
    finally:
        w.close()


def test_markdown_search_and_outline(qapp, tmp_path):
    w = _window(tmp_path)
    p = tmp_path / "s.md"
    p.write_text("# 一\n\nalpha\n\n## 二\n\nalpha\n", encoding="utf-8")
    try:
        tab = w.open_markdown(str(p))
        w._start_search("alpha", False, False)
        assert len(tab.search_hits) == 2
        w._goto_hit(1)
        assert tab.search_index == 1
        w._on_outline_jump(0, 1.0)          # 跳到第 2 个标题
        w._toggle_md_view_mode()
        assert tab.canvas.view_mode() == "source"
    finally:
        w.close()


def test_markdown_is_readonly(qapp, tmp_path):
    w = _window(tmp_path)
    p = tmp_path / "r.md"
    p.write_text("# R\n", encoding="utf-8")
    try:
        w.open_markdown(str(p))
        w.save()            # 只读：不写盘、不弹模态
        w.save_as()
        w.export_pdf()
        assert not w.dirty
    finally:
        w.close()


def test_markdown_session_restore(qapp, tmp_path):
    from src.ui.main_window import MainWindow

    s = AppSettings(path=str(tmp_path / "s.ini"))
    p = tmp_path / "e.md"
    p.write_text("# E\n", encoding="utf-8")
    s.set_open_tabs([{"kind": "md", "path": str(p)}])
    w = MainWindow(settings=s, restore_session=True, restore_geometry=False)
    try:
        assert any(t.kind == "md" for t in w._tabs)
    finally:
        w.close()


def test_markdown_theme_switch_keeps_content(qapp, tmp_path):
    w = _window(tmp_path)
    p = tmp_path / "t.md"
    p.write_text("# T\n\nalpha\n", encoding="utf-8")
    try:
        tab = w.open_markdown(str(p))
        w._toggle_theme()
        assert "alpha" in tab.canvas.toPlainText()
    finally:
        w.close()

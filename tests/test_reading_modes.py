"""PDF 阅读增强（v4.2）回归测试：打开提速、连续/单页阅读、阅读进度恢复。"""

from __future__ import annotations

import os

import pymupdf as _fitz
import pytest
from PySide6.QtCore import QEventLoop, QPointF, QTimer
from PySide6.QtWidgets import QApplication

from src.core.settings import AppSettings
from src.render import pdf_engine as pdf_engine_mod
from src.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    _app = QApplication.instance() or QApplication([])
    yield _app


def _spin(ms: int = 250) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _make_pdf(path: str, npages: int = 12) -> str:
    d = _fitz.open()
    for i in range(npages):
        pg = d.new_page(width=595, height=842)
        pg.insert_text((72, 100), "page %d keyword alpha" % (i + 1))
    d.set_toc([[1, "第 %d 章" % (i + 1), i + 1] for i in range(npages)])
    d.save(path)
    d.close()
    return path


def _settings(tmp_path, name: str = "settings.ini") -> AppSettings:
    """用 INI 文件存设置：受限环境下注册表可能只读，INI 才能验证持久化。"""
    return AppSettings(path=str(tmp_path / name))


def _window(tmp_path, name: str = "settings.ini") -> MainWindow:
    return MainWindow(settings=_settings(tmp_path, name))


# ====================================================================== #
# 一、打开提速
# ====================================================================== #
def test_open_skips_md5_when_no_annot(qapp, tmp_path):
    """没有 .annot 时不应为了 MD5 把整份文件读一遍。"""
    pdf = _make_pdf(str(tmp_path / "a.pdf"))
    w = _window(tmp_path)
    try:
        w.open_pdf(pdf)
        assert w.mode == "pdf"
        assert w.pdf_engine._md5 == "", "无 .annot 时不应计算 MD5"
    finally:
        w._close_current_pdf()
        w.close()


def test_open_uses_cached_md5(qapp, tmp_path, monkeypatch):
    """已有 .annot 且缓存命中时，重新打开不应再算 MD5。"""
    pdf = _make_pdf(str(tmp_path / "b.pdf"))
    w = _window(tmp_path)
    try:
        w.open_pdf(pdf)
        w.goto_page(2)
        w.save()                       # 生成 .annot，并把 MD5 写进缓存
        assert os.path.exists(str(tmp_path / "b.annot"))
        w._close_current_pdf()

        calls = {"n": 0}
        real = pdf_engine_mod.compute_file_md5

        def _spy(path, *args, **kwargs):
            calls["n"] += 1
            return real(path, *args, **kwargs)

        monkeypatch.setattr(pdf_engine_mod, "compute_file_md5", _spy)
        w.open_pdf(pdf)
        assert calls["n"] == 0, "命中 MD5 缓存时不应重算"
        assert w.annot_doc is not None
    finally:
        w._close_current_pdf()
        w.close()


def test_thumbnails_are_lazy(qapp, tmp_path):
    """大文档只为开头一段建条目，不再一次性创建上千个控件。"""
    pdf = _make_pdf(str(tmp_path / "c.pdf"), 200)
    w = _window(tmp_path)
    try:
        w.open_pdf(pdf)
        made = len(w.thumbnails._items)
        assert 0 < made <= w.thumbnails._WINDOW + 1
        assert made < 200
    finally:
        w._close_current_pdf()
        w.close()


# ====================================================================== #
# 二、连续阅读 / 单页阅读
# ====================================================================== #
def test_read_mode_toggle(qapp, tmp_path):
    pdf = _make_pdf(str(tmp_path / "d.pdf"))
    w = _window(tmp_path)
    try:
        w.open_pdf(pdf)
        assert w.canvas.read_mode == "single"          # 默认单页
        w._set_read_mode("continuous")
        assert w.canvas.read_mode == "continuous"
        assert w.btn_read_mode.isChecked()
        assert w.act_read_continuous.isChecked()
        assert not w.act_read_single.isChecked()
        w._toggle_read_mode()
        assert w.canvas.read_mode == "single"
        assert not w.btn_read_mode.isChecked()
    finally:
        w._close_current_pdf()
        w.close()


def test_single_page_mode_pages_normally(qapp, tmp_path):
    """单页模式行为不变：翻页 = 换页。"""
    pdf = _make_pdf(str(tmp_path / "d2.pdf"), 10)
    w = _window(tmp_path)
    try:
        w.open_pdf(pdf)
        w.next_page()
        assert w.current_page == 1
        w.last_page()
        assert w.current_page == 9
    finally:
        w._close_current_pdf()
        w.close()


def test_continuous_scroll_follows_page(qapp, tmp_path):
    """连续模式：滚动到下一页时「当前页」自动跟随。"""
    pdf = _make_pdf(str(tmp_path / "e.pdf"), 20)
    w = _window(tmp_path)
    try:
        w.open_pdf(pdf)
        w._set_read_mode("continuous")
        w.canvas.resize(700, 600)
        assert w.current_page == 0
        w.canvas.pan_by(0, -2500)                      # 向下滚
        _spin(150)
        assert w.canvas.pdf_index > 0, "滚动后当前页应跟随"
        assert w.current_page == w.canvas.pdf_index
    finally:
        w._close_current_pdf()
        w.close()


def test_continuous_renders_visible_pages(qapp, tmp_path):
    """连续模式一次要显示多页，缓存里应同时存在多页图像。"""
    pdf = _make_pdf(str(tmp_path / "f.pdf"), 20)
    w = _window(tmp_path)
    try:
        w.open_pdf(pdf)
        w._set_read_mode("continuous")
        w.canvas.resize(700, 500)
        w.canvas.grab()                                # 触发 _paint_continuous
        _spin(600)
        assert len(w.canvas._render_cache) >= 2, "连续模式应缓存多页"
        lo, hi = w.canvas._visible_page_range()
        assert hi >= lo
    finally:
        w._close_current_pdf()
        w.close()


def test_continuous_zoom_keeps_anchor(qapp, tmp_path):
    """连续模式缩放的锚点必须精确（布局会随缩放重排）。"""
    pdf = _make_pdf(str(tmp_path / "g.pdf"), 20)
    w = _window(tmp_path)
    try:
        w.open_pdf(pdf)
        w._set_read_mode("continuous")
        w.canvas.resize(700, 600)
        w.canvas.pan_by(0, -1200)
        _spin(120)
        anchor = QPointF(350.0, 300.0)
        before = w.canvas._to_page_point(anchor, 1.0)
        w.canvas.zoom_at(anchor, 1.5)
        after = w.canvas._to_page_point(anchor, 1.0)
        assert abs(before[0] - after[0]) < 0.5
        assert abs(before[1] - after[1]) < 0.5
    finally:
        w._close_current_pdf()
        w.close()


def test_continuous_rotation_and_search(qapp, tmp_path):
    """连续模式下的旋转、跳页、搜索高亮都要正常。"""
    pdf = _make_pdf(str(tmp_path / "h.pdf"), 10)
    w = _window(tmp_path)
    try:
        w.open_pdf(pdf)
        w._set_read_mode("continuous")
        w._rotate(90)
        _spin(150)
        assert w.canvas.rotation == 90
        w.goto_page(3)
        assert w.current_page == 3
        w._search_hits = w.pdf_engine.search("alpha")
        w._search_index = 0
        w._apply_search_highlight()
        assert len(w.canvas.search_rects) >= 1
        w.canvas.grab()
        w._rotate(-90)
        _spin(150)
        assert w.canvas.rotation == 0
    finally:
        w._close_current_pdf()
        w.close()


def test_continuous_note_mode_resets(qapp, tmp_path):
    """切回笔记必须回到单页模式（笔记没有「连续阅读」概念）。"""
    pdf = _make_pdf(str(tmp_path / "h2.pdf"), 6)
    w = _window(tmp_path)
    try:
        w.open_pdf(pdf)
        w._set_read_mode("continuous")
        w.new_note()
        assert w.mode == "note"
        assert w.canvas.read_mode == "single"
        assert w.canvas.stroke_provider is None
    finally:
        w._close_current_pdf()
        w.close()


# ====================================================================== #
# 三、阅读进度恢复
# ====================================================================== #
def test_reopen_restores_last_page(qapp, tmp_path):
    pdf = _make_pdf(str(tmp_path / "i.pdf"), 30)
    s = _settings(tmp_path)
    w = MainWindow(settings=s)
    try:
        w.open_pdf(pdf)
        w.goto_page(17)
        w._save_pdf_read_state()
        assert int(s.pdf_read_state(pdf)["page"]) == 17
        s.sync()
    finally:
        w._close_current_pdf()
        w.close()

    w2 = MainWindow(settings=_settings(tmp_path))     # 全新的设置对象：读文件
    try:
        w2.open_pdf(pdf)
        assert w2.current_page == 17, "重开 PDF 应回到上次阅读的页"
    finally:
        w2._close_current_pdf()
        w2.close()


def test_reopen_restores_continuous_mode_and_scroll(qapp, tmp_path):
    pdf = _make_pdf(str(tmp_path / "j.pdf"), 30)
    s = _settings(tmp_path)
    w = MainWindow(settings=s)
    try:
        w.open_pdf(pdf)
        w._set_read_mode("continuous")
        w.canvas.pan_by(0, -3000)
        _spin(150)
        page = w.current_page
        w._save_pdf_read_state()
        s.sync()
    finally:
        w._close_current_pdf()
        w.close()

    w2 = MainWindow(settings=_settings(tmp_path))
    try:
        w2.open_pdf(pdf)
        assert w2.canvas.read_mode == "continuous"
        assert w2.current_page == page
        assert w2.canvas.scroll_offset_frac() > 0.01
    finally:
        w2._close_current_pdf()
        w2.close()


def test_stale_state_ignored_when_file_changed(qapp, tmp_path):
    """文件被替换后，旧的阅读位置必须失效（否则会跳到错误的页）。"""
    pdf = _make_pdf(str(tmp_path / "k.pdf"), 10)
    s = _settings(tmp_path)
    w = MainWindow(settings=s)
    try:
        w.open_pdf(pdf)
        w.goto_page(6)
        w._save_pdf_read_state()
        s.sync()
    finally:
        w._close_current_pdf()
        w.close()

    tmp_pdf = str(tmp_path / "k2.pdf")
    d = _fitz.open()
    for _ in range(3):
        d.new_page()
    d.save(tmp_pdf)
    d.close()
    os.replace(tmp_pdf, pdf)                          # 内容/大小都变了

    w2 = MainWindow(settings=_settings(tmp_path))
    try:
        w2.open_pdf(pdf)
        assert w2.current_page == 0
    finally:
        w2._close_current_pdf()
        w2.close()

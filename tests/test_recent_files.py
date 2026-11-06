"""最近文件管理：置顶 / 分组 / 过滤 / 清理 / 兼容旧数据。"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from src.core.settings import MAX_RECENT, AppSettings


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _window(tmp_path):
    from src.ui.main_window import MainWindow

    return MainWindow(settings=AppSettings(path=str(tmp_path / "s.ini")),
                      restore_session=False, restore_geometry=False)


# ---------------------------------------------------------------------- #
# 设置层
# ---------------------------------------------------------------------- #
def test_add_recent_order_and_cap(tmp_path):
    s = AppSettings(path=str(tmp_path / "s.ini"))
    for i in range(MAX_RECENT + 5):
        s.add_recent(f"/tmp/f{i}.note")
    files = s.recent_files()
    assert len(files) == MAX_RECENT
    assert files[0] == f"/tmp/f{MAX_RECENT + 4}.note", "最新打开的排最前"
    assert AppSettings(path=str(tmp_path / "s.ini")).recent_files() == files


def test_add_recent_moves_existing_to_front(tmp_path):
    s = AppSettings(path=str(tmp_path / "s.ini"))
    s.add_recent("/a.note")
    s.add_recent("/b.note")
    s.add_recent("/a.note")
    assert s.recent_files() == ["/a.note", "/b.note"]


def test_pin_recent_groups_and_not_capped(tmp_path):
    s = AppSettings(path=str(tmp_path / "s.ini"))
    s.add_recent("/a.md")
    s.pin_recent("/a.md", True)
    for i in range(MAX_RECENT + 3):
        s.add_recent(f"/tmp/f{i}.note")
    entries = s.recent_entries()
    assert entries[0]["path"] == "/a.md" and entries[0]["pinned"] is True
    assert sum(1 for e in entries if e["pinned"]) == 1
    assert len(entries) == MAX_RECENT + 1, "置顶项不占最近条目的额度"
    s.pin_recent("/a.md", False)
    assert not any(e["pinned"] for e in s.recent_entries())


def test_prune_keeps_pinned_and_existing(tmp_path):
    s = AppSettings(path=str(tmp_path / "s.ini"))
    here = tmp_path / "here.md"
    here.write_text("x", encoding="utf-8")
    gone = str(tmp_path / "gone.md")
    s.add_recent(str(here))
    s.add_recent(gone)
    s.pin_recent(gone, True)
    kept = s.prune_recent()
    assert str(here) in kept
    assert gone in kept, "置顶项不因文件消失被误删"
    s.clear_recent()
    assert s.recent_files() == []


def test_prune_removes_missing_non_pinned(tmp_path):
    s = AppSettings(path=str(tmp_path / "s.ini"))
    s.add_recent(str(tmp_path / "missing1.md"))
    s.add_recent(str(tmp_path / "missing2.md"))
    assert s.prune_recent() == []


def test_legacy_list_migrates(tmp_path):
    ini = str(tmp_path / "s.ini")
    s = AppSettings(path=ini)
    s._qs.setValue("recent", ["/old/a.pdf", "/old/b.pdf"])
    assert s.recent_files() == ["/old/a.pdf", "/old/b.pdf"]
    s.add_recent("/old/c.md")
    again = AppSettings(path=ini)
    assert again.recent_files()[0] == "/old/c.md"
    assert len(again.recent_files()) == 3


def test_recent_entries_metadata(tmp_path):
    s = AppSettings(path=str(tmp_path / "s.ini"))
    s.add_recent("/x.md")
    e = s.recent_entries()[0]
    assert {"path", "pinned", "ts"} <= set(e)
    assert e["ts"] > 0
    assert e["pinned"] is False


# ---------------------------------------------------------------------- #
# 界面层
# ---------------------------------------------------------------------- #
def test_recent_list_groups_icons_and_filter(qapp, tmp_path):
    w = _window(tmp_path)
    try:
        md = tmp_path / "a.md"
        md.write_text("# A\n", encoding="utf-8")
        pdf = tmp_path / "b.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")      # 只作为最近项，不需要真的打开
        w.settings.add_recent(str(md))
        w.settings.add_recent(str(pdf))
        w.settings.pin_recent(str(md), True)
        w._build_recent_menu()

        assert w.recent_list.count() >= 2
        assert any(w.recent_list.item(i).text().startswith("★")
                   for i in range(w.recent_list.count()))
        assert any("清理失效项" == a.text() for a in w.recent_menu.actions())
        assert any("清空最近文件" == a.text() for a in w.recent_menu.actions())

        w.recent_filter.setText("a.md")
        assert w.recent_list.count() == 1, "过滤后不留空分组标题"
        w.recent_filter.setText("")
        assert w.recent_list.count() >= 2
    finally:
        w.close()


def test_recent_item_kind_and_missing_mark(qapp, tmp_path):
    w = _window(tmp_path)
    try:
        md = tmp_path / "a.md"
        md.write_text("# A\n", encoding="utf-8")
        w.settings.add_recent(str(md))
        w.settings.add_recent(str(tmp_path / "missing.md"))
        w._build_recent_menu()
        texts = {w.recent_list.item(i).text() for i in range(w.recent_list.count())}
        assert "a.md" in texts and "missing.md" in texts
        # 失效项有「文件已不存在」提示
        assert any("文件已不存在" in (w.recent_list.item(i).toolTip() or "")
                   for i in range(w.recent_list.count()))
    finally:
        w.close()

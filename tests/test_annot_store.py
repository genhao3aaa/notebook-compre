"""批注兜底存放（`src/core/annot_store.py`）回归。

线上问题：PDF 放在本程序写不进去的目录（Program Files、共享目录、被同步盘/杀毒锁住的目录）时，
`Ctrl+S` 只能报 `PermissionError`，批注保不住。现在会退到用户可写目录，并且**下次还能找回来**：

    %LOCALAPPDATA%\\手写笔记本与PDF批注\\annotations\\<PDF 路径哈希>-<原文件名>.annot
"""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from src.core import annot_store
from src.core.annot_document import AnnotDocument
from src.core.settings import AppSettings
from src.core.stroke import Stroke
from src.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    """可写探测是带缓存的（真实运行需要）：每个用例前后都清一次，避免互相影响。"""
    annot_store.clear_probe_cache()
    yield
    annot_store.clear_probe_cache()


def _spin(ms: int = 200) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _doc() -> AnnotDocument:
    doc = AnnotDocument.from_pdf("D:/x/y.pdf", "abc", 2)
    doc.pages[1].strokes.append(Stroke(tool="pen", color="#111111", width=3.0,
                                       points=[(1.0, 2.0, 0.5)]))
    return doc


# --------------------------------------------------------------------- #
# 路径解析（纯函数）
# --------------------------------------------------------------------- #
def test_primary_path_is_next_to_pdf(tmp_path):
    pdf = str(tmp_path / "书.pdf")
    assert annot_store.primary_path(pdf) == str(tmp_path / "书.annot")


def test_store_path_is_stable_and_sanitized(tmp_path):
    pdf = str(tmp_path / 'a<b>c.pdf')
    p1, p2 = annot_store.store_path(pdf), annot_store.store_path(pdf)
    assert p1 == p2, "兜底路径必须确定性（否则下次找不回来）"
    assert p1.endswith(".annot")
    assert os.path.basename(p1).find("<") < 0, "文件名里的非法字符要被清掉"
    assert os.path.dirname(p1) == annot_store.store_dir()
    other = annot_store.store_path(str(tmp_path / "另一个.pdf"))
    assert other != p1, "不同 PDF 不能撞到同一个兜底文件"


def test_find_existing_prefers_pdf_side_then_store(tmp_path):
    pdf = str(tmp_path / "doc.pdf")
    assert annot_store.find_existing(pdf) is None

    _doc().save(annot_store.store_path(pdf))
    found = annot_store.find_existing(pdf)
    assert found == (annot_store.store_path(pdf), True)

    _doc().save(annot_store.primary_path(pdf))
    found = annot_store.find_existing(pdf)
    assert found == (annot_store.primary_path(pdf), False), "PDF 旁边的优先"


def test_resolve_for_save_uses_current_when_writable(tmp_path, monkeypatch):
    pdf = str(tmp_path / "doc.pdf")
    monkeypatch.setattr(annot_store, "directory_writable", lambda d: True)
    path, fallback = annot_store.resolve_for_save(pdf, "")
    assert path == annot_store.primary_path(pdf) and fallback is False
    # 上次已经退到兜底位置：继续用兜底位置，不来回跳
    stored = annot_store.store_path(pdf)
    assert annot_store.resolve_for_save(pdf, stored) == (stored, False)


def test_resolve_for_save_falls_back_when_dir_unwritable(tmp_path, monkeypatch):
    pdf = str(tmp_path / "doc.pdf")
    monkeypatch.setattr(annot_store, "directory_writable", lambda d: False)
    path, fallback = annot_store.resolve_for_save(pdf, "")
    assert fallback is True and path == annot_store.store_path(pdf)


def test_saved_beside_unwritable_pdf_still_readable(qapp, tmp_path, monkeypatch):
    """目录不可写时保存：批注落到兜底位置，重新打开还能读回来。"""
    pdf = str(tmp_path / "locked.pdf")
    with open(pdf, "wb") as f:
        f.write(b"%PDF-1.4\n% fake\n")
    monkeypatch.setattr(annot_store, "directory_writable", lambda d: False)

    s = AppSettings(path=str(tmp_path / "s.ini"))
    doc = _doc()
    target, fallback = annot_store.resolve_for_save(pdf, "")
    assert fallback and os.path.isfile(target) is False
    doc.save(target)
    assert os.path.isfile(target)

    back = AnnotDocument.load(annot_store.find_existing(pdf)[0])
    assert len(back.pages[1].strokes) == 1


# --------------------------------------------------------------------- #
# 窗口接线：保存失败时自动改道
# --------------------------------------------------------------------- #
def test_annot_save_falls_back_when_primary_fails(qapp, tmp_path, monkeypatch):
    """`<pdf>.annot` 写不进去 → 自动改存兜底位置，且标签记住新位置。"""
    import pymupdf as fitz

    from src.core import serializer

    pdf = str(tmp_path / "book.pdf")
    d = fitz.open()
    d.new_page(width=595, height=842)
    d.save(pdf)
    d.close()

    win = MainWindow(settings=AppSettings(path=str(tmp_path / "s.ini")),
                     restore_session=False)
    try:
        tab = win.open_pdf(pdf)
        _spin(300)
        # 让「PDF 旁边的 .annot」一定失败（目录不可写），兜底位置保持可写
        real_writable = annot_store.directory_writable
        monkeypatch.setattr(annot_store, "directory_writable",
                            lambda d: False if os.path.normcase(d) == os.path.normcase(
                                os.path.dirname(annot_store.primary_path(pdf))) else
                            real_writable(d))

        tab.annot_doc.pages[0].strokes.append(
            Stroke(tool="pen", color="#000000", width=2.0, points=[(1.0, 1.0, 0.5)]))
        win.save()
        assert win._saver.wait_for_idle(8000)

        stored = annot_store.store_path(pdf)
        assert os.path.isfile(stored), "应改存到兜底位置"
        assert not os.path.isfile(annot_store.primary_path(pdf))
        assert os.path.normcase(tab.annot_path) == os.path.normcase(stored)
        assert len(AnnotDocument.load(stored).pages[0].strokes) == 1
        assert win.last_save_error == "", "改道成功后不该留下错误提示"

        # 再打开一次：必须从兜底位置读回批注
        win.close_tab(tab)
        tab2 = win.open_pdf(pdf)
        _spin(300)
        assert len(tab2.annot_doc.pages[0].strokes) == 1, "兜底位置的批注要能找回"
    finally:
        try:
            serializer.write_atomic(str(tmp_path / "probe.txt"), b"ok")
        except OSError:
            pass
        for t in list(win._tabs):
            t.dirty = False
        win._confirm_save = lambda *a, **k: True
        win.close()

# --------------------------------------------------------------------- #
# 第二轮（2026-10-01）：兜底目录本身也写不进去时，沿候选链继续换位置
# --------------------------------------------------------------------- #
def test_candidates_cover_history_and_temp(monkeypatch):
    """候选链必须包含历史兜底位置与系统临时目录，且顺序稳定、不重复。"""
    monkeypatch.delenv(annot_store.ENV_ANNOT_DIR, raising=False)
    cands = annot_store.store_candidates()
    assert cands, "候选链不能为空"
    assert len(cands) == len({os.path.normcase(os.path.abspath(c)) for c in cands}), "候选不能重复"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        expect = os.path.join(local, annot_store.APP_DIR_NAME, annot_store.STORE_SUBDIR)
        assert os.path.normcase(expect) in {os.path.normcase(c) for c in cands}, \
            "历史兜底位置必须还在链上（老用户的批注要能找回）"
    assert os.path.normcase(annot_store.store_dir()) in {os.path.normcase(c) for c in cands}


def _deny_dirs(monkeypatch, denied_dirs):
    """让 probe_writable 对指定目录一律返回 False，其余目录按真实情况探测。"""
    denied = {os.path.normcase(os.path.abspath(d)) for d in denied_dirs}
    real = annot_store.directory_writable
    monkeypatch.setattr(
        annot_store, "directory_writable",
        lambda d: False if os.path.normcase(os.path.abspath(d)) in denied else real(d))
    annot_store.clear_probe_cache()


def test_store_path_kept_when_dir_writable(tmp_path, monkeypatch):
    """PDF 旁边的 .annot 写不进去 → 用候选链里第一个可写的位置。"""
    pdf = str(tmp_path / "doc.pdf")
    # pytest 的 tmp_path 落在系统临时目录下（本来可写），所以必须显式声明
    # 「PDF 旁边不可写」；候选链则按真实情况探测。
    _deny_dirs(monkeypatch, [str(tmp_path)])
    path, fallback = annot_store.resolve_for_save(pdf, "")
    assert fallback is True
    assert path == annot_store.store_path(pdf)
    assert os.path.dirname(path) == annot_store.store_dir()


def test_resolve_switches_to_next_writable_candidate(tmp_path, monkeypatch):
    """首选兜底目录也不可写时，必须继续往后挑（线上截图就是这种环境）。"""
    pdf = str(tmp_path / "doc.pdf")
    cands = annot_store.store_candidates()
    assert len(cands) >= 2, "至少要有两个候选才能验证「继续往后挑」"
    _deny_dirs(monkeypatch, [str(tmp_path), cands[0]])
    path, fallback = annot_store.resolve_for_save(pdf, "")
    assert fallback is True
    assert os.path.normcase(os.path.dirname(path)) != os.path.normcase(cands[0]), \
        "钉死第一个候选目录就会重现线上那次失败"


def test_next_target_walks_the_chain_and_excludes_failures(tmp_path, monkeypatch):
    pdf = str(tmp_path / "doc.pdf")
    monkeypatch.delenv(annot_store.ENV_ANNOT_DIR, raising=False)
    primary = annot_store.primary_path(pdf)
    store = annot_store.store_path(pdf)
    monkeypatch.setitem(annot_store._WRITABLE_CACHE, annot_store._norm(str(tmp_path)), True)
    assert annot_store.next_target(pdf) == primary
    assert annot_store.next_target(pdf, exclude=primary) == store, \
        "排除了失败位置后要给出下一个可写目标"


def test_env_var_overrides_store_dir(tmp_path, monkeypatch):
    """HN_ANNOT_DIR：受限环境 / 想让批注跟工作区一起走时显式指定位置。"""
    custom = tmp_path / "my-annots"
    custom.mkdir()
    monkeypatch.setenv(annot_store.ENV_ANNOT_DIR, str(custom))
    monkeypatch.setitem(annot_store._WRITABLE_CACHE, annot_store._norm(str(custom)), True)
    try:
        assert annot_store.store_candidates()[0] == str(custom)
        assert annot_store.store_dir() == str(custom)
        assert annot_store.env_dir_in_use() is True
    finally:
        annot_store.clear_probe_cache()


def test_save_falls_through_to_temp_when_appdata_locked(qapp, tmp_path, monkeypatch):
    """端到端：PDF 旁边 + 首选兜底目录都不可写时，批注仍要落到可写候选并能读回。"""
    import pymupdf as fitz

    from src.core import serializer

    pdf = str(tmp_path / "locked.pdf")
    d = fitz.open()
    d.new_page(width=595, height=842)
    d.save(pdf)
    d.close()

    real = annot_store.directory_writable
    pdf_dir = os.path.normcase(str(tmp_path))
    cands = annot_store.store_candidates()
    locked = {os.path.normcase(c) for c in cands[:1] if c}     # 首选兜底目录也锁住
    monkeypatch.setattr(
        annot_store, "directory_writable",
        lambda d: False if os.path.normcase(os.path.abspath(d)) in (locked | {pdf_dir})
        else real(d))
    annot_store.clear_probe_cache()

    win = MainWindow(settings=AppSettings(path=str(tmp_path / "s.ini")),
                     restore_session=False)
    try:
        tab = win.open_pdf(pdf)
        _spin(300)
        tab.annot_doc.pages[0].strokes.append(
            Stroke(tool="pen", color="#000000", width=2.0, points=[(5.0, 5.0, 0.5)]))
        win.save()
        assert win._saver.wait_for_idle(8000)
        assert win.last_save_error == "", f"不该报错：{win.last_save_error}"
        assert tab.annot_path and os.path.isfile(tab.annot_path), \
            f"批注必须落到可写候选：{tab.annot_path}"
        assert os.path.normcase(os.path.dirname(tab.annot_path)) != pdf_dir
        expected_dirs = {os.path.normcase(c) for c in cands[1:] if c}
        assert os.path.normcase(os.path.dirname(tab.annot_path)) in expected_dirs, \
            "应落到候选链里第一个可写目录"
        assert len(AnnotDocument.load(tab.annot_path).pages[0].strokes) == 1

        win.close_tab(tab)
        tab2 = win.open_pdf(pdf)
        _spin(300)
        assert len(tab2.annot_doc.pages[0].strokes) == 1, "换位置存的批注要能找回"
    finally:
        annot_store.clear_probe_cache()
        try:
            serializer.write_atomic(str(tmp_path / "probe.txt"), b"ok")
        except OSError:
            pass
        for t in list(win._tabs):
            t.dirty = False
        win._confirm_save = lambda *a, **k: True
        win.close()


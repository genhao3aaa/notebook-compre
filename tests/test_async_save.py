"""A4 验收：后台保存（小文档同步、大文档不阻塞 UI、失败不弹模态框）。

关键口径：`window.save()` 在 GUI 线程里只做快照，编码 + 写盘落到后台线程；
因此「保存 50 页」时界面不能卡住。测试用「把写盘任务卡在 Event 上」来
确定性地证明 save() 没有等写盘完成。
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from src.core import serializer
from src.core.settings import AppSettings
from src.core.stroke import AddStrokeCmd, UndoManager
from src.ui.main_window import MainWindow
from src.ui.save_worker import AsyncSaver

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "benchmarks"))

import _common as C  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _close(win) -> None:
    try:
        for t in list(win._tabs):
            t.dirty = False
        win._confirm_save = lambda *a, **k: True
        win.close()
    except Exception:
        pass


def _window_with_note(tmp_path, strokes=5, path=None):
    win = MainWindow(settings=AppSettings(path=str(tmp_path / "s.ini")))
    doc = win.note_doc
    page = doc.pages[0]
    canvas = win.canvas
    # 让画布直接挂在文档这一页上（替换成新列表的话 save() 会写到空文档）
    canvas.page_strokes = page.strokes
    canvas.undo_mgr = page.undo
    for s in C.make_strokes(strokes, layout="realistic"):
        canvas.undo_mgr.push(AddStrokeCmd(s))
    win._active.path = path or str(tmp_path / "note.note")
    win.mark_dirty()
    return win


# --------------------------------------------------------------------- #
# AsyncSaver 本体
# --------------------------------------------------------------------- #
def test_small_task_is_written_synchronously(qapp, tmp_path):
    saver = AsyncSaver(sync_threshold=1000)
    seen = []
    saver.finished.connect(lambda p, ok, e: seen.append((p, ok, e)))
    done = saver.save(str(tmp_path / "x"), lambda: None, points=10)
    assert done is True
    assert seen and seen[0][1] is True
    assert saver.is_busy() is False
    saver.shutdown()


def test_large_task_runs_in_background_and_reports(qapp, tmp_path):
    saver = AsyncSaver(sync_threshold=0)
    seen = []
    saver.finished.connect(lambda p, ok, e: seen.append((p, ok, e)))
    started = threading.Event()
    order = []

    def slow():
        started.set()
        time.sleep(0.05)
        order.append("write")

    done = saver.save(str(tmp_path / "x"), slow, points=10_000)
    assert done is False, "超过阈值必须走后台线程"
    assert started.wait(2.0)
    assert saver.wait_for_idle(5000) is True
    assert order == ["write"]
    assert seen and seen[0][1] is True
    saver.shutdown()


def test_failure_is_reported_not_raised(qapp):
    saver = AsyncSaver(sync_threshold=0)
    seen = []
    saver.finished.connect(lambda p, ok, e: seen.append((p, ok, e)))

    def boom():
        raise OSError("磁盘满了")

    saver.save("p", boom, points=10_000)
    assert saver.wait_for_idle(5000)
    assert seen and seen[0][1] is False
    assert "磁盘满了" in seen[0][2]
    saver.shutdown()


def test_shutdown_is_idempotent(qapp):
    saver = AsyncSaver(sync_threshold=1000)
    saver.shutdown()
    saver.shutdown()
    assert saver.is_busy() is False


# --------------------------------------------------------------------- #
# 窗口接线
# --------------------------------------------------------------------- #
def test_window_saves_small_note_synchronously(qapp, tmp_path):
    path = str(tmp_path / "small.note")
    win = _window_with_note(tmp_path, strokes=5, path=path)
    try:
        win.save()
        assert os.path.exists(path), "小文档应同步写完（测试与「存完就打开」场景不竞态）"
        assert win._active.dirty is False
        from src.core.note_document import NoteDocument

        assert len(NoteDocument.load(path).pages[0].strokes) == 5
    finally:
        _close(win)


def test_window_save_does_not_block_ui(qapp, tmp_path, monkeypatch):
    """大文档保存：save() 必须立刻返回，写盘在后台线程完成。"""
    path = str(tmp_path / "big.note")
    win = _window_with_note(tmp_path, strokes=200, path=path)
    win._saver._threshold = 0                      # 强制走后台
    gate = threading.Event()
    started = threading.Event()
    real = serializer.write_snapshot

    def slow_write(p, kind, meta, pages):
        started.set()
        gate.wait(10.0)
        real(p, kind, meta, pages)

    monkeypatch.setattr(serializer, "write_snapshot", slow_write)
    try:
        t0 = time.perf_counter()
        win.save()
        elapsed = time.perf_counter() - t0
        assert started.wait(3.0), "写盘没有在后台线程开始"
        assert elapsed < 0.25, f"save() 阻塞了 {elapsed * 1000:.0f} ms（应只做快照）"
        assert not os.path.exists(path), "写盘被卡住时文件不应该已经完成"
        gate.set()
        assert win._saver.wait_for_idle(8000) is True
        assert os.path.exists(path)
        assert win._active.dirty is False
    finally:
        gate.set()
        win._saver.wait_for_idle(8000)
        _close(win)


def test_window_save_failure_keeps_dirty_and_hints(qapp, tmp_path):
    """后台保存失败：标签标回「脏」、记录错误、不弹模态框（不阻塞书写）。"""
    bad_dir = tmp_path / "no_such_dir"
    path = str(bad_dir / "x.note")
    win = _window_with_note(tmp_path, strokes=5, path=path)
    try:
        win.save()
        assert win.last_save_error, "失败必须记录原因"
        assert win._active.dirty is True, "保存失败后不能清掉未保存标记"
        assert win._pending_saves == {}
        assert not bad_dir.exists()
    finally:
        _close(win)


def test_close_waits_for_pending_save(qapp, tmp_path):
    """关窗口前必须等在途保存写完，否则会丢最后一次保存。"""
    path = str(tmp_path / "pending.note")
    win = _window_with_note(tmp_path, strokes=50, path=path)
    win._saver._threshold = 0
    try:
        win.save()
        assert win._saver.wait_for_idle(8000) is True
        assert os.path.exists(path)
        _close(win)
        assert os.path.exists(path)
        assert win._saver.is_busy() is False
    finally:
        _close(win)

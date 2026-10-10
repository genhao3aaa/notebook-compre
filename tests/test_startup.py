"""A6 验收：启动路径上不该做的事，别在启动时做。

两项：
1. **PyMuPDF 惰性导入**：`import src.ui.main_window`（也就是启动路径）不应把
   pymupdf 拉进来——新建空白笔记用不到它，实测热缓存下 ~60 ms；
2. **缩略图线程池惰性启动**：空白笔记启动时不起 4 个线程。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _run_child(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True)


def test_startup_does_not_import_pymupdf():
    """必须在**新进程**里验证：pytest 进程本身已经 import 了 pymupdf（造 PDF）。"""
    code = (
        "import sys;"
        "sys.path.insert(0, '.');"
        "import src.ui.main_window;"
        "print('LOADED' if 'pymupdf' in sys.modules else 'LAZY')"
    )
    proc = _run_child(code)
    assert proc.returncode == 0, proc.stderr
    assert "LAZY" in proc.stdout, f"启动路径把 pymupdf 拉进来了：{proc.stdout}"


def test_pdf_engine_imports_pymupdf_on_demand():
    code = (
        "import sys;"
        "sys.path.insert(0, '.');"
        "from src.render import pdf_engine;"
        "before = pdf_engine.fitz_loaded();"
        "m = pdf_engine.fitz_module();"
        "print(before, pdf_engine.fitz_loaded(), bool(m))"
    )
    proc = _run_child(code)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split()[0] == "False"
    assert proc.stdout.split()[1] == "True"


def test_thumbnail_pool_starts_threads_lazily(qapp):
    from src.render.thumbnail_pool import ThumbnailPool

    pool = ThumbnailPool(workers=3)
    try:
        assert pool.is_running() is False, "构造时不应创建线程"
        assert pool._threads == []
        # 空批次也不应起线程
        pool.submit("nonexistent.pdf", [])
        assert pool.is_running() is False
        # 真有任务时才起
        pool.submit("nonexistent.pdf", [0])
        assert pool.is_running() is True
        assert len(pool._threads) == 3
    finally:
        pool.shutdown()
    assert pool.is_running() is False


def test_main_window_note_startup_has_no_pdf_threads(qapp, tmp_path):
    """新建空白笔记（最常见启动路径）不应起缩略图线程、也不该 import pymupdf。"""
    code = (
        "import sys;"
        "sys.path.insert(0, '.');"
        "from PySide6.QtWidgets import QApplication;"
        "from src.core.settings import AppSettings;"
        "app = QApplication([]);"
        "from src.ui.main_window import MainWindow;"
        "w = MainWindow(settings=AppSettings(path=r'%s'));"
        "print('THREADS', w.thumbnails._pool.is_running());"
        "print('PYMUPDF', 'pymupdf' in sys.modules);"
        "w._confirm_save = lambda *a, **k: True;"
        "[setattr(t, 'dirty', False) for t in w._tabs];"
        "w.close()"
    ) % os.path.join(tmp_path, "startup.ini")
    proc = _run_child(code)
    assert proc.returncode == 0, proc.stderr
    assert "THREADS False" in proc.stdout, proc.stdout
    assert "PYMUPDF False" in proc.stdout, proc.stdout


def _spin(ms: int = 200) -> None:
    """等 Qt 事件循环把页面/标签相关的事处理完。"""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_cli_file_arguments_open_tabs(qapp, tmp_path):
    """命令行给的文件（拖到 exe 上 / 右键打开）要能开成标签，坏路径要忽略。"""
    import main as entry
    from src.core.document_tab import NOTE, PDF
    from src.core.note_document import NoteDocument
    from src.core.settings import AppSettings
    from src.ui.main_window import MainWindow

    import pymupdf as fitz

    pdf = str(tmp_path / "cli.pdf")
    d = fitz.open()
    for _ in range(3):
        d.new_page(width=595, height=842)
    d.save(pdf)
    d.close()
    note = str(tmp_path / "cli.note")
    NoteDocument().save(note)

    win = MainWindow(settings=AppSettings(path=str(tmp_path / "s.ini")),
                     restore_session=False)
    try:
        entry._open_paths(win, [pdf, note, str(tmp_path / "missing.pdf"),
                                str(tmp_path / "unknown.txt")])
        _spin(300)
        kinds = [(t.kind, os.path.basename(t.path or "")) for t in win._tabs]
        assert (PDF, "cli.pdf") in kinds, kinds
        assert (NOTE, "cli.note") in kinds, kinds
        assert all("missing" not in (t.path or "") for t in win._tabs)
        assert len(win._tabs) == 3, "无效参数不应开标签"
    finally:
        win._confirm_save = lambda *a, **k: True
        for t in list(win._tabs):
            t.dirty = False
        win.close()

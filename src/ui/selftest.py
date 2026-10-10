"""打包后自检（A5）：把「启动 → 打开 PDF → 手写 → 导出 → 保存」整条链跑一遍。

为什么需要它：PyInstaller 的裁剪最容易伤到平台插件、Qt 模块依赖、PyMuPDF/PIL 的
原生扩展——「exe 能启动」不等于「功能还在」。这个自检在**打包产物内部**运行，
因此能覆盖运行时才加载的东西（PyMuPDF 是惰性导入的，光启动根本不碰它）。

用法：
    <exe> --selftest [工作目录]        # 打包产物
    python main.py --selftest          # 源码（同一套逻辑）
成功打印 `SELFTEST OK` 并返回 0；失败打印 `SELFTEST FAILED: ...` 并返回非 0。
打包成 `-w`（无控制台）时 stdout 看不到，因此同时在 `<工作目录>/selftest.log` 写日志。
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional


def _spin(ms: int = 300) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _close(win) -> None:
    """收尾：不弹「是否保存」模态框（自检里没人点它）。"""
    try:
        for t in list(win._tabs):
            t.dirty = False
        win._confirm_save = lambda *a, **k: True
        win.close()
    except Exception:
        pass


def run_selftest(workdir: Optional[str] = None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # 每次都用一个**新的子目录**：同一个目录重跑时，上一次留下的 .pdf/.annot/settings.ini
    # 会让 `open_pdf` 走到错误分支并弹出模态框（无人点击 → 永久挂住）。
    # 受限文件环境（ACL / 沙箱）下 `mkdtemp` 的 0o700 目录连创建者都进不去，
    # 下一步 PyMuPDF 保存就会 `Permission denied`：先做一次可写探测并修正。
    try:
        from env_compat import ensure_writable_tempdir

        ensure_writable_tempdir()
    except Exception:                        # 打包环境里没有这个模块也不影响
        pass
    root = workdir or tempfile.mkdtemp(prefix="hn-selftest-")
    os.makedirs(root, exist_ok=True)
    work = tempfile.mkdtemp(prefix="run-", dir=root)
    log_path = os.path.join(work, "selftest.log")
    log_lines = []

    def say(msg: str) -> None:
        print(msg, flush=True)
        log_lines.append(msg)
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines) + "\n")
        except Exception:
            pass

    say(f"[selftest] 工作目录：{work}")
    win = None
    try:
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])

        # ---------------- 1. PyMuPDF：造一份 PDF ----------------
        say("[selftest] 1/5 创建 PDF（PyMuPDF）")
        import pymupdf as fitz

        pdf = os.path.join(work, "selftest.pdf")
        doc = fitz.open()
        for i in range(3):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 100), f"selftest page {i + 1} alpha")
        doc.set_toc([[1, f"第 {i + 1} 章", i + 1] for i in range(3)])
        doc.save(pdf)
        doc.close()

        # ---------------- 2. 打开 PDF + 目录 + 渲染 ----------------
        say("[selftest] 2/5 打开 PDF / 目录 / 页面渲染")
        from src.core.settings import AppSettings
        from src.ui.main_window import MainWindow

        win = MainWindow(settings=AppSettings(path=os.path.join(work, "settings.ini")),
                         restore_session=False)
        win.open_pdf(pdf)
        _spin(600)
        assert win.mode == "pdf", "打开 PDF 后模式不对"
        assert len(win._last_outline) == 3, "目录没读出来"
        assert not win.canvas.page_pixmap.isNull(), "页面没有渲染出来"

        # ---------------- 3. 手写一笔（压感渲染 + 脏矩形重绘） ----------------
        say("[selftest] 3/5 手写一笔（压感渲染 + 脏矩形重绘）")
        from src.core.stroke import AddStrokeCmd, Stroke, TOOL_PEN

        canvas = win.canvas
        stroke = Stroke(tool=TOOL_PEN, color="#111111", width=3.0,
                        points=[(60 + i * 4.0, 200 + (i % 9) * 6.0, 0.2 + 0.08 * (i % 10))
                                for i in range(120)])
        canvas.page_strokes = win.annot_doc.pages[canvas.pdf_index].strokes
        canvas.undo_mgr = win.annot_doc.pages[canvas.pdf_index].undo
        canvas.undo_mgr.push(AddStrokeCmd(stroke))
        canvas.rebuild_all()
        img = canvas.page_pixmap.toImage()
        inked = any(img.pixelColor(x, y).alpha() > 0
                    for x in range(0, min(400, img.width()), 7)
                    for y in range(0, min(400, img.height()), 7))
        assert inked, "笔画没有画到页面上"

        # ---------------- 4. 保存 .annot（v2 二进制）+ 导出 PDF ----------------
        say("[selftest] 4/5 保存批注（v2，含 MD5）+ 导出带批注 PDF")
        win.save()
        assert win._saver.wait_for_idle(8000), "批注保存没有完成"
        annot = os.path.splitext(pdf)[0] + ".annot"
        assert os.path.exists(annot), "没有生成 .annot"

        out_pdf = os.path.join(work, "exported.pdf")
        win.pdf_engine.export_with_annotations(win.annot_doc, out_pdf)
        assert os.path.getsize(out_pdf) > 1000, "导出的 PDF 不正常"

        from src.core.annot_document import AnnotDocument

        back = AnnotDocument.load(annot)
        total = sum(len(p.strokes) for p in back.pages)
        assert total == 1, f"批注读回不完整：{total}"

        # ---------------- 5. 新建笔记 + 保存 .note + 读回 ----------------
        say("[selftest] 5/5 新建笔记 / 保存 .note / 读回")
        win.new_note()
        _spin(150)
        note_stroke = Stroke(tool=TOOL_PEN, color="#000000", width=3.0,
                             points=[(50 + i * 3.0, 300 + (i % 7) * 4.0, 0.5)
                                     for i in range(80)])
        ncanvas = win.canvas
        ncanvas.page_strokes = win.note_doc.pages[0].strokes
        ncanvas.undo_mgr = win.note_doc.pages[0].undo
        ncanvas.undo_mgr.push(AddStrokeCmd(note_stroke))
        ncanvas.rebuild_all()
        note_path = os.path.join(work, "selftest.note")
        win._active.path = note_path
        win.save()
        assert win._saver.wait_for_idle(8000), "笔记保存没有完成"

        from src.core.note_document import NoteDocument

        back_note = NoteDocument.load(note_path)
        assert len(back_note.pages[0].strokes) == 1, "笔记读回不完整"

        say("SELFTEST OK")
        _finish(0)
        return 0
    except Exception as e:                      # pragma: no cover - 只在自检失败时走
        import traceback
        say(traceback.format_exc())
        say(f"SELFTEST FAILED: {type(e).__name__}: {e}")
        _finish(1)
        return 1
    finally:
        if win is not None:
            _close(win)


def _finish(code: int) -> None:
    """自检结束时**硬退出**。

    自检没有走 `app.exec()`，打包成 `-w` 之后解释器正常退出会被 Qt/平台插件拖住
    （实测进程一直活着、退出码拿不到）。这里是诊断入口，直接 `os._exit` 最干净；
    正常启动路径完全不受影响（它走 app.exec() → main 返回）。
    """
    import sys

    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)

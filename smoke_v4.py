"""v4 冒烟测试：PDF 阅读器功能 + 笔记背景色（offscreen 运行）。"""
import os
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_compat import ensure_writable_tempdir

ensure_writable_tempdir()  # 受限沙箱下 mkdtemp 目录不可写，见 env_compat.py

import pymupdf as fitz
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


def spin(ms=800):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def close_without_prompt(win):
    """关窗口但不弹「是否保存」——冒烟脚本专用。

    `closeEvent` 遇到脏标签会弹**模态**对话框，脚本里没人点它 → 永久卡住
    （基线版本就卡在这里，不是后来改出来的）。测试收尾统一走「不询问」路径。
    """
    try:
        for t in list(getattr(win, "_tabs", [])):
            t.dirty = False
        win._confirm_save = lambda *a, **k: True
    except Exception:
        pass
    win.close()


def make_pdf(path, npages=4):
    d = fitz.open()
    for i in range(npages):
        pg = d.new_page()
        pg.insert_text((72, 100), f"Chapter {i+1} sample text alpha")
    d.set_toc([[1, f"第 {i+1} 章", i + 1] for i in range(npages)])
    d.save(path)
    d.close()
    return path


def main():
    app = QApplication.instance() or QApplication([])
    tmp = tempfile.mkdtemp()
    pdf = make_pdf(os.path.join(tmp, "book.pdf"))

    w = MainWindow()
    w.show()
    spin()

    # ---- 打开 PDF ----
    w.open_pdf(pdf)
    spin()
    assert w.mode == "pdf"
    assert len(w._last_outline) == 4, "目录应为 4 条"
    assert w.side_tabs.isTabEnabled(1) and w.side_tabs.isTabEnabled(2)
    print("[1] 打开 PDF + 目录 OK")

    # ---- 目录跳转 ----
    w._on_outline_jump(2, 36.0)
    assert w.current_page == 2, f"目录跳转失败: {w.current_page}"
    print("[2] 目录跳转 OK")

    # ---- 页码跳转 ----
    w.goto_page(0)
    w.last_page()
    assert w.current_page == 3
    w.first_page()
    assert w.current_page == 0
    w.page_spin.setValue(3)
    assert w.current_page == 2
    print("[3] 页码跳转 OK")

    # ---- 旋转 ----
    w._rotate(90)
    spin()
    assert w.canvas.rotation == 90
    pw, ph = w.pdf_engine.page_size(0)
    dw, dh = w.canvas.display_size_pt()
    assert abs(dw - ph) < 0.01 and abs(dh - pw) < 0.01, "旋转后显示尺寸应宽高互换"
    # 旋转后再画一笔：坐标应存原始页面坐标
    from src.core.stroke import Stroke
    s = Stroke(tool="pen", color="#000000", width=3.0)
    s.points = [(10, 10, 1.0), (200, 200, 1.0)]
    w.annot_doc.pages[w.current_page].strokes.append(s)
    w.canvas.rebuild_pixmap()
    w._rotate(-90)
    spin()
    assert w.canvas.rotation == 0
    print("[4] 旋转 + 坐标一致性 OK")

    # ---- 搜索（同步走引擎 + 高亮过滤）----
    hits = w.pdf_engine.search("alpha")
    assert len(hits) == 4
    w._search_hits = hits
    w._search_index = 2
    w.goto_page(1)
    w._apply_search_highlight()
    assert len(w.canvas.search_rects) == 1, "当前页应只有 1 个高亮"
    w.goto_page(2)
    w._apply_search_highlight()
    assert len(w.canvas.search_rects) == 1
    w.canvas.clear_search()
    print("[5] 搜索 + 高亮过滤 OK")

    # ---- 视图模式 ----
    w._fit_width()
    w._actual_size()
    assert abs(w.canvas.scale - 1.0) < 1e-9
    w._fit_view()
    print("[6] 视图模式 OK")

    # ---- 切回笔记 + 背景色 ----
    w.new_note()
    assert w.mode == "note"
    w._apply_bg_color("#fdf6e3")
    assert w.note_doc.background_color == "#fdf6e3"
    assert w.canvas.bg_color == "#fdf6e3"
    img = w.canvas.page_pixmap.toImage()
    c = img.pixelColor(img.width() // 2, img.height() // 2)
    assert c.name().lower() == "#fdf6e3", f"画布底色未生效: {c.name()}"
    w.combo_bg.setCurrentIndex(w.combo_bg.findData("grid"))
    assert w.note_doc.background == "grid"
    print("[7] 笔记背景色 OK")

    # ---- 保存/加载 round-trip ----
    path = os.path.join(tmp, "bg.note")
    w.note_doc.save(path)
    from src.core.note_document import NoteDocument
    doc2 = NoteDocument.load(path)
    assert doc2.background_color == "#fdf6e3" and doc2.background == "grid"
    print("[8] .note 保存/加载 OK")

    # ---- 文档信息 / 复制文本（不弹窗路径）----
    w.open_pdf(pdf)
    spin()
    text = w.pdf_engine.page_text(0)
    assert "alpha" in text
    w._close_current_pdf()
    print("[9] 页面文本 OK")

    close_without_prompt(w)
    print("SMOKE ALL PASSED")


if __name__ == "__main__":
    main()

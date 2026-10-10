"""v5 冒烟测试：浏览器式多标签（offscreen 运行）。

跑一遍真实使用路径：混开笔记与 PDF → 切换保留状态 → 关闭释放 → 会话恢复。
"""
import os
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_compat import ensure_writable_tempdir

ensure_writable_tempdir()  # 受限沙箱下 mkdtemp 目录不可写，见 env_compat.py

import pymupdf as fitz
from PySide6.QtCore import QEventLoop, QPointF, QTimer
from PySide6.QtWidgets import QApplication

from src.core.settings import AppSettings
from src.ui.main_window import MainWindow


def spin(ms=700):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def close_without_prompt(win):
    """关窗口但不弹「是否保存」——冒烟脚本专用（脏标签会弹模态框卡死脚本）。"""
    try:
        for t in list(getattr(win, "_tabs", [])):
            t.dirty = False
        win._confirm_save = lambda *a, **k: True
    except Exception:
        pass
    win.close()


def make_pdf(path, npages=4, tag="alpha"):
    d = fitz.open()
    for i in range(npages):
        pg = d.new_page()
        pg.insert_text((72, 100), f"Chapter {i+1} sample text {tag}")
    d.set_toc([[1, f"第 {i+1} 章", i + 1] for i in range(npages)])
    d.save(path)
    d.close()
    return path


def draw(canvas, pts):
    css = [canvas.page_to_css(x, y) for x, y in pts]
    canvas._pointer_down(QPointF(css[0].x(), css[0].y()), 1.0)
    for p in css[1:]:
        canvas._pointer_move(QPointF(p.x(), p.y()), 1.0)
    canvas._pointer_up()


def main():
    app = QApplication.instance() or QApplication([])
    tmp = tempfile.mkdtemp()
    pdf_a = make_pdf(os.path.join(tmp, "a.pdf"), 4, "alpha")
    pdf_b = make_pdf(os.path.join(tmp, "b.pdf"), 6, "beta")

    ini = os.path.join(tmp, "smoke.ini")
    w = MainWindow(settings=AppSettings(path=ini))
    w.show()
    spin()

    # ---- [1] 混开：PDF -> 笔记 -> PDF ----
    ta = w.open_pdf(pdf_a)
    spin()
    note = w.new_tab_note()
    tb = w.open_pdf(pdf_b)
    spin()
    assert len(w._tabs) == 4, f"应有 4 个标签，实际 {len(w._tabs)}"
    assert w.tab is tb and w.mode == "pdf"
    print("[1] 混开 笔记/PDF 标签 OK")

    # ---- [2] 切换保留页码与缩放 ----
    w._switch_to_tab(ta)
    w.goto_page(2)
    w.canvas.zoom_to(2.0)
    scale_a = w.canvas.scale
    w._switch_to_tab(tb)
    assert w.current_page == 0, "另一个标签应从第 1 页开始"
    w._switch_to_tab(ta)
    assert w.current_page == 2, f"切回后应停在第 3 页，实际 {w.current_page + 1}"
    assert abs(w.canvas.scale - scale_a) < 1e-6, "切回后缩放应保留"
    print("[2] 切换保留页码/缩放 OK")

    # ---- [3] 标签互相隔离（在笔记上画一笔） ----
    w._switch_to_tab(note)
    draw(note.canvas, [(100, 100), (200, 200), (300, 280)])
    n_strokes = len(note.note_doc.pages[0].strokes)
    assert n_strokes >= 1, "这一笔应落在笔记标签上"
    w._switch_to_tab(ta)
    w._switch_to_tab(note)
    assert len(note.note_doc.pages[0].strokes) == n_strokes, "切回来笔画应还在"
    assert note.dirty is True, "画过之后标签应标记为未保存"
    assert "*" in w.tab_widget.tabText(w.tab_widget.indexOf(note.canvas))
    print("[3] 标签状态隔离 OK")

    # ---- [4] 重复打开同一文件：不新增标签 ----
    before = len(w._tabs)
    w.open_pdf(pdf_a)
    assert len(w._tabs) == before, "同一 PDF 不应开成两个标签"
    assert w.tab is ta, "应切回已有的那个标签"
    print("[4] 同一文件不重复开标签 OK")

    # ---- [5] 快捷键切换 ----
    w.goto_tab_number(1)
    assert w.tab is w._tabs[0]
    w.cycle_tab(1)
    assert w.tab is w._tabs[1]
    w.cycle_tab(-1)
    assert w.tab is w._tabs[0]
    print("[5] Ctrl+N / Ctrl+Tab 切换 OK")

    # ---- [6] 搜索（后台线程 -> 高亮当前标签） ----
    w._switch_to_tab(ta)
    w._start_search("alpha", False, False)
    spin(1500)
    assert len(ta.search_hits) >= 4, f"应搜到 >=4 处，实际 {len(ta.search_hits)}"
    assert w.canvas.search_rects, "当前页应有命中高亮"
    print(f"[6] 搜索 {len(ta.search_hits)} 处并高亮 OK")

    # ---- [7] 关闭标签释放 PDF ----
    engine = tb.pdf_engine
    w.close_tab(tb)
    assert tb not in w._tabs
    doc = getattr(engine, "doc", None)
    if doc is not None:
        assert doc.is_closed, "fitz 文档应已 close()"
    assert len(w._tabs) == 3
    print("[7] 关闭标签释放 PDF 句柄 OK")

    # ---- [8] 会话保存与恢复 ----
    # 此时还剩 3 个标签：空白笔记(无路径) + pdf_a(有路径) + 笔记(未保存，无路径)
    w._save_session()
    w.settings.sync()
    restored = AppSettings(path=ini).open_tabs()
    assert len(restored) == 1, f"只应记住有路径的那 1 个，实际 {len(restored)}"
    assert os.path.normcase(restored[0]["path"]) == os.path.normcase(pdf_a)

    w2 = MainWindow(settings=AppSettings(path=ini))
    w2.show()
    spin()
    paths = {os.path.normcase(t.path or "") for t in w2._tabs if t.path}
    assert os.path.normcase(pdf_a) in paths, "重启后应恢复之前打开的 PDF"
    print(f"[8] 会话恢复 {len(paths)} 个标签 OK")
    close_without_prompt(w2)

    # ---- [9] 关掉最后一个标签 -> 自动补空白笔记 ----
    for tab in list(w._tabs):
        w.close_tab(tab, ask_save=False)
    assert len(w._tabs) == 1, "关完应自动补一个空白标签"
    assert w.mode == "note" and w.canvas is not None
    print("[9] 关闭全部自动补空白标签 OK")

    # ---- [10] 常用色快捷盘：单击即换色 + 最近使用 ----
    w._set_tool("pen")
    w._apply_tool_color("#e11d48")
    assert w.canvas.pen_color == "#e11d48", "单击常用色应立即生效"
    assert w.settings.recent_colors("pen")[0] == "#e11d48"
    _popup = w._build_color_popup()          # 与点色块时构建的是同一个面板
    assert len(_popup.grid_common.colors()) == 12, "钢笔应有 12 个常用色"
    assert _popup.grid_recent is not None, "用过颜色后应出现「最近使用」"
    _popup.close()
    w._set_tool("highlighter")
    w._apply_tool_color("#2563eb")           # 不透明色 -> 自动补默认透明度
    assert w.canvas.highlighter_color == "#662563eb", w.canvas.highlighter_color
    w._set_tool("pen")
    print("[10] 常用色快捷盘（单击换色 / 最近使用 / 荧光笔补透明度）OK")

    # ---- [11] 触控笔圆圈反馈策略：切换 + 持久化 ----
    for _policy in ("quiet", "off", "system", "quiet"):
        w._set_touch_feedback(_policy)
        assert w.settings.touch_feedback() == _policy, f"{_policy} 应写入设置"
    assert w._fb_actions["quiet"].isChecked(), "菜单勾选应跟随策略"
    print("[11] 触控圆圈反馈策略切换 OK")

    close_without_prompt(w)
    spin(300)
    print("SMOKE ALL PASSED")


if __name__ == "__main__":
    main()

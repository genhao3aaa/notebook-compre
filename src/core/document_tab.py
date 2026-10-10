"""多标签文档模型：一个标签 = 一份文档 + 一份视图 + 一份搜索状态。

为什么每个标签独占一个 PageCanvas
---------------------------------
切换标签时如果只是「把当前文档状态写回、再加载另一个文档」，用户会明显感到
卡顿（PDF 要重新解析、缩略图要重排、渲染要重来）。浏览器的做法是每个标签页
保留自己的渲染树——切换只是把某一页显示出来。这里同理：

* 每个 DocumentTab 持有一个 PageCanvas，所有标签的 canvas 都挂在 QTabWidget 里；
* 切换标签 = QTabWidget.setCurrentWidget()，O(1)，PDF 文档句柄、渲染缓存、
  缩放/滚动位置、撤销栈全部原样保留，切回来还是刚才那一页那一个缩放比例；
* 代价是内存（每个 canvas 一份渲染缓存），由主窗口的缓存预算统一回收。

异步任务的「串台」防护
----------------------
搜索、缩略图、页面渲染都是后台线程。用户可能在任务飞行途中切走甚至关掉标签，
此时回调如果直接操作「当前标签」，就会把 A 的结果写到 B 上（这类 bug 极难复现）。
约定：所有跨线程回调都携带 tab_id，回调里先 `tab = self._find_tab(tab_id)`，
找不到（标签已关闭）就直接丢弃结果。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:  # 仅用于类型提示，避免循环导入
    from src.core.annot_document import AnnotDocument
    from src.core.note_document import NoteDocument
    from src.render.pdf_engine import PdfEngine
    from src.ui.canvas import PageCanvas

NOTE = "note"
PDF = "pdf"


def same_path(a: Optional[str], b: Optional[str]) -> bool:
    """跨平台、大小写不敏感地判断两个路径是否指向同一文件。

    Windows 上 `D:\\a.pdf` 与 `d:\\A.pdf` 是同一个文件；用 abspath + normcase
    统一，避免同一份 PDF 被开成两个标签（会导致批注互相覆盖）。
    """
    if not a or not b:
        return False
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except Exception:
        return a == b


class DocumentTab:
    """单个标签页的全部状态。"""

    _seq = 0

    def __init__(self, kind: str, canvas: "PageCanvas", title: str = "") -> None:
        DocumentTab._seq += 1
        self.tab_id: int = DocumentTab._seq
        self.kind: str = kind if kind in (NOTE, PDF) else NOTE
        self.canvas: Optional["PageCanvas"] = canvas
        self.title: str = title or "未命名笔记"

        #: 源文件路径（笔记 *.note 或 PDF）；未保存的笔记为 None
        self.path: Optional[str] = None
        self.dirty: bool = False

        # ---- 文档对象（笔记 / PDF 二选一） ----
        self.note_doc: Optional["NoteDocument"] = None
        self.pdf_engine: Optional["PdfEngine"] = None
        self.annot_doc: Optional["AnnotDocument"] = None
        self.annot_path: Optional[str] = None

        # ---- 视图状态（缩放/偏移/旋转/阅读模式都由 canvas 持有，这里只存页码） ----
        self.page: int = 0
        #: 这个标签被「看到的最新位置」的时刻（wall clock）。
        #: 阅读进度写盘时用它做**跨窗口**的新旧比较：多窗口/多实例开同一本书时，
        #: 手里拿着旧页码的那个窗口不会把别人刚读到的位置覆盖掉。
        self.view_ts: float = 0.0

        # ---- 搜索状态（每个标签独立：切回来时结果列表还在） ----
        self.search_hits: List = []
        self.search_index: int = -1
        self.search_worker = None
        self.search_token: int = 0
        self.last_query: str = ""
        self.search_running: bool = False

        # ---- 书签目录缓存（打开时算一次，切标签不再重算） ----
        self.outline: List = []

        #: 打开失败 / 文件失效的说明文字（用于状态栏提示）
        self.error: Optional[str] = None

    # ------------------------------------------------------------------ #
    # 基本查询
    # ------------------------------------------------------------------ #
    @property
    def is_pdf(self) -> bool:
        return self.kind == PDF

    @property
    def is_note(self) -> bool:
        return self.kind == NOTE

    def page_count(self) -> int:
        try:
            if self.is_pdf:
                return int(self.pdf_engine.page_count()) if self.pdf_engine else 1
            return int(self.note_doc.page_count()) if self.note_doc else 1
        except Exception:
            return 1

    def clamp_page(self, index: int) -> int:
        return max(0, min(int(index), max(0, self.page_count() - 1)))

    def display_name(self) -> str:
        """标签上显示的名字（不含 dirty 星号）。"""
        if self.path:
            return os.path.basename(self.path)
        return self.title or ("未命名笔记" if self.is_note else "未命名")

    def tab_text(self) -> str:
        return ("* " if self.dirty else "") + self.display_name()

    def tooltip(self) -> str:
        kind = "PDF 批注" if self.is_pdf else "手写笔记"
        path = self.path or "（尚未保存到磁盘）"
        return f"{kind}\n{path}"

    def is_same_source(self, path: Optional[str]) -> bool:
        """是否已经在别的标签里打开了同一个文件。"""
        return same_path(self.path, path)

    # ------------------------------------------------------------------ #
    # 资源释放
    # ------------------------------------------------------------------ #
    def stop_search(self) -> None:
        """停掉该标签自己的搜索线程（关闭标签、切走前调用）。"""
        w = self.search_worker
        self.search_worker = None
        self.search_running = False
        if w is None:
            return
        try:
            w.finished_hits.disconnect()
        except Exception:
            pass
        try:
            if w.isRunning():
                w.quit()
                w.wait(2000)
        except Exception:
            pass

    def release(self) -> None:
        """彻底释放：搜索线程 → 画布脱离 PDF（等渲染线程）→ 关闭 fitz 文档。

        顺序不能反：先让画布停止引用 engine，再 close()，否则后台渲染线程会
        访问已关闭的文档而崩溃（这是单标签时代踩过的坑）。
        """
        self.stop_search()
        canvas, self.canvas = self.canvas, None
        if canvas is not None:
            try:
                canvas.detach_pdf()
            except Exception:
                pass
            try:
                canvas.stroke_provider = None
                canvas.on_page_request = None
            except Exception:
                pass
        engine, self.pdf_engine = self.pdf_engine, None
        if engine is not None:
            try:
                engine.close()
            except Exception:
                pass
        self.annot_doc = None
        self.note_doc = None
        self.search_hits = []
        self.outline = []

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (f"<DocumentTab #{self.tab_id} {self.kind} {self.display_name()!r} "
                f"page={self.page} dirty={self.dirty}>")

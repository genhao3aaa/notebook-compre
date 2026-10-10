"""PDF 缩略图线程池。

为什么需要它
------------
旧实现「每页一个 QThread」——300 页的 PDF 会一次性创建 300 个线程，
几乎必然卡顿甚至失败。现在改为**有界线程池**（默认 4 个常驻线程 + 任务队列）。

同时提供「取消」语义：每次提交新批次会递增 generation，旧批次的结果直接丢弃，
切换 PDF 时不会把上一个文档的缩略图贴到新文档上。
"""

from __future__ import annotations

import queue
import time
from typing import Iterable, List, Optional

from PySide6.QtCore import QObject, QThread, Signal

from src.render.pdf_engine import fitz_module, fitz_pixmap_to_qimage, rotate_image

DEFAULT_WORKERS = 4


class _ThumbWorker(QThread):
    """常驻工作线程：不断从队列取任务，直到收到 None 哨兵。"""

    result = Signal(int, object, int)  # (page_index, QImage|None, generation)

    def __init__(self, jobs: "queue.Queue", parent: Optional[QObject] = None):
        super().__init__(parent)
        self._jobs = jobs
        self._running = True
        # 线程内复用的文档句柄：同一份 PDF 的 N 个缩略图只打开一次
        # （旧实现每个任务都 open() 一次，300 页就是 300 次解析 xref）
        self._doc = None
        self._doc_path = ""

    def stop(self) -> None:
        self._running = False

    def _close_doc(self) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass
        self._doc = None
        self._doc_path = ""

    def run(self) -> None:
        while self._running:
            job = self._jobs.get()
            if job is None:
                break
            path, index, max_w, gen, rot = job
            try:
                if self._doc is None or self._doc_path != path:
                    self._close_doc()
                    self._doc = fitz_module().open(path)
                    self._doc_path = path
                rect = self._doc[index].rect
                zoom = max_w / rect.width
                pix = self._doc[index].get_pixmap(matrix=fitz_module().Matrix(zoom, zoom),
                                                  alpha=False)
                img = fitz_pixmap_to_qimage(pix)
                self.result.emit(index, rotate_image(img, rot), gen)
            except Exception:
                self._close_doc()      # 文档可能损坏，下次任务重新打开
                self.result.emit(index, None, gen)
        self._close_doc()


class ThumbnailPool(QObject):
    """有界 PDF 缩略图加载池。

    A6：线程**惰性启动**——构造时不再创建 4 个线程，第一次 `submit()` 才起。
    空白笔记启动路径根本用不到缩略图池，省下线程创建与 4 份线程栈。
    """

    thumbnail_ready = Signal(int, object)  # (page_index, QImage)

    def __init__(self, workers: int = DEFAULT_WORKERS, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._workers = max(1, int(workers))
        self._jobs: "queue.Queue" = queue.Queue()
        self._gen = 0
        self._shutdown = False
        self._threads: List[_ThumbWorker] = []
        if parent is not None:
            # 父对象被销毁时先把线程停掉：即使调用方忘了走 closeEvent（例如窗口被
            # GC 回收、进程退出），也不会出现 "QThread: Destroyed while thread is
            # still running"（Qt 对这条会直接 fail-fast，进程退出码 0xC0000409）。
            parent.destroyed.connect(self.shutdown)

    def _ensure_threads(self) -> None:
        """第一次真的要干活时才起线程（构造时不起）。"""
        if self._threads or self._shutdown:
            return
        for _ in range(self._workers):
            t = _ThumbWorker(self._jobs, self)
            t.result.connect(self._on_result)
            t.start()
            self._threads.append(t)

    def is_running(self) -> bool:
        return bool(self._threads)

    # ------------------------------------------------------------------ #
    def submit(self, pdf_path: str, indices: Iterable[int], max_w: int = 150,
               rotation: int = 0, reset: bool = True) -> None:
        """提交缩略图任务。

        reset=True（默认）：开新批次，作废此前未完成的任务（切换文档/旋转时用）。
        reset=False：追加到当前批次（滚动时懒加载后续页面用，不会打断在跑的任务）。
        """
        if self._shutdown:
            return
        todo = [int(i) for i in indices]
        if not todo:
            return                       # 空批次不起线程（A6）
        self._ensure_threads()           # A6：第一次提交任务时才起线程
        if reset:
            self._gen += 1
            # 丢弃队列中尚未开始的旧任务
            try:
                while True:
                    self._jobs.get_nowait()
            except Exception:
                pass
        for i in todo:
            self._jobs.put((pdf_path, int(i), max_w, self._gen, int(rotation or 0)))

    def _on_result(self, index: int, img, gen: int) -> None:
        if gen != self._gen or img is None:
            return  # 过期批次或失败
        self.thumbnail_ready.emit(index, img)

    def clear_pending(self) -> None:
        """作废当前批次（不关闭线程）。"""
        self._gen += 1
        try:
            while True:
                self._jobs.get_nowait()
        except Exception:
            pass

    def shutdown(self, *args, timeout_ms: int = 3000) -> None:
        """停止全部工作线程并等待退出（可重复调用）。

        注意 `*args`：这个方法同时挂在 `parent.destroyed` 信号上，Qt 会把被销毁的
        对象当第一个参数传进来。

        两个细节都是为了不触发 Qt 的 fail-fast（在运行中销毁 QThread 会直接终止进程）：
        1. **先清空未开始的任务**：否则工作线程会先醒来渲染完一整页（可能超过 3 s），
           哨兵要排在它后面；
        2. **没停下来的线程保留引用**：宁可让 QThread 对象多活一会儿（进程退出时自然回收），
           也绝不能在它还 running 的时候析构。
        """
        if self._shutdown:
            return
        self._shutdown = True
        self.clear_pending()
        for t in self._threads:
            t.stop()
            self._jobs.put(None)
        deadline = time.monotonic() + max(0.0, timeout_ms / 1000.0)
        still_running = []
        for t in self._threads:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if not t.wait(remaining_ms):
                still_running.append(t)
        self._threads = still_running

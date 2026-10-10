"""A4：把「写盘」放到后台线程，避免大文档保存时卡住界面。

设计取舍
--------
- **快照在 GUI 线程做，编码 + 写盘在后台线程做**：`serializer.snapshot_*` 只做浅拷贝
  （复制每页的笔画列表，Stroke 对象共享），代价 O(笔画数) 而不是 O(点数)；
  真正的耗时部分（编码 + fsync + 磁盘）留在后台。
- **小文档直接同步写**（点数 ≤ `SYNC_POINT_THRESHOLD`）：省掉线程唤醒，行为可预期
  （测试与「刚存完就去看文件」的场景都不会竞态）。
- **一个常驻线程 + 队列**：连续 Ctrl+S 不会创建一堆线程；顺序写、先到先落盘。
- 失败**不弹模态框**（保存是后台发生的，模态框会打断正在书写的人）：
  通过 `finished` 信号交给窗口，窗口把标签标回「脏」并给非模态提示。
"""

from __future__ import annotations

import queue
import time
from typing import Callable, Optional

from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Signal

#: 点数不超过该值的文档直接同步写（约 10 ms 量级）
SYNC_POINT_THRESHOLD = 20000


class _SaveThread(QThread):
    """常驻后台线程：从队列里取「写盘任务」并执行。"""

    done = Signal(str, bool, str)          # path, ok, error

    def __init__(self, tasks: "queue.Queue", parent: Optional[QObject] = None):
        super().__init__(parent)
        self._tasks = tasks

    def run(self) -> None:                 # pragma: no cover - 线程体，逻辑由测试间接覆盖
        while True:
            item = self._tasks.get()
            if item is None:               # 停止哨兵
                return
            path, write = item
            try:
                write()
                self.done.emit(path, True, "")
            except Exception as e:         # 任何异常都必须回到 GUI 线程报告
                self.done.emit(path, False, f"{type(e).__name__}: {e}")

    def stop(self) -> None:
        self._tasks.put(None)


class AsyncSaver(QObject):
    """保存调度器：小文档同步写、大文档后台写，统一发 `finished(path, ok, error)`。"""

    finished = Signal(str, bool, str)

    def __init__(self, parent: Optional[QObject] = None,
                 sync_threshold: int = SYNC_POINT_THRESHOLD):
        super().__init__(parent)
        self._threshold = int(sync_threshold)
        self._tasks: "queue.Queue" = queue.Queue()
        self._thread: Optional[_SaveThread] = None
        self._busy = 0

    # ------------------------------------------------------------------ #
    def save(self, path: str, write: Callable[[], None], points: int = 0,
             force_sync: bool = False) -> bool:
        """调度一次写盘。返回 True 表示**已经同步写完**，False 表示交给了后台线程。"""
        if force_sync or points <= self._threshold:
            try:
                write()
                self.finished.emit(path, True, "")
            except Exception as e:
                self.finished.emit(path, False, f"{type(e).__name__}: {e}")
            return True

        self._ensure_thread()
        self._busy += 1
        self._tasks.put((path, write))
        return False

    def _ensure_thread(self) -> None:
        if self._thread is None:
            self._thread = _SaveThread(self._tasks, self)
            self._thread.done.connect(self._on_thread_done)
            self._thread.start()

    def _on_thread_done(self, path: str, ok: bool, error: str) -> None:
        self._busy = max(0, self._busy - 1)
        self.finished.emit(path, ok, error)

    # ------------------------------------------------------------------ #
    def is_busy(self) -> bool:
        return self._busy > 0

    def pending_count(self) -> int:
        return self._busy

    def wait_for_idle(self, timeout_ms: int = 5000) -> bool:
        """等所有排队/在写的任务完成；返回是否确实空闲。"""
        if self._busy == 0:
            return True
        loop = QEventLoop()

        def _check(*_a) -> None:
            if self._busy == 0:
                loop.quit()

        self.finished.connect(_check)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(max(0, int(timeout_ms)))
        try:
            if self._busy:
                loop.exec()
        finally:
            timer.stop()
            try:
                self.finished.disconnect(_check)
            except (RuntimeError, TypeError):
                pass
        return self._busy == 0

    def shutdown(self, timeout_ms: int = 3000) -> None:
        """停线程（先等在途任务结束）；幂等。"""
        if self._thread is None:
            return
        self.wait_for_idle(timeout_ms)
        self._thread.stop()
        self._thread.wait(timeout_ms)
        self._thread = None
        self._busy = 0


def sleep_brief(seconds: float = 0.001) -> None:      # pragma: no cover - 测试辅助
    time.sleep(seconds)

"""PDF 全文搜索侧栏。

交互与主流阅读器一致：
* 输入关键字回车 / 点击「搜索」开始检索（后台线程，界面不卡）。
* 结果以「第 N 页 · 上下文」列表展示，点击即跳转并高亮。
* 上一个 / 下一个 在当前结果间循环跳转，跨页自动翻页。
* 支持区分大小写 / 全词匹配。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from src.render.pdf_engine import SearchHit


class SearchPanel(QWidget):
    """全文搜索面板。"""

    search_requested = Signal(str, bool, bool)   # (关键字, 区分大小写, 全词)
    hit_activated = Signal(int)                  # 结果序号（0-based）
    prev_requested = Signal()
    next_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._hits: List[SearchHit] = []
        self._current = -1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("搜索关键字，回车开始")
        self.edit.returnPressed.connect(self._emit_search)
        row.addWidget(self.edit, 1)
        self.btn_go = QPushButton("搜索")
        self.btn_go.setFixedWidth(52)
        self.btn_go.clicked.connect(self._emit_search)
        row.addWidget(self.btn_go)
        layout.addLayout(row)

        opt = QHBoxLayout()
        opt.setSpacing(10)
        self.chk_case = QCheckBox("区分大小写")
        self.chk_word = QCheckBox("全词匹配")
        self.chk_case.stateChanged.connect(lambda _s: self._emit_search())
        self.chk_word.stateChanged.connect(lambda _s: self._emit_search())
        opt.addWidget(self.chk_case)
        opt.addWidget(self.chk_word)
        opt.addStretch(1)
        layout.addLayout(opt)

        self.status = QLabel("未打开 PDF")
        self.status.setObjectName("SideTitle")
        layout.addWidget(self.status)

        self.list = QListWidget()
        self.list.itemClicked.connect(lambda item: self.hit_activated.emit(
            int(item.data(Qt.UserRole))))
        self.list.itemActivated.connect(lambda item: self.hit_activated.emit(
            int(item.data(Qt.UserRole))))
        layout.addWidget(self.list, 1)

        nav = QHBoxLayout()
        nav.setSpacing(6)
        self.btn_prev = QPushButton("◀ 上一个")
        self.btn_next = QPushButton("下一个 ▶")
        self.btn_prev.clicked.connect(self.prev_requested.emit)
        self.btn_next.clicked.connect(self.next_requested.emit)
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.btn_next)
        layout.addLayout(nav)

        self._set_nav_enabled(False)

    # ------------------------------------------------------------------ #
    def query(self) -> str:
        return self.edit.text().strip()

    def _emit_search(self) -> None:
        q = self.query()
        if not q:
            self.set_results([], -1)
            self.set_status("请输入关键字")
            return
        self.search_requested.emit(q, self.chk_case.isChecked(), self.chk_word.isChecked())

    def focus_input(self) -> None:
        self.edit.setFocus()
        self.edit.selectAll()

    # ------------------------------------------------------------------ #
    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def set_busy(self, busy: bool, query: str = "") -> None:
        self.btn_go.setEnabled(not busy)
        if busy:
            self.set_status(f"正在搜索「{query}」…")

    def set_results(self, hits: List[SearchHit], current: int = -1) -> None:
        self._hits = list(hits or [])
        self.list.clear()
        for i, h in enumerate(self._hits):
            item = QListWidgetItem(f"第 {h.page + 1} 页  ·  {h.context or '(命中)'}")
            item.setData(Qt.UserRole, i)
            item.setToolTip(h.context)
            self.list.addItem(item)
        self._set_nav_enabled(bool(self._hits))
        if not self._hits:
            self.set_status("没有找到匹配结果")
        else:
            self.set_status(f"共 {len(self._hits)} 处匹配")
        self.set_current(current)

    def set_current(self, index: int) -> None:
        self._current = index
        self.list.blockSignals(True)
        if 0 <= index < self.list.count():
            self.list.setCurrentRow(index)
            self.list.scrollToItem(self.list.item(index))
        else:
            self.list.clearSelection()
        self.list.blockSignals(False)

    def hits(self) -> List[SearchHit]:
        return self._hits

    def current_index(self) -> int:
        return self._current

    def _set_nav_enabled(self, enabled: bool) -> None:
        self.btn_prev.setEnabled(enabled)
        self.btn_next.setEnabled(enabled)

    def reset(self) -> None:
        self._hits = []
        self._current = -1
        self.list.clear()
        self._set_nav_enabled(False)
        self.set_status("未打开 PDF")

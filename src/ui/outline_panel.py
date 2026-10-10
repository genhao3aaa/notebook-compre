"""PDF 书签 / 目录侧栏。

* 用 `QTreeWidget` 展示多级书签（level 1 为顶层）。
* 单击条目 → 跳转到对应页；若书签带目标位置（top），一并传给画布以便定位。
* 翻页时高亮「当前页所在」的书签条目。
* 没有书签时给出明确提示，而不是空白面板。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QLabel, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from src.render.pdf_engine import OutlineEntry


class OutlinePanel(QWidget):
    """书签 / 目录树。"""

    page_selected = Signal(int, object)   # (page_index, top_y or None)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._entries: List[OutlineEntry] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.tip = QLabel("该 PDF 没有书签目录")
        self.tip.setObjectName("SideTitle")
        self.tip.setWordWrap(True)
        self.tip.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.tip)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.itemActivated.connect(self._on_clicked)
        layout.addWidget(self.tree, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("SideTitle")
        layout.addWidget(self.count_label)

        self.clear()

    # ------------------------------------------------------------------ #
    def clear(self) -> None:
        self._entries = []
        self.tree.clear()
        self.tree.setVisible(False)
        self.count_label.setText("")
        self.tip.setVisible(True)
        self.tip.setText("未打开 PDF")

    def set_unavailable(self, reason: str) -> None:
        self._entries = []
        self.tree.clear()
        self.tree.setVisible(False)
        self.count_label.setText("")
        self.tip.setVisible(True)
        self.tip.setText(reason)

    def set_outline(self, entries: List[OutlineEntry]) -> None:
        """按 level 重建目录树；扁平列表 + level 表示层级。"""
        self._entries = list(entries or [])
        self.tree.clear()
        if not self._entries:
            self.tree.setVisible(False)
            self.count_label.setText("")
            self.tip.setVisible(True)
            self.tip.setText("该 PDF 没有书签目录\n（可用左侧「页面」标签浏览）")
            return

        self.tip.setVisible(False)
        self.tree.setVisible(True)
        # 扁平列表 + level 还原为树：stack[k] 存放「第 k+1 层」的最后一个节点
        stack: List[QTreeWidgetItem] = []
        for e in self._entries:
            level = max(1, int(e.level))
            parent = stack[level - 2] if (level >= 2 and len(stack) >= level - 1) else None
            item = QTreeWidgetItem(parent if parent is not None else self.tree, [e.display])
            item.setData(0, Qt.UserRole, (e.page, e.top))
            item.setToolTip(0, f"{e.display}  →  第 {e.page + 1} 页")
            item.setExpanded(level == 1)
            del stack[level - 1:]
            stack.append(item)
        self.count_label.setText(f"共 {len(self._entries)} 个书签")

    # ------------------------------------------------------------------ #
    def set_current_page(self, index: int) -> None:
        """高亮第一个指向当前页的书签（不自动滚动，避免打断用户浏览）。"""
        if not self._entries:
            return
        target: Optional[QTreeWidgetItem] = None
        for item in _walk_items(self.tree):
            data = item.data(0, Qt.UserRole)
            if isinstance(data, (tuple, list)) and data and int(data[0]) == index:
                target = item
                break
        if target is not None:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(target)
            self.tree.blockSignals(False)

    # ------------------------------------------------------------------ #
    def _on_clicked(self, item: QTreeWidgetItem, column: int = 0) -> None:
        data = item.data(0, Qt.UserRole)
        if isinstance(data, (tuple, list)) and data:
            self.page_selected.emit(int(data[0]), data[1] if len(data) > 1 else None)


def _walk_items(tree: QTreeWidget) -> List[QTreeWidgetItem]:
    """深度优先展开所有节点（避免引入 QTreeWidgetItemIterator 的额外类型复杂度）。"""
    out: List[QTreeWidgetItem] = []
    root = tree.invisibleRootItem()

    def _collect(item: QTreeWidgetItem) -> None:
        out.append(item)
        for i in range(item.childCount()):
            _collect(item.child(i))

    for i in range(root.childCount()):
        _collect(root.child(i))
    return out

"""空白手写笔记文档模型（.note 文件）。

格式演进
--------
- 1.0：初版（版本 + 页面尺寸 + 背景类型 + 页面列表）。
- 1.1：新增 `background_color`（背景底色）。读取旧文件时缺省为白色，向后兼容。
"""

from __future__ import annotations

from typing import List

from src.core import format as doc_format
from src.core.stroke import Stroke, UndoManager

# 背景类型
BG_BLANK = "blank"
BG_GRID = "grid"
BG_LINES = "lines"
BG_DOTS = "dots"
BACKGROUND_TYPES = [BG_BLANK, BG_GRID, BG_LINES, BG_DOTS]

BG_LABELS = {"blank": "空白", "grid": "网格", "lines": "横线", "dots": "点阵"}

# 背景底色（默认纯白）
DEFAULT_BG_COLOR = "#ffffff"

# 常用预设纸色：白 / 米黄 / 护眼绿 / 淡蓝 / 淡粉 / 浅灰 / 深灰 / 纯黑
BG_COLOR_PRESETS = [
    ("#ffffff", "白"),
    ("#fdf6e3", "米黄"),
    ("#f2f7ec", "护眼绿"),
    ("#eef4fb", "淡蓝"),
    ("#fbeef0", "淡粉"),
    ("#eceff1", "浅灰"),
    ("#3b3f45", "深灰"),
    ("#1e2124", "夜黑"),
]

# A4 纵向（points）：595.276 x 841.89
DEFAULT_PAGE_W = 595.28
DEFAULT_PAGE_H = 841.89

NOTE_FORMAT_VERSION = doc_format.NOTE_SCHEMA_VERSION   # 单一来源见 src/core/format.py


class NotePage:
    """笔记中的一页：持有该页笔画列表与撤销管理器。"""

    def __init__(self, strokes: List[Stroke] | None = None):
        self.strokes: List[Stroke] = strokes if strokes is not None else []
        self.undo = UndoManager(self.strokes, depth=100)

    def to_dict(self) -> dict:
        return {"strokes": [s.to_dict() for s in self.strokes]}

    @classmethod
    def from_dict(cls, d: dict) -> "NotePage":
        page = cls()
        page.strokes = [Stroke.from_dict(s) for s in d.get("strokes", [])]
        page.undo = UndoManager(page.strokes, depth=100)
        return page


class NoteDocument:
    """完整的手写笔记文档。"""

    def __init__(self, page_w: float = DEFAULT_PAGE_W, page_h: float = DEFAULT_PAGE_H,
                 background: str = BG_BLANK, background_color: str = DEFAULT_BG_COLOR):
        self.version = NOTE_FORMAT_VERSION
        self.page_w = page_w
        self.page_h = page_h
        self.background = background if background in BACKGROUND_TYPES else BG_BLANK
        self.background_color = (background_color or DEFAULT_BG_COLOR).strip() or DEFAULT_BG_COLOR
        self.pages: List[NotePage] = [NotePage()]

    # ------------------------------------------------------------------ #
    # 页面管理
    # ------------------------------------------------------------------ #
    def add_page(self, index: int | None = None) -> NotePage:
        page = NotePage()
        if index is None or index >= len(self.pages):
            self.pages.append(page)
        else:
            self.pages.insert(index, page)
        return page

    def delete_page(self, index: int) -> bool:
        if len(self.pages) <= 1:
            return False  # 至少保留一页
        self.pages.pop(index)
        return True

    def page_count(self) -> int:
        return len(self.pages)

    def set_background(self, background: str) -> None:
        if background in BACKGROUND_TYPES:
            self.background = background

    def set_background_color(self, color: str) -> None:
        self.background_color = (color or DEFAULT_BG_COLOR).strip() or DEFAULT_BG_COLOR

    # ------------------------------------------------------------------ #
    # 序列化
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "page_w": self.page_w,
            "page_h": self.page_h,
            "background": self.background,
            "background_color": self.background_color,
            "pages": [p.to_dict() for p in self.pages],
        }

    def save(self, path: str, version: int = 2) -> None:
        """保存文档。

        - `version=2`（默认）：v2 二进制容器（体积小、解析快、带 CRC 与原子写）；
        - `version=1`：旧的 JSON 格式（互操作/调试用，仍向后兼容）。

        识别与分发统一走 `src/core/format.py`（A7），两个文档类不再各写一套分支。
        """
        container = 2 if int(version) >= 2 else 1
        doc_format.write_document(
            path, "note",
            {
                "version": self.version,
                "page_w": self.page_w,
                "page_h": self.page_h,
                "background": self.background,
                "background_color": self.background_color,
            },
            [p.strokes for p in self.pages],
            container=container,
            json_dict=self.to_dict(),
        )

    @classmethod
    def load(cls, path: str) -> "NoteDocument":
        """读取文档：v2 二进制容器与 v1 JSON 都支持（按首字节自动识别）。"""
        kind, meta, pages = doc_format.read_document(path)
        if kind != "note":
            raise doc_format.FormatError(
                "这是一个 PDF 批注（.annot）文件，不是笔记（.note）")
        doc = cls.from_dict({**meta, "pages": []})
        doc.pages = [NotePage(list(strokes)) for strokes in pages] or [NotePage()]
        return doc

    @classmethod
    def from_dict(cls, data: dict) -> "NoteDocument":
        doc = cls(
            page_w=float(data.get("page_w", DEFAULT_PAGE_W)),
            page_h=float(data.get("page_h", DEFAULT_PAGE_H)),
            background=data.get("background", BG_BLANK),
            background_color=data.get("background_color", DEFAULT_BG_COLOR),
        )
        doc.version = str(data.get("version", NOTE_FORMAT_VERSION))
        pages = data.get("pages", [])
        if not pages:
            doc.pages = [NotePage()]
        else:
            doc.pages = [NotePage.from_dict(p) for p in pages]
        return doc

    @classmethod
    def new_blank(cls, background: str = BG_BLANK,
                  background_color: str = DEFAULT_BG_COLOR) -> "NoteDocument":
        return cls(background=background, background_color=background_color)

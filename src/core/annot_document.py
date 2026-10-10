"""PDF 批注文档模型（.annot 文件）。

批注与原始 PDF 分离保存，按页存放笔画。再次打开同一 PDF 时，
依据 PDF 的 MD5 自动匹配并加载批注。
"""

from __future__ import annotations

import hashlib
import time
from typing import List

from src.core import format as doc_format
from src.core.stroke import Stroke, UndoManager

ANNOT_FORMAT_VERSION = doc_format.ANNOT_SCHEMA_VERSION   # 单一来源见 src/core/format.py


def compute_file_md5(path: str, chunk_size: int = 1 << 20) -> str:
    """计算文件 MD5（分块读取，避免大文件占用过多内存）。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class AnnotPage:
    """PDF 某一页上的批注（笔画）。"""

    def __init__(self, strokes: List[Stroke] | None = None):
        self.strokes: List[Stroke] = strokes if strokes is not None else []
        self.undo = UndoManager(self.strokes, depth=100)

    def to_dict(self) -> dict:
        return {"strokes": [s.to_dict() for s in self.strokes]}

    @classmethod
    def from_dict(cls, d: dict) -> "AnnotPage":
        page = cls()
        page.strokes = [Stroke.from_dict(s) for s in d.get("strokes", [])]
        page.undo = UndoManager(page.strokes, depth=100)
        return page


class AnnotDocument:
    """PDF 批注容器。"""

    def __init__(self, pdf_path: str, pdf_md5: str, page_count: int):
        self.version = ANNOT_FORMAT_VERSION
        self.pdf_path = pdf_path
        self.pdf_md5 = pdf_md5
        self.created = time.time()
        self.modified = time.time()
        self.pages: List[AnnotPage] = [AnnotPage() for _ in range(page_count)]

    def ensure_page_count(self, page_count: int) -> None:
        """PDF 页数变化时，补齐或截断批注页。"""
        while len(self.pages) < page_count:
            self.pages.append(AnnotPage())
        if len(self.pages) > page_count:
            self.pages = self.pages[:page_count]

    def touch(self) -> None:
        self.modified = time.time()

    # ------------------------------------------------------------------ #
    # 序列化
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "pdf_path": self.pdf_path,
            "pdf_md5": self.pdf_md5,
            "created": self.created,
            "modified": self.modified,
            "pages": [p.to_dict() for p in self.pages],
        }

    def save(self, path: str, version: int = 2) -> None:
        """保存批注：`version=2` 为 v2 二进制容器（默认），`version=1` 为旧 JSON。"""
        self.touch()
        container = 2 if int(version) >= 2 else 1
        doc_format.write_document(
            path, "annot",
            {
                "version": self.version,
                "pdf_path": self.pdf_path,
                "pdf_md5": self.pdf_md5,
                "created": self.created,
                "modified": self.modified,
            },
            [p.strokes for p in self.pages],
            container=container,
            json_dict=self.to_dict(),
        )

    @classmethod
    def load(cls, path: str) -> "AnnotDocument":
        """读取批注：v2 二进制容器与 v1 JSON 都支持（分发见 src/core/format.py）。"""
        kind, meta, pages = doc_format.read_document(path)
        if kind != "annot":
            raise doc_format.FormatError(
                "这是一个笔记（.note）文件，不是 PDF 批注（.annot）")
        doc = cls.from_dict({**meta, "pages": []})
        doc.pages = [AnnotPage(list(strokes)) for strokes in pages] or [AnnotPage()]
        return doc

    @classmethod
    def from_dict(cls, data: dict) -> "AnnotDocument":
        doc = cls(
            pdf_path=data.get("pdf_path", ""),
            pdf_md5=data.get("pdf_md5", ""),
            page_count=len(data.get("pages", [])) or 1,
        )
        doc.version = str(data.get("version", ANNOT_FORMAT_VERSION))
        doc.created = data.get("created", time.time())
        doc.modified = data.get("modified", time.time())
        pages = data.get("pages", [])
        doc.pages = [AnnotPage.from_dict(p) for p in pages] if pages else [AnnotPage()]
        return doc

    @classmethod
    def from_pdf(cls, pdf_path: str, pdf_md5: str, page_count: int) -> "AnnotDocument":
        return cls(pdf_path=pdf_path, pdf_md5=pdf_md5, page_count=page_count)

"""Markdown 文档模型：读取、标题大纲、全文搜索。

这一层是纯 Python（不 import Qt），方便单测；富文本渲染在
`src/ui/markdown_view.py`。扩展「阅读器支持的格式」时，界面层复用这里的
解析结果即可，不必各自去碰原始文本。

编码
----
Windows 上的 .md 常见 UTF-8（含 BOM）/ GBK / Big5；逐个尝试后仍失败才用
latin-1 兜底（保证不抛异常、不丢字节）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

#: 阅读器接受的 Markdown 后缀（大小写不敏感）
MD_SUFFIXES = (".md", ".markdown", ".mdown", ".mkd", ".mdtxt")

#: 超过这个体积就不渲染，避免 QTextDocument 把界面卡死
MAX_MD_BYTES = 8 * 1024 * 1024

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT_RE = re.compile(r"^\s*(=+|-+)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def is_markdown(path: Optional[str]) -> bool:
    """路径后缀是否属于阅读器支持的 Markdown 格式。"""
    if not path:
        return False
    return os.path.splitext(path)[1].lower() in MD_SUFFIXES


def read_markdown(path: str) -> str:
    """把 .md 读成文本；编码逐个探测，最后 latin-1 兜底。"""
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) > MAX_MD_BYTES:
        raise ValueError(
            f"Markdown 文件过大（{len(raw) // 1024 // 1024} MB），为避免卡死界面已跳过渲染")
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


@dataclass
class MdHeading:
    """一个标题：层级 1~6、纯文字标题、0-based 源码行号。"""
    level: int
    title: str
    line: int


@dataclass
class MdSearchHit:
    """一次全文检索命中。

    字段刻意与 `pdf_engine.SearchHit` 对齐（page/rect/context），这样侧栏
    `SearchPanel` 不用改就能显示；`position/length` 是渲染文档内的字符位置，
    供 `MarkdownView` 定位与高亮。
    """
    page: int = 0
    rect: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    context: str = ""
    position: int = 0
    length: int = 0


def _strip_inline(text: str) -> str:
    """去掉标题里的行内标记（代码/链接/图片/粗斜体），目录里只留纯文字。"""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_~]", "", text)
    return text.strip()


def parse_headings(text: str) -> List[MdHeading]:
    """提取 ATX（`#`）与 setext（`===` / `---`）标题。

    跳过围栏代码块 ``` / ~~~ 与 4 空格缩进代码块，避免把示例里的 `#`
    当成目录项。
    """
    lines = text.splitlines()
    out: List[MdHeading] = []
    in_fence = False
    for i, raw in enumerate(lines):
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence or raw.startswith("    ") or raw.startswith("\t"):
            continue
        m = _HEADING_RE.match(raw)
        if m:
            title = _strip_inline(m.group(2))
            if title:
                out.append(MdHeading(len(m.group(1)), title, i))
            continue
        # setext：上一行是非空普通文字，当前行是 === 或 ---
        if i > 0 and _SETEXT_RE.match(raw):
            prev = lines[i - 1].strip()
            if prev and not _HEADING_RE.match(prev) and not _FENCE_RE.match(prev):
                level = 1 if raw.strip().startswith("=") else 2
                title = _strip_inline(prev)
                if title:
                    out.append(MdHeading(level, title, i - 1))
    return out


def search_markdown(text: str, query: str, case_sensitive: bool = False,
                    whole_word: bool = False, max_hits: int = 1000) -> List[MdSearchHit]:
    """逐行检索（供纯逻辑层单测 / 源码模式使用），口径与 PDF 搜索一致。

    界面里的「渲染模式」搜索走 `MarkdownView.find_all()`——直接查渲染后的
    文档，命中位置更准；这里的行/列结果用于源码模式与单元测试。
    """
    q = (query or "").strip()
    if not q:
        return []
    if whole_word and not any(ch.isspace() for ch in q):
        pattern = re.compile(r"(?<!\w)" + re.escape(q) + r"(?!\w)",
                             0 if case_sensitive else re.IGNORECASE)
    else:
        pattern = re.compile(re.escape(q), 0 if case_sensitive else re.IGNORECASE)
    hits: List[MdSearchHit] = []
    for lineno, line in enumerate(text.splitlines()):
        for m in pattern.finditer(line):
            start = max(0, m.start() - 28)
            end = min(len(line), m.end() + 28)
            ctx = line[start:end].strip()
            if start > 0:
                ctx = "…" + ctx
            if end < len(line):
                ctx += "…"
            hits.append(MdSearchHit(context=ctx, position=m.start(),
                                    length=len(m.group(0)), page=lineno))
            if len(hits) >= max_hits:
                return hits
    return hits


def document_title(text: str, fallback: str) -> str:
    """用第一个一级标题当文档标题（状态栏 / 窗口标题用），没有就用文件名。"""
    for h in parse_headings(text):
        if h.level == 1:
            return h.title
    return fallback

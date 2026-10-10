"""PDF 渲染引擎：页面渲染、缩略图、异步渲染、目录/搜索，以及带批注的导出。

依赖：PyMuPDF (fitz) 负责读取与页面光栅化；Pillow 负责把批注「烧录」进页面像素。

提供的「商用阅读器」能力
------------------------
* `outline()`  —— 书签 / 目录（多级，页序号已归一化为 0-based）
* `search()`   —— 全文检索（返回页码 + 页面坐标矩形 + 上下文）
* `page_text()`—— 页面纯文本（复制、搜索校验）
* `metadata()` —— 文档属性（标题 / 作者 / 主题 / 加密 / 页数）
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:      # 只给类型检查器看；运行时由 fitz_module() 惰性导入
    import pymupdf as fitz
    from PIL import Image, ImageDraw      # PIL 也在函数内才 import（保持启动轻）

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage, QTransform

from src.core.annot_document import AnnotDocument, compute_file_md5
from src.core.colors import grid_color_for, line_color_for, parse_hex, parse_rgba
from src.core.note_document import BG_BLANK, BG_GRID, BG_LINES, BG_DOTS, DEFAULT_BG_COLOR
from src.core.stroke import Stroke, TOOL_ERASER

#: PyMuPDF 惰性导入（A6）：它要读上百个 .pyd/DLL，实测热缓存下 ~60 ms、
#: 冷缓存下更久。而「新建空白笔记」这条最常见的启动路径根本不需要它。
#: 打开 PDF 时才真正 import（`fitz` 这个名字在下面用 `fitz_module()` 取）。
_fitz_mod = None


def fitz_module():
    """惰性拿到 pymupdf 模块（首次调用时 import）。"""
    global _fitz_mod
    if _fitz_mod is None:
        import pymupdf
        _fitz_mod = pymupdf
    return _fitz_mod


def fitz_loaded() -> bool:
    """pymupdf 是否已经真的被导入（基准/测试用）。"""
    return _fitz_mod is not None


# ---------------------------------------------------------------------- #
# 数据结构
# ---------------------------------------------------------------------- #
@dataclass
class OutlineEntry:
    """书签 / 目录条目。page 为 0-based 页序号。"""
    level: int
    title: str
    page: int
    top: Optional[float] = None      # 目标位置（页面坐标 y，可为空）

    @property
    def display(self) -> str:
        return self.title or "(无标题)"


@dataclass
class SearchHit:
    """一次命中：页码（0-based）+ 页面坐标矩形（points）+ 上下文文本。"""
    page: int
    rect: Tuple[float, float, float, float]
    context: str = ""


@dataclass
class PdfMeta:
    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    creator: str = ""
    producer: str = ""
    page_count: int = 0
    encrypted: bool = False
    file_size: int = 0


# ---------------------------------------------------------------------- #
# 基础转换
# ---------------------------------------------------------------------- #
def fitz_pixmap_to_qimage(pix: fitz.Pixmap) -> QImage:
    """把 fitz.Pixmap 转换为独立的 QImage（复制像素，避免悬空引用）。"""
    if pix.alpha:
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGBA8888)
    else:
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
    return img.copy()


def rotate_image(img: QImage, rotation: int) -> QImage:
    """按显示方向顺时针旋转图像（0/90/180/270）。"""
    if not rotation or img.isNull():
        return img
    return img.transformed(QTransform().rotate(rotation))


def draw_stroke_on_image(draw: "ImageDraw.ImageDraw", stroke: Stroke, zoom: float) -> None:
    """在 PIL 图像上绘制一条笔画（像素坐标 = point * zoom）。

    支持半透明颜色（荧光笔的 `#AARRGGBB`）：此时 fill 会带 alpha，调用方必须把
    draw 建在 RGBA 图层上（见 `_composite_strokes`），否则 alpha 会被忽略。
    """
    pts = stroke.points
    if not pts:
        return
    if stroke.tool == TOOL_ERASER:
        return
    r, g, b, a = parse_rgba(stroke.color, "#222222")
    rgb = (r, g, b) if a >= 255 else (r, g, b, a)
    width = max(1, int(round(stroke.width * zoom)))
    pix_pts = [(p[0] * zoom, p[1] * zoom) for p in pts]
    if len(pix_pts) == 1:
        x, y = pix_pts[0]
        r = max(1, width // 2)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=rgb)
        return
    draw.line(pix_pts, fill=rgb, width=width, joint="curve")


def _composite_strokes(img, strokes, zoom: float):
    """把笔画画到透明 RGBA 图层再与底图合成（支持荧光笔半透明）。"""
    from PIL import Image, ImageDraw

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for s in strokes:
        draw_stroke_on_image(draw, s, zoom)
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _paint_note_background(draw: "ImageDraw.ImageDraw", pix_w: int, pix_h: int,
                           background: str, bg_color: str, zoom: float) -> None:
    """在导出图像上绘制笔记背景（底色 + 网格/横线/点阵）。"""
    if background == BG_GRID:
        step = max(2, int(25 * zoom))
        col = parse_hex(grid_color_for(bg_color))
        for x in range(0, pix_w, step):
            draw.line([(x, 0), (x, pix_h)], fill=col, width=1)
        for y in range(0, pix_h, step):
            draw.line([(0, y), (pix_w, y)], fill=col, width=1)
    elif background == BG_LINES:
        step = max(2, int(35 * zoom))
        col = parse_hex(line_color_for(bg_color))
        for y in range(0, pix_h, step):
            draw.line([(0, y), (pix_w, y)], fill=col, width=1)
    elif background == BG_DOTS:
        step = max(2, int(25 * zoom))
        r = max(1, int(1.4 * zoom))
        col = parse_hex(line_color_for(bg_color))
        for y in range(step, pix_h, step):
            for x in range(step, pix_w, step):
                draw.ellipse([x - r, y - r, x + r, y + r], fill=col)


def export_note_to_pdf(note_doc, out_path: str, bake_zoom: float = 2.0) -> None:
    """把空白笔记导出为 PDF：每页光栅化为图片后写入新 PDF（保留背景色）。"""
    from PIL import Image, ImageDraw

    out = fitz_module().open()
    bg_color = getattr(note_doc, "background_color", DEFAULT_BG_COLOR) or DEFAULT_BG_COLOR
    fill = parse_hex(bg_color)
    for page in note_doc.pages:
        w_pt = note_doc.page_w
        h_pt = note_doc.page_h
        pix_w = int(w_pt * bake_zoom)
        pix_h = int(h_pt * bake_zoom)
        img = Image.new("RGB", (pix_w, pix_h), fill)
        draw = ImageDraw.Draw(img)
        _paint_note_background(draw, pix_w, pix_h, getattr(note_doc, "background", BG_BLANK),
                               bg_color, bake_zoom)
        img = _composite_strokes(img, page.strokes, bake_zoom)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        new_page = out.new_page(width=w_pt, height=h_pt)
        new_page.insert_image(new_page.rect, stream=buf.getvalue())
    out.save(out_path)
    out.close()


# ---------------------------------------------------------------------- #
# 引擎
# ---------------------------------------------------------------------- #
class PdfEngine:
    """封装一个打开的 PDF 文档，提供页面尺寸与光栅化。"""

    def __init__(self, path: str):
        self.path = path
        self.doc: fitz.Document = fitz_module().open(path)
        self._md5 = ""
        # 页面尺寸缓存：连续阅读模式每帧都要用到，反复访问 doc[i].rect 很浪费
        self._size_cache: Dict[int, Tuple[float, float]] = {}
        try:
            st = os.stat(path)
            self.file_size = int(st.st_size)
            self.mtime = float(st.st_mtime)
        except Exception:
            self.file_size = 0
            self.mtime = 0.0

    # ------------------------------------------------------------------ #
    @property
    def md5(self) -> str:
        """文件 MD5：**首次访问时才计算**。

        旧实现把它放在 __init__ 里，打开几百 MB 的 PDF 会先同步读完整份文件，
        界面要卡住近一秒。现在只有「需要与 .annot 比对」或「保存批注」时才计算。
        """
        if not self._md5:
            self._md5 = compute_file_md5(self.path)
        return self._md5

    @md5.setter
    def md5(self, value: str) -> None:
        """允许外部注入已知的 MD5（例如来自缓存），避免重复计算。"""
        self._md5 = value or ""

    def page_count(self) -> int:
        return self.doc.page_count

    def page_size(self, index: int) -> Tuple[float, float]:
        """返回页面尺寸（points），不受显示旋转影响（带缓存）。"""
        cached = self._size_cache.get(index)
        if cached is not None:
            return cached
        rect = self.doc[index].rect
        size = (rect.width, rect.height)
        self._size_cache[index] = size
        return size

    def render_page(self, index: int, zoom: float) -> fitz.Pixmap:
        """以 zoom 倍率（相对 72dpi，即 1.0=72dpi）渲染页面为 Pixmap。"""
        zoom = max(0.1, min(zoom, 8.0))
        mat = fitz_module().Matrix(zoom, zoom)
        return self.doc[index].get_pixmap(matrix=mat, alpha=False)

    def render_page_image(self, index: int, zoom: float, rotation: int = 0) -> QImage:
        """渲染并按显示方向旋转后返回 QImage（旋转在 Qt 侧完成，保证与视图变换一致）。"""
        pix = self.render_page(index, zoom)
        img = fitz_pixmap_to_qimage(pix)
        return rotate_image(img, rotation)

    def render_thumbnail(self, index: int, max_w: int = 160, rotation: int = 0) -> QImage:
        rect = self.doc[index].rect
        zoom = max_w / rect.width
        return self.render_page_image(index, zoom, rotation)

    def close(self) -> None:
        try:
            self.doc.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 阅读器能力：目录 / 文本 / 搜索 / 元信息
    # ------------------------------------------------------------------ #
    def outline(self) -> List[OutlineEntry]:
        """返回书签（目录）树，扁平列表 + level 表示层级（1 为顶层）。"""
        try:
            toc = self.doc.get_toc(simple=False)
        except Exception:
            return []
        entries: List[OutlineEntry] = []
        for item in toc:
            try:
                level = int(item[0])
                title = str(item[1]).strip()
                top_y = None
                if len(item) >= 4 and isinstance(item[3], dict):
                    # simple=False：dest['page'] 已是 0-based
                    dest = item[3]
                    pno = int(dest.get("page", int(item[2]) - 1))
                    to = dest.get("to", None)
                    if to is not None and hasattr(to, "y"):
                        top_y = float(to.y)
                else:
                    # simple=True：item[2] 是 1-based 页码
                    pno = int(item[2]) - 1
            except Exception:
                continue
            entries.append(OutlineEntry(level=max(1, level), title=title or "(无标题)",
                                        page=max(0, pno), top=top_y))
        return entries

    def has_outline(self) -> bool:
        try:
            return bool(self.doc.get_toc())
        except Exception:
            return False

    def page_text(self, index: int) -> str:
        """提取页面纯文本（失败返回空串，绝不抛异常）。"""
        try:
            return self.doc[index].get_text("text") or ""
        except Exception:
            return ""

    def page_links(self, index: int) -> List[dict]:
        """页面内部链接（供后续「点击链接跳转」扩展）。"""
        try:
            return self.doc[index].get_links() or []
        except Exception:
            return []

    def metadata(self) -> PdfMeta:
        try:
            m = self.doc.metadata or {}
        except Exception:
            m = {}
        return PdfMeta(
            title=str(m.get("title", "") or ""),
            author=str(m.get("author", "") or ""),
            subject=str(m.get("subject", "") or ""),
            keywords=str(m.get("keywords", "") or ""),
            creator=str(m.get("creator", "") or ""),
            producer=str(m.get("producer", "") or ""),
            page_count=self.doc.page_count,
            encrypted=bool(getattr(self.doc, "is_encrypted", False)),
            file_size=self.file_size,
        )

    def search(self, query: str, case_sensitive: bool = False, whole_word: bool = False,
               max_hits: int = 1000) -> List[SearchHit]:
        return search_document(self.doc, query, case_sensitive, whole_word, max_hits)

    # ------------------------------------------------------------------ #
    # 导出：把批注烧录进 PDF 页面，生成新 PDF
    # ------------------------------------------------------------------ #
    def export_with_annotations(self, annot: AnnotDocument, out_path: str,
                                bake_zoom: float = 2.0) -> None:
        """渲染每页（bake_zoom 倍率，默认 144dpi），在像素上绘制批注后写入新 PDF。

        批注坐标基于 PDF points；映射到像素：pixel = point * bake_zoom。保证导出位置准确。
        导出始终使用文档的原始方向（不受界面旋转影响）。
        """
        from PIL import Image, ImageDraw

        out = fitz_module().open()
        for i in range(self.doc.page_count):
            page = self.doc[i]
            pix = self.render_page(i, bake_zoom)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            strokes = annot.pages[i].strokes if i < len(annot.pages) else []
            img = _composite_strokes(img, strokes, bake_zoom)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            new_page = out.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(page.rect, stream=buf.getvalue())
        out.save(out_path)
        out.close()

    @staticmethod
    def _draw_stroke_on_image(draw: "ImageDraw.ImageDraw", stroke: Stroke, zoom: float) -> None:
        """在 PIL 图像上绘制一条笔画（像素坐标 = point * zoom）。"""
        draw_stroke_on_image(draw, stroke, zoom)


def search_document(doc, query: str, case_sensitive: bool = False,
                    whole_word: bool = False, max_hits: int = 1000) -> List[SearchHit]:
    """全文检索，返回按页序排列的命中列表（页码 0-based，矩形为 PDF points）。

    PyMuPDF 的 `search_for` 不区分大小写，因此「区分大小写」由提取命中区域的实际文本
    二次校验；「全词匹配」用 `get_text("words")` 找到命中矩形所在的完整单词再比对
    （直接用命中矩形的 textbox 会把 keyword 截断成 key，边界判断就会失效）。
    """
    query = (query or "").strip()
    if not query:
        return []
    hits: List[SearchHit] = []
    needle = query if case_sensitive else query.lower()
    phrase = any(ch.isspace() for ch in query)   # 短语查询不做全词过滤
    for i in range(doc.page_count):
        if len(hits) >= max_hits:
            break
        try:
            page = doc[i]
            rects = page.search_for(query) or []
        except Exception:
            continue
        words = None
        if whole_word and not phrase:
            try:
                words = page.get_text("words") or []
            except Exception:
                words = []
        for r in rects:
            if len(hits) >= max_hits:
                break
            box = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
            try:
                got = page.get_textbox(r) or ""
            except Exception:
                got = query
            if whole_word and not phrase:
                if not _whole_word_hit(words, r, needle, case_sensitive):
                    continue
            elif case_sensitive and query not in got:
                # 区分大小写但实际文本大小写不符 → 丢弃
                continue
            # 其余情况（连字符断行等导致文本不完全一致）保留命中，优先保证可视化
            hits.append(SearchHit(page=i, rect=box, context=_snippet(got)))
    return hits


def _whole_word_hit(words, rect, needle: str, case_sensitive: bool) -> bool:
    """命中矩形中心所在的完整单词是否恰好等于查询词。"""
    if not words:
        return True   # 拿不到分词信息时不拦截（可视化优先）
    cx = (rect.x0 + rect.x1) / 2.0
    cy = (rect.y0 + rect.y1) / 2.0
    for w in words:
        x0, y0, x1, y1, word = w[0], w[1], w[2], w[3], w[4]
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            w_cmp = word if case_sensitive else str(word).lower()
            return w_cmp == needle
    return False


def _snippet(text: str, limit: int = 60) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[:limit] + "…"


# ---------------------------------------------------------------------- #
# 异步渲染线程（避免大体积 PDF 光栅化阻塞 UI）
# ---------------------------------------------------------------------- #
class PdfRenderWorker(QThread):
    """一次性渲染某页，完成后通过 result 信号回传 QImage。"""

    result = Signal(int, float, object)  # (page_index, zoom, QImage)

    def __init__(self, engine: PdfEngine, index: int, zoom: float, rotation: int = 0):
        super().__init__()
        self._engine = engine
        self._index = index
        self._zoom = zoom
        self._rotation = rotation

    def run(self) -> None:
        try:
            img = self._engine.render_page_image(self._index, self._zoom, self._rotation)
            self.result.emit(self._index, self._zoom, img)
        except Exception:
            # 渲染失败静默处理，调用方仍显示旧内容或空白页
            pass


class PdfSearchWorker(QThread):
    """后台全文检索（自行打开独立文档，避免与渲染线程争用同一 fitz.Document）。

    大文档检索可能耗时数秒，放在线程里可保持界面可交互；每次新检索会作废上一次结果。
    """

    finished_hits = Signal(object, str, int)   # (List[SearchHit], query, token)

    def __init__(self, pdf_path: str, query: str, case_sensitive: bool = False,
                 whole_word: bool = False, token: int = 0):
        super().__init__()
        self._path = pdf_path
        self._query = query
        self._case = case_sensitive
        self._whole = whole_word
        self._token = token

    def run(self) -> None:
        hits: List[SearchHit] = []
        try:
            doc = fitz_module().open(self._path)
            try:
                hits = search_document(doc, self._query, self._case, self._whole)
            finally:
                doc.close()
        except Exception:
            hits = []
        self.finished_hits.emit(hits, self._query, self._token)


class PdfThumbnailWorker(QThread):
    """一次性生成缩略图后回传。

    每个 worker 自行打开一份独立的 fitz 文档（按路径），避免与页面渲染线程
    并发访问同一个 fitz.Document（PyMuPDF 文档对象非线程安全）。
    """

    result = Signal(int, object)  # (page_index, QImage)

    def __init__(self, pdf_path: str, index: int, max_w: int = 160, rotation: int = 0):
        super().__init__()
        self._pdf_path = pdf_path
        self._index = index
        self._max_w = max_w
        self._rotation = rotation

    def run(self) -> None:
        try:
            doc = fitz_module().open(self._pdf_path)
            rect = doc[self._index].rect
            zoom = self._max_w / rect.width
            pix = doc[self._index].get_pixmap(matrix=fitz_module().Matrix(zoom, zoom), alpha=False)
            img = fitz_pixmap_to_qimage(pix)
            doc.close()
            self.result.emit(self._index, rotate_image(img, self._rotation))
        except Exception:
            pass

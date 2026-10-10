"""主窗口：浏览器式多标签 + 侧边栏 + 顶部工具栏 + 中央画布 + 状态栏。

A7 之后本文件只保留「组装 + 属性代理」：
- 界面构建 / 主题响应式 → `src/ui/controllers/ui_build.py`、`ui_theme.py`
- 标签与多窗口 / 会话   → `controllers/tab_ops.py`、`window_ops.py`
- 文件与阅读 / 工具      → `controllers/file_ops.py`、`reading_ops.py`、`tool_ops.py`
- 画布                  → `src/ui/canvas*.py`（见 canvas.py 的说明）

`MainWindow` 上的 `mode` / `note_doc` / `canvas` 等属性是「当前标签」的代理：
单文档时代写好的业务代码几乎不用改，同时从根上杜绝「操作 A 却写到 B」。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCursor,
                           QGuiApplication, QKeySequence, QShortcut)
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QFileDialog,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSlider, QSpinBox, QSplitter, QStatusBar,
                               QTabBar, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)
from src.core.annot_document import AnnotDocument
from src.core.document_tab import DocumentTab, NOTE, PDF, same_path
from src.core.note_document import (BACKGROUND_TYPES, BG_BLANK, BG_COLOR_PRESETS,
                                    BG_LABELS, NoteDocument)
from src.core.settings import AppSettings
from src.render.pdf_engine import PdfEngine, PdfSearchWorker, export_note_to_pdf
from src.ui import win_feedback
from src.ui.canvas import PageCanvas
from src.ui.save_worker import AsyncSaver
from src.ui.theme import ThemeManager
from src.ui.layout_constants import APP_TITLE, COORD_LABEL_MIN_W, IDLE_CACHE_PAGES, MAX_RESTORED_TABS, SIDEBAR_AUTO_HIDE_W, SIDEBAR_MAX_W, SIDEBAR_MIN_W, WINDOW_MIN_H, WINDOW_MIN_W



from src.ui.controllers.file_ops import FileOpsMixin
from src.ui.controllers.reading_ops import ReadingOpsMixin
from src.ui.controllers.tab_ops import TabOpsMixin
from src.ui.controllers.tool_ops import ToolOpsMixin
from src.ui.controllers.ui_build import UiBuildMixin
from src.ui.controllers.ui_theme import UiThemeMixin
from src.ui.controllers.window_ops import WindowOpsMixin
from src.ui.window_registry import OPEN_WINDOWS as _OPEN_WINDOWS
from src.ui.window_registry import WindowRegistry as _WindowRegistry
from src.ui.window_registry import live_windows as _live_windows


class MainWindow(UiBuildMixin, UiThemeMixin, TabOpsMixin, WindowOpsMixin, FileOpsMixin, ReadingOpsMixin, ToolOpsMixin, QMainWindow):
    CANVAS_SIGNALS = ("zoom_changed", "page_info_changed", "cursor_pos_changed",
                      "content_modified", "undo_state_changed", "tool_changed",
                      "rotation_changed")
    def __init__(self, theme_name: str = "light",
                 settings: Optional[AppSettings] = None,
                 restore_session: bool = True,
                 restore_geometry: bool = True) -> None:
        """创建一个「浏览器窗口」。

        `restore_session=False` / `restore_geometry=False` 供「把标签拖出来」产生的新窗口
        使用：新窗口只接管被拖走的那一个标签，不重复恢复上次会话，也不套用主窗口保存的
        几何（位置由鼠标落点决定）。
        """
        super().__init__()
        self.settings = settings or AppSettings()
        self.theme = ThemeManager(theme_name or self.settings.theme())
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(WINDOW_MIN_W, WINDOW_MIN_H)

        # ---- 标签系统（唯一权威状态） ----
        self._tabs: List[DocumentTab] = []
        self._active: Optional[DocumentTab] = None
        # 侧边栏：区分「用户手动折叠」与「窄窗口自动折叠」，两者不要互相覆盖
        self._sidebar_auto_hidden = False
        self._sidebar_user_hidden = False
        self._was_narrow = False
        self._geo_restored = False
        #: 最近关闭的标签（供 Ctrl+Shift+T 恢复），只记能从磁盘重建的
        self._closed: List[Dict[str, str]] = []
        #: 正在被拖动（准备撕成独立窗口）的标签
        self._dragged_tab: Optional[DocumentTab] = None

        # ---- 全局（跟人走，不跟文档走）的工具状态 ----
        self._tool_id: str = self.settings.last_tool()
        # 触控笔 / 触摸的系统圆圈反馈策略（见 src/ui/win_feedback.py）
        self._touch_feedback: str = self.settings.touch_feedback()
        self._fb_actions: Dict[str, QAction] = {}

        self.recent_files: List[str] = self.settings.recent_files()
        self._icon_buttons: List[Tuple[QToolButton, str]] = []
        self._tool_buttons: Dict[str, QToolButton] = {}
        # 阅读进度写盘节流：翻页/滚动很频繁，不能每次都写 QSettings
        self._read_state_timer = QTimer(self)
        self._read_state_timer.setSingleShot(True)
        self._read_state_timer.setInterval(1200)
        self._read_state_timer.timeout.connect(self._save_current_read_state)

        # A4：保存调度器（小文档同步写、大文档后台写）
        self._saver = AsyncSaver(self)
        self._saver.finished.connect(self._on_save_finished)
        self._pending_saves: Dict[str, tuple] = {}
        self.last_save_error: str = ""
        #: 批注保存失败后「换位置重试」的记账（每个位置最多试一次，见 file_ops）
        self._annot_retry_done: bool = False
        self._annot_fallbacks: set = set()

        self._build_ui()
        self._connect_signals()
        self._build_recent_menu()
        self._apply_theme()
        if restore_geometry:
            self._restore_state()
            self._apply_initial_geometry()

        if not (restore_session and self._restore_session()):
            # 会话恢复失败（或这是「拖出来的」新窗口）时，初始空白标签继续用
            self._sync_ui_from_tab(self._active)
        self._set_tool(self._tool_id)
        self._update_undo_buttons()
        if self.canvas is not None:
            self.canvas.setFocus()
    @property
    def tab(self) -> Optional[DocumentTab]:
        return self._active
    @property
    def canvas(self) -> Optional[PageCanvas]:
        t = self._active
        return t.canvas if t is not None else None
    @property
    def mode(self) -> str:
        t = self._active
        return t.kind if t is not None else NOTE
    @mode.setter
    def mode(self, value: str) -> None:
        t = self._active
        if t is not None and value in (NOTE, PDF):
            t.kind = value
    @property
    def note_doc(self) -> Optional[NoteDocument]:
        t = self._active
        return t.note_doc if t is not None else None
    @property
    def note_path(self) -> Optional[str]:
        t = self._active
        return t.path if (t is not None and t.is_note) else None
    @note_path.setter
    def note_path(self, value: Optional[str]) -> None:
        t = self._active
        if t is not None:
            t.path = value
    @property
    def pdf_engine(self) -> Optional[PdfEngine]:
        t = self._active
        return t.pdf_engine if t is not None else None
    @pdf_engine.setter
    def pdf_engine(self, value) -> None:
        t = self._active
        if t is not None:
            t.pdf_engine = value
    @property
    def annot_doc(self) -> Optional[AnnotDocument]:
        t = self._active
        return t.annot_doc if t is not None else None
    @annot_doc.setter
    def annot_doc(self, value) -> None:
        t = self._active
        if t is not None:
            t.annot_doc = value
    @property
    def annot_path(self) -> Optional[str]:
        t = self._active
        return t.annot_path if t is not None else None
    @annot_path.setter
    def annot_path(self, value: Optional[str]) -> None:
        t = self._active
        if t is not None:
            t.annot_path = value
    @property
    def current_page(self) -> int:
        t = self._active
        return t.page if t is not None else 0
    @current_page.setter
    def current_page(self, value: int) -> None:
        t = self._active
        if t is not None:
            t.page = int(value)
    @property
    def dirty(self) -> bool:
        t = self._active
        return bool(t.dirty) if t is not None else False
    @dirty.setter
    def dirty(self, value: bool) -> None:
        t = self._active
        if t is None:
            return
        flag = bool(value)
        if t.dirty != flag:
            t.dirty = flag
            self._refresh_tab_title(t)
        if t is self._active:
            self._update_window_title()
    @property
    def _search_hits(self) -> List:
        t = self._active
        return t.search_hits if t is not None else []
    @_search_hits.setter
    def _search_hits(self, value) -> None:
        t = self._active
        if t is not None:
            t.search_hits = list(value or [])
    @property
    def _search_index(self) -> int:
        t = self._active
        return t.search_index if t is not None else -1
    @_search_index.setter
    def _search_index(self, value: int) -> None:
        t = self._active
        if t is not None:
            t.search_index = int(value)
    @property
    def _search_token(self) -> int:
        t = self._active
        return t.search_token if t is not None else 0
    @_search_token.setter
    def _search_token(self, value: int) -> None:
        t = self._active
        if t is not None:
            t.search_token = int(value)
    @property
    def _last_outline(self) -> List:
        t = self._active
        return t.outline if t is not None else []
    @_last_outline.setter
    def _last_outline(self, value) -> None:
        t = self._active
        if t is not None:
            t.outline = list(value or [])
    @property
    def _search_worker(self):
        t = self._active
        return t.search_worker if t is not None else None
    @_search_worker.setter
    def _search_worker(self, value) -> None:
        t = self._active
        if t is not None:
            t.search_worker = value
    def _connect_signals(self) -> None:
        """画布信号是「每个标签一份」的：建画布时连，槽里再按来源分流。

        侧边栏（缩略图/目录/搜索）是全局共享的，信号只在 _build_sidebar 里连一次。
        """
        for t in self._tabs:
            if t.canvas is not None:
                self._connect_canvas(t.canvas)
    def _connect_canvas(self, canvas: PageCanvas) -> None:
        """连接一个画布的信号。

        关键点：所有槽都额外带一个 `canvas` 参数，用来判断「这个信号是哪个标签
        发来的」。只有来自当前标签的信号才允许刷新界面，否则只更新数据——
        避免后台标签的迟到回调把当前标签的状态栏/页码写乱。
        """
        canvas.zoom_changed.connect(lambda v, c=canvas: self._on_canvas_zoom(c, v))
        canvas.page_info_changed.connect(
            lambda cur, tot, z, c=canvas: self._on_canvas_page_info(c, cur, tot, z))
        canvas.cursor_pos_changed.connect(
            lambda x, y, c=canvas: self._on_canvas_cursor(c, x, y))
        canvas.content_modified.connect(lambda c=canvas: self._on_canvas_modified(c))
        canvas.undo_state_changed.connect(
            lambda a, b, c=canvas: self._on_canvas_undo_state(c, a, b))
        canvas.tool_changed.connect(lambda tid, c=canvas: self._on_canvas_tool_changed(c, tid))
        canvas.rotation_changed.connect(
            lambda r, c=canvas: self._on_canvas_rotation(c, r))
    def _make_canvas(self) -> PageCanvas:
        """新建一个画布，并套用全局的工具/笔样式与主题底色。"""
        canvas = PageCanvas()
        self._connect_canvas(canvas)
        self._apply_tool_settings_to(canvas)
        canvas.set_canvas_bg(self.theme.canvas_bg())
        return canvas
    def _apply_tool_settings_to(self, canvas: PageCanvas) -> None:
        """工具与笔的样式是「跟人走」的全局设置，切标签时推给目标画布。"""
        canvas.set_pen_color(self.settings.pen_color())
        canvas.set_pen_width(self.settings.pen_width())
        canvas.set_eraser_width(self.settings.eraser_width())
        canvas.set_highlighter_color(self.settings.highlighter_color())
        canvas.set_highlighter_width(self.settings.highlighter_width())
        if self._tool_id:
            canvas.set_tool(self._tool_id)
    def _run_on_canvas(self, method: str) -> None:
        c = self.canvas
        if c is None:
            return
        fn = getattr(c, method, None)
        if callable(fn):
            fn()
    def _run_zoom(self, factor: float) -> None:
        c = self.canvas
        if c is not None:
            c.zoom_to(factor)
    def _status(self, text: str, timeout: int = 4000) -> None:
        """状态栏临时提示（替代烦人的模态弹窗）。"""
        self.statusBar().showMessage(text, timeout)

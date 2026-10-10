"""界面构建（A7 拆分自 main_window.py）：侧边栏 / 顶栏 / 菜单 / 快捷键 / 状态栏。

只负责「把控件搭起来并连信号」，不含业务判断；业务在 file/reading/tool/tab 各 mixin 里。
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
from src.core.document_tab import DocumentTab, NOTE, PDF, same_path
from src.core.note_document import (BACKGROUND_TYPES, BG_BLANK, BG_COLOR_PRESETS,
                                    BG_LABELS, NoteDocument)
from src.ui import win_feedback
from src.ui.outline_panel import OutlinePanel
from src.ui.search_panel import SearchPanel
from src.ui.tab_bar import DocTabBar
from src.ui.thumbnail_panel import ThumbnailPanel
from src.ui.topbar import TopBar
from src.ui.layout_constants import APP_TITLE, COORD_LABEL_MIN_W, IDLE_CACHE_PAGES, MAX_RESTORED_TABS, SIDEBAR_AUTO_HIDE_W, SIDEBAR_MAX_W, SIDEBAR_MIN_W, WINDOW_MIN_H, WINDOW_MIN_W




class UiBuildMixin:
    def _build_ui(self) -> None:
        self.sidebar = QWidget()
        self.sidebar.setObjectName("Sidebar")
        # 可拖拽调宽（宽度记忆在设置里）——不再写死 248px
        self.sidebar.setMinimumWidth(SIDEBAR_MIN_W)
        self.sidebar.setMaximumWidth(SIDEBAR_MAX_W)
        self._build_sidebar()

        # 标签栏要先建好：下面的工具栏按钮会引用 self.canvas（= 当前标签的画布）
        self._build_tab_widget()

        self.topbar = TopBar()
        self.topbar.setMinimumHeight(46)
        self.topbar.setMaximumHeight(62)
        self._build_topbar()

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self.topbar)
        center_layout.addWidget(self.tab_widget, 1)

        # 侧边栏 + 主区域放进 QSplitter：浏览器 / 编辑器式的可拖拽分隔条。
        # 旧实现是 setFixedWidth(248)：既不能拖，窄窗口下也不肯让位。
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("MainSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(4)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(center)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([self.settings.sidebar_width(), 1200])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        self.setCentralWidget(self.splitter)

        self._build_menus()
        self._build_shortcuts()
        self._build_statusbar()
    def _build_tab_widget(self) -> None:
        """标签栏 + 承载各标签画布的 QTabWidget（浏览器布局）。"""
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("DocTabs")
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.setMovable(True)              # 拖拽重排
        self.tab_widget.setElideMode(Qt.ElideRight)
        self.tab_widget.setUsesScrollButtons(True)    # 标签太多时滚动，不挤成一团

        bar = DocTabBar()
        self.tab_widget.setTabBar(bar)                # 必须在 addTab 之前

        # ⚠ setTabsClosable 必须在 setTabBar **之后**调用：QTabWidget 把「可关闭」状态
        # 记在自己身上并转交给「当时的」标签栏，先设再换栏，新栏就拿不到 × 按钮。
        # 曾经的真实缺陷：每个标签都没有关闭按钮（只能中键 / Ctrl+W 关）。
        self.tab_widget.setTabsClosable(True)
        # 新标签的按钮是 Qt 后来装上的，所以增删之后再补一次图标
        bar.tabs_changed.connect(self._schedule_tab_button_style)
        bar.middle_clicked.connect(self._on_tab_close_requested)
        # 拖出标签 → 独立窗口（或并进鼠标下方的另一个窗口）
        bar.tab_pressed.connect(self._on_tab_pressed)
        bar.detach_requested.connect(self._on_detach_requested)
        bar.empty_double_clicked.connect(lambda: self.new_tab_note())
        # tabMoved 是 QTabBar 的信号（QTabWidget 上没有），拖拽重排后同步内部顺序
        bar.tabMoved.connect(self._on_tab_moved)

        self.tab_widget.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tab_widget.currentChanged.connect(self._on_tab_current_changed)
        self.tab_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tab_widget.customContextMenuRequested.connect(self._show_tab_menu)

        self.btn_add_tab = QToolButton()
        self.btn_add_tab.setObjectName("NewTabButton")
        self.btn_add_tab.setText("＋")
        self.btn_add_tab.setAutoRaise(True)
        self.btn_add_tab.setToolTip("新建标签 (Ctrl+T)")
        self.btn_add_tab.clicked.connect(lambda: self.new_tab_note())
        # 右侧留一点边距，别让 + 贴着窗口边
        holder = QWidget()
        h = QHBoxLayout(holder)
        h.setContentsMargins(0, 0, 6, 0)
        h.setSpacing(0)
        h.addWidget(self.btn_add_tab)
        self.tab_widget.setCornerWidget(holder, Qt.TopRightCorner)

        # 首个空白标签必须在这里就建好：下面的工具栏按钮会引用 self.canvas，
        # 而画布是「每个标签一个」的，没有标签就没有画布可用。
        doc = NoteDocument(background=BG_BLANK,
                           background_color=self.settings.note_bg_color())
        first = DocumentTab(NOTE, self._make_canvas(), "未命名笔记")
        self._add_tab(first)
        self._load_note_into_tab(first, doc)
    def _build_sidebar(self) -> None:
        layout = QVBoxLayout(self.sidebar)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.side_title = QLabel("笔记本")
        self.side_title.setObjectName("SideTitle")
        self.side_title.setStyleSheet("font-size:15px; font-weight:700; color:#1f2328;")
        layout.addWidget(self.side_title)

        self.btn_new = QPushButton("+ 新建标签")
        self.btn_new.setObjectName("PrimaryBtn")
        self.btn_new.clicked.connect(lambda: self.new_tab_note())
        layout.addWidget(self.btn_new)

        recent_label = QLabel("最近文件")
        recent_label.setObjectName("SideTitle")
        layout.addWidget(recent_label)
        self.recent_list = QListWidget()
        self.recent_list.setMaximumHeight(150)
        self.recent_list.itemClicked.connect(self._on_recent_clicked)
        layout.addWidget(self.recent_list)

        # 页面管理（旧版本重构时丢失，现已补回）
        page_row = QHBoxLayout()
        page_row.setSpacing(6)
        self.btn_add_page = QPushButton("+ 添加页")
        self.btn_add_page.clicked.connect(self._add_page)
        self.btn_del_page = QPushButton("- 删除页")
        self.btn_del_page.clicked.connect(self._delete_page)
        page_row.addWidget(self.btn_add_page)
        page_row.addWidget(self.btn_del_page)
        layout.addLayout(page_row)

        # 侧边栏分页：页面缩略图 / 目录 / 搜索
        self.side_tabs = QTabWidget()
        self.thumbnails = ThumbnailPanel()
        self.thumbnails.page_selected.connect(self.goto_page)
        self.outline_panel = OutlinePanel()
        self.outline_panel.page_selected.connect(self._on_outline_jump)
        self.search_panel = SearchPanel()
        self.search_panel.search_requested.connect(self._start_search)
        self.search_panel.hit_activated.connect(self._goto_hit)
        self.search_panel.prev_requested.connect(
            lambda: self._goto_hit(self._search_index - 1))
        self.search_panel.next_requested.connect(
            lambda: self._goto_hit(self._search_index + 1))

        self.side_tabs.addTab(self.thumbnails, "页面")
        self.side_tabs.addTab(self.outline_panel, "目录")
        self.side_tabs.addTab(self.search_panel, "搜索")
        self.side_tabs.setTabEnabled(1, False)
        self.side_tabs.setTabEnabled(2, False)
        layout.addWidget(self.side_tabs, 1)
    @staticmethod
    def _cluster(*widgets: QWidget) -> QWidget:
        """把几个控件打包成一个整体，溢出时同进同退。

        QToolBar 的溢出是「一个控件一个控件」地收的，直接排开会出现
        「上一页还在、下一页没了」这种半截状态；打包后只会整组进溢出菜单。
        """
        box = QWidget()
        box.setObjectName("TopBarCluster")
        box.setStyleSheet("QWidget#TopBarCluster { background: transparent; }")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        for w in widgets:
            lay.addWidget(w)
        return box
    def _mk_btn(self, kind: str, tooltip: str, checkable: bool = False,
                slot=None, icon_size: int = 20, text: str = "") -> QToolButton:
        b = QToolButton()
        icon = self.theme.icon(kind, icon_size)
        if not icon.isNull():
            b.setIcon(icon)
        else:
            b.setText(text or tooltip)  # 无图标时退化显示文字
        b.setToolTip(tooltip)
        b.setIconSize(QSize(icon_size, icon_size))
        b.setCheckable(checkable)
        if slot is not None:
            b.clicked.connect(slot)
        self._icon_buttons.append((b, kind))
        return b
    def _build_topbar(self) -> None:
        """构建响应式顶栏：顺序即优先级（详见 src/ui/topbar.py）。

        * **核心组**永远可见，窗口最小宽度由它们决定；
        * **次要组**越靠后加入越先被收进 « 溢出菜单。

        旧实现是一条 QHBoxLayout 硬排 36 个控件（布局 minimumSize 1625px），
        加上固定 248px 侧边栏，把窗口最小宽度顶到 1873px —— 比 150% 缩放下的
        1080p 屏幕（逻辑宽 1280）还宽，右侧按钮必然被切掉。
        """
        bar = self.topbar

        # ---- 核心组：文件 + 撤销重做 + 工具 + 颜色笔宽 ----
        bar.add_core(self._mk_btn("menu", "折叠/展开侧边栏", slot=self._toggle_sidebar))
        bar.add_core(self._mk_btn("new", "新建标签 (Ctrl+N / Ctrl+T)",
                                  slot=lambda: self.new_tab_note()))
        bar.add_core(self._mk_btn("open", "打开笔记… (Ctrl+O)", slot=self._open_note_dialog))
        bar.add_core(self._mk_btn("open", "打开 PDF…", slot=self._open_pdf_dialog))
        bar.add_core(self._mk_btn("save", "保存 (Ctrl+S)", slot=self.save))
        bar.separator()

        # 注意：撤销/重做/工具都作用在「当前标签的画布」上，
        # 不能像以前那样直接绑定某个 canvas 实例的方法，否则切了标签按钮就失效了。
        self.btn_undo_tb = self._mk_btn("undo", "撤销 (Ctrl+Z)",
                                        slot=lambda: self._run_on_canvas("undo"))
        self.btn_redo_tb = self._mk_btn("redo", "重做 (Ctrl+Y)",
                                        slot=lambda: self._run_on_canvas("redo"))
        bar.add_core(self.btn_undo_tb)
        bar.add_core(self.btn_redo_tb)
        bar.separator()

        # 工具按钮：由注册表动态生成（扩展点）
        for tid, name in self.canvas.available_tools():
            b = self._mk_btn(tid, f"{name}", checkable=True,
                             slot=lambda checked=False, t=tid: self._set_tool(t))
            self._tool_buttons[tid] = b
            bar.add_core(b)

        self.btn_color = QPushButton()
        self.btn_color.setObjectName("ColorButton")
        self.btn_color.setFixedSize(24, 24)
        self.btn_color.setToolTip("笔迹颜色：单击弹出常用色（更多颜色在弹窗里）")
        self.btn_color.clicked.connect(self._show_color_menu)
        bar.add_core(self.btn_color)

        self.slider_width = QSlider(Qt.Horizontal)
        self.slider_width.setRange(1, 40)
        self.slider_width.setFixedWidth(80)
        self.slider_width.setToolTip("笔宽（当前工具）")
        bar.add_core(self.slider_width)
        self.lbl_width = QLabel("3px")
        self.lbl_width.setFixedWidth(34)
        bar.add_core(self.lbl_width)
        bar.separator()

        # ---- 次要组：从下往上依次是「最先被收进溢出菜单」的顺序 ----

        # 翻页：文档阅读最基本的导航，所以在次要组里优先级最高
        #（整组同进同退，不会出现「有上一页、没下一页」的半截状态）
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.setFixedWidth(50)
        # 关闭键盘跟踪：输入「12」时不会先跳到第 1 页再跳到第 12 页
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.setToolTip("页码，输入后回车跳转 (Ctrl+G)")
        self.page_spin.valueChanged.connect(lambda v: self.goto_page(v - 1))
        self.page_total_label = QLabel("/ 1")
        self.page_total_label.setFixedWidth(30)
        bar.add_optional(self._cluster(
            self._mk_btn("prev", "上一页 (PgUp)", slot=self.prev_page),
            self.page_spin, self.page_total_label,
            self._mk_btn("next", "下一页 (PgDn)", slot=self.next_page)))
        bar.separator()

        # 视图缩放（Ctrl+滚轮也能缩放，所以排在翻页之后）
        self.lbl_zoom_tb = QLabel("100%")
        self.lbl_zoom_tb.setFixedWidth(34)
        self.btn_actual = self._mk_btn("", "实际大小 100% (Ctrl+Shift+1)", text="100%",
                                       slot=self._actual_size)
        bar.add_optional(self._cluster(
            self._mk_btn("zoomout", "缩小", slot=lambda: self._run_zoom(1 / 1.2)),
            self.lbl_zoom_tb,
            self._mk_btn("zoomin", "放大", slot=lambda: self._run_zoom(1.2)),
            self._mk_btn("fit", "适应页面 (Ctrl+0)", slot=self._fit_view),
            self._mk_btn("fitwidth", "适应宽度 (Ctrl+Shift+9)", slot=self._fit_width),
            self.btn_actual))
        bar.separator()

        # 连续阅读 / 单页阅读切换（PDF 阅读模式）
        self.btn_read_mode = self._mk_btn("read_single",
                                          "单页阅读（点击切到连续滚动） (Ctrl+Shift+L)",
                                          checkable=True, slot=self._toggle_read_mode)
        bar.add_optional(self.btn_read_mode)

        # 笔记背景（样式 + 颜色）
        self.combo_bg = QComboBox()
        self.combo_bg.setFixedWidth(72)
        self.combo_bg.setToolTip("笔记背景样式")
        for bg in BACKGROUND_TYPES:
            self.combo_bg.addItem(BG_LABELS.get(bg, bg), bg)
        self.combo_bg.currentIndexChanged.connect(self._on_background_changed)
        self.btn_bg_color = QPushButton()
        self.btn_bg_color.setObjectName("ColorButton")
        self.btn_bg_color.setFixedSize(24, 24)
        self.btn_bg_color.setToolTip("笔记背景色")
        self.btn_bg_color.clicked.connect(self._show_bg_color_menu)
        bar.add_optional(self._cluster(self.combo_bg, self.btn_bg_color))
        bar.separator()

        # 旋转（PDF 扫描件才需要）
        self.btn_rot_left = self._mk_btn("rotleft", "向左旋转 90° (Ctrl+Shift+Left)",
                                         slot=lambda: self._rotate(-90))
        self.btn_rot_right = self._mk_btn("rotright", "向右旋转 90° (Ctrl+Shift+Right)",
                                          slot=lambda: self._rotate(90))
        bar.add_optional(self._cluster(self.btn_rot_left, self.btn_rot_right))

        # 以下三个都有菜单/快捷键入口，最窄时最先被收起来
        bar.add_optional(self._mk_btn("export", "导出 PDF… (Ctrl+E)", slot=self.export_pdf))
        self.btn_search = self._mk_btn("search", "搜索 PDF (Ctrl+F)", slot=self._focus_search)
        bar.add_optional(self.btn_search)
        self.btn_theme = self._mk_btn("moon", "切换深色/浅色主题", slot=self._toggle_theme)
        bar.add_optional(self.btn_theme)

        # 注意：宽度滑块的信号在工具同步后再连接，避免初始化时误写设置
        self.slider_width.valueChanged.connect(self._on_width_changed)
        self._set_color_swatch(self.settings.pen_color())
        self._set_bg_swatch(self.settings.note_bg_color())
    def _build_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")
        self.act_new = QAction("新建标签", self)
        self.act_new.setShortcut(QKeySequence.New)
        self.act_new.triggered.connect(lambda: self.new_tab_note())
        file_menu.addAction(self.act_new)
        self.act_open_note = QAction("打开笔记…", self)
        self.act_open_note.setShortcut(QKeySequence.Open)
        self.act_open_note.triggered.connect(self._open_note_dialog)
        file_menu.addAction(self.act_open_note)
        self.act_open_pdf = QAction("打开 PDF…", self)
        self.act_open_pdf.triggered.connect(self._open_pdf_dialog)
        file_menu.addAction(self.act_open_pdf)
        self.act_save = QAction("保存", self)
        self.act_save.setShortcut(QKeySequence.Save)
        self.act_save.triggered.connect(self.save)
        file_menu.addAction(self.act_save)
        self.act_save_as = QAction("另存为…", self)
        self.act_save_as.setShortcut(QKeySequence.SaveAs)
        self.act_save_as.triggered.connect(self.save_as)
        file_menu.addAction(self.act_save_as)
        self.act_export = QAction("导出 PDF…", self)
        self.act_export.setShortcut(QKeySequence("Ctrl+E"))
        self.act_export.triggered.connect(self.export_pdf)
        file_menu.addAction(self.act_export)
        self.recent_menu = file_menu.addMenu("最近文件")
        file_menu.addSeparator()
        self.act_doc_info = QAction("文档信息…", self)
        self.act_doc_info.triggered.connect(self._show_doc_info)
        file_menu.addAction(self.act_doc_info)
        self.act_exit = QAction("退出", self)
        self.act_exit.triggered.connect(self.close)
        file_menu.addAction(self.act_exit)

        edit_menu = menubar.addMenu("编辑(&E)")
        self.act_undo = QAction("撤销", self)
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_undo.triggered.connect(lambda: self._run_on_canvas("undo"))
        edit_menu.addAction(self.act_undo)
        self.act_redo = QAction("重做", self)
        self.act_redo.setShortcut(QKeySequence.Redo)
        self.act_redo.triggered.connect(lambda: self._run_on_canvas("redo"))
        edit_menu.addAction(self.act_redo)
        self.act_clear = QAction("清空当前页", self)
        self.act_clear.setShortcut(QKeySequence("Ctrl+K"))
        self.act_clear.triggered.connect(lambda: self._run_on_canvas("clear_page"))
        edit_menu.addAction(self.act_clear)
        edit_menu.addSeparator()
        self.act_add_page = QAction("添加页", self)
        self.act_add_page.triggered.connect(self._add_page)
        edit_menu.addAction(self.act_add_page)
        self.act_del_page = QAction("删除当前页", self)
        self.act_del_page.triggered.connect(self._delete_page)
        edit_menu.addAction(self.act_del_page)
        edit_menu.addSeparator()
        self.act_copy_text = QAction("复制当前页文本", self)
        self.act_copy_text.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self.act_copy_text.triggered.connect(self._copy_page_text)
        edit_menu.addAction(self.act_copy_text)

        # ---- 标签菜单（v5 新增，浏览器习惯的入口集中在这里） ----
        tab_menu = menubar.addMenu("标签(&A)")
        self.act_new_tab = QAction("新建标签", self)
        self.act_new_tab.setShortcut(QKeySequence("Ctrl+T"))
        self.act_new_tab.triggered.connect(lambda: self.new_tab_note())
        tab_menu.addAction(self.act_new_tab)
        self.act_close_tab = QAction("关闭标签", self)
        self.act_close_tab.setShortcut(QKeySequence("Ctrl+W"))
        self.act_close_tab.triggered.connect(self.close_current_tab)
        tab_menu.addAction(self.act_close_tab)
        self.act_close_others = QAction("关闭其他标签", self)
        self.act_close_others.triggered.connect(self.close_other_tabs)
        tab_menu.addAction(self.act_close_others)
        self.act_close_all = QAction("关闭全部标签", self)
        self.act_close_all.triggered.connect(self.close_all_tabs)
        tab_menu.addAction(self.act_close_all)
        tab_menu.addSeparator()
        self.act_next_tab = QAction("下一个标签", self)
        self.act_next_tab.setShortcut(QKeySequence("Ctrl+Tab"))
        self.act_next_tab.triggered.connect(lambda: self.cycle_tab(1))
        tab_menu.addAction(self.act_next_tab)
        self.act_prev_tab = QAction("上一个标签", self)
        self.act_prev_tab.setShortcut(QKeySequence("Ctrl+Shift+Tab"))
        self.act_prev_tab.triggered.connect(lambda: self.cycle_tab(-1))
        tab_menu.addAction(self.act_prev_tab)
        self.act_reopen_tab = QAction("恢复关闭的标签", self)
        self.act_reopen_tab.setShortcut(QKeySequence("Ctrl+Shift+T"))
        self.act_reopen_tab.triggered.connect(self.reopen_closed_tab)
        tab_menu.addAction(self.act_reopen_tab)
        tab_menu.addSeparator()
        self.act_copy_path = QAction("复制当前文件路径", self)
        self.act_copy_path.triggered.connect(self._copy_current_path)
        tab_menu.addAction(self.act_copy_path)

        view_menu = menubar.addMenu("视图(&V)")
        self.act_zoom_in = QAction("放大", self)
        self.act_zoom_in.setShortcut(QKeySequence.ZoomIn)
        self.act_zoom_in.triggered.connect(lambda: self._run_zoom(1.2))
        view_menu.addAction(self.act_zoom_in)
        self.act_zoom_out = QAction("缩小", self)
        self.act_zoom_out.setShortcut(QKeySequence.ZoomOut)
        self.act_zoom_out.triggered.connect(lambda: self._run_zoom(1 / 1.2))
        view_menu.addAction(self.act_zoom_out)
        self.act_fit = QAction("适应页面", self)
        self.act_fit.setShortcut(QKeySequence("Ctrl+0"))
        self.act_fit.triggered.connect(self._fit_view)
        view_menu.addAction(self.act_fit)
        # Ctrl+9 / Ctrl+1 已让位给「切换第 N 个标签」（浏览器习惯），
        # 视图快捷键相应改为 Ctrl+Shift+9 / Ctrl+Shift+1。
        self.act_fit_width = QAction("适应宽度", self)
        self.act_fit_width.setShortcut(QKeySequence("Ctrl+Shift+9"))
        self.act_fit_width.triggered.connect(self._fit_width)
        view_menu.addAction(self.act_fit_width)
        self.act_actual = QAction("实际大小 100%", self)
        self.act_actual.setShortcut(QKeySequence("Ctrl+Shift+1"))
        self.act_actual.triggered.connect(self._actual_size)
        view_menu.addAction(self.act_actual)
        view_menu.addSeparator()
        self.act_rot_left = QAction("向左旋转 90°", self)
        self.act_rot_left.setShortcut(QKeySequence("Ctrl+Shift+Left"))
        self.act_rot_left.triggered.connect(lambda: self._rotate(-90))
        view_menu.addAction(self.act_rot_left)
        self.act_rot_right = QAction("向右旋转 90°", self)
        self.act_rot_right.setShortcut(QKeySequence("Ctrl+Shift+Right"))
        self.act_rot_right.triggered.connect(lambda: self._rotate(90))
        view_menu.addAction(self.act_rot_right)
        view_menu.addSeparator()
        self.act_read_single = QAction("单页阅读", self)
        self.act_read_single.setCheckable(True)
        self.act_read_single.triggered.connect(lambda checked=False: self._set_read_mode("single"))
        view_menu.addAction(self.act_read_single)
        self.act_read_continuous = QAction("连续阅读（滚动）", self)
        self.act_read_continuous.setCheckable(True)
        self.act_read_continuous.triggered.connect(
            lambda checked=False: self._set_read_mode("continuous"))
        view_menu.addAction(self.act_read_continuous)
        view_menu.addSeparator()
        self.act_theme_menu = QAction("切换主题", self)
        self.act_theme_menu.triggered.connect(self._toggle_theme)
        view_menu.addAction(self.act_theme_menu)
        self.act_toggle_sidebar = QAction("折叠/展开侧边栏", self)
        self.act_toggle_sidebar.triggered.connect(self._toggle_sidebar)
        view_menu.addAction(self.act_toggle_sidebar)
        view_menu.addSeparator()
        # 触控笔圆圈反馈：Windows 会在落笔处画半透明圆圈，书写时频繁触发。
        # 这里按窗口开关（只影响本程序），默认「安静」——保留长按反馈，去掉轻点圆圈。
        self.feedback_menu = view_menu.addMenu("触控笔圆圈反馈（系统）")
        self._fb_group = QActionGroup(self)
        self._fb_group.setExclusive(True)
        for policy, label in win_feedback.POLICY_LABELS.items():
            act_fb = QAction(label, self)
            act_fb.setCheckable(True)
            act_fb.setChecked(policy == self._touch_feedback)
            act_fb.triggered.connect(
                lambda checked=False, p=policy: self._set_touch_feedback(p))
            self._fb_group.addAction(act_fb)
            self.feedback_menu.addAction(act_fb)
            self._fb_actions[policy] = act_fb

        # 工具菜单（由注册表动态生成，含快捷键）
        self.tool_menu = menubar.addMenu("工具(&T)")
        self._tool_actions: Dict[str, QAction] = {}
        shortcuts = {"pen": "B", "eraser": "E", "highlighter": "H"}
        for tid, name in self.canvas.available_tools():
            act = QAction(name, self)
            sc = shortcuts.get(tid)
            if sc:
                act.setShortcut(QKeySequence(sc))
            act.setCheckable(True)
            act.triggered.connect(lambda checked=False, t=tid: self._set_tool(t))
            self.tool_menu.addAction(act)
            self._tool_actions[tid] = act
        self.tool_menu.addSeparator()
        self.act_find = QAction("搜索…", self)
        self.act_find.setShortcut(QKeySequence.Find)
        self.act_find.triggered.connect(self._focus_search)
        self.tool_menu.addAction(self.act_find)
        self.act_goto = QAction("跳转到页…", self)
        self.act_goto.setShortcut(QKeySequence("Ctrl+G"))
        self.act_goto.triggered.connect(self._goto_dialog)
        self.tool_menu.addAction(self.act_goto)

        help_menu = menubar.addMenu("帮助(&H)")
        about = QAction("关于", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)
    def _build_shortcuts(self) -> None:
        """阅读器键盘导航 + 浏览器式标签快捷键。"""
        pairs = [
            ("PgDown", self.next_page),
            ("PgUp", self.prev_page),
            ("Right", self.next_page),
            ("Left", self.prev_page),
            ("Home", self.first_page),
            ("End", self.last_page),
            ("Ctrl+F", self._focus_search),
            ("Ctrl+G", self._goto_dialog),
            ("Ctrl+Shift+L", self._toggle_read_mode),
            ("Ctrl+Shift+T", self.reopen_closed_tab),
            ("Ctrl+Tab", lambda: self.cycle_tab(1)),
            ("Ctrl+Shift+Tab", lambda: self.cycle_tab(-1)),
        ]
        # 注意：必须用 WindowShortcut（默认值），不能用 ApplicationShortcut ——
        # 多窗口时同一个 ApplicationShortcut 会被多个窗口同时注册，Qt 判定为
        # 「ambiguous shortcut overload」，结果是按了没反应。
        self._shortcuts = []
        for seq, slot in pairs:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)

        # Ctrl+1..8 直达第 N 个标签，Ctrl+9 直达最后一个（与浏览器一致）
        for n in range(1, 10):
            sc = QShortcut(QKeySequence(f"Ctrl+{n}"), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda num=n: self.goto_tab_number(num))
            self._shortcuts.append(sc)
    def _build_statusbar(self) -> None:
        sb: QStatusBar = self.statusBar()
        self.lbl_tool = QLabel("工具: 钢笔")
        self.lbl_page = QLabel("页: 1 / 1")
        self.lbl_zoom = QLabel("缩放: 100%")
        self.lbl_coord = QLabel("坐标: -")
        for w in (self.lbl_tool, self.lbl_page, self.lbl_zoom, self.lbl_coord):
            w.setMinimumWidth(110)
            sb.addWidget(w)
    def _restore_state(self) -> None:
        geo = self.settings.window_geometry()
        self._geo_restored = geo is not None
        if geo is not None:
            self.restoreGeometry(geo)
        # 历史几何可能是旧版留下的「超宽窗口」（旧版最小宽度 1873px），恢复后必须夹一次
        self._clamp_to_screen()

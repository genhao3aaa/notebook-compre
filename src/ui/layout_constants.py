"""窗口与侧边栏的尺寸/常量（A7 拆分自 main_window.py）。

放在独立模块里，是为了让拆分出去的 controllers/* 与 main_window 共用同一份数值，
避免「复制一份常量、改的时候漏掉一处」。
"""

from __future__ import annotations

APP_TITLE = "手写笔记本与 PDF 批注"
MAX_RESTORED_TABS = 8
IDLE_CACHE_PAGES = 3
WINDOW_MIN_W, WINDOW_MIN_H = 720, 480
SIDEBAR_MIN_W, SIDEBAR_MAX_W = 180, 460
SIDEBAR_AUTO_HIDE_W = 940
COORD_LABEL_MIN_W = 860

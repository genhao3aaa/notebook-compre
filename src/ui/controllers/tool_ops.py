"""工具与样式（A7 拆分自 main_window.py）：钢笔/荧光笔/橡皮、颜色、笔宽、背景色、触控反馈。

样式是**全局**的（跟人走）：换色/换宽会推给所有标签的画布。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCursor,
                           QGuiApplication, QKeySequence, QShortcut)
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QFileDialog,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSlider, QSpinBox, QSplitter, QStatusBar,
                               QTabBar, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)
from src.core.colors import (DEFAULT_HIGHLIGHTER_COLOR, DEFAULT_PEN_COLOR,
                             HIGHLIGHTER_ALPHA, HIGHLIGHTER_COLOR_PRESETS,
                             PEN_COLOR_PRESETS, argb_hex, blend_over,
                             normalize_hex, parse_rgba, with_alpha)
from src.core.document_tab import DocumentTab, NOTE, PDF, same_path
from src.ui import win_feedback
from src.ui.canvas import PageCanvas
from src.ui.color_palette import ColorPalettePopup




class ToolOpsMixin:
    def _set_tool(self, tool_id: str) -> None:
        self._tool_id = tool_id
        c = self.canvas
        if c is not None:
            c.set_tool(tool_id)
        self._sync_tool_ui()
    def _on_canvas_tool_changed(self, canvas: PageCanvas, tool_id: str) -> None:
        self._tool_id = tool_id
        self._sync_tool_ui()
    def _sync_tool_ui(self) -> None:
        c = self.canvas
        tid = c.current_tool_id() if c is not None else self._tool_id
        for k, b in self._tool_buttons.items():
            b.setChecked(k == tid)
        for k, act in getattr(self, "_tool_actions", {}).items():
            act.setChecked(k == tid)
        # 建窗口的过程中 canvas 会先于状态栏创建，此时这些控件还不存在
        if getattr(self, "lbl_tool", None) is None:
            return
        name = dict(c.available_tools()).get(tid, tid) if c is not None else tid
        tool = c.tool if c is not None else None
        self.lbl_tool.setText(f"工具: {name}" + (f" {tool.status_text()}" if tool else ""))
        if tid:
            self.settings.set_last_tool(tid)
        self._set_color_swatch(self._current_tool_color())
        self._sync_width_control()
    def _sync_width_control(self) -> None:
        """宽度滑块跟随当前工具（钢笔=笔宽，橡皮=橡皮大小，荧光笔=荧光笔粗细）。"""
        if getattr(self, "slider_width", None) is None:
            return
        c = self.canvas
        tid = c.current_tool_id() if c is not None else self._tool_id
        if tid == "eraser":
            value = self.settings.eraser_width()
        elif tid == "highlighter":
            value = self.settings.highlighter_width()
        else:
            value = self.settings.pen_width()
        self.slider_width.blockSignals(True)
        self.slider_width.setValue(int(round(value)))
        self.slider_width.blockSignals(False)
        self.lbl_width.setText(f"{int(round(value))}px")
    def _on_width_changed(self, v: int) -> None:
        self.lbl_width.setText(f"{v}px")
        c = self.canvas
        tid = c.current_tool_id() if c is not None else self._tool_id
        # 笔宽是全局设置：同步给所有标签的画布，保持手感一致
        for t in self._tabs:
            if t.canvas is None:
                continue
            if tid == "eraser":
                t.canvas.set_eraser_width(float(v))
            elif tid == "highlighter":
                t.canvas.set_highlighter_width(float(v))
            else:
                t.canvas.set_pen_width(float(v))
        if tid == "eraser":
            self.settings.set_eraser_width(float(v))
        elif tid == "highlighter":
            self.settings.set_highlighter_width(float(v))
        else:
            self.settings.set_pen_width(float(v))
    def _current_tool_color(self) -> str:
        """当前工具的颜色（荧光笔有自己的半透明颜色）。"""
        c = self.canvas
        if c is None:
            return self.settings.pen_color()
        if c.current_tool_id() == "highlighter":
            return c.highlighter_color
        return c.pen_color
    def _set_color_swatch(self, color: str) -> None:
        # 半透明色（荧光笔）先叠加到卡片底色上，QSS 里显示实色最稳妥
        preview = blend_over(color, self.theme.colors()["card"])
        self.btn_color.setStyleSheet(
            f"background-color: {preview}; border: 1px solid #888; border-radius: 6px;")
        self.btn_color.setToolTip(
            f"当前工具颜色 {color} · 单击选常用色，更多颜色在弹窗里")
    def _color_tool_id(self) -> str:
        """颜色作用在哪个工具上：荧光笔有自己的半透明色盘，其余（含橡皮）都算钢笔。"""
        c = self.canvas
        tid = c.current_tool_id() if c is not None else self._tool_id
        return "highlighter" if tid == "highlighter" else "pen"
    def _default_color_for(self, tool: str) -> str:
        return DEFAULT_HIGHLIGHTER_COLOR if tool == "highlighter" else DEFAULT_PEN_COLOR
    def _recent_color_items(self, tool: str):
        """最近使用的颜色 -> [(hex, 名称)]，名称优先取常用色盘里的中文名。"""
        presets = HIGHLIGHTER_COLOR_PRESETS if tool == "highlighter" else PEN_COLOR_PRESETS
        names = {hexc.lower(): name for hexc, name in presets}
        return [(hexc, names.get(hexc.lower(), hexc))
                for hexc in self.settings.recent_colors(tool)]
    def _build_color_popup(self) -> Optional[ColorPalettePopup]:
        """构建颜色面板（只构建、不弹出）：弹出与冒烟测试共用同一份构建逻辑。"""
        if self.canvas is None:
            return None
        tool = self._color_tool_id()
        is_hl = tool == "highlighter"
        c = self.theme.colors()
        return ColorPalettePopup(
            self,
            title="荧光笔颜色" if is_hl else "笔颜色",
            common=HIGHLIGHTER_COLOR_PRESETS if is_hl else PEN_COLOR_PRESETS,
            recent=self._recent_color_items(tool),
            current=self._current_tool_color(),
            on_pick=self._apply_tool_color,
            on_more=self._choose_color,
            on_default=lambda: self._apply_tool_color(self._default_color_for(tool)),
            card=c["card"], text=c["text"], muted=c["text_secondary"],
            accent=c["accent"])
    def _show_color_menu(self) -> None:
        """点色块：先给一屏常用色（单击即换），完整调色板收在「更多颜色…」。"""
        popup = self._build_color_popup()
        if popup is None:
            return
        popup.exec(self.btn_color.mapToGlobal(self.btn_color.rect().bottomLeft()))
    def _apply_tool_color(self, color: str) -> None:
        """把颜色应用到当前工具（全局样式：同步所有标签的画布）并记住最近使用。"""
        if self.canvas is None or not color:
            return
        tool = self._color_tool_id()
        if tool == "highlighter":
            # 完整调色板可能给的是不透明色：补上默认透明度，避免荧光笔变成实心块
            hexc = (with_alpha(color, HIGHLIGHTER_ALPHA)
                    if parse_rgba(color)[3] == 255 else argb_hex(color))
            for t in self._tabs:
                if t.canvas is not None:
                    t.canvas.set_highlighter_color(hexc)
            self.settings.set_highlighter_color(hexc)
        else:
            hexc = normalize_hex(color)
            for t in self._tabs:
                if t.canvas is not None:
                    t.canvas.set_pen_color(hexc)
            self.settings.set_pen_color(hexc)
        self.settings.add_recent_color(tool, hexc)
        self._set_color_swatch(hexc)
    def _choose_color(self) -> None:
        """完整调色板（弹窗里的「更多颜色…」）：取色器 / 色轮 / HSV / 透明度。"""
        if self.canvas is None:
            return
        is_hl = self._color_tool_id() == "highlighter"
        opts = (QColorDialog.ColorDialogOption.ShowAlphaChannel if is_hl
                else QColorDialog.ColorDialogOption(0))
        color = QColorDialog.getColor(
            QColor(self._current_tool_color()), self,
            "选择荧光笔颜色" if is_hl else "选择笔颜色", opts)
        if not color.isValid():
            return
        self._apply_tool_color(color.name(QColor.NameFormat.HexArgb) if is_hl
                               else color.name())
    def _on_background_changed(self, index: int) -> None:
        tab = self._active
        if tab is None or not tab.is_note or tab.note_doc is None:
            return
        bg = self.combo_bg.itemData(index)
        if bg is None:
            return
        tab.note_doc.set_background(bg)
        tab.canvas.set_background(bg, tab.note_doc.background_color)
        self.thumbnails.set_note_doc(tab.note_doc)
        self.thumbnails.set_current(tab.page)
        self.mark_dirty()
    def _show_about(self) -> None:
        QMessageBox.about(
            self, "关于",
            "手写笔记本与 PDF 批注 v5\n\n"
            "PySide6 + PyMuPDF 实现的 Windows 桌面手写程序。\n"
            "支持空白笔记手写（可自定义背景色）、PDF 批注、\n"
            "书签目录、全文搜索、页码跳转、页面旋转、撤销/重做、导出。\n"
            "v5：浏览器式多标签——笔记与 PDF 可以任意混开，\n"
            "每个标签独立保留缩放、页码与撤销栈，切换即时。\n\n"
            "工具可通过 src.tools.register_tool 扩展。")
    def _set_touch_feedback(self, policy: str) -> None:
        """切换系统圆圈反馈策略（只影响本程序窗口，不动系统全局设置）。"""
        self._touch_feedback = policy
        self.settings.set_touch_feedback(policy)
        act = self._fb_actions.get(policy)
        if act is not None:
            act.setChecked(True)
        if not win_feedback.available():
            self._status("当前系统不支持按窗口调整触控反馈（仅 Windows 8+）", 3500)
            return
        win_feedback.apply_policy(self, policy)
        self._status(f"触控圆圈反馈：{win_feedback.POLICY_LABELS.get(policy, policy)}", 3500)

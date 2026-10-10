"""画布输入路由（A7 拆分自 canvas.py）：鼠标 / 触控笔 / 滚轮 / 键盘。

输入层的几条历史教训（改这里之前先读）：
1. **鼠标与触控笔必须双通道互斥**（`_active_device`）：某些 Windows 机型「按下走 tablet、
   松开走 mouse」，早期实现会卡死导致鼠标完全画不出字；
2. **非笔类型的 tablet 事件要显式 `e.ignore()`**：Qt 只在「笔事件未被接受」时才补发
   鼠标事件，不 ignore 就等于把这类输入彻底吞掉；
3. **绝不要把 Qt 枚举交给 `int()`**：PySide6 6.11 起 `Qt.MouseButton` 是 pybind11 的
   flag（不再是 int 子类），`int(e.button())` 抛 `TypeError`，而且抛在事件处理函数
   里 —— 症状就是「手写笔能写、鼠标完全画不出字」（v5.1 的真实回归）；
4. **鼠标悬停（不按键）也要能进 `_pointer_move`**：否则橡皮圈提示与状态栏坐标只在
   按住键时才刷新；
5. **切页时不能清掉设备标记**：连续阅读在别的页上起笔会先切页（`_bind_page()` 会
   重置 `_active_device` / `drawing`），不还原就只剩一个点被提交。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QTabletEvent, QTransform)
from src.core.hn_log import get_logger, log_debug, log_warning

BG_BLANK = "blank"
BG_GRID = "grid"
BG_LINES = "lines"
BG_DOTS = "dots"
#: 明确「不是手写笔」的指针类型：显式 ignore，交给鼠标通道。
#:
#: 口径说明（别照着 Qt5 的文档改这里）：PySide6 6.11 的 `pointerType()` 是
#: `QPointingDevice.PointerType` **flag**，`.name` 只会返回 "Pen" / "Eraser" /
#: "Unknown" / "Generic" / "Finger" / "Cursor" 及其组合，Qt5 的
#: "UnknownPointer" / "Mouse" 编号语义已经不存在；而真实鼠标也不会走 tablet 事件。
#: 因此这个集合只是「老绑定 / 老驱动的兜底」，重点是**不要**把 Unknown / Generic
#: 收进来：那类上报往往是识别不准的手写笔，走笔通道至少还能保住压感。
_NON_PEN_POINTERS = frozenset({"Mouse", "UnknownPointer"})
#: 旧式（Qt5 语义）数值 → 名字，仅在拿不到 `.name` 的老版本绑定上兜底。
_TABLET_NAME_BY_VALUE = {0: "UnknownPointer", 1: "Pen", 2: "Cursor",
                         3: "Eraser", 4: "Mouse"}
#: 手写笔通道有多久没收到事件就认为它已经「死」了（秒）。
#: Windows 上笔离开感应区 / 切窗口时 Release 会丢，此时必须自愈，否则
#: `_active_device` 永久卡在 "tablet"，鼠标再也画不出字。
_TABLET_STALE_SEC = 1.5
_log = get_logger("ui.canvas")
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
def _tablet_pointer_name(e: QTabletEvent) -> str:
    """手写笔指针类型名（Qt6 用 flag 的 `.name`；老绑定回退到数值表）。"""
    pt = e.pointerType()
    name = getattr(pt, "name", None)
    if isinstance(name, str) and name:
        return name
    raw = getattr(pt, "value", pt)
    try:
        return _TABLET_NAME_BY_VALUE.get(int(raw), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"
def _mouse_event_from_pen(e) -> bool:
    """这次鼠标事件是不是 Qt 为手写笔事件补发的？是的话必须丢掉。

    Qt 在「手写笔事件没有被接受」时会补发一套鼠标事件（兼容老程序），
    它带 `MouseEventSynthesizedByQt` 标记。漏掉这个判断，一条笔画会被
    笔通道和鼠标通道各画一半。
    """
    src = getattr(e, "source", None)
    if src is None:                      # 极老版本没有 source()：保守起见不丢
        return False
    try:
        return src() == Qt.MouseEventSource.MouseEventSynthesizedByQt
    except Exception:
        return False
def _event_local_pos(e) -> QPointF:
    """兼容 Qt6 各版本：优先 position()，回退 posF()/pos()。"""
    if hasattr(e, "position"):
        return e.position()
    if hasattr(e, "posF"):
        return e.posF()
    return QPointF(e.pos())



class CanvasInputMixin:
    def _prepare_ctx(self) -> None:
        self.ctx.strokes = self.page_strokes
        self.ctx.undo = self.undo_mgr
        self.ctx.page_w = self.page_w_pt
        self.ctx.page_h = self.page_h_pt
        self.ctx.scale = self.scale
    def _pointer_down(self, css: QPointF, pressure: float) -> None:
        if self._panning or self.tool is None:
            return
        if not self._inside_page(css):
            log_debug(_log, "pointer_down_outside_page", x=round(css.x(), 1), y=round(css.y(), 1))
            return
        # 切页会重绑画布状态（set_pdf_page → _bind_page 会把 _active_device / drawing
        # 清掉），而设备标记是输入层的事：必须在绑定之后还原，否则后续 move 事件
        # 全被「设备不匹配」丢掉 —— 表现是「在别的页上只能点出一个点」。
        device = self._active_device
        if self.read_mode == "continuous" and self.mode == "pdf":
            idx = self._page_at_css(css)
            if (idx is not None and idx != self.pdf_index
                    and self.on_page_request is not None):
                self.on_page_request(idx)   # 同步把「当前页」切过去，批注写到正确的页
        self._active_device = device
        self._prepare_ctx()
        pt = self._to_page_point(css, pressure)
        self.tool.begin(self.ctx, pt)
        self.drawing = True
        if self.tool.paints_live and self.tool.current_stroke is not None:
            self._draw_live_ink(self.tool.current_stroke)
        self.update()
        self._emit_cursor(pt[0], pt[1])
        log_debug(_log, "pointer_down", tool=self._current_tool_id,
                  x=round(pt[0], 1), y=round(pt[1], 1))
    def _pointer_move(self, css: QPointF, pressure: float) -> None:
        if self.drawing and self.tool is not None:
            self._prepare_ctx()
            pt = self._to_page_point(css, pressure)
            added = self.tool.move(self.ctx, pt)
            if added and self.tool.paints_live and self.tool.current_stroke is not None:
                self._draw_live_ink(self.tool.current_stroke)
            self.update()
            self._emit_cursor(pt[0], pt[1])
        else:
            self._hover = True
            self._hover_css = css
            self._emit_cursor(*self._to_page_point(css, 1.0)[:2])
            self.update()
    def _pointer_up(self) -> None:
        if not self.drawing or self.tool is None:
            self.drawing = False
            return
        self._prepare_ctx()
        cur = self.tool.current_stroke
        last = cur.points[-1] if (cur is not None and cur.points) else (0.0, 0.0, 1.0)
        changed = self.tool.end(self.ctx, last)
        self.drawing = False
        # 抬笔后按「受影响笔画」重绘一次：
        # - 橡皮（paints_live=False）：删掉笔画后必须重画；
        # - 钢笔 / 荧光笔：抬笔时做了 RDP 简化（A3），必须把这段区域按简化后的点重画，
        #   否则屏幕上留的是简化前的墨迹、存下来的是简化后的点（「抬笔变形」回归）。
        # 实测一次区域重绘约 1 ms（1000 笔画页、真实手写长度），不影响书写延迟。
        if changed:
            changed_strokes = self.undo_mgr.last_changed if self.undo_mgr else None
            if changed_strokes:
                self.repaint_strokes(changed_strokes)
            else:
                self.rebuild_all()
                self.update()
        self._emit_undo_state()
        self.update()
        log_debug(_log, "pointer_up", changed=changed)
    def _tablet_channel_dead(self) -> bool:
        """手写笔通道是否已经「僵死」（标记还在，但久未收到笔事件）。"""
        if self._active_device != "tablet":
            return False
        return (time.monotonic() - self._tablet_last_ts) > _TABLET_STALE_SEC
    def _recover_stale_gesture(self) -> None:
        """自愈：上一次手势的收尾事件丢了，把输入状态清干净。

        典型场景：笔在感应区外松手、书写中切走窗口、驱动把 Release 吞了。
        不做这件事的后果是**鼠标永久失效**（`_active_device` 卡在 "tablet"，
        之后每次鼠标按下都被当成「笔补发的鼠标事件」丢掉）。
        半途的笔画按「取消」处理（与 Esc 一致，不写进撤销栈）。
        """
        abandoned = self.tool.current_stroke if self.tool is not None else None
        if self.tool is not None:
            self.tool.current_stroke = None
        self.drawing = False
        self._panning = False
        self._using_tablet = False
        self._active_device = None
        if self._eraser_override:
            self._switch_tool_temp(self._pre_override_tool)
            self._eraser_override = False
        log_warning(_log, "stale_gesture_recovered")
        if abandoned is not None and abandoned.points:
            self.repaint_strokes([abandoned])   # A2：只擦掉这条草稿墨迹
        else:
            self.rebuild_all()
            self.update()
    def mousePressEvent(self, e) -> None:
        # 自愈优先：无论这次鼠标事件从哪来，都不能让「卡住的笔通道」把鼠标永久堵死。
        if self._tablet_channel_dead():
            self._recover_stale_gesture()
        elif self._active_device == "tablet":
            return                              # 笔正在书写：忽略系统补发的鼠标事件
        if _mouse_event_from_pen(e):
            return                              # 笔补发的鼠标事件：笔通道自己会处理
        self._active_device = "mouse"
        self._using_tablet = False
        pos = _event_local_pos(e)
        # 注意：不要把 Qt 枚举交给 int()。PySide6 6.11 起 Qt.MouseButton 是
        # pybind11 的 flag（不是 int 子类），int(e.button()) 会抛 TypeError，
        # 而且是在事件处理函数里抛 —— 直接导致「鼠标完全画不出字」。
        # 日志层（src/core/hn_log.py 的 _field_text）会把枚举/标志转成数值。
        log_debug(_log, "mouse_press", button=e.button(), x=round(pos.x(), 1),
                  y=round(pos.y(), 1))
        if e.button() == Qt.MiddleButton or (self.space_pressed and e.button() == Qt.LeftButton):
            self._panning = True
            self._pan_last = pos
            self.setCursor(QCursor(Qt.ClosedHandCursor))
            return
        if e.button() == Qt.LeftButton:
            self._pointer_down(pos, 1.0)
    def mouseMoveEvent(self, e) -> None:
        pos = _event_local_pos(e)
        if self._panning:
            delta = pos - self._pan_last
            self.pan_by(delta.x(), delta.y())
            self._pan_last = pos
            return
        # `None` 也要放行：没按键的移动是「悬停」，橡皮圈提示与状态栏坐标靠它刷新。
        # （只排除 "tablet"：书写中系统补发的鼠标移动不能混进笔通道。）
        if self._active_device not in (None, "mouse"):
            return
        self._pointer_move(pos, 1.0)
    def mouseReleaseEvent(self, e) -> None:
        if self._active_device == "tablet":
            if not self._tablet_channel_dead():
                # 笔还在写：这次鼠标松开是系统/平台补发的，**不能**动状态，
                # 否则会把正在写的笔画拦腰截断（笔的用户会立刻感知到）。
                return
            # 笔通道确定已经僵死（Release 丢了）：自愈，让鼠标重新可用。
            self._recover_stale_gesture()
            return
        if self._panning and e.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._panning = False
            self._update_cursor()
            self._active_device = None
            return
        if e.button() == Qt.LeftButton:
            log_debug(_log, "mouse_release")
            self._pointer_up()
        self._active_device = None
    def wheelEvent(self, e) -> None:
        dy = e.angleDelta().y()
        if not dy:
            # 高精度触摸板 / 触控滚轮只上报 pixelDelta（angleDelta 为 0）：
            # 旧实现把它当成「没滚」，触摸板用户完全翻不动 PDF。
            dy = e.pixelDelta().y()
            if not dy:
                return
        if e.modifiers() & Qt.ControlModifier:
            factor = 1.1 if dy > 0 else 1.0 / 1.1
            self.zoom_at(_event_local_pos(e), factor)
            return
        if e.modifiers() & Qt.ShiftModifier:
            self.pan_by(-dy, 0.0)          # Shift+滚轮：横向平移
            return
        # 向上滚（dy>0）→ 视图上移，看到页面上方内容（与 Adobe / 资源管理器一致）
        self.pan_by(0.0, dy)
        self._emit_page_info()
    def _scroll_by_wheel(self, vertical: bool, delta: float) -> None:
        """滚轮落在滑动条上时由滑动条转发过来：与画布滚轮同一套语义。

        滚动条自己不吃滚动是网页的常识（指针在条上也该能滚），所以原样转发。
        """
        if vertical:
            self.pan_by(0.0, delta)
        else:
            self.pan_by(-delta, 0.0)
        self._emit_page_info()
    def _scroll_by_key(self, e) -> bool:
        """键盘微调滚动：`↑` / `↓` 一次 48 px（配合滑动条的精调手感）。

        只做这一件事，别在这里补 PgUp / PgDn / `←` / `→` / Home / End：它们已被主窗口的
        QShortcut 占作「翻页 / 到首尾」（见 ui_build._build_shortcuts），而 QShortcut
        的优先级高于控件的 keyPressEvent —— 在这里再写一遍只会得到永远走不到的死分支，
        测试还会因为「直接调 keyPressEvent」而假装它是活的。
        """
        key = e.key()
        if key == Qt.Key.Key_Down:
            self.pan_by(0.0, -48.0)          # 内容上移 = 看到下方
        elif key == Qt.Key.Key_Up:
            self.pan_by(0.0, 48.0)
        else:
            return False
        self._emit_page_info()
        return True
    def tabletEvent(self, e: QTabletEvent) -> None:
        pname = _tablet_pointer_name(e)
        if pname in _NON_PEN_POINTERS:
            # 不是真实手写笔：显式 ignore —— Qt 只在「笔事件未被接受」时才补发
            # 鼠标事件，不 ignore 就等于把这类输入彻底吞掉（某些驱动把笔上报成
            # Unknown/Mouse，用户会觉得「笔和鼠标都不能写」）。
            e.ignore()
            return
        # 只有真正进入笔通道的事件才刷新「心跳」，否则被忽略的鼠标类 tablet 事件
        # 会一直把僵死的笔通道「续命」，鼠标就再也拿不回控制权了。
        self._tablet_last_ts = time.monotonic()
        t = e.type()
        pos = _event_local_pos(e)
        pressure = e.pressure() if (e.pressure() and e.pressure() > 0) else 1.0

        if t == QEvent.Type.TabletPress:
            if self._active_device == "mouse":
                return
            self._active_device = "tablet"
            self._using_tablet = True
            # 笔尾橡皮：临时切到橡皮，抬笔后还原
            if pname == "Eraser" and self._current_tool_id != "eraser":
                self._eraser_override = True
                self._pre_override_tool = self._current_tool_id
                self._switch_tool_temp("eraser")
            self._pointer_down(pos, pressure)
            e.accept()
        elif t == QEvent.Type.TabletMove:
            if self._active_device != "tablet":
                return
            if self.drawing:
                self._pointer_move(pos, pressure)
            else:
                self._hover = True
                self._hover_css = pos
                self._emit_cursor(*self._to_page_point(pos, 1.0)[:2])
                self.update()
            e.accept()
        elif t == QEvent.Type.TabletRelease:
            self._pointer_up()
            if self._eraser_override:
                self._switch_tool_temp(self._pre_override_tool)
                self._eraser_override = False
            self._using_tablet = False
            self._active_device = None
            e.accept()
    def _switch_tool_temp(self, tool_id: str) -> None:
        """数位板笔尾橡皮：临时切换工具（不发 tool_changed，避免 UI 抖动）。"""
        if tool_id not in self._tools:
            return
        self._current_tool_id = tool_id
        self.tool = self._tools[tool_id]
        self._update_cursor()
    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Space and not self.space_pressed:
            self.space_pressed = True
            self._update_cursor()
        elif not self.drawing and self._scroll_by_key(e):
            return
        elif e.key() == Qt.Key_Escape and self.drawing:
            abandoned = self.tool.current_stroke if self.tool is not None else None
            if self.tool is not None:
                self.tool.current_stroke = None
            self.drawing = False
            self._active_device = None
            if abandoned is not None and abandoned.points:
                self.repaint_strokes([abandoned])   # A2：只擦掉这条草稿墨迹
            else:
                self.rebuild_all()
                self.update()
    def keyReleaseEvent(self, e) -> None:
        if e.key() == Qt.Key_Space:
            self.space_pressed = False
            self._update_cursor()
    def focusOutEvent(self, event) -> None:
        # 失焦时把「按住式」的瞬时状态清掉：空格平移 / 平移手势都可能因为
        # 切窗口再也收不到 release 而永久卡住（`_panning` 卡住 = 鼠标批注失效，
        # `_pointer_down` 第一行就 return）。
        if self.space_pressed:
            self.space_pressed = False
        if self._panning:
            self._panning = False
            self._active_device = None
        self._update_cursor()
        super().focusOutEvent(event)
    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

"""Windows 触摸 / 触控笔「圆圈反馈」的按窗口控制。

现象
----
用触控笔（或手指）在屏幕上落笔时，Windows 会在接触点画半透明的圆圈
（触点可视化、轻点反馈、长按圆环）。书写时几乎每一次落笔都会触发，
视觉干扰很大。**这些圆圈是系统画在窗口上的，不是本程序画的**
（本程序唯一自绘的圆圈是橡皮工具的半径提示）。

做法
----
`user32!SetWindowFeedbackSetting` 允许**按窗口**开关指定类型的反馈，
只影响本程序的窗口，不改系统全局设置、也不影响其它程序：

* `quiet`（默认）：关掉「写字时一直在闪」的两类（触点可视化、轻点反馈），
  保留长按 / 右键这类「刻意操作」才出现的反馈 —— **特效还在，只是不再频繁触发**；
* `off`：所有反馈类型全部关掉；
* `system`：恢复系统默认（不做任何设置）。

非 Windows 平台或系统不支持该 API 时，全部函数都是安全的空操作。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Dict, Optional, Tuple

__all__ = [
    "FEEDBACK_TOUCH_CONTACTVISUALIZATION", "FEEDBACK_PEN_TAP", "FEEDBACK_PEN_PRESSANDHOLD",
    "NOISY_TYPES", "ALL_TYPES", "POLICIES", "POLICY_LABELS",
    "available", "policy_types", "apply_policy", "restore",
]

# FEEDBACK_TYPE（winuser.h）
FEEDBACK_TOUCH_CONTACTVISUALIZATION = 1
FEEDBACK_PEN_BARRELVISUALIZATION = 2
FEEDBACK_PEN_TAP = 3
FEEDBACK_PEN_DOUBLETAP = 4
FEEDBACK_PEN_PRESSANDHOLD = 5
FEEDBACK_PEN_RIGHTTAP = 6
FEEDBACK_TOUCH_TAP = 7
FEEDBACK_TOUCH_DOUBLETAP = 8
FEEDBACK_TOUCH_PRESSANDHOLD = 9
FEEDBACK_TOUCH_RIGHTTAP = 10
FEEDBACK_GESTURE_PRESSANDHOLD = 11

ALL_TYPES: Tuple[int, ...] = (
    FEEDBACK_TOUCH_CONTACTVISUALIZATION,
    FEEDBACK_PEN_BARRELVISUALIZATION,
    FEEDBACK_PEN_TAP,
    FEEDBACK_PEN_DOUBLETAP,
    FEEDBACK_PEN_PRESSANDHOLD,
    FEEDBACK_PEN_RIGHTTAP,
    FEEDBACK_TOUCH_TAP,
    FEEDBACK_TOUCH_DOUBLETAP,
    FEEDBACK_TOUCH_PRESSANDHOLD,
    FEEDBACK_TOUCH_RIGHTTAP,
    FEEDBACK_GESTURE_PRESSANDHOLD,
)

#: 落笔 / 触碰时几乎每次都会闪的反馈 —— 默认关掉（这就是「太频繁」的来源）
NOISY_TYPES: Tuple[int, ...] = (
    FEEDBACK_TOUCH_CONTACTVISUALIZATION,   # 手指触点圆圈
    FEEDBACK_TOUCH_TAP,                    # 手指轻点圆圈
    FEEDBACK_PEN_TAP,                      # 触控笔轻点圆圈（书写时最常触发）
)

#: 策略 -> {反馈类型: True 打开 / False 关闭 / None 恢复系统默认}
POLICIES: Dict[str, Dict[int, Optional[bool]]] = {
    "quiet": {t: False for t in NOISY_TYPES},
    "off": {t: False for t in ALL_TYPES},
    "system": {t: None for t in ALL_TYPES},
}

POLICY_LABELS: Dict[str, str] = {
    "quiet": "安静（推荐：书写不再频繁闪圈，长按仍保留）",
    "off": "完全关闭系统圆圈反馈",
    "system": "系统默认（不干预）",
}

_WINTYPES = None
_FN = None
_PROBED = False


def _api():
    """惰性解析 user32!SetWindowFeedbackSetting（不可用返回 (None, None)）。"""
    global _WINTYPES, _FN, _PROBED
    if not _PROBED:
        _PROBED = True
        if sys.platform == "win32":
            try:
                from ctypes import wintypes              # 仅 Windows 有意义的类型别名
                lib = ctypes.WinDLL("user32", use_last_error=True)
                fn = lib.SetWindowFeedbackSetting
                fn.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.DWORD,
                               wintypes.UINT, ctypes.c_void_p]
                fn.restype = wintypes.BOOL
                _WINTYPES, _FN = wintypes, fn
            except Exception:                            # pragma: no cover - 取决于系统
                _WINTYPES, _FN = None, None
    return _WINTYPES, _FN


def available() -> bool:
    """当前系统是否支持按窗口控制反馈（Windows 8+）。"""
    return _api()[1] is not None


def policy_types(policy: str) -> Dict[int, Optional[bool]]:
    """策略对应的反馈类型表（未知策略退化为 quiet）。"""
    return dict(POLICIES.get(policy, POLICIES["quiet"]))


def _set_feedback(fn, wintypes, hwnd: int, feedback: int,
                  enable: Optional[bool]) -> bool:
    try:
        if enable is None:
            # size = 0 且 configuration = NULL：清除本窗口的设置 → 回到系统默认
            return bool(fn(wintypes.HWND(hwnd), int(feedback), 0, 0, None))
        value = wintypes.BOOL(bool(enable))
        return bool(fn(wintypes.HWND(hwnd), int(feedback), 0,
                        ctypes.sizeof(value), ctypes.byref(value)))
    except Exception:                                    # pragma: no cover
        return False


def apply_policy(widget, policy: str) -> int:
    """把策略应用到窗口（取其 HWND）。返回成功下发的条目数。

    注意：Qt 可能重建原生窗口（改窗口标志等），因此窗口每次显示时都应重新调用；
    重复调用是幂等的。
    """
    wintypes, fn = _api()
    if fn is None or widget is None:
        return 0
    try:
        hwnd = int(widget.winId())
    except Exception:
        return 0
    if not hwnd:
        return 0
    done = 0
    for feedback, enable in policy_types(policy).items():
        if _set_feedback(fn, wintypes, hwnd, feedback, enable):
            done += 1
    return done


def restore(widget) -> int:
    """恢复系统默认（等价于 `apply_policy(widget, "system")`）。"""
    return apply_policy(widget, "system")

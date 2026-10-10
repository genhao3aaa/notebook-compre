"""颜色工具：解析、明暗判定与「在给定底色上可读」的线条色。

为什么单独成模块
----------------
笔记背景色可由用户指定（可能为深色），而网格/横线/点阵的线条色必须随之反转，
否则深色底上会看不到辅助线。画布、缩略图、PDF 导出三处都要用到同一套判定。
"""

from __future__ import annotations

from typing import List, Tuple

DEFAULT_BG_COLOR = "#ffffff"

RGB = Tuple[int, int, int]
RGBA = Tuple[int, int, int, int]


def parse_hex(color: str, default: str = DEFAULT_BG_COLOR) -> RGB:
    """把 #rgb / #rrggbb / rrggbb 解析为 (r, g, b)。解析失败返回 default。"""
    s = (color or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        s = default.lstrip("#")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return parse_hex(default, "ffffff")


def normalize_hex(color: str, default: str = DEFAULT_BG_COLOR) -> str:
    """规范化为 #rrggbb 小写形式。"""
    r, g, b = parse_hex(color, default)
    return "#%02x%02x%02x" % (r, g, b)


def _channel_lin(c: int) -> float:
    v = c / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    """相对亮度（0=黑，1=白），用于判断底色明暗。"""
    r, g, b = parse_hex(color)
    return 0.2126 * _channel_lin(r) + 0.7152 * _channel_lin(g) + 0.0722 * _channel_lin(b)


def is_dark(color: str) -> bool:
    """底色是否偏暗（阈值 0.45，兼顾米黄/浅灰等常用纸色）。"""
    return luminance(color) < 0.45


def line_color_for(bg: str) -> str:
    """根据底色返回可读的辅助线颜色（浅底深线 / 深底浅线）。"""
    return "#5a6577" if is_dark(bg) else "#c9d6e5"


def grid_color_for(bg: str) -> str:
    return "#6b7686" if is_dark(bg) else "#d8e0ea"


def ink_contrast_color(bg: str) -> str:
    """在给定底色上「对比最强」的默认墨色（用于 UI 提示）。"""
    return "#f2f4f7" if is_dark(bg) else "#1f2328"


def parse_rgba(color: str, default: str = DEFAULT_BG_COLOR) -> RGBA:
    """解析颜色为 (r, g, b, a)。

    支持 #rgb / #rrggbb（不透明）以及 Qt 的 #aarrggbb（alpha 在前）。
    解析失败时回退到 default（默认白色不透明）。
    """
    s = (color or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    elif len(s) == 4:            # #argb
        s = "".join(ch * 2 for ch in s)
    alpha = 255
    if len(s) == 8:              # #aarrggbb
        try:
            alpha = int(s[0:2], 16)
        except ValueError:
            return parse_rgba(default, "ffffff")
        s = s[2:]
    if len(s) != 6:
        return parse_rgba(default, "ffffff")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), alpha)
    except ValueError:
        return parse_rgba(default, "ffffff")


def is_translucent(color: str) -> bool:
    """颜色是否带透明度（荧光笔用）。"""
    return parse_rgba(color)[3] < 255


def blend_over(color: str, bg: str = DEFAULT_BG_COLOR) -> str:
    """把可能带透明度的颜色叠加到不透明底色上，返回 #rrggbb。

    用途：UI 色块预览——QSS 的 background-color 对 8 位色支持不一致，
    直接显示叠加后的实色最稳妥。
    """
    r, g, b, a = parse_rgba(color)
    br, bg_g, bb, _ = parse_rgba(bg)
    f = a / 255.0
    return "#%02x%02x%02x" % (round(r * f + br * (1 - f)),
                              round(g * f + bg_g * (1 - f)),
                              round(b * f + bb * (1 - f)))


# --------------------------------------------------------------------------- #
# 笔 / 荧光笔的常用色盘（工具栏「常用色」快捷盘的数据源）
#
# 为什么需要：工具栏色块原来直接弹完整调色板（取色器 + 色轮 + HSV + 十六进制），
# 换一支常用色要「点色块 → 找颜色 → 确定」三四下。这里给出「一屏常用色」，
# 单击即生效；完整调色板退到弹窗里的「更多颜色…」入口。
# --------------------------------------------------------------------------- #
DEFAULT_PEN_COLOR = "#000000"

#: 荧光笔不透明度（0x66 ≈ 40%），与 stroke.py 的 HIGHLIGHTER_COLOR 保持一致
HIGHLIGHTER_ALPHA = 0x66
DEFAULT_HIGHLIGHTER_COLOR = "#66ffd54f"

#: 钢笔常用墨色：6 × 2 网格，单击即换色
PEN_COLOR_PRESETS: List[Tuple[str, str]] = [
    ("#000000", "黑"),
    ("#4b5563", "深灰"),
    ("#e11d48", "红"),
    ("#f97316", "橙"),
    ("#facc15", "黄"),
    ("#16a34a", "绿"),
    ("#06b6d4", "青"),
    ("#2563eb", "蓝"),
    ("#4f46e5", "靛"),
    ("#9333ea", "紫"),
    ("#92400e", "棕"),
    ("#ffffff", "白"),
]

#: 荧光笔常用色（#AARRGGBB，40% 不透明；叠加后仍能看清原文）
HIGHLIGHTER_COLOR_PRESETS: List[Tuple[str, str]] = [
    ("#66ffd54f", "琥珀"),
    ("#66a3e635", "嫩绿"),
    ("#6622d3ee", "天青"),
    ("#663b82f6", "蓝"),
    ("#66f472b6", "粉"),
    ("#66fb923c", "橙"),
    ("#66c084fc", "紫"),
    ("#66f87171", "红"),
]

#: 「最近使用」最多记住几种颜色
RECENT_COLOR_LIMIT = 8


def argb_hex(color: str) -> str:
    """规范化为 #aarrggbb 小写形式（Qt 的 HexArgb 约定：alpha 在前）。"""
    r, g, b, a = parse_rgba(color)
    return "#%02x%02x%02x%02x" % (a, r, g, b)


def with_alpha(color: str, alpha: int) -> str:
    """给颜色套上指定不透明度，返回 #aarrggbb。"""
    r, g, b, _a = parse_rgba(color)
    return argb_hex("#%02x%02x%02x%02x" % (max(0, min(255, int(alpha))), r, g, b))


def same_color(a: str, b: str) -> bool:
    """两个颜色是否等价（忽略大小写与写法差异，透明度参与比较）。"""
    return parse_rgba(a) == parse_rgba(b)


def push_recent_color(items: List[str], color: str, limit: int = RECENT_COLOR_LIMIT) -> List[str]:
    """把 color 放到「最近使用」最前面：去重（忽略大小写）、截断到 limit。"""
    if not color:
        return list(items)[:limit]
    out = [c for c in items if not same_color(c, color)]
    out.insert(0, color)
    return out[:limit]

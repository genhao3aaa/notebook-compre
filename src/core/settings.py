r"""应用设置持久化（主题、工具偏好、最近文件、窗口几何）。

集中管理 QSettings，避免各模块散落读写；也是未来新增偏好项的扩展点。

「写不进去」怎么办（2026-10-01 线上问题）
----------------------------------------
默认后端是 Windows 注册表（`HKCU\Software\WorkBuddy\HandwritingNotebook`）。
在受限策略 / 沙箱 / 只读注册表的环境里，**注册表写会失败**：QSettings 此时只是把
`status()` 置为 `AccessError`，`setValue` 不抛异常、也不报错——于是「上次读到第几页」
这类状态看起来在程序里生效了，**关掉再开就全没了**（用户看到的现象就是「页码不
更新 / 记不住位置」）。

因此这里做一次**可写探测**：

1. 注册表（历史行为，正常环境原样不动）；
2. 注册表写不进去（或 `HN_SETTINGS_INI=1` 强制）→ 落到 INI 文件，候选顺序：
   `HN_SETTINGS_FILE` → `%APPDATA%\WorkBuddy\HandwritingNotebook.ini`
   → `%LOCALAPPDATA%\手写笔记本与PDF批注\settings.ini` → `%TEMP%\...\settings.ini`；
3. 每次启动按同一顺序找**已有的**文件，找不到再按可写探测选一个——所以换后端
   之后设置照样能读回来。

`AppSettings.storage()` 可以查当前用的是哪个后端（排查用）。
"""

from __future__ import annotations

import json
import os
import time
from typing import List, Optional

from PySide6.QtCore import QByteArray, QSettings

from src.core.hn_log import get_logger, log_warning
from src.core.stroke import HIGHLIGHTER_COLOR, HIGHLIGHTER_WIDTH

_log = get_logger(__name__)

MAX_RECENT = 10

#: 阅读状态 / MD5 缓存最多保留多少条（超出后按写入顺序淘汰最早的）
MAX_PDF_STATE = 200
MAX_MD5_CACHE = 200

#: 「最近使用」的颜色最多记几种（工具栏常用色快捷盘用）
MAX_RECENT_COLORS = 8

#: 显式指定设置文件（INI）路径
ENV_SETTINGS_FILE = "HN_SETTINGS_FILE"
#: 置 1/true/yes 时强制用 INI 文件（跳过注册表）
ENV_FORCE_INI = "HN_SETTINGS_INI"
#: 设置文件名
SETTINGS_INI_NAME = "settings.ini"

#: 探测结果缓存：'registry' / 'ini' / 'none'（None = 还没探测过）
_BACKEND_CACHE: Optional[str] = None


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _flags_path() -> str:
    """标记「注册表曾经写不进去」的小文件路径（换到 INI 后写在这里）。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ""
    if base:
        return os.path.join(base, "手写笔记本与PDF批注", "registry-unavailable.flag")
    return ""


def settings_candidates() -> List[str]:
    """INI 候选路径（保持顺序，去重）。"""
    out: List[str] = []
    env = (os.environ.get(ENV_SETTINGS_FILE) or "").strip().strip('"')
    if env:
        out.append(os.path.abspath(os.path.expandvars(os.path.expanduser(env))))
    roaming = os.environ.get("APPDATA") or ""
    if roaming:
        out.append(os.path.join(roaming, "WorkBuddy", "HandwritingNotebook.ini"))
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        out.append(os.path.join(local, "手写笔记本与PDF批注", SETTINGS_INI_NAME))
    try:
        import tempfile
        out.append(os.path.join(tempfile.gettempdir(), "手写笔记本与PDF批注", SETTINGS_INI_NAME))
    except Exception:
        pass
    seen = set()
    uniq: List[str] = []
    for p in out:
        if not p:
            continue
        key = _norm(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def _ini_readable(path: str) -> bool:
    """这个 INI 文件已经存在且有内容（说明它曾经被真的写成功过）。"""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _ini_writable(path: str) -> bool:
    """真写一次再读回来，确认 INI 后端可用（QSettings 失败时会静默吞掉）。"""
    try:
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        qs = QSettings(path, QSettings.Format.IniFormat)
        if qs.status() != QSettings.Status.NoError:
            return False
        qs.setValue("_write_probe_", "1")
        qs.sync()
        if qs.status() != QSettings.Status.NoError:
            return False
        qs.remove("_write_probe_")
        qs.sync()
        return os.path.isfile(path)
    except OSError:
        return False


def _registry_writable(org: str, app: str) -> bool:
    """注册表真的能写吗（QSettings 的 status() 会如实报告 AccessError）。"""
    try:
        qs = QSettings(org, app)
        qs.setValue("_write_probe_", "1")
        qs.sync()
        ok = qs.status() == QSettings.Status.NoError
        try:
            qs.remove("_write_probe_")
            qs.sync()
        except Exception:                        # pragma: no cover - 清理失败无所谓
            pass
        return ok
    except Exception:                            # pragma: no cover - 防御性
        return False


def choose_settings_file() -> str:
    """选一个可用的 INI 设置文件；都不可用时返回空串。"""
    candidates = settings_candidates()
    for path in candidates:
        if _ini_readable(path):                  # 以前写过：直接沿用，保证读回旧偏好
            return path
    for path in candidates:
        if _ini_writable(path):
            return path
    return ""


def detect_settings_backend(org: str = "WorkBuddy", app: str = "HandwritingNotebook",
                           refresh: bool = False) -> str:
    r"""决定用哪个后端：'registry'（注册表，历史行为）或 'ini'（文件）。

    判定顺序：
    1. `HN_SETTINGS_INI=1` → 强制 INI（用 `HN_SETTINGS_FILE` 可指定路径）；
    2. `%LOCALAPPDATA%\手写笔记本与PDF批注\registry-unavailable.flag` 存在 → INI
       （说明上次启动已经发现注册表写不进去，不再反复探测）；
    3. 真探一次注册表：写得进 → registry；写不进 → 记下标记并改用 INI。

    结果在进程内缓存（`refresh=True` 可以强制重探）。
    """
    global _BACKEND_CACHE
    if _BACKEND_CACHE is not None and not refresh:
        return _BACKEND_CACHE
    force = (os.environ.get(ENV_FORCE_INI) or "").strip().lower()
    if force in ("1", "true", "yes", "on"):
        _BACKEND_CACHE = "ini"
        return _BACKEND_CACHE
    if check_registry_flagged():
        _BACKEND_CACHE = "ini"
        return _BACKEND_CACHE
    if _registry_writable(org, app):
        _BACKEND_CACHE = "registry"
        return _BACKEND_CACHE
    mark_registry_unavailable()
    log_warning(_log, "settings_registry_unavailable",
                fallback="；".join(settings_candidates()))
    _BACKEND_CACHE = "ini"
    return _BACKEND_CACHE


def check_registry_flagged() -> bool:
    """上次启动是否已经发现注册表写不进去（避免每次启动都去探测注册表）。"""
    path = _flags_path()
    if not path:
        return False
    try:
        if not os.path.isfile(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def mark_registry_unavailable() -> None:
    """记一笔「注册表写不进去」（尽力而为，失败也无所谓）。"""
    path = _flags_path()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("1")
    except OSError:
        pass


def _doc_key(path: str) -> str:
    """文档标识：绝对路径 + 规范化大小写（Windows 路径大小写不敏感）。"""
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        return str(path)

_DEFAULTS = {
    "theme": "light",
    "last_tool": "pen",
    "pen_color": "#000000",
    "pen_width": 3.0,
    "eraser_width": 20.0,
    "highlighter_color": HIGHLIGHTER_COLOR,
    "highlighter_width": HIGHLIGHTER_WIDTH,
    "last_dir": "",
    "note_bg_color": "#ffffff",
    "pdf_read_mode": "single",
    # 触控笔 / 触摸的系统圆圈反馈策略，见 src/ui/win_feedback.py
    "touch_feedback": "quiet",
}


class AppSettings:
    """对 QSettings 的薄封装，带类型安全的默认值。

    后端选择见模块文档：注册表优先，写不进去自动换 INI 文件（并记住选择）。
    """

    def __init__(self, org: str = "WorkBuddy", app: str = "HandwritingNotebook",
                 path: Optional[str] = None, backend: Optional[str] = None) -> None:
        self._org = org
        self._app = app
        self._backend = ""
        self._ini_path = ""
        if path:
            # 测试用：改用 INI 文件存储，避免依赖注册表（受限环境下注册表可能只读）
            self._qs = QSettings(path, QSettings.Format.IniFormat)
            self._backend = "ini"
            self._ini_path = os.path.abspath(path)
            return
        backend = backend or detect_settings_backend(org, app)
        if backend == "ini":
            ini = choose_settings_file()
            if ini:
                self._qs = QSettings(ini, QSettings.Format.IniFormat)
                self._backend = "ini"
                self._ini_path = ini
                return
            # 连 INI 都写不了：仍然退回注册表（至少同一次运行内可读）
            log_warning(_log, "settings_backend_unavailable",
                        candidates="；".join(settings_candidates()))
        self._qs = QSettings(org, app)
        self._backend = "registry"

    # ------------------------------------------------------------------ #
    # 后端信息（排查用）
    # ------------------------------------------------------------------ #
    def storage(self) -> dict:
        """当前设置后端：{'backend': 'registry'|'ini', 'path': 路径或空}。"""
        return {"backend": self._backend or "registry",
                "path": self._ini_path}

    def storage_text(self) -> str:
        """给「文档信息」/日志用的一行说明。"""
        info = self.storage()
        if info["backend"] == "ini":
            return f"INI 文件：{info['path']}"
        return f"注册表：HKCU\\Software\\{self._org}\\{self._app}"

    # ------------------------------------------------------------------ #
    # 通用
    # ------------------------------------------------------------------ #
    def sync(self) -> None:
        self._qs.sync()

    # ------------------------------------------------------------------ #
    # 外观 / 工具
    # ------------------------------------------------------------------ #
    def theme(self) -> str:
        v = self._qs.value("theme", _DEFAULTS["theme"], type=str)
        return v if v in ("light", "dark") else _DEFAULTS["theme"]

    def set_theme(self, theme: str) -> None:
        self._qs.setValue("theme", theme)

    def last_tool(self) -> str:
        return self._qs.value("last_tool", _DEFAULTS["last_tool"], type=str)

    def set_last_tool(self, tool_id: str) -> None:
        self._qs.setValue("last_tool", tool_id)

    def pen_color(self) -> str:
        return self._qs.value("pen_color", _DEFAULTS["pen_color"], type=str)

    def set_pen_color(self, color: str) -> None:
        self._qs.setValue("pen_color", color)

    def pen_width(self) -> float:
        return float(self._qs.value("pen_width", _DEFAULTS["pen_width"], type=float))

    def set_pen_width(self, w: float) -> None:
        self._qs.setValue("pen_width", float(w))

    def eraser_width(self) -> float:
        return float(self._qs.value("eraser_width", _DEFAULTS["eraser_width"], type=float))

    def set_eraser_width(self, w: float) -> None:
        self._qs.setValue("eraser_width", float(w))

    def highlighter_color(self) -> str:
        return self._qs.value("highlighter_color", _DEFAULTS["highlighter_color"], type=str)

    def set_highlighter_color(self, color: str) -> None:
        self._qs.setValue("highlighter_color", color)

    def highlighter_width(self) -> float:
        return float(self._qs.value("highlighter_width", _DEFAULTS["highlighter_width"],
                                     type=float))

    def set_highlighter_width(self, w: float) -> None:
        self._qs.setValue("highlighter_width", float(w))

    def last_dir(self) -> str:
        return self._qs.value("last_dir", _DEFAULTS["last_dir"], type=str)

    def set_last_dir(self, path: str) -> None:
        self._qs.setValue("last_dir", path)

    def note_bg_color(self) -> str:
        return self._qs.value("note_bg_color", _DEFAULTS["note_bg_color"], type=str)

    def set_note_bg_color(self, color: str) -> None:
        self._qs.setValue("note_bg_color", color)

    # ------------------------------------------------------------------ #
    # 界面布局（v5.2：响应式窗口）
    # ------------------------------------------------------------------ #
    def sidebar_width(self) -> int:
        """侧边栏宽度（浏览器式可拖拽）；限幅避免拖成一条缝或占满屏幕。"""
        try:
            v = int(self._qs.value("sidebar_width", 248, type=int))
        except (TypeError, ValueError):
            v = 248
        return max(180, min(460, v))

    def set_sidebar_width(self, width: int) -> None:
        self._qs.setValue("sidebar_width", max(180, min(460, int(width))))

    # ------------------------------------------------------------------ #
    # 最近使用的颜色（工具栏常用色快捷盘）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _color_key(tool: str) -> str:
        """钢笔与荧光笔各记一份（荧光笔是半透明色，混在一起会串味）。"""
        return "highlighter_recent_colors" if tool == "highlighter" else "pen_recent_colors"

    def recent_colors(self, tool: str = "pen") -> List[str]:
        raw = self._qs.value(self._color_key(tool), "", type=str)
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        out: List[str] = []
        for c in data:
            if isinstance(c, str) and c.startswith("#") and c not in out:
                out.append(c)
        return out[:MAX_RECENT_COLORS]

    def add_recent_color(self, tool: str, color: str) -> List[str]:
        if not color:
            return self.recent_colors(tool)
        items = [c for c in self.recent_colors(tool) if c.lower() != color.lower()]
        items.insert(0, color)
        items = items[:MAX_RECENT_COLORS]
        self._qs.setValue(self._color_key(tool), json.dumps(items, ensure_ascii=False))
        return items

    # ------------------------------------------------------------------ #
    # 触控 / 触控笔的系统「圆圈反馈」策略
    # ------------------------------------------------------------------ #
    def touch_feedback(self) -> str:
        """quiet（默认，只关掉书写时频繁闪的圆圈）/ off（全关）/ system（系统默认）。"""
        v = self._qs.value("touch_feedback", _DEFAULTS["touch_feedback"], type=str)
        return v if v in ("quiet", "off", "system") else _DEFAULTS["touch_feedback"]

    def set_touch_feedback(self, policy: str) -> None:
        self._qs.setValue("touch_feedback",
                          policy if policy in ("quiet", "off", "system") else "quiet")

    # ------------------------------------------------------------------ #
    # 最近文件
    # ------------------------------------------------------------------ #
    def recent_files(self) -> List[str]:
        v = self._qs.value("recent", [], type=list)
        return [str(p) for p in v]

    def add_recent(self, path: str) -> List[str]:
        files = self.recent_files()
        if path in files:
            files.remove(path)
        files.insert(0, path)
        files = files[:MAX_RECENT]
        self._qs.setValue("recent", files)
        return files

    def remove_recent(self, path: str) -> List[str]:
        files = self.recent_files()
        if path in files:
            files.remove(path)
            self._qs.setValue("recent", files)
        return files

    # ------------------------------------------------------------------ #
    # PDF 阅读状态（连续/单页模式 + 上次读到第几页）
    # ------------------------------------------------------------------ #
    def pdf_read_mode(self) -> str:
        """新文档的默认阅读模式：single（单页）或 continuous（连续滚动）。"""
        v = self._qs.value("pdf_read_mode", _DEFAULTS["pdf_read_mode"], type=str)
        return v if v in ("single", "continuous") else "single"

    def set_pdf_read_mode(self, mode: str) -> None:
        self._qs.setValue("pdf_read_mode",
                          "continuous" if mode == "continuous" else "single")

    def pdf_read_state(self, path: str) -> dict:
        """某份 PDF 的上次阅读状态 {page, mode, scroll, size, mtime}；无则空字典。"""
        state = self._json_map("pdf_read_state").get(_doc_key(path))
        return state if isinstance(state, dict) else {}

    def set_pdf_read_state(self, path: str, page: int, mode: str, scroll: float,
                           size: int, mtime: float, ts: Optional[float] = None,
                           pages: int = 0) -> bool:
        """写入阅读进度；返回是否真的写进去了。

        `ts` 是「这个位置是什么时候看到的」（wall clock）。已存的进度如果**更新**
        就不覆盖——多窗口 / 多实例开同一本书时，手里拿着旧页码的那个窗口在退出时
        会把别人刚读到的位置盖掉，用户看到的现象就是「每次打开都回到那一页」。

        `pages` 是文件的总页数：下次打开时用它判断「文档有没有被换掉」。
        只比文件大小会把「同步盘改了元数据 / 别的软件重写过一遍」误判成换了文档，
        于是每次都从第 1 页开始（用户看到的就是「记不住读到哪」）。
        """
        stamp = time.time() if ts is None else float(ts)
        data = self._json_map("pdf_read_state")
        key = _doc_key(path)
        old = data.get(key)
        if isinstance(old, dict):
            old_ts = float(old.get("ts", 0.0))
            # 系统时钟回拨 / 历史脏数据会让旧时间戳永远「更新」，
            # 结果是这份文档的进度再也写不进去 —— 这里把明显来自未来的时间戳作废。
            if old_ts > max(stamp, time.time()) + 5.0:
                log_warning(_log, "read_state_future_ts", path=path, ts=old_ts)
                old_ts = 0.0
            if old_ts > stamp:
                return False
        data.pop(key, None)          # 先删再插，保证 LRU 顺序
        entry = {
            "page": int(page),
            "mode": "continuous" if mode == "continuous" else "single",
            "scroll": round(float(scroll), 4),
            "size": int(size),
            "mtime": float(mtime),
            "ts": stamp,
        }
        if int(pages or 0) > 0:
            entry["pages"] = int(pages)
        data[key] = entry
        self._set_json_map("pdf_read_state", data, MAX_PDF_STATE)
        return True

    # ------------------------------------------------------------------ #
    # 笔记阅读位置（多页 .note 也要记得「上次翻到第几页」）
    # ------------------------------------------------------------------ #
    def note_read_state(self, path: str) -> int:
        """某个笔记文件上次看到的页序号（0 起）；没有记录则返回 0。"""
        data = self._json_map("note_read_state")
        try:
            return max(0, int(data.get(_doc_key(path), 0)))
        except (TypeError, ValueError):
            return 0

    def set_note_read_state(self, path: str, page: int) -> None:
        """记住某个笔记文件翻到了第几页（和 PDF 进度分开存，互不影响）。"""
        key = _doc_key(path)
        data = self._json_map("note_read_state")
        data.pop(key, None)          # 先删再插，保证 LRU 顺序
        data[key] = max(0, int(page))
        self._set_json_map("note_read_state", data, MAX_PDF_STATE)

    # ------------------------------------------------------------------ #
    # 会话：上次退出时打开的标签（[{kind, path}, ...]）
    # ------------------------------------------------------------------ #
    def open_tabs(self) -> list:
        """上次退出时打开的标签列表；没有或解析失败则返回空列表。"""
        raw = self._qs.value("open_tabs", "", type=str)
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        # 只保留结构正确的条目，避免脏数据把启动流程带崩
        out = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                kind = item.get("kind")
                out.append({"kind": kind if kind in ("note", "pdf") else "note",
                            "path": item["path"]})
        return out

    def set_open_tabs(self, items: list) -> None:
        clean = [{"kind": i.get("kind", "note"), "path": i.get("path", "")}
                 for i in (items or []) if isinstance(i, dict) and i.get("path")]
        self._qs.setValue("open_tabs", json.dumps(clean[:16], ensure_ascii=False))

    # ------------------------------------------------------------------ #
    # PDF MD5 缓存（避免每次打开都重算整份文件）
    # ------------------------------------------------------------------ #
    def cached_pdf_md5(self, path: str, size: int, mtime: float) -> str:
        """文件未被修改过时返回缓存的 MD5，否则返回空串。"""
        entry = self._json_map("pdf_md5_cache").get(self._md5_key(path, size, mtime))
        return entry if isinstance(entry, str) else ""

    def set_pdf_md5(self, path: str, size: int, mtime: float, md5: str) -> None:
        if not md5:
            return
        data = self._json_map("pdf_md5_cache")
        key = self._md5_key(path, size, mtime)
        data.pop(key, None)
        data[key] = md5
        self._set_json_map("pdf_md5_cache", data, MAX_MD5_CACHE)

    @staticmethod
    def _md5_key(path: str, size: int, mtime: float) -> str:
        return "%s|%d|%.3f" % (_doc_key(path), int(size), float(mtime))

    # ------------------------------------------------------------------ #
    def _json_map(self, key: str) -> dict:
        raw = self._qs.value(key, "", type=str)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _set_json_map(self, key: str, data: dict, limit: int) -> None:
        while len(data) > limit:
            data.pop(next(iter(data)), None)
        self._qs.setValue(key, json.dumps(data, ensure_ascii=False))

    # ------------------------------------------------------------------ #
    # 窗口几何
    # ------------------------------------------------------------------ #
    def window_geometry(self) -> Optional[QByteArray]:
        v = self._qs.value("geometry")
        return v if isinstance(v, QByteArray) and not v.isEmpty() else None

    def set_window_geometry(self, geo: QByteArray) -> None:
        self._qs.setValue("geometry", geo)

    def window_state(self) -> Optional[QByteArray]:
        v = self._qs.value("windowState")
        return v if isinstance(v, QByteArray) and not v.isEmpty() else None

    def set_window_state(self, state: QByteArray) -> None:
        self._qs.setValue("windowState", state)

r"""批注（.annot）存哪儿：优先 PDF 旁边，写不了就退到**确定能写**的目录。

为什么需要这个模块（线上问题）
------------------------------
PDF 常常放在本程序写不进去的位置——Program Files、别人的共享目录、被同步盘/杀毒
锁住的目录。此时 Ctrl+S 只能报 PermissionError，用户既保不住批注，也不知道
该做什么。最早的实现是「退到 %LOCALAPPDATA%」，但**兜底目录本身也可能写不进去**
（受 ACL 限制的账户、被沙箱/安全软件接管的进程、漫游配置异常……），于是出现线上
截图里的那一幕：

    WriteError: 创建临时文件失败：保存位置（文件夹）不允许写入
    文件：C:\\Users\\…\\AppData\\Local\\手写笔记本与PDF批注\\annotations\\<哈希>-<文件名>.annot

所以这里不假设「某个目录一定可写」，而是准备一条**候选链**，按顺序用真探针
（在目录里建一个临时文件再删掉）挑出第一个真的能写的：

1. 环境变量 HN_ANNOT_DIR（用户显式指定，最高优先）；
2. %LOCALAPPDATA%\手写笔记本与PDF批注\annotations（历史兜底位置，保持兼容）；
3. %APPDATA%\手写笔记本与PDF批注\annotations；
4. %USERPROFILE%\Documents\手写笔记本与PDF批注\annotations（目录已存在时才探测）；
5. 系统临时目录（一定可写；被系统清理也不影响，PDF 旁边/下次保存还会再写一份）。

命名规则不变：<PDF 绝对路径哈希>-<原文件名>.annot，因此**换目录也能找回**——
find_existing() 会按「PDF 旁边 → 候选链」的顺序找，resolve_for_save() 优先沿用
已经存在的那个位置。

规则（大部分是纯函数，便于测试）：
- 默认位置：与 PDF 同名的 <pdf>.annot（历史行为，不动）；
- 保存时若同目录不可写 → 用候选链里第一个可写位置；
- 保存失败时由上层再兜一次（见 src/ui/controllers/file_ops.py）；
- 打开文档时：先看同目录，再沿候选链找。

探针结果会缓存（目录 → 是否可写），所以连续保存不会反复建探针文件。
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from typing import List, Optional, Tuple

from src.core.hn_log import get_logger, log_debug
from src.core.serializer import directory_writable

_log = get_logger(__name__)

#: 兜底目录的目录名（与打包产物同名，便于用户找到）
APP_DIR_NAME = "手写笔记本与PDF批注"
#: 子目录名
STORE_SUBDIR = "annotations"
ANNOT_SUFFIX = ".annot"

#: 用户显式指定批注目录的环境变量（受限环境 / 想让批注跟工作区一起走的场景）
ENV_ANNOT_DIR = "HN_ANNOT_DIR"

#: 文件名里不允许出现的字符（Windows）
_BAD_NAME_CHARS = '<>:"/\\|?*'

#: 目录是否可写的探测缓存（目录绝对路径 → bool）。进程内有效：
#: 一次保存要问好几个目录，不能每次都建探针文件。
_WRITABLE_CACHE: dict = {}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def probe_writable(directory: str, use_cache: bool = True) -> bool:
    """这个目录现在真的能建文件吗（建一个探针文件再删掉）。

    directory_writable() 是底层实现；这里多一层缓存，避免每次保存都探测。
    """
    if not directory:
        return False
    key = _norm(directory)
    if use_cache and key in _WRITABLE_CACHE:
        return _WRITABLE_CACHE[key]
    ok = bool(directory_writable(directory))
    _WRITABLE_CACHE[key] = ok
    return ok


def actual_writable(directory: str, use_cache: bool = True) -> bool:
    """除了探针，还确认目录真的存在（不存在的目录不建，避免污染用户目录）。"""
    return os.path.isdir(directory) and probe_writable(directory, use_cache=use_cache)


def clear_probe_cache() -> None:
    """清掉可写探测缓存（测试 / 目录权限被修复后用）。"""
    _WRITABLE_CACHE.clear()


def primary_path(pdf_path: str) -> str:
    """默认位置：与 PDF 同名的 .annot。"""
    return os.path.splitext(os.path.abspath(pdf_path))[0] + ANNOT_SUFFIX


def _env_dir() -> str:
    """HN_ANNOT_DIR 指定的目录；没设置时返回空串。"""
    raw = (os.environ.get(ENV_ANNOT_DIR) or "").strip().strip('"')
    if not raw:
        return ""
    return os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))


def _appdata_dir(base_var: str) -> str:
    base = os.environ.get(base_var) or ""
    if not base:
        return ""
    return os.path.join(base, APP_DIR_NAME, STORE_SUBDIR)


def store_candidates() -> List[str]:
    """兜底目录候选链（保持顺序，去重、去空）。"""
    out: List[str] = []
    env_dir = _env_dir()
    if env_dir:
        out.append(env_dir)
    local = _appdata_dir("LOCALAPPDATA")
    if local:
        out.append(local)
    roaming = _appdata_dir("APPDATA")
    if roaming:
        out.append(roaming)
    # 「文档」目录：只在它已经存在时才当候选，不主动创建用户目录
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents") if home and home != "~" else ""
    if docs and os.path.isdir(docs):
        out.append(os.path.join(docs, APP_DIR_NAME, STORE_SUBDIR))
    try:
        tmp = tempfile.gettempdir()
    except Exception:
        tmp = ""
    if tmp:
        out.append(os.path.join(tmp, APP_DIR_NAME, STORE_SUBDIR))
    seen = set()
    uniq: List[str] = []
    for d in out:
        if not d:
            continue
        key = _norm(d)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(d)
    return uniq


def first_writable_dir(directory: str = "") -> str:
    """第一个真的能写的兜底目录；找不到时返回空串。

    directory 非空时把它放在候选链最前面（用于「换个位置再试一次」）。
    候选目录不存在时会尝试创建（仅限候选链里的目录，不会乱建用户目录）。
    """
    order = ([directory] if directory else []) + store_candidates()
    for d in order:
        if d and os.path.isdir(d) and probe_writable(d):
            return d
    for d in order:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            continue
        if probe_writable(d, use_cache=False):
            return d
    return ""


def store_dir(create: bool = True) -> str:
    """当前生效的兜底目录。

    **返回空串表示「一个能写的兜底目录都没有」**（调用方必须处理，不要拿它拼路径）。
    create=True 时会尝试创建候选目录。
    """
    found = first_writable_dir()
    if found:
        return found
    if not create:
        return ""
    for d in store_candidates():
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            continue
        if probe_writable(d, use_cache=False):
            return d
    return ""


def _safe_name(pdf_path: str) -> str:
    name = os.path.basename(pdf_path)
    cleaned = "".join(c for c in name if c not in _BAD_NAME_CHARS and ord(c) >= 32)
    return (cleaned or "document.pdf")[:64]


def _file_name(pdf_path: str) -> str:
    key = os.path.normcase(os.path.abspath(pdf_path)).encode("utf-8", "surrogatepass")
    digest = hashlib.sha1(key).hexdigest()[:12]
    return f"{digest}-{_safe_name(pdf_path)}{ANNOT_SUFFIX}"


def store_path(pdf_path: str, directory: str = "") -> str:
    """兜底位置的完整路径：<目录>/<哈希>-<文件名>.annot。

    directory 省略时用 store_dir()（当前生效的候选目录）；显式传入时按该目录
    计算（file_ops 的「换一个位置重试」用它逐个候选生成路径）。
    """
    base = directory or store_dir() or _env_dir()
    if not base:
        # 连一个候选目录都拿不到：退到临时目录（调用方写入失败时会给出明确错误）
        base = os.path.join(tempfile.gettempdir(), APP_DIR_NAME, STORE_SUBDIR)
    return os.path.join(base, _file_name(pdf_path))


def store_paths(pdf_path: str) -> List[str]:
    """候选链里每一个目录对应的兜底路径（按优先级）。"""
    return [store_path(pdf_path, d) for d in store_candidates()]


def find_existing(pdf_path: str) -> Optional[Tuple[str, bool]]:
    """找这篇 PDF 已经存在的批注：返回 (路径, 是否兜底位置)；都没有返回 None。

    顺序：PDF 旁边 → 候选链里第一个已经有该批注的目录（含历史上用过的
    %LOCALAPPDATA% 位置，所以升级后旧批注照样找得回来）。
    """
    primary = primary_path(pdf_path)
    if os.path.isfile(primary):
        return primary, False
    for cand in store_paths(pdf_path):
        if os.path.isfile(cand):
            return cand, True
    return None


def resolve_for_save(pdf_path: str, current: str = "") -> Tuple[str, bool]:
    """决定这次保存写到哪里：返回 (路径, 是否**这次**改用了备用位置)。

    - `current`：标签上已经记住的路径（上次就已经退到兜底位置）——**只要它还
      能写就继续沿用**，避免同一份批注在「旁边 / 兜底」之间来回跳；沿用时不报
      `fallback`（界面不必重复提示「已改存备用位置」）；
    - 同目录不可写 → 用候选链里第一个真的可写的位置（而不是想当然的某一个）；
    - 全都不可写 → 仍然返回首选兜底路径（写盘会失败并给出明确错误 + 建议）。
    """
    primary = primary_path(pdf_path)
    path = current or primary
    directory = os.path.dirname(os.path.abspath(path)) or "."
    if probe_writable(directory):
        return path, False
    for cand in store_paths(pdf_path):
        cand_dir = os.path.dirname(cand) or "."
        if probe_writable(cand_dir):
            return cand, True
    return store_path(pdf_path), True


def next_target(pdf_path: str, exclude: str = "") -> str:
    """「换一个位置再试」：按 PDF 旁边 → 候选链的顺序，给出下一个可写目标。

    找不到可写位置时返回空串（调用方据此给出「哪儿都写不进去」的提示）。
    """
    excluded = _norm(exclude) if exclude else ""
    order = [primary_path(pdf_path)] + store_paths(pdf_path)
    for cand in order:
        if excluded and _norm(cand) == excluded:
            continue
        d = os.path.dirname(os.path.abspath(cand)) or "."
        if os.path.isdir(d) and probe_writable(d):
            return cand
    for cand in order:
        if excluded and _norm(cand) == excluded:
            continue
        d = os.path.dirname(os.path.abspath(cand)) or "."
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            continue
        if probe_writable(d, use_cache=False):
            return cand
    return ""


def describe_candidates() -> str:
    """候选链的可读描述（写进日志/错误提示，便于现场排查）。"""
    parts = []
    for d in store_candidates():
        mark = "可写" if probe_writable(d) else "不可写"
        parts.append(f"{d}（{mark}）")
    return "；".join(parts) if parts else "（无候选目录）"


def env_dir_in_use() -> bool:
    """HN_ANNOT_DIR 是否正在生效（日志/状态栏用）。"""
    env_dir = _env_dir()
    if not env_dir:
        return False
    cur = store_dir()
    return bool(cur) and _norm(cur) == _norm(env_dir)


def log_choice(kind: str, pdf_path: str, path: str) -> None:
    """把「这次选了哪个位置」记进结构化日志（HN_LOG=debug 可见）。"""
    log_debug(_log, "annot_location", kind=kind, pdf=pdf_path, path=path,
              candidates=describe_candidates())


def platform_hint() -> str:
    """给用户看的「怎么指定可写目录」说明。"""
    if sys.platform == "win32":
        return ("也可以显式指定一个可写目录（例如文档目录下的自建文件夹）："
                "set HN_ANNOT_DIR=D:\\我的批注 ；"
                "或把 PDF 另存到「文档」等自己有写权限的目录。")
    return ("也可以显式指定一个可写目录：export HN_ANNOT_DIR=~/annots ；"
            "或把 PDF 另存到有写权限的目录。")

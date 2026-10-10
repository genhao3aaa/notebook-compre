"""`.note` / `.annot` 的二进制序列化（A4，格式 v2）。

为什么需要它
------------
v1 是带缩进的 JSON：1 页 × 1000 笔画 = 11.9 MB、保存要 0.45 s（50 页要 5.1 s，
还全在 UI 线程里）。JSON 的问题是「每个点一个字典/数组 + 十进制文本」，
点坐标与压感被写成十几字节的文本。

A3 已经把点数砍掉约 91%，但格式本身的浪费还在。v2 改成**列式二进制**：

```
magic(8) | u32 meta_len | meta(JSON, UTF-8) | 每页每笔画的二进制记录 | u32 crc32
```

笔画记录（小端）：

```
u8  tool_idx     工具表下标（meta["tools"]）
u8  color_idx    颜色表下标（meta["colors"]）
f32 width        基础笔宽（points）
f64 timestamp
u32 n_points
f32 × n_points    x 列
f32 × n_points    y 列
u8  × n_points    压感列（0~1 线性映射到 0~255）
```

设计要点
--------
- **列式**：同列数据连续存放，体积 ≈ 9 字节/点（x4 + y4 + 压感1），且能被
  `array.frombytes` 一次性解出来，不用逐点解析文本；
- **颜色/工具表**：颜色是少数几个值的重复，进 meta 表，每笔只存 1 字节；
- **magic + 版本 + CRC32**：不是本程序的二进制、被截断或改坏时**立刻报错**，
  而不是解析出一堆错位数据把用户笔记画花；
- **原子写**：先写同目录临时文件、flush+fsync 后 `os.replace()`，
  断电/崩溃不会留下半个文件（ROADMAP §8 的要求）；覆盖失败的处理见 `write_atomic`
  （旧文件只读 / 被占用 / 目录不可写各有对应动作与提示）；
- **向后兼容**：v1 的 JSON 仍然能读（`load` 按首字节自动识别）；
  需要写回 v1 时用 `save(path, version=1)`。

格式细节变更时：`FORMAT_VERSION` 加一，并在 `decode_document` 里保留旧分支。
"""

from __future__ import annotations

import array
import errno
import json
import os
import secrets
import stat
import struct
import time
import zlib
from typing import Dict, List, Optional, Sequence, Tuple

from src.core.hn_log import get_logger, log_event, log_warning
from src.core.stroke import Stroke

_log = get_logger(__name__)

#: 容器版本（v1 = JSON，没有 magic）
FORMAT_VERSION = 2

MAGIC_NOTE = b"HNNOTE2\x00"
MAGIC_ANNOT = b"HNANNT2\x00"
_MAGIC_BY_KIND = {"note": MAGIC_NOTE, "annot": MAGIC_ANNOT}
_KIND_BY_MAGIC = {MAGIC_NOTE: "note", MAGIC_ANNOT: "annot"}

_META_LEN = struct.Struct("<I")
_STROKE_HEAD = struct.Struct("<BBfdI")
_CRC = struct.Struct("<I")

_TOOL_CODES = ["pen", "eraser", "highlighter"]


class DocumentFormatError(Exception):
    """文件不是本程序的 v2 容器、被截断或校验和不符。"""


# ---------------------------------------------------------------------- #
# 编码
# ---------------------------------------------------------------------- #
def encode_document(kind: str, doc_meta: dict,
                    pages: Sequence[Sequence[Stroke]]) -> bytes:
    """把文档编码成 v2 容器字节串。`kind` ∈ {"note","annot"}。"""
    if kind not in _MAGIC_BY_KIND:
        raise ValueError(f"未知文档类型：{kind}")

    tools: List[str] = []
    colors: List[str] = []

    def tool_idx(tool: str) -> int:
        if tool not in tools:
            tools.append(tool)
        return tools.index(tool)

    def color_idx(color: str) -> int:
        if color not in colors:
            colors.append(color)
        return colors.index(color)

    meta = dict(doc_meta)
    meta["format"] = FORMAT_VERSION
    meta["kind"] = kind

    body = bytearray()
    page_meta = []
    for page in pages:
        page_meta.append(len(page))
        for s in page:
            n = len(s.points)
            body += _STROKE_HEAD.pack(tool_idx(s.tool), color_idx(s.color),
                                      float(s.width), float(s.timestamp), n)
            if n:
                xs = array.array("f", (p[0] for p in s.points))
                ys = array.array("f", (p[1] for p in s.points))
                ps = array.array("B",
                                 (min(255, max(0, int(round(p[2] * 255.0)))) for p in s.points))
                body += xs.tobytes()
                body += ys.tobytes()
                body += ps.tobytes()

    meta["pages"] = page_meta
    meta["tools"] = tools
    meta["colors"] = colors
    meta_bytes = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    out = bytearray()
    out += _MAGIC_BY_KIND[kind]
    out += _META_LEN.pack(len(meta_bytes))
    out += meta_bytes
    out += body
    out += _CRC.pack(zlib.crc32(bytes(out)) & 0xFFFFFFFF)
    return bytes(out)


# ---------------------------------------------------------------------- #
# 解码
# ---------------------------------------------------------------------- #
def looks_like_v2(data: bytes) -> bool:
    return data[:8] in _KIND_BY_MAGIC


def decode_document(data: bytes) -> Tuple[str, dict, List[List[Stroke]]]:
    """解码 v2 容器 → (kind, doc_meta, pages)。校验和不符时抛 DocumentFormatError。"""
    if len(data) < 8 + 4 + 4:
        raise DocumentFormatError("文件太短，不是有效的 v2 文档")
    kind = _KIND_BY_MAGIC.get(data[:8])
    if kind is None:
        raise DocumentFormatError("magic 不匹配：不是本程序的 v2 文档")

    expected_crc = _CRC.unpack_from(data, len(data) - 4)[0]
    actual_crc = zlib.crc32(data[:-4]) & 0xFFFFFFFF
    if expected_crc != actual_crc:
        raise DocumentFormatError("校验和不符：文件已损坏或被截断")

    meta_len = _META_LEN.unpack_from(data, 8)[0]
    meta_start = 12
    meta_end = meta_start + meta_len
    if meta_end > len(data) - 4:
        raise DocumentFormatError("元数据长度越界：文件已损坏")
    try:
        meta = json.loads(data[meta_start:meta_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DocumentFormatError(f"元数据不是合法 JSON：{e}") from e

    version = int(meta.get("format", 2))
    if version != FORMAT_VERSION:
        raise DocumentFormatError(f"不支持的格式版本：{version}")

    tools = meta.get("tools", _TOOL_CODES)
    colors = meta.get("colors", ["#000000"])
    pages_meta = meta.get("pages", [])

    offset = meta_end
    pages: List[List[Stroke]] = []
    for count in pages_meta:
        strokes: List[Stroke] = []
        for _ in range(int(count)):
            if offset + _STROKE_HEAD.size > len(data) - 4:
                raise DocumentFormatError("笔画头越界：文件已损坏")
            t_i, c_i, width, ts, n = _STROKE_HEAD.unpack_from(data, offset)
            offset += _STROKE_HEAD.size
            need = n * 9
            if offset + need > len(data) - 4:
                raise DocumentFormatError("点数据越界：文件已损坏")
            xs = array.array("f")
            xs.frombytes(data[offset:offset + n * 4])
            offset += n * 4
            ys = array.array("f")
            ys.frombytes(data[offset:offset + n * 4])
            offset += n * 4
            ps = array.array("B")
            ps.frombytes(data[offset:offset + n])
            offset += n
            strokes.append(Stroke(
                tool=tools[t_i] if t_i < len(tools) else "pen",
                color=colors[c_i] if c_i < len(colors) else "#000000",
                width=float(width),
                points=[(x, y, p / 255.0) for x, y, p in zip(xs, ys, ps)],
                timestamp=float(ts),
            ))
        pages.append(strokes)

    doc_meta = {k: v for k, v in meta.items()
                if k not in ("format", "kind", "pages", "tools", "colors")}
    return kind, doc_meta, pages


# ---------------------------------------------------------------------- #
# 读写（原子写）
# ---------------------------------------------------------------------- #
#: 常见的「暂时性」失败：文件被别的进程短暂持有（杀毒扫描 / 索引 / 网盘同步）
#: WinError 5 = 拒绝访问（目标被独占打开时也报这个）、32 = 共享冲突、33 = 锁定冲突
_TRANSIENT_WINERROR = (5, 32, 33)

#: 临时性失败的重试节奏（秒）。总等待 ~0.2 s：够杀毒/同步盘松手，又不让界面明显卡顿。
_RETRY_DELAYS = (0.0, 0.05, 0.15)

_FILE_ATTRIBUTE_READONLY = getattr(stat, "FILE_ATTRIBUTE_READONLY", 0x1)


class WriteError(OSError):
    """写盘失败：把 OS 级错误翻译成「哪一步 / 为什么 / 怎么办」。

    为什么单独一个类型：线上「保存失败」时用户看到的是
    `PermissionError: [WinError 5] 拒绝访问。: '…\\.x.annot.tmp-1234' -> '…\\x.annot'`
    ——路径指的是**临时文件**，而且没有任何可执行信息。这里保证：
      * 消息里出现的一定是**真正的目标路径**；
      * 附带一条能照着做的建议（关闭占用程序 / 取消只读 / 另存到可写目录）。
    """

    def __init__(self, path: str, stage: str, reason: str, hint: str = "",
                 cause: Optional[BaseException] = None):
        self.path = path
        self.stage = stage
        self.reason = reason
        self.hint = hint
        self.cause = cause
        text = f"{stage}失败：{reason}\n文件：{path}"
        if hint:
            text += f"\n建议：{hint}"
        super().__init__(text)


def _is_transient(exc: BaseException) -> bool:
    """这次失败值不值得重试。

    除 WinError 5/32/33 外，还要认「CRT 直接给的 EACCES」：`open()` 走的是
    `_wopen`，失败时只填 errno=13、**winerror 是 None**（线上日志里
    `PermissionError: [Errno 13] Permission denied: '…\\.x.annot.tmp-23436'`
    就是这一种）。它同样多半来自杀毒扫描 / 同步盘 / 被占用的目标文件，重试一次往往就过了。
    """
    if not isinstance(exc, PermissionError):
        return False
    winerr = getattr(exc, "winerror", None)
    if winerr is None:
        return True
    return winerr in _TRANSIENT_WINERROR


def _stat_or_none(path: str):
    try:
        return os.stat(path)
    except OSError:
        return None


def _has_readonly_attribute(path: str) -> bool:
    """Windows 的「只读」属性（POSIX 下恒为 False，重命名不受它限制）。"""
    st = _stat_or_none(path)
    return bool(st is not None
                and getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_READONLY)


def _clear_readonly_attribute(path: str) -> bool:
    """清掉 Windows 的「只读」属性；没清掉（或本来就没有）返回 False。

    为什么要自动清：从只读介质（光盘 / 压缩包 / 别人拷来的 U 盘）复制出来的
    `.annot` / `.note` 会带上只读位，之后每次 Ctrl+S 都会被 `os.replace` 拒绝。
    这是**用户自己的数据文件**，用户按下保存就表示想覆盖它，所以先自愈再重试。
    """
    st = _stat_or_none(path)
    if st is None or not _has_readonly_attribute(path):
        return False
    try:
        os.chmod(path, stat.S_IWRITE | (st.st_mode & 0o666))
    except OSError:
        return False
    return True


def directory_writable(directory: str) -> bool:
    """真的往目录里建一个探针文件试试（`os.access` 在 Windows 上对目录不可靠）。"""
    try:
        fd, probe = _open_temp_file(os.path.join(directory, ".hn-write-probe"))
    except OSError:
        return False
    os.close(fd)
    _remove_quietly(probe)
    return True


def _target_locked(path: str) -> bool:
    """目标是否被别的程序「握着不放」——`os.replace` 需要目标文件的 DELETE 权限。

    为什么不能只看错误码：Windows 上「只读属性」和「被独占打开」都是
    WinError 5（拒绝访问），只按错误码只能给出含糊的提示。这里补一次**只读探测**：
    以 DELETE 权限试开（不读、不写、不删任何内容），共享模式里**不含**「共享删除」，
    于是别的程序若正以「不共享删除」的方式打开着它，这次试开就会失败。
    Windows 之外（rename 只看目录权限）恒为 False。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        delete, share_read_write, open_existing = 0x00010000, 0x1 | 0x2, 3
        handle = create(str(path), delete, share_read_write, None, open_existing, 0, None)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return True
        kernel32.CloseHandle(handle)
        return False
    except Exception:                        # pragma: no cover - 探测失败不该误导用户
        return False


def _open_temp_file(path: str) -> Tuple[int, str]:
    """在目标同目录建一个**唯一**临时文件，返回 `(fd, 路径)`。

    唯一性很关键：历史上临时名只有 pid，而 Windows 的 pid 会复用——
    上一次崩溃留下的 `.<名>.tmp-<pid>` 一旦存在（甚至是别人留下的只读文件），
    下一次同 pid 保存就会直接 `[Errno 13]` 失败。这里用 `O_EXCL` + 随机后缀，
    既不会撞上残留，也**不会误删别人的文件**（清理只清自己那一个）。
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    base = os.path.basename(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(100):
        cand = os.path.join(directory, f".{base}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
        try:
            # 0o666 而不是 mkstemp 的 0o600：受限环境里过窄的权限会生成
            # 一份连创建者都进不去的 ACL（同 env_compat.py 记录的问题）。
            return os.open(cand, flags, 0o666), cand
        except FileExistsError:
            continue
    raise FileExistsError(f"在 {directory} 里尝试 100 次都没能创建唯一临时文件名")


def _explain(exc: BaseException, path: str, stage: str,
             readonly_cleared: bool = False) -> WriteError:
    """把 OSError 翻译成用户看得懂、能照着做的 `WriteError`。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    winerr = getattr(exc, "winerror", None)
    code = getattr(exc, "errno", None)

    if isinstance(exc, IsADirectoryError) or os.path.isdir(path):
        reason = "目标路径是一个文件夹，同名文件写不进去"
        hint = "把该文件夹改名或移走，再用「另存为」选一个文件路径"
    elif code == errno.ENOSPC:
        reason = "磁盘空间不足"
        hint = "清理磁盘空间后重试"
    elif isinstance(exc, FileNotFoundError) or code == errno.ENOENT:
        if os.path.isdir(directory):
            reason = "临时文件没能建立（路径被其他程序占用或不可用）"
            hint = "关闭同步盘/杀毒软件对该文件夹的占用后重试，或用「另存为」换一个目录"
        else:
            reason = "保存位置不存在（文件夹被删除或移动了）"
            hint = "用「另存为」选一个存在的文件夹"
    elif isinstance(exc, PermissionError):
        if winerr in (32, 33):
            reason = f"文件正被其他程序占用（WinError {winerr}）"
            hint = ("关闭正在阅读/同步该文件的程序（PDF 阅读器、网盘同步、杀毒扫描）后重试，"
                    "或用「另存为」存到别处")
        elif not directory_writable(directory):
            reason = "保存位置（文件夹）不允许写入"
            hint = ("换到「文档」等可写目录另存；确需写这个目录时用管理员修复权限："
                    f'icacls "{directory}" /grant "%USERNAME%":(OI)(CI)M')
        elif _has_readonly_attribute(path):
            reason = "拒绝访问：目标文件带「只读」属性"
            hint = f'先取消只读：attrib -r "{path}"，再保存'
        elif _target_locked(path):
            reason = "拒绝访问：文件正被其他程序占用，无法替换"
            hint = ("关闭可能打开它的程序（PDF 阅读器、网盘同步、杀毒扫描）后重试，"
                    "或用「另存为」存到别处")
        elif readonly_cleared:
            reason = "拒绝访问：已清除只读属性，但仍被其他程序占用"
            hint = "关闭占用该文件的程序后重试，或用「另存为」存到别处"
        else:
            reason = "拒绝访问：当前账户对这个文件没有写权限"
            hint = ("用「另存为」存到「文档」等自己有权限的目录；"
                    f'或修复文件权限：icacls "{path}" /grant "%USERNAME%":M')
    else:
        reason = f"{type(exc).__name__}: {exc}"
        hint = ""

    return WriteError(path, stage, reason, hint, cause=exc)


def _write_temp(fd: int, data: bytes, path: str) -> None:
    """把数据写进临时文件并落盘（fd 的所有权在这里交给文件对象）。"""
    try:
        f = os.fdopen(fd, "wb")
    except OSError:                          # pragma: no cover - 极罕见：fd 不可用
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    try:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    except OSError as e:
        raise _explain(e, path, "写入临时文件") from e
    finally:
        try:
            f.close()
        except OSError:                      # 关闭时的落盘错误不掩盖真正的失败原因
            pass


def _replace_into_place(tmp: str, path: str) -> None:
    """`os.replace(tmp, path)`，带只读自愈与瞬时占用重试。"""
    readonly_cleared = False
    last: Optional[OSError] = None
    for attempt, delay in enumerate(_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
            log_event(_log, "save_retry", path=path, attempt=attempt)
        try:
            os.replace(tmp, path)
            return
        except OSError as e:
            last = e
            if isinstance(e, PermissionError) and not readonly_cleared:
                readonly_cleared = _clear_readonly_attribute(path)
                if readonly_cleared:
                    log_event(_log, "save_readonly_cleared", path=path)
                    continue             # 清掉只读位后立刻重试
            if not _is_transient(e):
                break
    assert last is not None
    raise _explain(last, path, "替换目标文件", readonly_cleared=readonly_cleared) from last


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def write_atomic(path: str, data: bytes) -> None:
    """先写同目录临时文件、fsync 后 `os.replace()`，避免崩溃留下半个文件。

    「旧文件挡住新写入」的处理（线上 PermissionError 的三个真实来源都覆盖）：
    1. **临时文件名唯一**（pid + 随机后缀 + `O_EXCL`），不会撞上崩溃残留，
       也不会去删别人的文件；
    2. **只读属性自愈**：目标带只读位（拷来的旧 `.annot`）时清掉后重试；
    3. **瞬时占用重试**：WinError 5/32/33 按 0.05 / 0.15 s 重试，杀毒/同步盘的
       毫秒级占用可以自愈；
    4. 仍然失败时抛 `WriteError`，消息给的是**真正的目标路径**与可执行建议；
       原文件保持不动（要么被完整替换，要么一点没变）。
    """
    fd = tmp = None
    last: Optional[OSError] = None
    for attempt, delay in enumerate(_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
            log_event(_log, "save_create_retry", path=path, attempt=attempt)
        try:
            fd, tmp = _open_temp_file(path)
            break
        except OSError as e:
            last = e
            if not _is_transient(e):
                break
    if fd is None or tmp is None:
        err = _explain(last, path, "创建临时文件")   # type: ignore[arg-type]
        log_warning(_log, "write_failed", path=path, stage=err.stage, reason=err.reason)
        raise err from last

    try:
        _write_temp(fd, data, path)
        _replace_into_place(tmp, path)
    except WriteError as e:
        log_warning(_log, "write_failed", path=path, stage=e.stage, reason=e.reason)
        raise
    finally:
        _remove_quietly(tmp)                 # 只删自己刚建的那个临时文件


def read_all(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def detect_kind(data: bytes) -> str:
    """按首字节判断文件是 v2 容器（note/annot）还是 v1 JSON。"""
    if looks_like_v2(data):
        return _KIND_BY_MAGIC[data[:8]]
    stripped = data.lstrip()[:1]
    if stripped == b"{":
        return "json"
    raise DocumentFormatError("无法识别的文件格式")


# ---------------------------------------------------------------------- #
# 快照（给后台线程用）
# ---------------------------------------------------------------------- #
def snapshot_note(doc) -> Tuple[dict, List[List[Stroke]], int]:
    """给笔记文档拍一个「可以安全地在别的线程里写」的快照。

    只做**浅拷贝**：把每页的笔画列表复制一份（列表本身复制，Stroke 对象共享）。
    这是安全的——书写中的笔画还在工具手里，抬笔后才进列表；已入列表的笔画
    不会被原地修改（A3 的简化发生在入列表之前）。
    """
    pages = [list(p.strokes) for p in doc.pages]
    meta = {
        "version": doc.version,
        "page_w": doc.page_w,
        "page_h": doc.page_h,
        "background": doc.background,
        "background_color": doc.background_color,
    }
    points = sum(len(s.points) for page in pages for s in page)
    return meta, pages, points


def snapshot_annot(doc) -> Tuple[dict, List[List[Stroke]], int]:
    """给 PDF 批注文档拍快照（同上）。"""
    pages = [list(p.strokes) for p in doc.pages]
    meta = {
        "version": doc.version,
        "pdf_path": doc.pdf_path,
        "pdf_md5": doc.pdf_md5,
        "created": doc.created,
        "modified": doc.modified,
    }
    points = sum(len(s.points) for page in pages for s in page)
    return meta, pages, points


def write_snapshot(path: str, kind: str, meta: dict,
                   pages: Sequence[Sequence[Stroke]]) -> None:
    """把快照编码 + 原子写盘。**只依赖入参**，因此可以安全地在后台线程里调用。"""
    write_atomic(path, encode_document(kind, meta, pages))


def dumps_json_v1(doc_dict: dict) -> bytes:
    """v1（JSON + 缩进）字节串，供 `save(version=1)` 与互操作测试使用。"""
    return json.dumps(doc_dict, ensure_ascii=False, indent=2).encode("utf-8")


def loads_json_v1(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))

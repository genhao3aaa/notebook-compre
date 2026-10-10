"""文档格式的统一入口（A7）：版本常量、读写分发、v1→v2 升级。

为什么要单独一个模块
--------------------
改格式时最容易出的问题不是「编码写错」，而是**版本/兼容分支散落在两个文档类里**：
`NoteDocument` 和 `AnnotDocument` 各写一套「先看 magic、再看 JSON」的分支，
迟早出现「一边支持 v1、另一边忘了」的偏差。这里把「怎么识别、读成什么、怎么写」
收敛到一处，两个文档类只负责把自己的字段映射进来。

三层版本号（读代码时别混）
--------------------------
- **容器版本** `CONTAINER_VERSION`：文件长什么样（1 = JSON 文本，2 = 二进制容器）；
- **笔记字段版本** `NOTE_SCHEMA_VERSION`：`.note` 里字段的结构（1.1 起有 background_color）；
- **批注字段版本** `ANNOT_SCHEMA_VERSION`：`.annot` 里字段的结构。

读旧文件永远向后兼容；写文件默认写最新容器。需要交给旧版本程序时用 `container=1`。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from src.core import serializer
from src.core.stroke import Stroke

#: 文件容器版本（v1 = JSON 文本；v2 = serializer 的二进制容器）
CONTAINER_VERSION = serializer.FORMAT_VERSION

#: `.note` 字段结构版本（1.0 = 初版；1.1 = 新增 background_color）
NOTE_SCHEMA_VERSION = "1.1"
#: `.annot` 字段结构版本
ANNOT_SCHEMA_VERSION = "1.0"

#: 与容器版本对应的可读名字（诊断输出用）
CONTAINER_NAMES = {1: "json", 2: "binary"}


class FormatError(serializer.DocumentFormatError):
    """格式/版本相关的错误（统一成一个类型，方便上层 catch）。"""


def detect_container(data: bytes) -> int:
    """只看字节头判断容器版本：1 = JSON，2 = 二进制容器。"""
    if serializer.looks_like_v2(data):
        return 2
    stripped = data.lstrip()[:1]
    if stripped == b"{":
        return 1
    raise FormatError("无法识别的文件格式（既不是 v1 JSON，也不是 v2 容器）")


def kind_of_v1(meta: Dict) -> str:
    """按字段特征判断 v1 JSON 是笔记还是批注：带 `pdf_md5` / `pdf_path` 的是批注。

    v1 的 JSON 里没有类型标记，只能看字段。这个判断必须**只有一个出处**：
    历史上 `upgrade_file()` 用了它、`read_document()` 却把所有 v1 都当成笔记，
    后果是旧版程序写的 `.annot` 会被判成「这是笔记，不是批注」而**拒绝加载**，
    打开 PDF 时批注全空、下一次 Ctrl+S 又把旧批注覆盖掉（静默丢数据）。
    """
    return "annot" if ("pdf_md5" in meta or "pdf_path" in meta) else "note"


def read_document(path: str) -> Tuple[str, Dict, List[List[Stroke]]]:
    """读任意版本的文档 → `(kind, meta, pages)`。

    - `kind`：``"note"`` / ``"annot"``（v1 JSON 无类型标记，按字段特征识别，
      见 `kind_of_v1`）；
    - `meta`：文档级字段（page_w/background/pdf_md5/version… 视类型而定）；
    - `pages`：`[[Stroke, ...], ...]`，两种容器统一成同一形状。
    """
    data = serializer.read_all(path)
    container = detect_container(data)

    if container == 2:
        kind, meta, pages = serializer.decode_document(data)
        return kind, meta, pages

    # v1：JSON 文本
    try:
        parsed = serializer.loads_json_v1(data)
    except Exception as e:
        raise FormatError(f"不是合法的 JSON 文档：{e}") from e
    meta = {k: v for k, v in parsed.items() if k != "pages"}
    pages = [[Stroke.from_dict(s) for s in page.get("strokes", [])]
             for page in parsed.get("pages", [])]
    return kind_of_v1(meta), meta, pages


def write_document(path: str, kind: str, meta: Dict,
                   pages: Sequence[Sequence[Stroke]], *,
                   container: int = CONTAINER_VERSION,
                   json_dict: Optional[Dict] = None) -> None:
    """按指定容器版本原子写盘。

    `container=1` 时必须提供 `json_dict`（v1 的 JSON 结构，含 pages 的字典形式）。
    """
    if int(container) >= 2:
        serializer.write_atomic(path, serializer.encode_document(kind, meta, pages))
        return
    if json_dict is None:
        raise FormatError("写 v1（JSON）需要提供 json_dict")
    serializer.write_atomic(path, serializer.dumps_json_v1(json_dict))


def format_info(data: bytes) -> Dict:
    """给诊断/测试用的格式描述（不抛异常，识别不了就返回 unknown）。"""
    try:
        container = detect_container(data)
    except FormatError:
        return {"container": None, "name": "unknown", "size": len(data)}
    info = {
        "container": container,
        "name": CONTAINER_NAMES.get(container, "?"),
        "size": len(data),
    }
    if container == 2:
        try:
            kind, meta, pages = serializer.decode_document(data)
        except serializer.DocumentFormatError:
            info["valid"] = False
            return info
        info["valid"] = True
        info["kind"] = kind
        info["format"] = meta.get("format")
        info["pages"] = len(pages)
    else:
        info["valid"] = True
        try:
            parsed = serializer.loads_json_v1(data)
            info["kind"] = kind_of_v1(parsed)
            info["format"] = 1
            info["pages"] = len(parsed.get("pages", []))
            info["doc_version"] = str(parsed.get("version", ""))
        except Exception:
            info["valid"] = False
    return info


def upgrade_file(path: str) -> Dict:
    """把 v1 JSON 文件**就地**升级成 v2 二进制容器。

    返回 `{"upgraded": bool, "kind": str, "pages": int, "bytes_before": int, "bytes_after": int}`；
    已经是 v2 时 `upgraded=False`（幂等，可安全重复调用）。
    升级是原子写，且写完后会读回校验——旧文件不会被破坏在半路。
    """
    data = serializer.read_all(path)
    container = detect_container(data)
    if container >= 2:
        kind, _meta, pages = serializer.decode_document(data)
        return {"upgraded": False, "kind": kind, "pages": len(pages),
                "bytes_before": len(data), "bytes_after": len(data)}

    kind, meta, pages = read_document(path)          # v1 路径（类型已按字段特征判好）
    write_document(path, kind, meta, pages)
    check_kind, _check_meta, check_pages = read_document(path)
    if check_kind != kind or len(check_pages) != len(pages):
        raise FormatError("升级后校验失败：读回的内容与升级前不一致")
    after = serializer.read_all(path)
    return {"upgraded": True, "kind": kind, "pages": len(pages),
            "bytes_before": len(data), "bytes_after": len(after)}

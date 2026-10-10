"""A7 验收：`src/core/format.py` 统一格式入口（版本常量、读写分发、v1→v2 升级）。"""

from __future__ import annotations

import json
import os

import pytest

from src.core import format as fmt
from src.core import serializer
from src.core.annot_document import AnnotDocument
from src.core.note_document import NoteDocument
from src.core.stroke import Stroke


def _note(n_pages=2, strokes=3):
    doc = NoteDocument(background="grid", background_color="#fdf6e3")
    doc.pages = []
    for _ in range(n_pages):
        page = doc.add_page()
        for i in range(strokes):
            page.strokes.append(Stroke(tool="pen", color="#000000", width=3.0,
                                       points=[(1.0 + i, 2.0 + i, 0.5)]))
    return doc


# --------------------------------------------------------------------- #
# 版本常量
# --------------------------------------------------------------------- #
def test_version_constants_have_single_source():
    assert fmt.CONTAINER_VERSION == serializer.FORMAT_VERSION == 2
    assert fmt.NOTE_SCHEMA_VERSION == "1.1"
    assert fmt.ANNOT_SCHEMA_VERSION == "1.0"
    assert NoteDocument().version == fmt.NOTE_SCHEMA_VERSION
    assert AnnotDocument("p", "m", 1).version == fmt.ANNOT_SCHEMA_VERSION


# --------------------------------------------------------------------- #
# 读：v2 / v1 统一成同一形状
# --------------------------------------------------------------------- #
def test_read_document_v2_and_v1_agree(tmp_path):
    doc = _note()
    p2, p1 = str(tmp_path / "a.note"), str(tmp_path / "b.note")
    doc.save(p2)
    doc.save(p1, version=1)

    kind2, meta2, pages2 = fmt.read_document(p2)
    kind1, meta1, pages1 = fmt.read_document(p1)
    assert kind2 == kind1 == "note"
    assert meta2["background"] == meta1["background"] == "grid"
    assert len(pages2) == len(pages1) == 2
    assert [len(p) for p in pages2] == [len(p) for p in pages1] == [3, 3]
    assert pages2[0][0].points[0][0] == pytest.approx(pages1[0][0].points[0][0])


def test_v1_annot_is_recognized_as_annot(tmp_path):
    """旧版程序写的 v1 JSON 批注必须仍被识别成批注。

    回归背景：`read_document()` 曾把所有 v1 JSON 都当成笔记，于是
    `AnnotDocument.load()` 抛「这是一个笔记（.note）文件」→ 打开 PDF 时旧批注
    全部看不到，下一次 Ctrl+S 又把旧文件覆盖成空批注（静默丢数据）。
    """
    path = str(tmp_path / "old.annot")
    AnnotDocument(pdf_path="D:/x.pdf", pdf_md5="deadbeef", page_count=1).save(path, version=1)
    assert open(path, encoding="utf-8").read().lstrip().startswith("{"), "version=1 应写 JSON"

    kind, meta, pages = fmt.read_document(path)
    assert kind == "annot" and meta["pdf_md5"] == "deadbeef"
    assert AnnotDocument.load(path).pdf_md5 == "deadbeef"
    with pytest.raises(fmt.FormatError):
        NoteDocument.load(path)                  # 反向不能把批注当笔记读

    with open(path, "rb") as f:
        assert fmt.format_info(f.read())["kind"] == "annot"


def test_v1_note_is_still_a_note(tmp_path):
    """字段特征判断不能误伤笔记（没有 pdf_* 字段→笔记）。"""
    path = str(tmp_path / "old.note")
    _note(n_pages=1, strokes=1).save(path, version=1)
    assert fmt.read_document(path)[0] == "note"
    assert fmt.kind_of_v1({"page_w": 595.28, "background": "grid"}) == "note"
    assert fmt.kind_of_v1({"pdf_path": "x", "pdf_md5": "y"}) == "annot"


def test_detect_container(tmp_path):
    p2 = str(tmp_path / "a.note")
    _note().save(p2)
    with open(p2, "rb") as f:
        assert fmt.detect_container(f.read()) == 2

    p1 = str(tmp_path / "b.note")
    _note().save(p1, version=1)
    with open(p1, "rb") as f:
        assert fmt.detect_container(f.read()) == 1

    with pytest.raises(fmt.FormatError):
        fmt.detect_container(b"\x00\x01nonsense")


# --------------------------------------------------------------------- #
# 写：两种容器
# --------------------------------------------------------------------- #
def test_write_document_both_containers(tmp_path):
    doc = _note(n_pages=1, strokes=2)
    p2 = str(tmp_path / "w2.note")
    fmt.write_document(p2, "note",
                       {"version": "1.1", "page_w": 595.28, "page_h": 841.89,
                        "background": "blank", "background_color": "#ffffff"},
                       [p.strokes for p in doc.pages])
    assert open(p2, "rb").read()[:8] == serializer.MAGIC_NOTE

    p1 = str(tmp_path / "w1.note")
    fmt.write_document(p1, "note",
                       {"version": "1.1", "page_w": 595.28, "page_h": 841.89,
                        "background": "blank", "background_color": "#ffffff"},
                       [p.strokes for p in doc.pages],
                       container=1, json_dict=doc.to_dict())
    assert open(p1, "rb").read().lstrip()[:1] == b"{"
    assert json.loads(open(p1, encoding="utf-8").read())["version"] == "1.1"

    for path in (p2, p1):
        kind, _meta, pages = fmt.read_document(path)
        assert kind == "note" and len(pages[0]) == 2


def test_write_v1_requires_json_dict(tmp_path):
    with pytest.raises(fmt.FormatError):
        fmt.write_document(str(tmp_path / "x.note"), "note", {}, [[]], container=1)


# --------------------------------------------------------------------- #
# 升级：v1 → v2
# --------------------------------------------------------------------- #
def test_upgrade_file_converts_and_is_idempotent(tmp_path):
    doc = _note(n_pages=2, strokes=2)
    path = str(tmp_path / "old.note")
    doc.save(path, version=1)
    size_before = os.path.getsize(path)

    info = fmt.upgrade_file(path)
    assert info["upgraded"] is True and info["kind"] == "note" and info["pages"] == 2
    assert info["bytes_before"] == size_before
    assert info["bytes_after"] == os.path.getsize(path)
    assert info["bytes_after"] < info["bytes_before"], "二进制容器应更小"
    assert open(path, "rb").read()[:8] == serializer.MAGIC_NOTE

    # 内容没丢
    loaded = NoteDocument.load(path)
    assert len(loaded.pages) == 2 and len(loaded.pages[1].strokes) == 2
    assert loaded.background == "grid" and loaded.background_color == "#fdf6e3"

    again = fmt.upgrade_file(path)
    assert again["upgraded"] is False, "已是 v2 时不该重复升级"


def test_upgrade_file_detects_annot(tmp_path):
    doc = AnnotDocument(pdf_path="D:/x.pdf", pdf_md5="deadbeef", page_count=2)
    doc.pages[0].strokes.append(Stroke(tool="pen", color="#000000", width=3.0,
                                       points=[(1.0, 2.0, 0.5)]))
    path = str(tmp_path / "old.annot")
    doc.save(path, version=1)

    info = fmt.upgrade_file(path)
    assert info["upgraded"] is True and info["kind"] == "annot"
    back = AnnotDocument.load(path)
    assert back.pdf_md5 == "deadbeef"
    assert len(back.pages[0].strokes) == 1


# --------------------------------------------------------------------- #
# 诊断
# --------------------------------------------------------------------- #
def test_format_info(tmp_path):
    p2 = str(tmp_path / "a.note")
    _note(n_pages=3).save(p2)
    with open(p2, "rb") as f:
        info = fmt.format_info(f.read())
    assert info["container"] == 2 and info["name"] == "binary"
    assert info["valid"] is True and info["kind"] == "note" and info["pages"] == 3

    p1 = str(tmp_path / "b.note")
    _note(n_pages=1).save(p1, version=1)
    with open(p1, "rb") as f:
        info1 = fmt.format_info(f.read())
    assert info1["container"] == 1 and info1["name"] == "json" and info1["pages"] == 1

    junk = fmt.format_info(b"\x00\x01\x02")
    assert junk["container"] is None and junk["name"] == "unknown"

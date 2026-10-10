"""A4 验收：`.note` / `.annot` 的 v2 二进制容器。

覆盖：往返一致、v1 JSON 仍可读、类型/magic/版本/校验和/截断的报错、
原子写不留临时文件、体积与耗时的前后对比。
"""

from __future__ import annotations

import json
import os

import pytest

from src.core import serializer
from src.core.annot_document import AnnotDocument
from src.core.note_document import NoteDocument
from src.core.serializer import DocumentFormatError
from src.core.stroke import Stroke


def _doc(n_pages=3, strokes_per_page=20, points=30):
    doc = NoteDocument(background="grid", background_color="#fdf6e3")
    doc.pages = []
    for p in range(n_pages):
        page = doc.add_page()
        for i in range(strokes_per_page):
            tool = ("pen", "highlighter", "eraser")[i % 3]
            color = ("#000000", "#ff0000", "#66ffd54f")[i % 3]
            page.strokes.append(Stroke(
                tool=tool, color=color, width=2.0 + (i % 5),
                points=[(10.0 + j, 20.0 + j * 0.5, (j % 10) / 10.0) for j in range(points)],
                timestamp=1234.5 + i,
            ))
    return doc


# --------------------------------------------------------------------- #
# 往返一致
# --------------------------------------------------------------------- #
def test_note_roundtrip_binary(tmp_path):
    doc = _doc()
    path = str(tmp_path / "a.note")
    doc.save(path)
    raw = open(path, "rb").read()
    assert raw[:8] == serializer.MAGIC_NOTE, "默认应写 v2 二进制容器"

    back = NoteDocument.load(path)
    assert len(back.pages) == len(doc.pages)
    assert back.background == "grid" and back.background_color == "#fdf6e3"
    assert back.page_w == pytest.approx(doc.page_w)
    for pa, pb in zip(doc.pages, back.pages):
        assert len(pa.strokes) == len(pb.strokes)
        for sa, sb in zip(pa.strokes, pb.strokes):
            assert (sa.tool, sa.color) == (sb.tool, sb.color)
            assert sb.width == pytest.approx(sa.width)
            assert sb.timestamp == pytest.approx(sa.timestamp, abs=1e-6)
            assert len(sb.points) == len(sa.points)
            for qa, qb in zip(sa.points, sb.points):
                assert qb[0] == pytest.approx(qa[0], abs=1e-3)
                assert qb[1] == pytest.approx(qa[1], abs=1e-3)
                assert qb[2] == pytest.approx(qa[2], abs=1.0 / 255.0 + 1e-6)


def test_annot_roundtrip_binary(tmp_path):
    doc = AnnotDocument(pdf_path="D:/x/y.pdf", pdf_md5="abc123", page_count=4)
    doc.pages[1].strokes.append(Stroke(tool="pen", color="#123456", width=4.0,
                                       points=[(1.0, 2.0, 0.5), (3.0, 4.0, 0.9)]))
    doc.created, doc.modified = 111.0, 222.0
    path = str(tmp_path / "a.annot")
    doc.save(path)
    assert open(path, "rb").read()[:8] == serializer.MAGIC_ANNOT

    back = AnnotDocument.load(path)
    assert back.pdf_path == "D:/x/y.pdf" and back.pdf_md5 == "abc123"
    assert back.created == pytest.approx(111.0) and back.modified >= 222.0
    assert len(back.pages) == 4
    assert len(back.pages[1].strokes) == 1
    assert back.pages[1].strokes[0].points[0][0] == pytest.approx(1.0)


def test_empty_document_roundtrip(tmp_path):
    doc = NoteDocument()
    path = str(tmp_path / "empty.note")
    doc.save(path)
    back = NoteDocument.load(path)
    assert len(back.pages) == 1 and back.pages[0].strokes == []


def test_single_point_stroke_roundtrip(tmp_path):
    doc = NoteDocument()
    doc.pages[0].strokes.append(Stroke(tool="pen", color="#000000", width=3.0,
                                       points=[(5.5, 6.5, 0.42)]))
    path = str(tmp_path / "one.note")
    doc.save(path)
    back = NoteDocument.load(path)
    p = back.pages[0].strokes[0].points[0]
    assert (p[0], p[1]) == pytest.approx((5.5, 6.5))
    assert p[2] == pytest.approx(0.42, abs=1.0 / 255.0 + 1e-6)


# --------------------------------------------------------------------- #
# 向后兼容
# --------------------------------------------------------------------- #
def test_v1_json_still_readable(tmp_path):
    doc = _doc(n_pages=2, strokes_per_page=5)
    path = str(tmp_path / "old.note")
    doc.save(path, version=1)
    raw = open(path, "rb").read()
    assert raw.lstrip()[:1] == b"{", "version=1 应写回 JSON"
    back = NoteDocument.load(path)
    assert len(back.pages) == 2
    assert len(back.pages[1].strokes) == 5


def test_handwritten_old_note_file(tmp_path):
    """手写的 1.0 老文件（缺 background_color）必须照常打开。"""
    path = str(tmp_path / "legacy.note")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "version": "1.0", "page_w": 595.28, "page_h": 841.89, "background": "lines",
            "pages": [{"strokes": [{"tool": "pen", "color": "#111111", "width": 2.0,
                                    "points": [[1.0, 2.0, 0.5]], "timestamp": 0.0}]}],
        }, f)
    doc = NoteDocument.load(path)
    assert doc.background == "lines"
    assert doc.background_color == "#ffffff"
    assert len(doc.pages[0].strokes) == 1


# --------------------------------------------------------------------- #
# 损坏与误用必须明确报错
# --------------------------------------------------------------------- #
def test_corrupted_checksum_rejected(tmp_path):
    path = str(tmp_path / "a.note")
    _doc().save(path)
    data = bytearray(open(path, "rb").read())
    data[len(data) // 2] ^= 0xFF                     # 中间改一个字节
    open(path, "wb").write(bytes(data))
    with pytest.raises(DocumentFormatError):
        NoteDocument.load(path)


def test_truncated_file_rejected(tmp_path):
    path = str(tmp_path / "a.note")
    _doc().save(path)
    data = open(path, "rb").read()
    open(path, "wb").write(data[:len(data) // 2])
    with pytest.raises(DocumentFormatError):
        NoteDocument.load(path)


def test_wrong_document_kind_rejected(tmp_path):
    annot = str(tmp_path / "a.annot")
    AnnotDocument(pdf_path="x", pdf_md5="y", page_count=1).save(annot)
    with pytest.raises(DocumentFormatError):
        NoteDocument.load(annot)
    note = str(tmp_path / "a.note")
    NoteDocument().save(note)
    with pytest.raises(DocumentFormatError):
        AnnotDocument.load(note)


def test_garbage_file_rejected(tmp_path):
    path = str(tmp_path / "junk.note")
    open(path, "wb").write(b"\x00\x01\x02not a document")
    with pytest.raises(DocumentFormatError):
        NoteDocument.load(path)


def test_unknown_version_rejected(tmp_path):
    path = str(tmp_path / "a.note")
    data = serializer.encode_document("note", {"version": "1.1"}, [[]])
    # 把 meta 里的 format 改成 99 再补上正确校验和
    import struct
    import zlib
    meta_len = struct.unpack_from("<I", data, 8)[0]
    meta = json.loads(data[12:12 + meta_len].decode("utf-8"))
    meta["format"] = 99
    meta_bytes = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = data[12 + meta_len:-4]
    out = bytearray(serializer.MAGIC_NOTE + struct.pack("<I", len(meta_bytes)) + meta_bytes + body)
    out += struct.pack("<I", zlib.crc32(bytes(out)) & 0xFFFFFFFF)
    open(path, "wb").write(bytes(out))
    with pytest.raises(DocumentFormatError):
        NoteDocument.load(path)


# --------------------------------------------------------------------- #
# 原子写
# --------------------------------------------------------------------- #
def test_atomic_write_leaves_no_temp_file(tmp_path):
    doc = _doc(n_pages=1, strokes_per_page=2)
    path = str(tmp_path / "a.note")
    doc.save(path)
    leftovers = [n for n in os.listdir(tmp_path) if n.startswith(".") and "tmp" in n]
    assert leftovers == [], f"原子写留下了临时文件：{leftovers}"
    assert os.path.getsize(path) > 0


def test_save_overwrites_atomically(tmp_path):
    path = str(tmp_path / "a.note")
    NoteDocument().save(path)
    small = os.path.getsize(path)
    _doc(n_pages=4, strokes_per_page=10).save(path)
    assert os.path.getsize(path) > small
    NoteDocument.load(path)                     # 覆盖后仍可读


# --------------------------------------------------------------------- #
# 体积
# --------------------------------------------------------------------- #
def test_binary_is_smaller_than_json(tmp_path):
    """同一点数下 v2 必须明显小于 v1 JSON（列式 9 字节/点 vs 文本 ~25 字节/点）。"""
    doc = _doc(n_pages=1, strokes_per_page=200, points=60)
    p2, p1 = str(tmp_path / "v2.note"), str(tmp_path / "v1.note")
    doc.save(p2)
    doc.save(p1, version=1)
    s2, s1 = os.path.getsize(p2), os.path.getsize(p1)
    assert s2 < s1 * 0.65, f"v2 {s2} 字节 vs JSON {s1} 字节，压缩不够"


def test_binary_size_per_point(tmp_path):
    """列式格式的开销应接近 9 字节/点（x4 + y4 + 压感1）。"""
    points = 5000
    doc = NoteDocument()
    doc.pages[0].strokes.append(Stroke(
        tool="pen", color="#000000", width=3.0,
        points=[(float(i % 500), float(i // 500), 0.5) for i in range(points)]))
    path = str(tmp_path / "dense.note")
    doc.save(path)
    size = os.path.getsize(path)
    assert size / points < 12.0, f"每点 {size / points:.1f} 字节，超出预期（~9 字节）"

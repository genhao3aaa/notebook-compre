"""保存失败的排查与回归：旧 `.annot` / `.note` 挡住新写入时的行为。

线上现象是 Ctrl+S 弹出：

    PermissionError: [WinError 5] 拒绝访问。:
    '…\\.x.annot.tmp-1234' -> '…\\x.annot'

排查结论（见 README「保存失败排查（PermissionError）」）：

1. 与旧文件的**格式**无关——旧 v1 JSON 批注照常能被覆盖（这里有用例钉住）；
2. 真正的原因是文件级冲突：旧批注带「只读」属性 / 被其他程序占用 / 文件夹不可写；
3. 旧的原子写实现本身放大了问题：
   * 临时文件名只有 pid，pid 复用时会撞上上一次崩溃留下的残留（Errno 13）；
   * 清理逻辑会顺手删掉「不是自己建的」残留文件；
   * 错误消息里的路径是临时文件，用户看不出该做什么。

本文件把每种成因都钉成用例，防止再退化。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess

import pytest

from src.core import serializer
from src.core.annot_document import AnnotDocument
from src.core.note_document import NoteDocument
from src.core.serializer import WriteError

WIN_ONLY = pytest.mark.skipif(os.name != "nt", reason="只读属性/文件占用语义只存在于 Windows")

#: 一份「以前的 annot」：v1 JSON 容器 + 一笔，字段结构与旧版程序一致
LEGACY_ANNOT = {
    "version": "1.0", "pdf_path": "D:/old/x.pdf", "pdf_md5": "abc", "created": 1.0,
    "modified": 1.0,
    "pages": [{"strokes": [{"tool": "pen", "color": "#111111", "width": 2.0,
                            "points": [[1.0, 2.0, 0.5]], "timestamp": 0.0}]}],
}


def _legacy_annot(path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(LEGACY_ANNOT, f)


def _stroke_total(path) -> int:
    return sum(len(p.strokes) for p in AnnotDocument.load(str(path)).pages)


def _temp_leftovers(directory) -> list:
    """我们这个实现建的临时文件：`.<名字>.tmp-<pid>-<随机>`。"""
    return sorted(n for n in os.listdir(str(directory)) if ".tmp-" in n)


def _can_create_file(directory) -> bool:
    probe = os.path.join(str(directory), ".writable-probe")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("x")
    except OSError:
        return False
    os.remove(probe)
    return True


# --------------------------------------------------------------------- #
# 旧文件格式：不是冲突来源（对照组）
# --------------------------------------------------------------------- #
def test_overwriting_legacy_v1_annot_works(tmp_path):
    """旧 v1 JSON 批注可以被直接覆盖成 v2 —— 说明「格式冲突」不成立。"""
    path = tmp_path / "old.annot"
    _legacy_annot(path)
    AnnotDocument.load(str(path)).save(str(path))
    assert path.read_bytes()[:8] == serializer.MAGIC_ANNOT
    assert _stroke_total(path) == 1, "旧批注的内容必须保留"


# --------------------------------------------------------------------- #
# 成因 1：旧文件带「只读」属性（从只读介质/别的机器拷来）
# --------------------------------------------------------------------- #
@WIN_ONLY
def test_readonly_legacy_annot_is_overwritten(tmp_path):
    path = tmp_path / "readonly.annot"
    _legacy_annot(path)
    os.chmod(path, stat.S_IREAD)
    attrs = os.stat(path).st_file_attributes
    assert attrs & stat.FILE_ATTRIBUTE_READONLY, "前置条件：文件确实带只读属性"

    AnnotDocument.load(str(path)).save(str(path))          # 修复前：PermissionError

    assert not os.stat(path).st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    assert path.read_bytes()[:8] == serializer.MAGIC_ANNOT
    assert _stroke_total(path) == 1
    assert _temp_leftovers(tmp_path) == []


@WIN_ONLY
def test_readonly_note_is_overwritten(tmp_path):
    path = tmp_path / "readonly.note"
    NoteDocument(background="grid").save(str(path))
    os.chmod(path, stat.S_IREAD)
    NoteDocument().save(str(path))
    assert not os.stat(path).st_file_attributes & stat.FILE_ATTRIBUTE_READONLY


# --------------------------------------------------------------------- #
# 成因 2：临时文件名只有 pid（pid 复用撞上崩溃残留）
# --------------------------------------------------------------------- #
@WIN_ONLY
def test_stale_temp_file_with_same_pid_does_not_block_save(tmp_path):
    """上一次崩溃留下的 `.<名字>.tmp-<pid>`（只读）不该影响这次保存，也不该被删掉。"""
    path = tmp_path / "x.annot"
    _legacy_annot(path)
    stale = tmp_path / f".{path.name}.tmp-{os.getpid()}"    # 老实现的命名方式
    stale.write_bytes(b"leftover")
    os.chmod(stale, stat.S_IREAD)

    AnnotDocument.load(str(path)).save(str(path))           # 修复前：Errno 13

    assert stale.exists(), "不是自己建的残留文件不能删"
    assert stale.read_bytes() == b"leftover"
    assert _temp_leftovers(tmp_path) == [stale.name], "自己那个临时文件必须清理干净"
    assert _stroke_total(path) == 1


# --------------------------------------------------------------------- #
# 成因 3：旧文件被其他程序占用（阅读器 / 同步盘 / 杀毒）
# --------------------------------------------------------------------- #
@WIN_ONLY
def test_locked_legacy_annot_reports_actionable_error(tmp_path):
    """占用情况下仍会失败（无法强改别人握着的文件），但**报错必须能照着做**。"""
    path = tmp_path / "locked.annot"
    _legacy_annot(path)
    before = path.read_bytes()

    with open(path, "rb"):                                  # 句柄不放：等价于别的程序占着
        with pytest.raises(WriteError) as ei:
            AnnotDocument.load(str(path)).save(str(path))

    msg = str(ei.value)
    assert str(path) in msg, "消息里必须是被覆盖的目标文件"
    assert ".tmp-" not in msg, "不能把临时文件路径丢给用户"
    assert "占用" in msg and "建议：" in msg

    assert path.read_bytes() == before, "失败的保存不能动到原文件"
    assert _temp_leftovers(tmp_path) == [], "失败后不能留下临时文件"


# --------------------------------------------------------------------- #
# 成因 4：保存位置（文件夹）不可写
# --------------------------------------------------------------------- #
@WIN_ONLY
def test_unwritable_directory_reports_actionable_error(tmp_path):
    user = os.environ.get("USERNAME", "")
    if not user or shutil.which("icacls") is None:
        pytest.skip("环境没有 icacls / 用户名")

    path = tmp_path / "denied.annot"
    _legacy_annot(path)
    subprocess.run(["icacls", str(tmp_path), "/deny", f"{user}:(WD,AD)"],
                   capture_output=True, text=True)
    try:
        if _can_create_file(tmp_path):
            pytest.skip("本环境无法用 icacls 拒写（权限模型不同）")
        with pytest.raises(WriteError) as ei:
            AnnotDocument.load(str(path)).save(str(path))
        msg = str(ei.value)
        assert "不允许写入" in msg and str(path) in msg
        assert str(tmp_path) in msg, "要告诉用户是哪个目录写不了"
    finally:
        subprocess.run(["icacls", str(tmp_path), "/remove:d", user],
                       capture_output=True, text=True)
    assert _can_create_file(tmp_path), "测试必须把目录权限恢复回去"


# --------------------------------------------------------------------- #
# 其他路径：目录不存在 / 同名目标是文件夹
# --------------------------------------------------------------------- #
def test_missing_directory_reports_missing_location(tmp_path):
    missing = tmp_path / "no_such_dir" / "x.note"
    with pytest.raises(WriteError) as ei:
        NoteDocument().save(str(missing))
    msg = str(ei.value)
    assert str(missing) in msg and "不存在" in msg
    assert not missing.parent.exists(), "不能顺手把目录建出来"


def test_target_is_a_directory_reports_clearly(tmp_path):
    target = tmp_path / "weird.annot"
    target.mkdir()
    with pytest.raises(WriteError) as ei:
        AnnotDocument(pdf_path="x", pdf_md5="y", page_count=1).save(str(target))
    assert "文件夹" in str(ei.value)


# --------------------------------------------------------------------- #
# 消息映射（不依赖真实 ACL，直接喂 OS 错误）
# --------------------------------------------------------------------- #
# --------------------------------------------------------------------- #
# 排查脚本：把「查得到、修得掉」也钉住
# --------------------------------------------------------------------- #
def test_diagnose_script_finds_and_fixes(tmp_path):
    """排查脚本要能指出只读/残骸，并在 --fix 后复检为 OK。"""
    import diagnose_save_permission as diag

    path = tmp_path / "ro.annot"
    _legacy_annot(path)
    stale = tmp_path / f".{path.name}.tmp-9999"
    stale.write_bytes(b"leftover")
    if os.name == "nt":
        os.chmod(path, stat.S_IREAD)

    rec = diag.inspect(str(path))
    assert rec["temp_leftovers"] == [str(stale)]
    if os.name == "nt":
        assert rec["status"] == diag.BAD
        assert any("只读" in p for p in rec["problems"])

    actions = diag.repair(rec)
    assert actions, "至少要把残骸清掉"
    assert not stale.exists()
    after = diag.inspect(str(path))
    assert after["status"] == diag.OK, after["problems"]


def test_diagnose_script_exit_codes(tmp_path, capsys):
    import diagnose_save_permission as diag

    NoteDocument().save(str(tmp_path / "good.note"))
    assert diag.main([str(tmp_path)]) == 0

    bad = tmp_path / "locked.note"
    NoteDocument().save(str(bad))
    if os.name == "nt":
        with open(bad, "rb"):                            # 占用：脚本必须报出来
            assert diag.main([str(tmp_path)]) == 1
        assert "占用" in capsys.readouterr().out


def test_temp_create_permission_error_is_retried(tmp_path, monkeypatch):
    """创建临时文件时的瞬时 EACCES 也要重试。

    线上截图就是这一种：`PermissionError: [Errno 13] Permission denied: '…\\.x.annot.tmp-23436'`
    —— `open()` 走 CRT，失败时**没有 winerror**，旧实现据此认为「不是瞬时问题」直接报错。
    """
    path = tmp_path / "x.note"
    NoteDocument().save(str(path))
    real_open = serializer._open_temp_file
    calls = {"n": 0}

    def flaky(p):
        calls["n"] += 1
        if calls["n"] < len(serializer._RETRY_DELAYS):
            raise PermissionError(13, "Permission denied")     # winerror 为 None
        return real_open(p)

    monkeypatch.setattr(serializer, "_open_temp_file", flaky)
    NoteDocument(background="grid").save(str(path))
    assert calls["n"] == len(serializer._RETRY_DELAYS), "瞬时 EACCES 必须重试"
    assert _temp_leftovers(tmp_path) == []


class _BusyError(PermissionError):
    """模拟「另一个程序正在使用此文件」（WinError 32 = 共享冲突）。"""

    def __init__(self) -> None:
        super().__init__(13, "另一个程序正在使用此文件，进程无法访问。")
        self.winerror = 32


class _OsShim:
    """只把 `os.replace` 换成会抖动的版本，其余属性透传（避免污染全局 os）。"""

    def __init__(self, real, replace) -> None:
        self._real = real
        self._replace = replace

    def __getattr__(self, name):
        return getattr(self._real, name)

    def replace(self, src, dst):
        return self._replace(src, dst)


def test_transient_errors_are_retried_then_reported(tmp_path, monkeypatch):
    """瞬时占用（WinError 32）先重试；重试用尽后仍失败也必须给出占用提示。"""
    path = tmp_path / "busy.note"
    NoteDocument().save(str(path))
    real_replace = os.replace
    calls = {"n": 0}
    always_busy = {"on": False}

    def flaky(src, dst):
        calls["n"] += 1
        if always_busy["on"] or calls["n"] < len(serializer._RETRY_DELAYS):
            raise _BusyError()
        return real_replace(src, dst)

    monkeypatch.setattr(serializer, "os", _OsShim(os, flaky))
    NoteDocument().save(str(path))                          # 第 3 次尝试成功
    assert calls["n"] == len(serializer._RETRY_DELAYS), "瞬时占用应当被重试"
    assert _temp_leftovers(tmp_path) == []

    always_busy["on"] = True                                # 一直失败：消息要说清是占用
    with pytest.raises(WriteError) as ei:
        NoteDocument().save(str(path))
    assert "占用" in str(ei.value) and "建议：" in str(ei.value)

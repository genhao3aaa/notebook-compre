"""实测「保存 `.annot` / `.note` 时报 PermissionError」的每一种成因。

用途：定位现场问题时，先把这份「成因清单」跑一遍——每种成因都用真实文件复现，
结果可对照 README「缺陷修复：保存时报 PermissionError」里的表格。

用法：
    python probe_save_permission.py           # 跑完自动清理临时目录
    python probe_save_permission.py --keep    # 保留现场（临时目录路径会打印出来）

判定口径与 `src/core/serializer.py::write_atomic` 一致（只读自愈 / 瞬时占用重试 /
唯一临时文件名），所以修复前后跑它的输出对比如下：

| 场景 | 修复前 | 现在 |
| --- | --- | --- |
| 旧 v1 JSON 批注覆盖（对照组） | OK | OK |
| 旧 `.annot` 带只读属性 | `PermissionError [WinError 5]` | **OK**（清只读位后重试成功） |
| 同 pid 残留临时文件（只读） | `PermissionError [Errno 13]` | **OK**（唯一临时名，且不删别人的残骸） |
| 旧 `.annot` 被别的程序独占打开 | `PermissionError [WinError 5]` | 仍失败，但消息可照做（目标路径 + 建议） |
| 目录被 ACL 拒写 | `PermissionError [Errno 13]` | 仍失败，但指出「文件夹不允许写入」 |
| 目录带只读属性（对照） | OK | OK（Windows 上目录只读属性不阻止写入） |
| 同名目标其实是文件夹 | `PermissionError [WinError 5]` | 仍失败，但指出「目标是一个文件夹」 |
"""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.annot_document import AnnotDocument           # noqa: E402

#: 「以前的 annot」：旧版程序写的 v1 JSON 容器
OLD_JSON = (b'{\n  "version": "1.0",\n  "pdf_path": "x.pdf",\n  "pdf_md5": "abc",\n'
            b'  "pages": [{"strokes": []}]\n}\n')

OPEN_EXISTING = 3


class _ExclusiveOpen:
    """把文件以「不共享」的方式打开，模拟别的程序（阅读器/同步盘）占着旧批注。"""

    def __init__(self, path: str, enabled: bool = True):
        self.path = path
        self.enabled = enabled
        self.handle = None

    def __enter__(self):
        if not self.enabled:
            return self
        kernel32 = ctypes.windll.kernel32
        self.handle = kernel32.CreateFileW(str(self.path), 0x80000000, 0, None,
                                           OPEN_EXISTING, 0, None)
        return self

    def __exit__(self, *exc):
        if self.handle not in (None, 0, -1):
            ctypes.windll.kernel32.CloseHandle(self.handle)
        return False


def _try_save(path: str) -> str:
    doc = AnnotDocument.from_pdf("x.pdf", "abc", 1)
    try:
        doc.save(path)
        return "OK"
    except Exception as e:                                  # noqa: BLE001 - 诊断脚本要原样展示
        return str(e).replace("\n", "\n      ")


def _prepare(work: str, name: str, exists: bool = True) -> str:
    directory = os.path.join(work, name)
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(directory)
    path = os.path.join(directory, "old.annot")
    if exists:
        with open(path, "wb") as f:
            f.write(OLD_JSON)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="复现保存 .annot 时的 PermissionError 成因")
    parser.add_argument("--keep", action="store_true", help="保留现场，不删临时目录")
    args = parser.parse_args(argv)

    work = tempfile.mkdtemp(prefix="hn-save-probe-")
    print(f"工作目录：{work}\n")
    windows = os.name == "nt"

    # 对照组：旧 v1 JSON 批注，正常覆盖（说明「格式冲突」不成立）
    path = _prepare(work, "1-legacy-json")
    print("[1] 旧 v1 JSON 批注，普通覆盖        :", _try_save(path))

    if windows:
        # 成因 1：旧 .annot 带只读属性
        path = _prepare(work, "2-readonly-attr")
        os.chmod(path, stat.S_IREAD)
        print("[2] 旧 .annot 带「只读」属性         :", _try_save(path))

        # 成因 2：崩溃残留的临时文件（旧实现按 pid 命名）
        path = _prepare(work, "3-stale-temp")
        stale = os.path.join(os.path.dirname(path), f".old.annot.tmp-{os.getpid()}")
        with open(stale, "wb") as f:
            f.write(b"leftover")
        os.chmod(stale, stat.S_IREAD)
        print("[3] 同 pid 残留只读临时文件          :", _try_save(path))
        print("    别人的残骸还在吗（不该被删）    :", os.path.exists(stale))

        # 成因 3：旧 .annot 被别的程序占着
        path = _prepare(work, "4-locked")
        with _ExclusiveOpen(path):
            print("[4] 旧 .annot 被别的程序独占打开     :", _try_save(path))

        # 成因 4：目录被 ACL 拒写
        path = _prepare(work, "5-denied-dir")
        user = os.environ.get("USERNAME", "")
        directory = os.path.dirname(path)
        if user and shutil.which("icacls"):
            subprocess.run(["icacls", directory, "/deny", f"{user}:(WD,AD)"],
                           capture_output=True, text=True)
            print("[5] 目录被 ACL 拒写                  :", _try_save(path))
            subprocess.run(["icacls", directory, "/remove:d", user],
                           capture_output=True, text=True)

        # 对照：目录带只读属性（Windows 上不阻止写入）
        path = _prepare(work, "6-dir-readonly-attr")
        os.chmod(os.path.dirname(path), stat.S_IREAD)
        print("[6] 目录带只读属性（对照）           :", _try_save(path))

    # 成因 5：同名目标其实是文件夹
    path = _prepare(work, "7-target-is-dir", exists=False)
    os.makedirs(path)
    print("[7] 同名目标其实是文件夹             :", _try_save(path))

    print("\n结论：第 2、3 项现在能自动修好（只读自愈 / 唯一临时名），"
          "第 4、5、7 项无法强改，但报错会指出**哪个文件、为什么、怎么办**。")
    if args.keep:
        print(f"现场保留在：{work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

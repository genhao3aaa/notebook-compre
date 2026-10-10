"""运行环境兼容层：让测试与冒烟脚本在「文件沙箱」环境下也能正常写临时文件。

背景
----
Python 的 `tempfile.mkdtemp()` 在 Windows 上以 `os.mkdir(path, 0o700)` 创建目录，
CPython 会据此生成一份「只授予当前用户」的 DACL。在启用了文件访问控制的受限环境
（例如 DSH 的文件沙箱）中，该 DACL 不包含沙箱自身的 SID，于是**新建的临时目录
无法写入任何文件**，表现为：

    PermissionError: [Errno 13] Permission denied: '...\\tmpXXXXXXXX\\x.txt'

注意这是环境限制，不是项目缺陷：同一台机器上普通 PowerShell / 普通 Python 完全正常。

处理
----
`ensure_writable_tempdir()` 启动时做一次探测：若 `mkdtemp` 出来的目录不可写，
就把 `tempfile.mkdtemp` 换成使用宽松权限（0o777，继承父目录 ACL）创建目录的实现，
行为与标准库一致（随机名、100 次重试、支持 suffix/prefix/dir）。

在正常环境里探测会直接通过，本模块等于什么都不做。
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile

_PATCHED = False


def _mkdtemp_writable(suffix: str | None = None, prefix: str | None = None,
                      dir: str | None = None) -> str:
    """`tempfile.mkdtemp` 的替代实现：以可写权限创建目录。"""
    base = dir or tempfile.gettempdir()
    if prefix is None:
        prefix = tempfile.gettempprefix()
    suffix = suffix or ""
    for _ in range(100):
        name = "%s%s%s" % (prefix, secrets.token_hex(6), suffix)
        path = os.path.join(base, name)
        try:
            # 关键差别：mode 用 0o777（继承父目录 ACL），而非标准库的 0o700。
            os.makedirs(path, mode=0o777)
        except FileExistsError:
            continue
        return path
    raise FileExistsError("无法创建临时目录：尝试 100 次仍未获得唯一名称")


def tempdir_is_writable() -> bool:
    """探测当前环境的临时目录是否真的可写。"""
    probe = None
    try:
        probe = tempfile.mkdtemp()
        with open(os.path.join(probe, ".probe"), "w", encoding="utf-8") as f:
            f.write("ok")
        return True
    except OSError:
        return False
    finally:
        if probe:
            shutil.rmtree(probe, ignore_errors=True)


def _patch_pathlib_mkdir() -> None:
    """把 pathlib.Path.mkdir 的 0o700 换成 0o777。

    pytest 的 tmp_path / basetemp 全部以 `Path.mkdir(mode=0o700)` 创建，在受限环境下
    会生成一份连创建者都进不去的 DACL，于是 fixture 直接报 PermissionError。
    这里只改「恰好等于 0o700」的调用，其他模式原样透传。
    """
    import pathlib

    original = pathlib.Path.mkdir

    def _mkdir(self, mode: int = 0o777, parents: bool = False,
               exist_ok: bool = False):
        return original(self, 0o777 if mode == 0o700 else mode,
                        parents=parents, exist_ok=exist_ok)

    pathlib.Path.mkdir = _mkdir  # type: ignore[assignment]


def _fresh_writable_root() -> str:
    """在系统临时目录下开一个「本次进程专用」的可写子目录。"""
    base = tempfile.gettempdir()
    # 清理已退出进程留下的同名残留目录（删除失败不影响运行）。
    try:
        for name in os.listdir(base):
            if name.startswith("hn-tmp-"):
                shutil.rmtree(os.path.join(base, name), ignore_errors=True)
    except OSError:
        pass
    root = os.path.join(base, "hn-tmp-%d" % os.getpid())
    os.makedirs(root, mode=0o777, exist_ok=True)
    return root


def ensure_writable_tempdir() -> bool:
    """确保 `tempfile.mkdtemp()` 可用；返回是否打了补丁。

    正常环境下探测即通过，直接返回 False，本函数没有任何副作用。
    受限环境下会：1) 替换 mkdtemp 实现；2) 把临时根目录切到全新的可写子目录，
    避免历史上以 0o700 创建、当前已不可读的残留目录把 pytest 的 tmp_path 卡死。
    """
    global _PATCHED
    if _PATCHED:
        return True
    if tempdir_is_writable():
        return False
    tempfile.mkdtemp = _mkdtemp_writable  # type: ignore[assignment]
    _patch_pathlib_mkdir()
    tempfile.tempdir = _fresh_writable_root()
    _PATCHED = True
    return True

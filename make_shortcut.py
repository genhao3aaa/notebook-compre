"""在桌面创建「手写笔记本与PDF批注.lnk」启动快捷方式。

为什么是两套实现
----------------
1. **首选 COM（WScript.Shell）**：Windows 原生方式，Explorer 一定能解析。
2. **回退：按 MS-SHLLINK 规范手写二进制**。某些受限环境会拦截 COM 实例化，
   此时至少还能写出一个结构合法的 .lnk。

注意：历史版本只用第 2 套实现，写出的 .lnk 结构自洽、但 Explorer 解析出的目标为空
（表现为「快捷方式在，双击没反应」）。因此现在默认走 COM，并且**写完会回读校验**。

目标 exe 自动探测：取 exe 修改时间最新的 `dist*/` 目录（**不**无条件优先 `dist/`，
理由见 `find_target()` 的注释）。
"""

import base64
import os
import struct
import subprocess
import sys

APP_DIR_NAME = "手写笔记本与PDF批注"
EXE_NAME = "手写笔记本与PDF批注.exe"
LNK_NAME = "手写笔记本与PDF批注.lnk"

FLAG_HAS_LINK_INFO = 0x0002
FLAG_HAS_RELATIVE_PATH = 0x0008
FLAG_HAS_WORKING_DIR = 0x0010
FLAG_IS_UNICODE = 0x0080


# ---------------------------------------------------------------------- #
# 定位
# ---------------------------------------------------------------------- #
def project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def find_target(root: str):
    """在项目目录里找到打包好的 exe：取 exe 最新的 dist*；规范目录 dist/ 不旧于别人时优先。

    规则是两条真实教训合成的：

    1. **不能无条件优先 `dist/`**——它可能是历史遗留的旧产物，那样会把快捷方式指向旧版本
       （真实发生过：`dist/` 是 9-08 的旧 exe，`dist_v5/` 才是刚打的）。
    2. **也不能只认最新**——若 `dist/` 已经是最新的，就该直接用它，别让快捷方式绕经
       `dist_xxx` 这类临时别名：别名一旦被清理，快捷方式就悬空了。
    """
    candidates = []
    try:
        entries = os.listdir(root)
    except OSError:
        return None
    for entry in entries:
        if not entry.startswith("dist"):
            continue
        exe = os.path.join(root, entry, APP_DIR_NAME, EXE_NAME)
        if os.path.isfile(exe):
            candidates.append((entry, exe))
    if not candidates:
        return None
    newest = max(candidates, key=lambda c: os.path.getmtime(c[1]))
    newest_time = os.path.getmtime(newest[1])
    for entry, exe in candidates:
        if entry == "dist" and os.path.getmtime(exe) >= newest_time:
            return exe
    return newest[1]


def get_desktop_dir() -> str:
    """从注册表取真实桌面目录（兼容 OneDrive 重定向），失败回退默认。"""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        val, _typ = winreg.QueryValueEx(key, "Desktop")
        winreg.CloseKey(key)
        path = os.path.expandvars(val) if isinstance(val, str) else ""
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass
    return os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")


# ---------------------------------------------------------------------- #
# 方式一：COM
# ---------------------------------------------------------------------- #
def create_with_com(target: str, lnk_path: str) -> bool:
    """用 WScript.Shell 创建快捷方式；成功返回 True。

    两个 PowerShell 版本的坑都在这里躲开了：

    1. **脚本用 `-EncodedCommand` 传**（UTF-16LE + base64），不当命令行参数传。
       脚本里有中文（快捷方式描述），而 Windows PowerShell 5.1 的参数传递受控制台
       代码页影响，非 UTF-8 代码页下会乱码甚至语法报错；编码后 5.1 与 7.x 行为一致。
    2. **优先 pwsh（PowerShell 7.x）**，没有再退回 powershell（5.1）。两者在
       `Remove-Item` 处理目录联接、默认编码等地方行为不同，能选就选新的。
    """
    script = (
        "$ErrorActionPreference='Stop';"
        "$s = New-Object -ComObject WScript.Shell;"
        "$l = $s.CreateShortcut('%s');"
        "$l.TargetPath = '%s';"
        "$l.WorkingDirectory = '%s';"
        "$l.IconLocation = '%s,0';"
        "$l.Description = '手写笔记本与 PDF 批注';"
        "$l.Save();"
        "'OK'"
    ) % (lnk_path, target, os.path.dirname(target), target)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    for exe in ("pwsh", "powershell"):
        try:
            proc = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                check=False)
        except FileNotFoundError:
            continue
        except Exception as e:  # noqa: BLE001
            print("  COM 创建异常:", repr(e), file=sys.stderr)
            continue
        if proc.returncode == 0 and os.path.isfile(lnk_path):
            return True
    return False


# ---------------------------------------------------------------------- #
# 方式二：手写 .lnk 二进制（MS-SHLLINK 最小子集）
# ---------------------------------------------------------------------- #
def build_lnk(target: str, working_dir: str) -> bytes:
    link_flags = (FLAG_HAS_LINK_INFO | FLAG_HAS_RELATIVE_PATH |
                  FLAG_HAS_WORKING_DIR | FLAG_IS_UNICODE)
    file_size = os.path.getsize(target) if os.path.exists(target) else 0
    header = struct.pack("<I", 0x0000004C)
    header += struct.pack("<16s", bytes([
        0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
        0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46]))
    header += struct.pack("<I", link_flags)
    header += struct.pack("<I", 0x00000020)
    header += struct.pack("<Q", 0) * 3
    header += struct.pack("<I", file_size)
    header += struct.pack("<i", 0)
    header += struct.pack("<I", 1)
    header += struct.pack("<H", 0)
    header += struct.pack("<H", 0)
    header += struct.pack("<I", 0)
    header += struct.pack("<I", 0)
    assert len(header) == 76, "header size %d" % len(header)

    label = b"\x00"
    volume_id = struct.pack("<IIII", 0x10 + len(label), 3, 0, 0x10) + label
    lbp = target.encode("mbcs") + b"\x00"
    suffix = b"\x00"
    li_header = 0x1C
    vol_off = li_header
    lbp_off = vol_off + len(volume_id)
    suffix_off = lbp_off + len(lbp)
    li_size = suffix_off + len(suffix)
    link_info = struct.pack("<IIIIIII", li_size, li_header, 0x0001,
                            vol_off, lbp_off, 0, suffix_off)
    link_info += volume_id + lbp + suffix
    assert len(link_info) == li_size, "linkinfo size %d != %d" % (len(link_info), li_size)

    def s(text: str) -> bytes:
        raw = text.encode("utf-16-le") + b"\x00\x00"
        return struct.pack("<H", len(text) + 1) + raw

    strings = s(target) + s(working_dir)
    terminal = b"\x00\x00\x00\x00"
    return header + link_info + strings + terminal


def create_with_bytes(target: str, lnk_path: str) -> bool:
    try:
        with open(lnk_path, "wb") as f:
            f.write(build_lnk(target, os.path.dirname(target)))
    except Exception as e:  # noqa: BLE001
        print("  写 .lnk 失败:", repr(e), file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------- #
def verify(lnk_path: str, target: str) -> bool:
    """回读校验：.lnk 里必须真的含有目标路径（UTF-16LE）。"""
    try:
        data = open(lnk_path, "rb").read()
    except OSError as e:
        print("  读取 .lnk 失败:", e, file=sys.stderr)
        return False
    if target.encode("utf-16-le") not in data:
        print("  校验失败：.lnk 中找不到目标路径", file=sys.stderr)
        return False
    return True


def main() -> int:
    root = project_root()
    target = find_target(root)
    if not target:
        print("找不到打包好的 exe。请先执行：pyinstaller build.spec", file=sys.stderr)
        return 1

    desktop = get_desktop_dir()
    lnk_path = os.path.join(desktop, LNK_NAME)
    print("目标 exe :", target)
    print("快捷方式 :", lnk_path)

    ok = create_with_com(target, lnk_path)
    print("  COM 创建:", "成功" if ok else "不可用，回退到二进制写法")
    if not ok:
        ok = create_with_bytes(target, lnk_path)
    if not ok:
        return 1

    if not verify(lnk_path, target):
        if create_with_bytes(target, lnk_path) and verify(lnk_path, target):
            print("  已用二进制写法重建并通过校验")
        else:
            return 1

    print("完成：", lnk_path, "(%d bytes)" % os.path.getsize(lnk_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())

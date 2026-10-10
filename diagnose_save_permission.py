"""保存失败排查工具：找出「为什么这个 `.annot` / `.note` 存不进去」。

用法
----
    python diagnose_save_permission.py                    # 用程序上次打开的目录
    python diagnose_save_permission.py "D:\\笔记" "D:\\论文"  # 指定目录 / 文件
    python diagnose_save_permission.py "D:\\笔记" --fix     # 顺手清掉只读属性与崩溃残骸
    python diagnose_save_permission.py "D:\\笔记" --json    # 机器可读输出

为什么要单独一个工具：线上「保存失败」弹窗里的
`PermissionError: [WinError 5] 拒绝访问。: '….tmp-1234' -> '…x.annot'` 既看不出
**是哪个文件的问题**，也看不出**该做什么**。这个脚本把四类真实成因逐个查出来
（用到的判定逻辑与 `src/core/serializer.py` 的写盘实现同源）：

  1. 目标带「只读」属性——从只读介质 / 别的机器拷来的旧批注最常见；
  2. 目标被别的程序占用——PDF 阅读器、网盘同步、杀毒扫描；
  3. 所在文件夹不允许写入——ACL 受限的位置（Program Files、受限沙箱目录）；
  4. 崩溃残留的 `.<名字>.tmp-<pid>` 临时文件——旧实现按 pid 命名，复用 pid 时直接 Errno 13。

`--fix` 能直接修好 1 与 4；2 与 3 需要按提示处理（关掉占用的程序 / 换目录或修 ACL）。

退出码：0 = 没发现问题或已全部修好，1 = 仍有需要人工处理的问题，2 = 用法错误。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import stat
import sys
import time
from typing import Dict, List, Optional

#: 会被检查的文档：本程序写的批注与笔记
DOC_SUFFIXES = (".annot", ".note")

#: 我们（以及旧版本）留下的临时文件前缀中缀
TMP_MARK = ".tmp-"

_MAGIC_KIND = {b"HNNOTE2\x00": "note(v2)", b"HNANNT2\x00": "annot(v2)"}

_FILE_ATTRIBUTE_READONLY = getattr(stat, "FILE_ATTRIBUTE_READONLY", 0x1)
_FILE_ATTRIBUTE_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)

OK, WARN, BAD = "ok", "warn", "bad"


# ---------------------------------------------------------------------- #
# 判定（与 src/core/serializer.py 的写盘路径保持同一口径）
# ---------------------------------------------------------------------- #
def file_attributes(path: str) -> int:
    st = os.stat(path)
    return int(getattr(st, "st_file_attributes", 0))


def is_readonly(path: str) -> bool:
    try:
        return bool(file_attributes(path) & _FILE_ATTRIBUTE_READONLY)
    except OSError:
        return False


def directory_writable(directory: str) -> bool:
    """真往目录里建一个探针文件试试（Windows 上 `os.access` 对目录不可靠）。"""
    probe = os.path.join(directory, ".hn-write-probe")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("x")
    except OSError:
        return False
    try:
        os.remove(probe)
    except OSError:
        pass
    return True


def can_replace(path: str) -> bool:
    """目标能否被 `os.replace` 换掉：需要目标文件的 DELETE 权限。

    只读属性 / 被别的程序以「不共享删除」的方式打开，都会让这次试开失败——
    这正是保存时 `[WinError 5]/[WinError 32]` 的直接原因。
    """
    if os.name != "nt":
        return os.access(path, os.W_OK)
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        handle = create(str(path), 0x00010000, 0x1 | 0x2, None, 3, 0, None)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:                      # pragma: no cover - 探测失败不该判成有问题
        return True


def temp_leftovers(path: str) -> List[str]:
    """同目录里属于这个文档的临时残骸（旧版 naming 与新版 naming 都算）。"""
    directory, base = os.path.dirname(os.path.abspath(path)) or ".", os.path.basename(path)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted(os.path.join(directory, n) for n in names
                  if n.startswith(f".{base}{TMP_MARK}"))


def describe_container(path: str) -> str:
    """认出文件是 v2 容器还是 v1 JSON（顺带判 v1 的归属：批注 / 笔记）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
            if head in _MAGIC_KIND:
                return _MAGIC_KIND[head]
            f.seek(0)
            text = f.read(4096).lstrip()[:1]
    except OSError as e:
        return f"读不了（{e.__class__.__name__}）"
    if text == b"{":
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return "v1(JSON，无法解析)"
        kind = "annot" if ("pdf_md5" in data or "pdf_path" in data) else "note"
        return f"v1({kind} JSON)"
    return "未知格式"


# ---------------------------------------------------------------------- #
# 检查与修复
# ---------------------------------------------------------------------- #
def inspect(path: str) -> Dict:
    """检查一个文档（或同名的文件夹）能不能被保存。"""
    rec: Dict = {"path": path, "status": OK, "problems": [], "fixes": []}

    if os.path.isdir(path):
        rec["status"] = BAD
        rec["problems"].append("同名目标是**文件夹**，文件写不进去")
        return rec
    if not os.path.isfile(path):
        rec["status"] = BAD
        rec["problems"].append("目标不存在（且不是文件夹）")
        return rec

    st = os.stat(path)
    rec["size"] = st.st_size
    rec["mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
    rec["container"] = describe_container(path)

    directory = os.path.dirname(os.path.abspath(path)) or "."
    rec["directory"] = directory
    rec["directory_writable"] = directory_writable(directory)
    rec["readonly"] = is_readonly(path)
    rec["replaceable"] = can_replace(path)
    rec["temp_leftovers"] = temp_leftovers(path)

    if not rec["directory_writable"]:
        rec["status"] = BAD
        rec["problems"].append(f"所在文件夹不允许写入：{directory}")
        rec["fixes"].append(f'icacls "{directory}" /grant "%USERNAME%":(OI)(CI)M   （需管理员）')
        rec["fixes"].append("或用「另存为」保存到「文档」等可写目录")
    if rec["readonly"]:
        rec["status"] = BAD
        rec["problems"].append("文件带「只读」属性")
        rec["fixes"].append(f'attrib -r "{path}"（或用本脚本 --fix）')
    elif not rec["replaceable"]:
        rec["status"] = BAD
        rec["problems"].append("文件正被别的程序占用（无法替换）")
        rec["fixes"].append("关闭可能打开它的程序：PDF 阅读器 / 网盘同步 / 杀毒扫描 / 另一个本程序窗口")
        rec["fixes"].append("确认已关闭后重试保存（本程序会自动重试 ~0.2 s）")
    if rec["temp_leftovers"]:
        if rec["status"] == OK:
            rec["status"] = WARN
        rec["problems"].append(f"有 {len(rec['temp_leftovers'])} 个崩溃残留的临时文件")
        rec["fixes"].append("用本脚本 --fix 清理（旧版本按 pid 命名，复用 pid 时会直接保存失败）")
    if getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_HIDDEN:
        rec["problems"].append("文件带「隐藏」属性（不影响保存，但排查时容易被漏看）")
    return rec


def repair(rec: Dict) -> List[str]:
    """能自动修的都修掉：只读属性 + 崩溃残骸。返回做过的事。"""
    actions: List[str] = []
    path = rec["path"]
    if rec.get("readonly"):
        try:
            mode = os.stat(path).st_mode
            os.chmod(path, stat.S_IWRITE | (mode & 0o666))
            actions.append(f"已取消只读：{path}")
        except OSError as e:
            actions.append(f"取消只读失败：{path}（{e}）")
    for tmp in rec.get("temp_leftovers", []):
        try:
            os.remove(tmp)
            actions.append(f"已删除残留临时文件：{tmp}")
        except OSError as e:
            actions.append(f"删除残留失败：{tmp}（{e}）")
    return actions


def collect(paths: List[str], recursive: bool = True) -> List[str]:
    """把参数展开成「要检查的文档」清单（目录 → 递归找 .annot/.note）。"""
    found: List[str] = []
    for raw in paths:
        p = os.path.abspath(raw)
        if os.path.isfile(p) or not os.path.isdir(p):
            found.append(p)                     # 显式给的文件（含不存在的）都直接检查
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if not d.startswith("_") and d not in
                       (".git", ".venv", "build", "dist", "__pycache__")]
            for name in files:
                if name.lower().endswith(DOC_SUFFIXES) and TMP_MARK not in name:
                    found.append(os.path.join(root, name))
            if not recursive:
                break
    return sorted(set(found))


def settings_last_dir() -> Optional[str]:
    """读取程序记录的上次打开目录（QSettings 在注册表里的 `last_dir`）。"""
    if os.name != "nt":
        return None
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\WorkBuddy\HandwritingNotebook")
        try:
            value, _ = winreg.QueryValueEx(key, "last_dir")
            return str(value) or None
        finally:
            winreg.CloseKey(key)
    except OSError:
        return None


def default_paths() -> List[str]:
    """没给参数时：用程序上次打开的目录，其次「文档」。"""
    for cand in (settings_last_dir(), os.path.join(os.path.expanduser("~"), "Documents")):
        if cand and os.path.isdir(cand):
            return [cand]
    return []


def settings_info() -> Dict[str, str]:
    """程序当前的设置后端（注册表还是 INI 文件）——「记不住阅读位置」先看这里。

    受限策略 / 沙箱 / 只读注册表下 QSettings 写注册表会**静默失败**
    （只把 `status()` 置为 AccessError，不抛异常），偏好与阅读进度就会丢。
    程序现在会自动改用 INI 文件，这里把实际情况报出来。
    """
    try:
        from src.core.settings import AppSettings, settings_candidates
    except Exception as e:                       # 没装依赖 / 不在项目根目录
        return {"backend": "unknown", "path": "", "detail": f"无法导入 src.core.settings：{e}"}
    try:
        s = AppSettings()
        info = s.storage()
        return {"backend": str(info.get("backend", "?")),
                "path": str(info.get("path", "")),
                "candidates": "；".join(settings_candidates())}
    except Exception as e:
        return {"backend": "unknown", "path": "", "detail": repr(e)}


def annot_store_records() -> List[Dict]:
    """批注兜底目录候选链：逐个真探测「能不能建文件」。"""
    try:
        from src.core import annot_store as store
    except Exception as e:
        return [{"dir": "(无法导入 src.core.annot_store)", "writable": False,
                 "exists": False, "note": repr(e)}]
    out: List[Dict] = []
    for d in store.store_candidates():
        exists = os.path.isdir(d)
        out.append({"dir": d, "exists": exists,
                    "writable": bool(store.probe_writable(d))})
    return out


def report_environment() -> None:
    """先报「存哪儿」：设置后端 + 批注兜底候选链。"""
    info = settings_info()
    backend = {"registry": "注册表", "ini": "INI 文件",
               "unknown": "无法判定"}.get(info.get("backend", ""), info.get("backend", ""))
    line = f"设置后端：{backend}"
    if info.get("path"):
        line += f"（{info['path']}）"
    print(line)
    if info.get("detail"):
        print(f"       · {info['detail']}")
    if info.get("backend") == "ini":
        print("       · 说明：注册表写不进去（受限策略/沙箱/只读），已自动改用 INI 文件；")
        print("         偏好与阅读进度都存在上面这个文件里，不再静默丢失。")
    print("批注兜底目录（按优先级，程序取第一个「可写」的）：")
    for rec in annot_store_records():
        marks = ("存在" if rec["exists"] else "不存在") + "/" + ("可写" if rec["writable"] else "不可写")
        print(f"       · [{marks}] {rec['dir']}")
    print()


def _label(rec: Dict) -> str:
    return {"ok": "OK", "warn": "注意", "bad": "问题"}[rec["status"]]


def report(records: List[Dict], actions: Dict[str, List[str]]) -> None:
    print(f"检查了 {len(records)} 个文档\n")
    for rec in records:
        head = f"[{_label(rec)}] {rec['path']}"
        if rec.get("container"):
            head += f"  ({rec['container']}, {rec.get('size', 0)} 字节, {rec.get('mtime', '')})"
        print(head)
        for item in rec["problems"]:
            print(f"       · {item}")
        for act in actions.get(rec["path"], []):
            print(f"       ✓ {act}")
        if rec["status"] != OK:
            for hint in rec["fixes"]:
                if hint not in actions.get(rec["path"], []):
                    print(f"       → {hint}")
    bad = sum(1 for r in records if r["status"] == BAD)
    fixed = sum(len(v) for v in actions.values())
    print(f"\n总共：{len(records)} 个文档，仍有 {bad} 个有问题，"
          f"--fix 处理了 {fixed} 项")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="排查 .annot / .note 保存失败（PermissionError）的原因",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python diagnose_save_permission.py \"D:\\我的论文\" --fix")
    parser.add_argument("paths", nargs="*", help="要检查的目录或文件；省略则用上次打开的目录")
    parser.add_argument("--fix", action="store_true", help="顺带取消只读属性、清理残留临时文件")
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于脚本处理）")
    parser.add_argument("--no-recursive", action="store_true", help="只在给定目录里查，不递归")
    args = parser.parse_args(argv)

    if not args.json:
        report_environment()          # 先报「存哪儿」：设置后端 + 批注兜底候选链

    targets = args.paths or default_paths()
    if not targets:
        print("请给出要检查的目录或文件（例如：python diagnose_save_permission.py \"D:\\我的论文\"）",
              file=sys.stderr)
        return 2

    files = collect(targets, recursive=not args.no_recursive)
    if not files:
        print("没有找到 .annot / .note 文档。给出的目录里可能还没有保存过批注。")
        return 0

    records: List[Dict] = []
    actions: Dict[str, List[str]] = {}
    for path in files:
        rec = inspect(path)
        if args.fix:
            actions[path] = repair(rec)
            # 修完复检，让输出反映现状
            rec = inspect(path)
        records.append(rec)

    if args.json:
        print(json.dumps({"environment": {"settings": settings_info(),
                                          "annot_store": annot_store_records()},
                          "records": records, "fixes": actions},
                         ensure_ascii=False, indent=2))
    else:
        report(records, actions)

    return 1 if any(rec["status"] == BAD for rec in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())

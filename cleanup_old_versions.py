"""把「历史版本」的构建目录送进回收站（可恢复），并写结果日志。

为什么用 send2trash：Windows 上没有标准库的「移入回收站」API，
send2trash 通过 SHFileOperation(FOF_ALLOWUNDO) 实现，删除可撤销，
比永久删除安全得多。

⚠️ 安全约定（重要）
-------------------
**绝不删除 `dist/` 与 `build/`**：前者是当前交付物，后者是当前打包的中间目录。
本脚本的历史版本把 `dist` 也列进了清理名单，结果把正在使用的程序删掉了
（用户看到的现象就是「文件被误删」）。现在只清理带版本后缀的历史目录
（`dist_prev_v3`、`build_v4` 之类），并且每次运行都会打印实际清理清单。

另有一道保险：**交付物必须叫 `dist/`**。如果 `dist/` 不存在、却存在 `dist_*` 目录，
说明当前交付物很可能正住在带后缀的目录里（例如临时打包的 `dist_v5`），
此时清理 `dist_*` 就等于删掉正在用的程序 —— 这种情况默认**跳过**，
要强行清理得显式加 `--force`。
"""

import os
import sys

from send2trash import send2trash

BASE = os.path.dirname(os.path.abspath(__file__))

#: 当前产物，永远不清理
KEEP = {"dist", "build"}
#: 只清理这些前缀的历史目录
PREFIXES = ("dist_", "build_")
#: 项目外的历史产物（存在才处理）
EXTRA = [r"D:\work zone\_old_exe_trash"]

LOG = os.path.join(BASE, "_cleanup_log.txt")


def _is_link(path: str) -> bool:
    """目录联接（junction）/ 符号链接。删链接本身也可能牵连目标，一律不碰。"""
    checker = getattr(os.path, "isjunction", None)   # Python 3.12+
    try:
        if checker is not None and checker(path):
            return True
    except OSError:
        pass
    return os.path.islink(path)


def collect_targets() -> list:
    targets = []
    for name in sorted(os.listdir(BASE)):
        if name.startswith(PREFIXES) and name not in KEEP:
            full = os.path.join(BASE, name)
            if _is_link(full):
                print("SKIP(目录联接/符号链接，不清理)", full, flush=True)
                continue
            targets.append(full)
    targets.extend(EXTRA)
    return targets


def live_build_looks_versioned(targets: list) -> bool:
    """`dist/` 不存在但有 `dist_*` —— 当前交付物可能就住在里面（删不得）。"""
    if os.path.isdir(os.path.join(BASE, "dist")):
        return False
    return any(os.path.basename(t).startswith("dist_") for t in targets)


def main() -> None:
    lines = []
    targets = collect_targets()
    if live_build_looks_versioned(targets) and "--force" not in sys.argv[1:]:
        risky = [t for t in targets if os.path.basename(t).startswith("dist_")]
        print("⚠ 没有找到规范的 dist/，但存在带后缀的 dist_*：", flush=True)
        for t in risky:
            print("   -", t, flush=True)
        print("  当前交付物很可能就是其中之一 —— 为免误删正在用的程序，本次跳过它们；"
              "\n  正确做法是重新打包到 dist/（pyinstaller build.spec），"
              "确认要删就加 --force。", flush=True)
        targets = [t for t in targets if t not in risky]
    print("将清理以下历史目录（当前 dist/ 与 build/ 已排除）：", flush=True)
    for t in targets:
        print("  -", t, flush=True)
    for t in targets:
        if os.path.isdir(t):
            try:
                send2trash(t)
                lines.append("RECYCLED\t" + t)
                print("RECYCLED", t, flush=True)
            except Exception as e:  # noqa: BLE001
                lines.append("FAILED\t%s\t%r" % (t, e))
                print("FAILED", t, repr(e), flush=True)
        else:
            lines.append("SKIP\t" + t)
            print("SKIP", t, flush=True)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()

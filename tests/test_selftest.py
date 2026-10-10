"""A5 验收：`--selftest` 自检入口本身要可靠。

打包裁剪的验收靠 `<exe> --selftest`，所以这个入口必须：
1. 跑通「打开 PDF → 手写 → 保存批注 → 导出 → 保存笔记」；
2. **同一个工作目录重跑两次都要通过**（第一次会留下 .pdf/.annot/settings.ini，
   直接复用会走到错误分支弹模态框 → 挂死；自检内部改用唯一子目录解决）。
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_selftest(workdir: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    # 必须显式指定 utf-8：Windows 上 subprocess 默认按本地代码页（GBK）解码子进程输出
    return subprocess.run([sys.executable, "main.py", "--selftest", workdir],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)


def test_selftest_passes(tmp_path):
    proc = _run_selftest(str(tmp_path))
    assert "SELFTEST OK" in proc.stdout, f"自检失败：\n{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0


def test_selftest_is_rerunnable_in_same_dir(tmp_path):
    first = _run_selftest(str(tmp_path))
    assert first.returncode == 0, first.stdout + first.stderr
    second = _run_selftest(str(tmp_path))       # 同一目录再来一次
    assert second.returncode == 0, second.stdout + second.stderr
    assert "SELFTEST OK" in second.stdout

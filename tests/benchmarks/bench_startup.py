"""冷启动基准：**必须是新进程**。

为什么不能在同一进程里测：模块已在 `sys.modules` 时 `import` 是 no-op，
ROADMAP §1.2 的「热启动 0.087 s」就是这么来的——它测的不是重启，是空操作。
本脚本每次都在子进程里从零 import，并分别报告各阶段与总墙钟时间。

用法：
    .venv\\Scripts\\python.exe tests/benchmarks/bench_startup.py
    .venv\\Scripts\\python.exe tests/benchmarks/bench_startup.py --repeat 5 --json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

import _common as C

CHILD = r'''
import json, os, sys, time
repo = os.environ["HN_BENCH_REPO"]
sys.path.insert(0, repo)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.chdir(repo)

t0 = time.perf_counter()
import PySide6.QtWidgets  # noqa: F401
t1 = time.perf_counter()
from src.ui.main_window import MainWindow
t2 = time.perf_counter()
from PySide6.QtWidgets import QApplication
app = QApplication([])
t3 = time.perf_counter()
from src.core.settings import AppSettings
win = MainWindow(settings=AppSettings(path=os.environ["HN_BENCH_INI"]))
t4 = time.perf_counter()
win.show()
app.processEvents()
t5 = time.perf_counter()

# A6：真实启动路径（新建空白笔记）不该 import PyMuPDF；打开 PDF 时才按需加载
pymupdf_at_startup = "pymupdf" in sys.modules
from src.render import pdf_engine
t6 = time.perf_counter()
pdf_engine.fitz_module()
t7 = time.perf_counter()

print("HN_BENCH_JSON " + json.dumps({
    "import_pyside6": t1 - t0,
    "import_main_window": t2 - t1,
    "qapplication": t3 - t2,
    "main_window_ctor": t4 - t3,
    "show_first_frame": t5 - t4,
    "phases_total_without_pdf": t5 - t0,
    "pymupdf_loaded_at_startup": pymupdf_at_startup,
    "import_pymupdf_on_demand": t7 - t6,
}))
win._confirm_save = lambda *a, **k: True
for _t in list(win._tabs):
    _t.dirty = False
win.close()
'''


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="冷启动基准（子进程）")
    ap.add_argument("--repeat", type=int, default=3, help="重复次数（每次都新进程）")
    ap.add_argument("--dpr", type=float, default=1.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    C.configure_stdio()
    C.ensure_writable_temp()
    ini_dir = tempfile.mkdtemp(prefix="hn-bench-startup-")
    env = dict(os.environ)
    env["HN_BENCH_REPO"] = C.REPO_ROOT
    env["HN_BENCH_INI"] = os.path.join(ini_dir, "bench.ini")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_SCALE_FACTOR"] = str(a.dpr)

    runs = []
    phases = None
    for i in range(max(1, a.repeat)):
        t = time.perf_counter()
        proc = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=C.REPO_ROOT,
                              capture_output=True, text=True)
        wall = time.perf_counter() - t
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            return proc.returncode
        parsed = None
        for line in proc.stdout.splitlines():
            if line.startswith("HN_BENCH_JSON "):
                parsed = json.loads(line[len("HN_BENCH_JSON "):])
        if parsed is None:
            print("子进程没有输出基准 JSON：", proc.stdout, file=sys.stderr)
            return 2
        phases = parsed
        runs.append({"run": i + 1, "wall_s": round(wall, 3), **parsed})

    walls = [r["wall_s"] for r in runs]
    payload = {
        "device": "startup",
        "qt_scale_factor": a.dpr,
        "repeat": a.repeat,
        "wall_best_s": min(walls),
        "wall_median_s": round(statistics.median(walls), 3),
        "phases_last_run": {k: round(v, 3) for k, v in phases.items()},
        "runs": runs,
    }
    lines = [
        f"冷启动（新进程 × {a.repeat}，QT_SCALE_FACTOR={a.dpr}，offscreen）",
        f"  墙钟 best {min(walls):.3f} s / median {statistics.median(walls):.3f} s",
        "  阶段（最后一次）：",
    ]
    for k, v in phases.items():
        lines.append(f"    {k:<20} {v:6.3f} s")
    C.dump(payload, as_json=a.json, table="\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

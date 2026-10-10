"""A2 基准：整页重建 vs 脏矩形增量重绘。

口径：
- 「一次编辑」= 撤销/擦除一条笔画 → 只重绘该笔画包围盒（外扩线宽）覆盖的区域；
- 同时报告命中笔画数——脏矩形命中几条笔画，直接决定这次重绘要花多少钱；
- `--layout realistic`（默认，真实手写长度）是主口径；`scatter`（ROADMAP 的长笔画）
  与 `dense`（高重叠）作为上下界参考。

用法：
    .venv\\Scripts\\python.exe tests/benchmarks/bench_incremental.py
    .venv\\Scripts\\python.exe tests/benchmarks/bench_incremental.py --strokes 1000 --layout scatter --json
"""

from __future__ import annotations

import argparse
import sys

import _common as C

C.ensure_repo_on_path()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="整页重建 vs 增量重绘")
    ap.add_argument("--strokes", default="200,1000")
    ap.add_argument("--layout", choices=C.LAYOUTS, default="realistic")
    ap.add_argument("--scale", type=float, default=C.DEFAULT_SCALE)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    C.configure_stdio()
    C.ensure_offscreen()
    C.ensure_writable_temp()

    from PySide6.QtWidgets import QApplication

    from src.core.settings import AppSettings
    from src.core.stroke import AddStrokeCmd, UndoManager
    from src.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])

    import os
    import tempfile

    win = MainWindow(settings=AppSettings(path=os.path.join(
        tempfile.mkdtemp(prefix="hn-bench-inc-"), "b.ini")))
    canvas = win.canvas
    canvas.resize(1000, 1300)
    canvas.scale = a.scale

    rows = []
    for n in C.parse_int_list(a.strokes):
        canvas.page_strokes = []
        canvas.undo_mgr = UndoManager(canvas.page_strokes, depth=n + 10)
        for s in C.make_strokes(n, layout=a.layout):
            canvas.undo_mgr.push(AddStrokeCmd(s))

        full = C.bench(canvas.rebuild_all, repeat=max(3, a.repeat // 2))["best"]
        victim = canvas.page_strokes[len(canvas.page_strokes) // 2]
        rect = canvas._dirty_page_rect([victim])
        hits = sum(1 for s in canvas.page_strokes if canvas._stroke_touches(s, rect))
        inc = C.bench(lambda: canvas.repaint_region(rect), repeat=a.repeat)["best"]
        rows.append({
            "strokes": n,
            "points": n * C.POINTS_PER_STROKE,
            "layout": a.layout,
            "dirty_hits": hits,
            "full_rebuild_s": round(full, 4),
            "incremental_ms": round(inc * 1000, 3),
            "speedup": round(full / inc, 1) if inc > 0 else None,
        })

    payload = {"device": "incremental_repaint", "scale": a.scale,
               "layout": a.layout, "qt_scale_factor": a.dpr if hasattr(a, "dpr") else 1.0,
               "results": rows}
    lines = [
        f"整页重建 vs 增量重绘  layout={a.layout} scale={a.scale}",
        f"{'笔画':>7} {'脏矩形命中':>10} {'整页重建(s)':>12} {'增量重绘(ms)':>13} {'倍数':>7}",
    ]
    for r in rows:
        lines.append(f"{r['strokes']:>7} {r['dirty_hits']:>10} {r['full_rebuild_s']:>12.3f} "
                     f"{r['incremental_ms']:>13.2f} {r['speedup']:>7.1f}")

    C.dump(payload, as_json=a.json, table="\n".join(lines))

    canvas.page_strokes = []
    for t in list(getattr(win, "_tabs", [])):
        t.dirty = False
    win._confirm_save = lambda *x, **k: True
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

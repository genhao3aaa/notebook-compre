"""真机口径基准：整页重建 `PageCanvas.rebuild_pixmap()`。

与 `bench_render.py` 的区别（也是它存在的理由）：
- 走真实代码路径：页面背景（可选网格）+ `_page_xform` + `paint_strokes` + dpr；
- dpr 通过 `QT_SCALE_FACTOR` 生效，因此在 150% / 200% 缩放屏上测到的是真实像素量；
- 输出 `page_pixmap` 的实际像素尺寸，防止「以为测了 dpr=1.5 其实还是 dpr=1」。

用法：
    .venv\\Scripts\\python.exe tests/benchmarks/bench_canvas_rebuild.py
    .venv\\Scripts\\python.exe tests/benchmarks/bench_canvas_rebuild.py --strokes 1000 --dpr 1.5 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

import _common as C

C.configure_stdio()
C.ensure_repo_on_path()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="整页重建基准（真实画布路径）")
    ap.add_argument("--strokes", default="100,500,1000")
    ap.add_argument("--dpr", type=float, default=1.0, help="QT_SCALE_FACTOR（1.0 / 1.25 / 1.5 / 2.0）")
    ap.add_argument("--scale", type=float, default=C.DEFAULT_SCALE, help="页面缩放")
    ap.add_argument("--pressure", choices=C.PRESSURE_MODES, default="smooth")
    ap.add_argument("--layout", choices=C.LAYOUTS, default="scatter")
    ap.add_argument("--background", choices=("blank", "grid"), default="blank")
    ap.add_argument("--viewport", default="1000x1300")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    C.ensure_offscreen()
    C.ensure_writable_temp()
    os.environ["QT_SCALE_FACTOR"] = str(a.dpr)

    from PySide6.QtWidgets import QApplication

    from src.core.settings import AppSettings
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    vw, vh = (int(x) for x in a.viewport.lower().split("x"))
    ini_dir = tempfile.mkdtemp(prefix="hn-bench-")
    win = MainWindow(settings=AppSettings(path=os.path.join(ini_dir, "bench.ini")))
    canvas = win.canvas
    canvas.resize(vw, vh)
    canvas.background = a.background
    canvas.scale = a.scale

    rows = []
    pixel_size = None
    for n in C.parse_int_list(a.strokes):
        canvas.page_strokes = C.make_strokes(n, pressure=a.pressure, layout=a.layout)
        r = C.bench(canvas.rebuild_pixmap, repeat=a.repeat)
        pm = canvas.page_pixmap
        pixel_size = [pm.width(), pm.height()]
        r["strokes"] = n
        r["points"] = n * C.POINTS_PER_STROKE
        r["ms_per_stroke"] = round(r["best"] / max(1, n) * 1000.0, 4)
        rows.append(r)

    payload = {
        "device": "canvas_rebuild",
        "page_pt": [C.PAGE_W, C.PAGE_H],
        "scale": a.scale,
        "qt_scale_factor": a.dpr,
        "device_pixel_ratio": canvas.devicePixelRatio(),
        "viewport": [vw, vh],
        "page_pixmap_px": pixel_size,
        "background": a.background,
        "pressure": a.pressure,
        "layout": a.layout,
        "repeat": a.repeat,
        "results": rows,
    }

    lines = [
        f"整页重建  scale={a.scale} QT_SCALE_FACTOR={a.dpr} "
        f"dpr={canvas.devicePixelRatio()} pixmap={pixel_size} "
        f"bg={a.background} pressure={a.pressure} layout={a.layout}",
        f"{'笔画':>7} {'点数':>9} {'best(s)':>9} {'median(s)':>10} {'ms/笔画':>9}",
    ]
    for r in rows:
        lines.append(f"{r['strokes']:>7} {r['points']:>9} {r['best']:>9.3f} "
                     f"{r['median']:>10.3f} {r['ms_per_stroke']:>9.3f}")

    C.dump(payload, as_json=a.json, table="\n".join(lines))

    canvas.page_strokes = []
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

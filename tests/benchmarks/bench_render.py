"""渲染微基准：`paint_strokes` 在不同 dpr / 表面 / 压感下的耗时。

这是**算法微基准**（只在离屏 QImage/QPixmap 上跑笔迹），用于比较渲染算法改动的
相对差异；判「用户可感知耗时」请用 `bench_canvas_rebuild.py`。

用法：
    .venv\\Scripts\\python.exe tests/benchmarks/bench_render.py
    .venv\\Scripts\\python.exe tests/benchmarks/bench_render.py --strokes 500,1000 --dpr 1.5 --json
    .venv\\Scripts\\python.exe tests/benchmarks/bench_render.py --pressure quant16 --aa off
"""

from __future__ import annotations

import argparse
import os
import sys

import _common as C

C.configure_stdio()
C.ensure_repo_on_path()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="笔迹绘制微基准")
    ap.add_argument("--strokes", default="100,300,500,1000",
                    help="笔画数列表，逗号分隔")
    ap.add_argument("--dpr", type=float, default=1.0, help="设备像素比（像素面积按平方放大）")
    ap.add_argument("--surface", choices=C.SURFACES, default="image")
    ap.add_argument("--aa", choices=("on", "off"), default="on", help="抗锯齿（画布实际是 on）")
    ap.add_argument("--pressure", choices=C.PRESSURE_MODES, default="smooth")
    ap.add_argument("--layout", choices=C.LAYOUTS, default="scatter")
    ap.add_argument("--scale", type=float, default=C.DEFAULT_SCALE)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    C.ensure_offscreen()
    from PySide6.QtGui import QImage, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    from src.render.stroke_painter import paint_strokes

    QApplication.instance() or QApplication([])

    css_w, css_h = C.PAGE_W * a.scale, C.PAGE_H * a.scale
    pw, ph = int(css_w * a.dpr), int(css_h * a.dpr)
    painter_scale = a.scale * a.dpr

    # 目标表面在计时区间外创建：这里只量「绘制」本身
    if a.surface == "image":
        target = QImage(pw, ph, QImage.Format_ARGB32_Premultiplied)
    else:
        target = QPixmap(pw, ph)

    rows = []
    for n in C.parse_int_list(a.strokes):
        strokes = C.make_strokes(n, pressure=a.pressure, layout=a.layout)

        def run() -> None:
            target.fill(0)
            p = QPainter(target)
            if a.aa == "on":
                p.setRenderHint(QPainter.Antialiasing, True)
                p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            paint_strokes(p, strokes, painter_scale)
            p.end()

        r = C.bench(run, repeat=a.repeat)
        r["strokes"] = n
        r["points"] = n * C.POINTS_PER_STROKE
        r["ms_per_stroke"] = round(r["best"] / max(1, n) * 1000.0, 4)
        rows.append(r)

    payload = {
        "device": "render_micro",
        "page_pt": [C.PAGE_W, C.PAGE_H],
        "scale": a.scale,
        "dpr": a.dpr,
        "pixels": [pw, ph],
        "surface": a.surface,
        "aa": a.aa,
        "pressure": a.pressure,
        "layout": a.layout,
        "repeat": a.repeat,
        "results": rows,
    }

    lines = [
        f"渲染微基准  surface={a.surface} aa={a.aa} pressure={a.pressure} "
        f"layout={a.layout} scale={a.scale} dpr={a.dpr} ({pw}×{ph}px)",
        f"{'笔画':>7} {'点数':>9} {'best(s)':>9} {'median(s)':>10} {'ms/笔画':>9}",
    ]
    for r in rows:
        lines.append(f"{r['strokes']:>7} {r['points']:>9} {r['best']:>9.3f} "
                     f"{r['median']:>10.3f} {r['ms_per_stroke']:>9.3f}")
    C.dump(payload, as_json=a.json, table="\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

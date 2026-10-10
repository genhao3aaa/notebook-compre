"""A3 基准：采样间距（0.8 → 1.2 pt）+ 抬笔 RDP 简化（0.35 pt）省了多少。

口径：用 `raw_handwriting_stream`（细采样 + 手抖 + 平滑压感）模拟真实数位板点流，
分别统计「原始点流 / 只改采样间距 / 采样间距 + RDP」的点数与 `.note` 体积。

用法：
    .venv\\Scripts\\python.exe tests/benchmarks/bench_simplify.py
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

import _common as C

C.ensure_repo_on_path()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A3 采样简化基准")
    ap.add_argument("--strokes", type=int, default=200, help="笔画数（一页的量级）")
    ap.add_argument("--raw-per-stroke", type=int, default=400, help="每条笔画的原始点数")
    ap.add_argument("--tol", type=float, default=0.35)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    C.configure_stdio()
    C.ensure_writable_temp()

    from src.core.note_document import NoteDocument
    from src.core.stroke import Stroke, simplify_stroke

    raw = C.raw_handwriting_stream(a.strokes, seed=5, points_per_stroke=a.raw_per_stroke)

    def build(min_dist: float, tol: float):
        doc = NoteDocument()
        doc.pages = []
        page = doc.add_page()
        total = 0
        for pts in raw:
            filtered = C.filter_min_dist(pts, min_dist)
            s = Stroke(tool="pen", color="#000000", width=3.0, points=filtered)
            if tol > 0:
                simplify_stroke(s, tol)
            total += len(s.points)
            page.strokes.append(s)
        return doc, total

    workdir = tempfile.mkdtemp(prefix="hn-bench-simplify-")
    rows = []
    baseline_total = None
    for label, min_dist, tol in (
        ("raw(0.2pt 原始流)", 0.0, 0.0),
        ("min_dist=0.8（旧）", 0.8, 0.0),
        ("min_dist=1.2", 1.2, 0.0),
        ("min_dist=1.2 + RDP0.35（A3）", 1.2, a.tol),
    ):
        doc, total = build(min_dist, tol)
        path = os.path.join(workdir, f"{abs(hash(label)) % 10**8}.note")
        doc.save(path)
        size_mb = os.path.getsize(path) / 1e6
        ref = baseline_total if baseline_total is not None else total
        rows.append({
            "case": label,
            "points_total": total,
            "points_per_stroke": round(total / max(1, a.strokes), 1),
            "note_mb": round(size_mb, 2),
            "vs_0.8": round(1 - total / ref, 4) if ref else 0.0,
        })
        if min_dist == 0.8 and tol == 0:
            baseline_total = total

    payload = {"device": "simplify", "strokes": a.strokes,
               "raw_points_per_stroke": a.raw_per_stroke,
               "rdp_tol": a.tol, "results": rows}
    lines = [
        f"A3 采样简化（{a.strokes} 笔画 × {a.raw_per_stroke} 原始点）",
        f"{'方案':<28} {'总点数':>9} {'点/笔画':>8} {'体积(MB)':>9} {'点数降幅':>8}",
    ]
    for r in rows:
        lines.append(f"{r['case']:<28} {r['points_total']:>9} {r['points_per_stroke']:>8.1f} "
                     f"{r['note_mb']:>9.2f} {r['vs_0.8']:>7.1%}")
    C.dump(payload, as_json=a.json, table="\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

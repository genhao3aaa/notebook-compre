"""存储基准：`.note`（JSON）保存 / 加载的体积与耗时。

口径与 ROADMAP §1.3 一致：1 页 × 1000 笔画、10 页 × 500 笔画、50 页 × 200 笔画。
写盘用**工作区内的临时目录**（受限沙箱下系统临时目录可能不可写）。

用法：
    .venv\\Scripts\\python.exe tests/benchmarks/bench_storage.py
    .venv\\Scripts\\python.exe tests/benchmarks/bench_storage.py --scales 1x1000 --json
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time

import _common as C

C.configure_stdio()
C.ensure_repo_on_path()

DEFAULT_SCALES = "1x1000,10x500,50x200"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=".note 存储基准")
    ap.add_argument("--scales", default=DEFAULT_SCALES, help="页数x每页笔画数，逗号分隔")
    ap.add_argument("--points", type=int, default=C.POINTS_PER_STROKE)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    C.ensure_writable_temp()
    from src.core.note_document import NoteDocument

    workdir = tempfile.mkdtemp(prefix="hn-bench-storage-")
    rows = []
    try:
        for spec in a.scales.replace(" ", "").split(","):
            if not spec:
                continue
            pages_s, per_page_s = spec.lower().split("x")
            pages, per_page = int(pages_s), int(per_page_s)
            doc = NoteDocument()
            doc.pages = []
            for _ in range(pages):
                page = doc.add_page()
                page.strokes.extend(C.make_strokes(per_page, points_per_stroke=a.points))

            row = {
                "pages": pages,
                "strokes_per_page": per_page,
                "strokes_total": pages * per_page,
                "points_total": pages * per_page * a.points,
            }
            for version, tag in ((2, "v2"), (1, "v1json")):
                path = os.path.join(workdir, f"{pages}x{per_page}_{tag}.note")
                t = time.perf_counter()
                doc.save(path, version=version)
                save = time.perf_counter() - t
                t = time.perf_counter()
                loaded = NoteDocument.load(path)
                load = time.perf_counter() - t
                row[f"{tag}_mb"] = round(os.path.getsize(path) / 1e6, 2)
                row[f"{tag}_save_s"] = round(save, 3)
                row[f"{tag}_load_s"] = round(load, 3)
                row[f"{tag}_ok"] = sum(len(p.strokes) for p in loaded.pages) == pages * per_page
            row["size_ratio"] = round(row["v2_mb"] / row["v1json_mb"], 3) if row["v1json_mb"] else None
            rows.append(row)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    payload = {
        "device": "storage",
        "points_per_stroke": a.points,
        "results": rows,
    }
    lines = [
        f"存储基准（JSON .note，{a.points} 点/笔画）",
        f"{'页数':>5} {'每页笔画':>9} {'总点数':>10} {'v2(MB)':>8} {'v2 save':>8} {'v2 load':>8} "
        f"{'JSON(MB)':>9} {'JSON save':>10} {'v2/JSON':>8}",
    ]
    for r in rows:
        lines.append(f"{r['pages']:>5} {r['strokes_per_page']:>9} {r['points_total']:>10} "
                     f"{r['v2_mb']:>8.2f} {r['v2_save_s']:>8.3f} {r['v2_load_s']:>8.3f} "
                     f"{r['v1json_mb']:>9.2f} {r['v1json_save_s']:>10.3f} {r['size_ratio']:>8.3f}")
    C.dump(payload, as_json=a.json, table="\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

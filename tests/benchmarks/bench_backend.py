"""路线 B 的对拍 + 基准：Python 参考后端 vs Rust（hn_core）后端。

用法
----
    python tests/benchmarks/bench_backend.py                # 对拍 + 计时
    python tests/benchmarks/bench_backend.py --strokes 1000  # 换规模（默认 300 / 1000 / 5000）
    python tests/benchmarks/bench_backend.py --json          # 机器可读（CI 存档）

对拍口径（ROADMAP §6「对拍像素差达标」）
-------------------------------------
- `simplify`：保留点数与 Python 参考实现**完全一致**（RDP 是确定性算法，阈值相同）；
- `hit_test`：候选集合必须**覆盖**参考实现的全表扫描结果（Rust 只做包围盒粗筛，
  精确判定仍在 Python 侧，因此允许「多给候选」但不允许漏）；
- `rasterize`：比较 RGBA 的**墨迹覆盖率**（alpha > 8 的像素占比）与平均绝对差。
  两边抗锯齿实现不同（Qt vs tiny-skia），因此给容差而不是要求逐像素相同。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Dict, List, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from src.render import backend as B                      # noqa: E402
from src.core.stroke import Stroke                       # noqa: E402
from src.core.stroke import stroke_intersects_eraser     # noqa: E402

#: 覆盖率容差（绝对值，百分比）。Qt 的 AA 与 tiny-skia 的 AA 不可能逐像素一致：
#: 每条笔画的边缘都有 1px 左右的半透明带，笔画越密、重叠越多，累计差越大
#: （实测 300 笔画 0.20%、1000 笔画 0.54%、5000 笔画 0.47%）。
COVERAGE_TOL = 0.8        # %
#: alpha 通道平均绝对差容差（0~255）：体现「哪里算墨迹」的一致性
ALPHA_DIFF_TOL = 6.0
#: 墨迹像素上的 RGB 平均绝对差容差（0~255）：主要来自 AA 边缘的灰度差
MEAN_DIFF_TOL = 20.0


def make_strokes(n: int, points: int = 60, width: float = 2.5,
                 seed: int = 7) -> List[Stroke]:
    """造一批合成笔画：带平滑压感（相邻点高度相关，贴近真实数位板）。"""
    rng = np.random.default_rng(seed)
    out: List[Stroke] = []
    for i in range(n):
        x0, y0 = float(rng.uniform(40, 500)), float(rng.uniform(40, 700))
        dx, dy = float(rng.uniform(30, 90)), float(rng.uniform(-40, 40))
        press = 0.35 + 0.3 * rng.random()
        pts = []
        for j in range(points):
            t = j / float(points - 1)
            press = float(np.clip(press + rng.normal(0, 0.02), 0.05, 1.0))
            pts.append((x0 + dx * t + math.sin(t * 6.0) * 6.0, y0 + dy * t, press))
        out.append(Stroke(tool="pen", color="#111111", width=width, points=pts))
    return out


def _timed(fn, repeat: int = 3) -> Tuple[float, object]:
    best = float("inf")
    result = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - t0)
    return best, result


# ---------------------------------------------------------------------- #
# 三个接口的对拍
# ---------------------------------------------------------------------- #
def parity_simplify(strokes: List[Stroke], tol: float = 0.35) -> Dict:
    py, rs = B.PythonBackend(), B.RustBackend(__import__("hn_core"))
    same = 0
    for s in strokes:
        flat = np.asarray([c for p in s.points for c in (p[0], p[1])], dtype=np.float32)
        a = py.simplify(flat, tol)
        b = rs.simplify(flat, tol)
        if len(a) == len(b):
            same += 1
    return {"strokes": len(strokes), "same_kept_points": same,
            "ratio": same / max(1, len(strokes))}


def parity_hit_test(strokes: List[Stroke], radius: float = 12.0) -> Dict:
    py, rs = B.PythonBackend(), B.RustBackend(__import__("hn_core"))
    bounds = np.asarray([s.bounding_rect() for s in strokes], dtype=np.float32)
    rng = np.random.default_rng(11)
    missed = extra = total = 0
    for _ in range(40):
        x = float(rng.uniform(0, 600))
        y = float(rng.uniform(0, 800))
        ref = set(py.hit_test(bounds, x, y, radius))
        got = set(rs.hit_test(bounds, x, y, radius))
        missed += len(ref - got)
        extra += len(got - ref)
        total += len(ref)
    return {"queries": 40, "reference_hits": total, "missing": missed, "extra": extra}


def _coverage(rgba: np.ndarray) -> float:
    return float((rgba[..., 3] > 8).mean() * 100.0)


def parity_rasterize(strokes: List[Stroke], size: Tuple[int, int] = (420, 420),
                     scale: float = 1.0) -> Dict:
    """比较两张 RGBA 图。

    口径说明：两边都用「透明背景」，完全透明的像素其 RGB 是**无意义的**
    （Qt 保留填充色，tiny-skia 预乘还原后是 0），所以：
    - 覆盖率按 alpha > 8 统计（这才是「墨迹画了多少」）；
    - RGB 差异只在**双方都判定为墨迹**的像素上比较（AA 边缘的灰度差由此体现）。
    """
    w, h = size
    packed = B.pack_strokes(strokes, scale)
    py_img = B.PythonBackend().rasterize(packed, w, h, (255, 255, 255, 0))
    rs_img = B.RustBackend(__import__("hn_core")).rasterize(packed, w, h, (255, 255, 255, 0))

    alpha_py, alpha_rs = py_img[..., 3].astype(np.int16), rs_img[..., 3].astype(np.int16)
    ink = (alpha_py > 8) & (alpha_rs > 8)
    cov_py, cov_rs = _coverage(py_img), _coverage(rs_img)
    alpha_diff = float(np.abs(alpha_py - alpha_rs).mean())
    rgb_diff = float(np.abs(py_img[..., :3].astype(np.int16)[ink]
                            - rs_img[..., :3].astype(np.int16)[ink]).mean()) if ink.any() else 0.0
    return {
        "coverage_python_pct": round(cov_py, 3),
        "coverage_rust_pct": round(cov_rs, 3),
        "coverage_delta_pct": round(abs(cov_py - cov_rs), 3),
        "alpha_mean_diff": round(alpha_diff, 3),
        "ink_pixels": int(ink.sum()),
        "ink_rgb_mean_diff": round(rgb_diff, 3),
        "coverage_ok": abs(cov_py - cov_rs) <= COVERAGE_TOL,
        "diff_ok": alpha_diff <= ALPHA_DIFF_TOL and rgb_diff <= MEAN_DIFF_TOL,
    }


# ---------------------------------------------------------------------- #
# 计时
# ---------------------------------------------------------------------- #
def timings(strokes: List[Stroke], size: Tuple[int, int] = (420, 420)) -> Dict:
    py, rs = B.PythonBackend(), B.RustBackend(__import__("hn_core"))
    w, h = size
    packed = B.pack_strokes(strokes, 1.0)
    flat = packed["xy"]
    bounds = packed["bounds"]
    out = {}

    t_py, _ = _timed(lambda: py.simplify(flat, 0.35))
    t_rs, _ = _timed(lambda: rs.simplify(flat, 0.35))
    out["simplify_ms"] = {"python": round(t_py * 1e3, 3), "rust": round(t_rs * 1e3, 3)}

    x, y = float(flat[0]), float(flat[1])

    def py_hits():
        return [i for i in py.hit_test(bounds, x, y, 12.0)]

    def rs_hits():
        return [i for i in rs.hit_test(bounds, x, y, 12.0)]

    t_py, _ = _timed(py_hits, repeat=5)
    t_rs, _ = _timed(rs_hits, repeat=5)
    out["hit_test_ms"] = {"python": round(t_py * 1e3, 3), "rust": round(t_rs * 1e3, 3)}

    t_py, _ = _timed(lambda: py.rasterize(packed, w, h, (255, 255, 255, 0)), repeat=2)
    t_rs, _ = _timed(lambda: rs.rasterize(packed, w, h, (255, 255, 255, 0)), repeat=2)
    out["rasterize_ms"] = {"python": round(t_py * 1e3, 2), "rust": round(t_rs * 1e3, 2)}
    out["speedup"] = {k: round(v["python"] / max(v["rust"], 1e-9), 2)
                      for k, v in out.items() if isinstance(v, dict)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="路线 B 后端对拍 + 基准")
    ap.add_argument("--strokes", type=int, nargs="*", default=[300, 1000, 5000])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report: Dict = {"backend": B.get_backend().name, "rust_available": B.rust_available(),
                    "cases": []}
    for n in args.strokes:
        strokes = make_strokes(n)
        case = {"strokes": n,
                "simplify": parity_simplify(strokes),
                "hit_test": parity_hit_test(strokes),
                "rasterize": parity_rasterize(strokes),
                "timing": timings(strokes)}
        report["cases"].append(case)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"后端：{report['backend']}（Rust 可用：{report['rust_available']}）")
    for c in report["cases"]:
        r = c["rasterize"]
        print(f"\n=== {c['strokes']} 笔画 ===")
        print(f"  simplify : Rust 保留点与参考实现一致 {c['simplify']['same_kept_points']}"
              f"/{c['simplify']['strokes']} 条")
        print(f"  hit_test : 参考命中 {c['hit_test']['reference_hits']} 个，"
              f"漏 {c['hit_test']['missing']}，多给候选 {c['hit_test']['extra']}")
        print(f"  rasterize: 覆盖率 {r['coverage_python_pct']}% vs {r['coverage_rust_pct']}%"
              f"（差 {r['coverage_delta_pct']}%；alpha 均值差 {r['alpha_mean_diff']}，"
              f"墨迹 RGB 均值差 {r['ink_rgb_mean_diff']} / {r['ink_pixels']} 像素）"
              f" → {'达标' if r['coverage_ok'] and r['diff_ok'] else '不达标'}")
        t = c["timing"]
        print(f"  耗时     : simplify {t['simplify_ms']} "
              f"hit_test {t['hit_test_ms']} rasterize {t['rasterize_ms']}")
        print(f"  加速     : {t['speedup']}")
    ok = all(c["rasterize"]["coverage_ok"] and c["rasterize"]["diff_ok"]
             and c["hit_test"]["missing"] == 0
             and c["simplify"]["same_kept_points"] == c["simplify"]["strokes"]
             for c in report["cases"])
    print("\n对拍结论：" + ("全部达标" if ok else "存在不达标项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

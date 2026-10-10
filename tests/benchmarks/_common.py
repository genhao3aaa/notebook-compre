"""基准脚本共用工具：合成笔画、计时、JSON 输出。

口径约定（与 ROADMAP §11.3 一致）
----------------------------------
1. **真机口径**：`bench_canvas_rebuild.py` 走真实页面重建路径（含背景 + 笔画 + dpr），
   这是唯一可以用来判定「用户可感知耗时」的基准；`bench_render.py` 只是算法微基准。
2. **压感**：默认 `continuous`（连续随机，模拟真实数位板）；`quant16` / `fixed12` 仅作对照，
   避免「量化后测出来的数字比真实输入好看」。
3. **冷启动**：必须新进程，见 `bench_startup.py`（同进程 re-import 是 no-op，测不出东西）。
4. **可复现**：所有基准固定随机种子，只依赖仓库内代码，不读用户设置（INI 走临时文件）。
"""

from __future__ import annotations

import json
import math
import os
import random
import statistics
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def configure_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8（Windows 控制台默认代码页会把中文表格打成乱码）。

    **必须在脚本的 `main()` 里显式调用**，不能在 import 时执行：
    pytest 用自己的捕获对象替换 sys.stdout，import 期改编码会让捕获回读失败
    （曾导致 111 个 `UnicodeDecodeError`）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # pragma: no cover - 已被替换/不支持时忽略
            pass

#: 页面尺寸（A4，pt）与 ROADMAP §1.1 一致
PAGE_W = 595.28
PAGE_H = 841.89
#: ROADMAP §1.1 使用的缩放（A4 适应 1000×1300 视口）
DEFAULT_SCALE = 1.49
#: 每条笔画的采样点数（ROADMAP §1.1：1000 笔画 = 12 万点）
POINTS_PER_STROKE = 120

PRESSURE_MODES = ("smooth", "continuous", "quant16", "fixed12")
#: scatter/dense = ROADMAP §1.1 口径的长笔画（便于与原文对照）；
#: realistic = 真实手写长度（约 40 pt/笔，一页十几行），A2 脏矩形/命中率用它才有意义
LAYOUTS = ("scatter", "dense", "realistic")
SURFACES = ("image", "pixmap")


def ensure_repo_on_path() -> None:
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)


def ensure_offscreen() -> None:
    """基准一律离屏运行（不弹窗、不抢焦点）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def ensure_writable_temp() -> None:
    """受限环境下 `tempfile.mkdtemp()` 建的 0o700 目录可能不可写，复用项目里的兼容层。"""
    ensure_repo_on_path()
    try:
        from env_compat import ensure_writable_tempdir

        ensure_writable_tempdir()
    except Exception:  # pragma: no cover - 兼容层缺失时不影响普通环境
        pass


def make_strokes(count: int, *, points_per_stroke: int = POINTS_PER_STROKE,
                 pressure: str = "smooth", layout: str = "scatter",
                 width: float = 3.0, seed: int = 7, page_w: float = PAGE_W,
                 page_h: float = PAGE_H):
    """生成 `count` 条合成笔画（固定种子，可复现）。

    - `layout="scatter"`：长笔画（步长 5 pt × 120 点 ≈ 200 pt），ROADMAP §1.1 口径
    - `layout="dense"`：笔画挤在同一小块区域（高重叠，测描边缓存最坏情况）
    - `layout="realistic"`：真实手写长度（约 40 pt/笔，沿行基线），A2 脏矩形命中率用它
    - `pressure="smooth"`（默认）：一阶自回归的平滑曲线，**接近真实数位板**
      （均匀随机压感在现实里不存在：相邻采样点的压力高度相关）
    - `pressure="continuous"`：每点独立均匀随机（最坏情况，用于压测）
    - `pressure="quant16"` / `"fixed12"`：量化压感，仅用于对照
    """
    ensure_repo_on_path()
    from src.core.stroke import Stroke

    if pressure not in PRESSURE_MODES:
        raise ValueError(f"pressure 必须是 {PRESSURE_MODES} 之一")
    if layout not in LAYOUTS:
        raise ValueError(f"layout 必须是 {LAYOUTS} 之一")

    rng = random.Random(seed)
    strokes = []
    lines = max(1, int(page_h // 60))          # realistic：每 60 pt 一行
    for i in range(count):
        if layout == "dense":
            x = rng.uniform(page_w * 0.2, page_w * 0.8)
            y = rng.uniform(page_h * 0.2, page_h * 0.8)
            step = 2.0
        elif layout == "realistic":
            # 真实手写：笔画长度约 40 pt（120 点 × 0.35 步长），走在某一行的基线上
            x = rng.uniform(page_w * 0.08, page_w * 0.9)
            y = 40.0 + (i % lines) * 60.0 + rng.uniform(-6, 6)
            step = 0.35
        else:
            x = rng.uniform(page_w * 0.1, page_w * 0.5)
            y = rng.uniform(page_h * 0.1, page_h * 0.8)
            step = 5.0
        p = rng.uniform(0.3, 0.9)
        pts = []
        for j in range(points_per_stroke):
            x = min(max(x + rng.uniform(-step, step), 1.0), page_w - 1.0)
            y = min(max(y + rng.uniform(-step, step), 1.0), page_h - 1.0)
            if pressure == "smooth":
                # 一阶自回归：p ← 0.92p + 0.08·目标 + 小噪声，再夹到 [0.1, 1.0]
                p = 0.92 * p + 0.08 * rng.uniform(0.1, 1.0) + rng.gauss(0, 0.01)
                p = min(1.0, max(0.1, p))
            elif pressure == "continuous":
                p = rng.uniform(0.15, 1.0)
            elif pressure == "quant16":
                p = rng.randint(1, 16) / 16.0
            else:  # fixed12：每笔只有 12 个离散宽度（旧复测脚本的口径）
                p = 0.2 + 0.8 * ((j % 12) / 11.0)
            pts.append((round(x, 2), round(y, 2), round(p, 4)))
        strokes.append(Stroke(tool="pen", color="#000000", width=width, points=pts))
    return strokes


def bench(fn, *, repeat: int = 3, warmup: int = 1) -> dict:
    """跑 `warmup` 次预热 + `repeat` 次计时，返回 best / median / worst。"""
    for _ in range(warmup):
        fn()
    runs = []
    for _ in range(max(1, repeat)):
        t = time.perf_counter()
        fn()
        runs.append(time.perf_counter() - t)
    return {
        "best": min(runs),
        "median": statistics.median(runs),
        "worst": max(runs),
        "runs": [round(r, 4) for r in runs],
    }


def raw_handwriting_stream(count: int, *, seed: int = 7, step: float = 0.2,
                           points_per_stroke: int = 400, tremor: float = 0.15,
                           page_w: float = PAGE_W, page_h: float = PAGE_H):
    """模拟**数位板原始点流**（细采样 + 轻微手抖 + 平滑压感），返回若干条折线。

    用于 A3（采样间距 / RDP 简化）：真实设备每几毫秒发一个点，一条 40 pt 的笔画
    会有几百个原始点；基准要能反映「过滤 + 简化」前后差多少。
    """
    rng = random.Random(seed)
    lines = max(1, int(page_h // 60))
    strokes = []
    for i in range(count):
        x = rng.uniform(page_w * 0.08, page_w * 0.75)
        y = 40.0 + (i % lines) * 60.0 + rng.uniform(-6, 6)
        heading = rng.uniform(-0.2, 0.2)
        bend = rng.uniform(-0.004, 0.004)
        pressure = rng.uniform(0.3, 0.9)
        pts = []
        for j in range(points_per_stroke):
            heading += bend + rng.gauss(0, 0.004)
            x += step * math.cos(heading)
            y += step * math.sin(heading) + rng.gauss(0, tremor * 0.35)
            pressure = min(1.0, max(0.1, 0.95 * pressure + 0.05 * rng.uniform(0.1, 1.0)
                                     + rng.gauss(0, 0.006)))
            pts.append((round(x, 3), round(y, 3), round(pressure, 4)))
        strokes.append(pts)
    return strokes


def filter_min_dist(points, min_dist: float):
    """复刻工具里的最小间距过滤（页面坐标）。"""
    if not points:
        return []
    out = [points[0]]
    d2 = min_dist * min_dist
    for p in points[1:]:
        last = out[-1]
        if (p[0] - last[0]) ** 2 + (p[1] - last[1]) ** 2 >= d2:
            out.append(p)
    return out


def dump(payload: dict, *, as_json: bool, table: str = "") -> None:
    """统一输出：`--json` 时打 JSON（给脚本/CI 消费），否则打人类可读表格。"""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if table:
            print(table)
        print(json.dumps(payload, ensure_ascii=False))


def parse_int_list(text: str) -> list:
    return [int(x) for x in text.replace(" ", "").split(",") if x]

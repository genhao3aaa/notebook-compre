# 基准脚本（ROADMAP P0）

这些脚本是 ROADMAP 全部数字的唯一来源。**改优化前后都要跑一遍并存下 JSON**，
否则「12 倍」「0.24 s」这类结论无法复核（ROADMAP §1.1 就是这么变成不可复现的）。

## 口径（与 ROADMAP §11.3 一致）

| 问题 | 约定 |
|---|---|
| 渲染耗时以哪个为准？ | `bench_canvas_rebuild.py`（真实 `PageCanvas.rebuild_pixmap`，含背景/变换/dpr）。`bench_render.py` 只是算法微基准。 |
| dpr 怎么算？ | 通过 `QT_SCALE_FACTOR` 生效，输出里会打印 `page_pixmap_px` 与 `device_pixel_ratio`，防止「以为测了 1.5 其实还是 1」。 |
| 压感用哪种？ | 默认 `smooth`（一阶自回归，贴近真实数位板）。`continuous`/`quant16`/`fixed12` 只做对照——用美化过的输入测出来的数字不算数。 |
| 笔画长度用哪种？ | 默认 `scatter`（ROADMAP §1.1 的约 200 pt 长笔画，便于与原文对照）；`realistic`（约 40 pt/笔，沿行基线）才是真实手写长度，A2 脏矩形命中率必须用它；`dense` 是高重叠上下界。 |
| 冷启动怎么算？ | 必须新进程：`bench_startup.py`（同进程 re-import 是 no-op）。 |
| 随机性 | 固定种子，只依赖仓库代码，不读用户真实设置。 |

## 用法

```bash
.venv\Scripts\python.exe tests/benchmarks/bench_render.py            # 微基准
.venv\Scripts\python.exe tests/benchmarks/bench_canvas_rebuild.py    # 真机口径（判定用这个）
.venv\Scripts\python.exe tests/benchmarks/bench_canvas_rebuild.py --dpr 1.5 --strokes 1000,3000
.venv\Scripts\python.exe tests/benchmarks/bench_incremental.py       # 整页重建 vs 单笔增量重绘
.venv\Scripts\python.exe tests/benchmarks/bench_simplify.py      # A3 采样简化（点数/体积）
.venv\Scripts\python.exe tests/benchmarks/bench_storage.py
.venv\Scripts\python.exe tests/benchmarks/bench_startup.py --repeat 5
```

所有脚本都支持 `--json`，便于存档与 CI 比较。示例：

```bash
.venv\Scripts\python.exe tests/benchmarks/bench_canvas_rebuild.py --json > bench_before.json
# ... 改代码 ...
.venv\Scripts\python.exe tests/benchmarks/bench_canvas_rebuild.py --json > bench_after.json
```

## 快速守门

`tests/test_perf_budget.py` 是 pytest 里的防退化闸门（宽阈值、跑得快）：

```bash
.venv\Scripts\python.exe -m pytest -q tests/test_perf_budget.py
HN_PERF_SCALE=2.0 ...   # 慢机器整体放宽
HN_SKIP_PERF=1 ...      # 跳过
```

## 历史（供对照）

| 日期 | 备注 |
|---|---|
| 2026-09-08 | ROADMAP §1.1 的原始数据（500 笔画 3.03 s / 1000 笔画 10.43 s），**脚本未入库**，现已无法复现 |
| 2026-09-29 | 复核复测为线性（500 ≈ 0.40–0.50 s、1000 ≈ 0.81–1.01 s）；脚本入库，见 `ROADMAP_REVIEW.md` §1 |

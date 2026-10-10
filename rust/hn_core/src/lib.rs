//! `hn_core`：Episode2（ROADMAP 路线 B）的 Rust 核心。
//!
//! 只放**纯函数**：不碰 Qt、不碰 PDF、不碰文件系统。这样它才可能与 Python
//! 参考实现做逐点/逐像素对拍，也才能在出问题时用 `HN_RENDER_BACKEND=python` 一键回退。
//!
//! 三个函数与 `src/render/backend.py` 的 `RenderBackend` 契约一一对应：
//!
//! | Rust | 契约 | 说明 |
//! |---|---|---|
//! | `simplify` | `simplify(xy, tol)` | RDP，输入/输出都是 x,y 交错的 `f32` 缓冲 |
//! | `hit_test` | `hit_test(bounds, x, y, radius)` | 包围盒 R 树，返回**候选**笔画下标 |
//! | `rasterize` | `rasterize(...)` | tiny-skia 填充变宽轮廓，返回直通 RGBA 字节 |
//!
//! 数据一律走 numpy 缓冲（`PyReadonlyArray1` / `into_pyarray`），不做逐点跨语言调用。
//!
//! 几何口径与 Python 侧 `src/render/stroke_painter.py` 保持一致（半宽下限 0.25 CSS px、
//! 圆头 6 段折线、压感在相邻采样点之间线性过渡），因此两边的墨迹覆盖率可以用小误差对拍。

use numpy::{IntoPyArray, PyArray1, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::prelude::*;
use pyo3::types::PyModule;
use rstar::{RTree, RTreeObject, AABB};
use tiny_skia::{Color, FillRule, Paint, PathBuilder, Pixmap, Transform};

/// 半宽下限（CSS px），与 `stroke_painter._MIN_HALF_WIDTH` 一致。
const MIN_HALF_WIDTH: f32 = 0.25;
/// 圆头采样段数，与 `stroke_painter.CAP_SEGMENTS` 一致。
const CAP_SEGMENTS: usize = 6;

/// 点到线段距离（RDP 用）。
fn point_seg_dist(px: f64, py: f64, ax: f64, ay: f64, bx: f64, by: f64) -> f64 {
    let (dx, dy) = (bx - ax, by - ay);
    let len2 = dx * dx + dy * dy;
    if len2 <= 1e-18 {
        return ((px - ax).powi(2) + (py - ay).powi(2)).sqrt();
    }
    let t = (((px - ax) * dx + (py - ay) * dy) / len2).clamp(0.0, 1.0);
    let (qx, qy) = (ax + t * dx, ay + t * dy);
    ((px - qx).powi(2) + (py - qy).powi(2)).sqrt()
}

/// 核心版本（Python 侧用来判断扩展是否可用 / 打印日志）。
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// RDP 简化：`xy` 是 x,y 交错的 `f32`，返回同样式（首末点必定保留）。
#[pyfunction]
fn simplify<'py>(py: Python<'py>, xy: PyReadonlyArray1<'py, f32>, tol: f32)
    -> Bound<'py, PyArray1<f32>> {
    let flat = xy.as_slice().unwrap_or(&[]);
    let n = flat.len() / 2;
    let mut keep = vec![false; n];
    if n > 0 {
        keep[0] = true;
        keep[n - 1] = true;
    }
    if n > 2 {
        // 用显式栈而不是递归：一条笔画可以有上万个点
        let mut stack: Vec<(usize, usize)> = vec![(0, n - 1)];
        while let Some((a, b)) = stack.pop() {
            if b <= a + 1 {
                continue;
            }
            let (ax, ay) = (flat[2 * a] as f64, flat[2 * a + 1] as f64);
            let (bx, by) = (flat[2 * b] as f64, flat[2 * b + 1] as f64);
            let mut best = (a, -1.0f64);
            for i in (a + 1)..b {
                let d = point_seg_dist(flat[2 * i] as f64, flat[2 * i + 1] as f64, ax, ay, bx, by);
                if d > best.1 {
                    best = (i, d);
                }
            }
            if best.1 > tol as f64 {
                keep[best.0] = true;
                stack.push((a, best.0));
                stack.push((best.0, b));
            }
        }
    }
    let mut out: Vec<f32> = Vec::with_capacity(n);
    for i in 0..n {
        if keep[i] {
            out.push(flat[2 * i]);
            out.push(flat[2 * i + 1]);
        }
    }
    out.into_pyarray(py)
}

struct BBoxEntry {
    aabb: AABB<[f64; 2]>,
    idx: usize,
}

impl RTreeObject for BBoxEntry {
    type Envelope = AABB<[f64; 2]>;
    fn envelope(&self) -> Self::Envelope {
        self.aabb
    }
}

/// 包围盒命中测试：`bounds` 是 `(n, 4)` 的 `[x0, y0, x1, y1]`，返回**候选**下标（升序）。
///
/// 为什么只返回候选：橡皮的精确判据是「点到笔画线段距离 ≤ 半径」，几何判定仍放在
/// Python 侧（`stroke_intersects_eraser`）——这里只负责把 O(n) 全表扫描压到
/// O(log n + k)，正确性完全由对拍测试保证。
#[pyfunction]
fn hit_test<'py>(py: Python<'py>, bounds: PyReadonlyArray2<'py, f32>, x: f64, y: f64, radius: f64)
    -> Bound<'py, PyArray1<i64>> {
    let arr = bounds.as_array();
    let n = arr.nrows();
    let mut entries: Vec<BBoxEntry> = Vec::with_capacity(n);
    for i in 0..n {
        let (x0, y0) = (arr[[i, 0]] as f64, arr[[i, 1]] as f64);
        let (x1, y1) = (arr[[i, 2]] as f64, arr[[i, 3]] as f64);
        entries.push(BBoxEntry {
            aabb: AABB::from_corners([x0 - radius, y0 - radius], [x1 + radius, y1 + radius]),
            idx: i,
        });
    }
    let tree = RTree::bulk_load(entries);
    let mut hits: Vec<i64> = tree
        .locate_in_envelope_intersecting(&AABB::from_point([x, y]))
        .map(|e| e.idx as i64)
        .collect();
    hits.sort_unstable();
    hits.into_pyarray(py)
}

/// 采样圆头（与 Python `_cap_points` 同一套几何：从 `from` 出发、扫过 180°、中点在正前方）。
fn cap_points(cx: f32, cy: f32, radius: f32, from: f32, forward: f32, segs: usize, pts: &mut Vec<(f32, f32)>) {
    let mut delta = forward - from;
    while delta <= -std::f32::consts::PI {
        delta += 2.0 * std::f32::consts::PI;
    }
    while delta > std::f32::consts::PI {
        delta -= 2.0 * std::f32::consts::PI;
    }
    let sweep = 2.0 * delta;
    for j in 1..segs.max(1) {
        let a = from + sweep * (j as f32 / segs as f32);
        pts.push((cx + a.cos() * radius, cy + a.sin() * radius));
    }
}

/// 变宽轮廓（与 `stroke_painter.build_pressure_outline` 同口径）。
fn build_outline(pts: &[f32], pressure: &[f32], a: usize, b: usize, width: f32) -> Option<tiny_skia::Path> {
    let n = b - a;
    if n < 2 {
        return None;
    }
    let mut xs = Vec::with_capacity(n);
    let mut ys = Vec::with_capacity(n);
    let mut half = Vec::with_capacity(n);
    for i in a..b {
        xs.push(pts[2 * i]);
        ys.push(pts[2 * i + 1]);
        let p = if pressure.is_empty() { 1.0 } else { pressure[i] };
        half.push((width * p / 2.0).max(MIN_HALF_WIDTH));
    }

    let mut dirs: Vec<(f32, f32)> = Vec::with_capacity(n);
    for i in 0..n {
        let (mut dx, mut dy) = if i == 0 {
            (xs[1] - xs[0], ys[1] - ys[0])
        } else if i == n - 1 {
            (xs[n - 1] - xs[n - 2], ys[n - 1] - ys[n - 2])
        } else {
            (xs[i + 1] - xs[i - 1], ys[i + 1] - ys[i - 1])
        };
        let mut dist = (dx * dx + dy * dy).sqrt();
        if dist < 1e-6 {
            let (px, py) = dirs.last().copied().unwrap_or((1.0, 0.0));
            dx = px;
            dy = py;
            dist = (dx * dx + dy * dy).sqrt().max(1e-6);
        }
        dirs.push((dx / dist, dy / dist));
    }

    let mut left: Vec<(f32, f32)> = Vec::with_capacity(n);
    let mut right: Vec<(f32, f32)> = Vec::with_capacity(n);
    for i in 0..n {
        let (ux, uy) = dirs[i];
        let (nx, ny) = (-uy, ux);
        let hw = half[i];
        left.push((xs[i] + nx * hw, ys[i] + ny * hw));
        right.push((xs[i] - nx * hw, ys[i] - ny * hw));
    }

    let mut pb = PathBuilder::new();
    pb.move_to(left[0].0, left[0].1);
    for q in &left[1..] {
        pb.line_to(q.0, q.1);
    }

    let (ux, uy) = dirs[n - 1];
    let end_angle = uy.atan2(ux);
    let mut cap: Vec<(f32, f32)> = Vec::new();
    cap_points(xs[n - 1], ys[n - 1], half[n - 1], end_angle + std::f32::consts::FRAC_PI_2,
               end_angle, CAP_SEGMENTS, &mut cap);
    for q in &cap {
        pb.line_to(q.0, q.1);
    }
    pb.line_to(right[n - 1].0, right[n - 1].1);
    for q in right[..n - 1].iter().rev() {
        pb.line_to(q.0, q.1);
    }

    let (ux0, uy0) = dirs[0];
    let start_back = (-uy0).atan2(-ux0);
    cap.clear();
    cap_points(xs[0], ys[0], half[0], start_back + std::f32::consts::FRAC_PI_2,
               start_back, CAP_SEGMENTS, &mut cap);
    for q in &cap {
        pb.line_to(q.0, q.1);
    }
    pb.close();
    pb.finish()
}

fn paint_of(argb: u32) -> Paint<'static> {
    let a = ((argb >> 24) & 0xFF) as u8;
    let r = ((argb >> 16) & 0xFF) as u8;
    let g = ((argb >> 8) & 0xFF) as u8;
    let b = (argb & 0xFF) as u8;
    let mut paint = Paint::default();
    paint.set_color(Color::from_rgba8(r, g, b, a));
    paint.anti_alias = true;
    paint
}

/// 把笔画光栅化成 RGBA 字节（直通 `QImage(Format_RGBA8888)` 的内存布局）。
///
/// - `xy`：所有笔画串联的 x,y（CSS px，调用方已经乘过 scale）；
/// - `pressure`：每个点的压感（可传空数组 → 全部按 1.0）；
/// - `offsets`：`n + 1` 个下标，第 s 条笔画是 `offsets[s]..offsets[s+1]`；
/// - `widths`：每条笔画的宽度（CSS px）；`colors`：`0xAARRGGBB`。
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn rasterize<'py>(
    py: Python<'py>,
    xy: PyReadonlyArray1<'py, f32>,
    pressure: PyReadonlyArray1<'py, f32>,
    offsets: PyReadonlyArray1<'py, u32>,
    widths: PyReadonlyArray1<'py, f32>,
    colors: PyReadonlyArray1<'py, u32>,
    width_px: u32,
    height_px: u32,
    bg: (u8, u8, u8, u8),
) -> PyResult<Bound<'py, PyArray1<u8>>> {
    let pts = xy.as_slice().unwrap_or(&[]);
    let press = pressure.as_slice().unwrap_or(&[]);
    let offs = offsets.as_slice().unwrap_or(&[]);
    let ws = widths.as_slice().unwrap_or(&[]);
    let cs = colors.as_slice().unwrap_or(&[]);

    let mut pixmap = Pixmap::new(width_px, height_px)
        .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("画布尺寸非法（0 或过大）"))?;
    pixmap.fill(Color::from_rgba8(bg.0, bg.1, bg.2, bg.3));

    let strokes = offs.len().saturating_sub(1);
    for s in 0..strokes {
        let (a, b) = (offs[s] as usize, offs[s + 1] as usize);
        if b <= a {
            continue;
        }
        let paint = paint_of(*cs.get(s).unwrap_or(&0xFF000000));
        if b - a == 1 {
            // 单点：一个圆点（半径 = 半宽）
            let (cx, cy) = (pts[2 * a], pts[2 * a + 1]);
            let p = if press.is_empty() { 1.0 } else { press[a] };
            let r = (*ws.get(s).unwrap_or(&1.0) * p / 2.0).max(MIN_HALF_WIDTH);
            let mut pb = PathBuilder::new();
            pb.push_circle(cx, cy, r);
            if let Some(path) = pb.finish() {
                pixmap.fill_path(&path, &paint, FillRule::Winding, Transform::identity(), None);
            }
            continue;
        }
        if let Some(path) = build_outline(pts, press, a, b, *ws.get(s).unwrap_or(&1.0)) {
            pixmap.fill_path(&path, &paint, FillRule::Winding, Transform::identity(), None);
        }
    }

    // tiny-skia 内部是**预乘** alpha；这里还原成直通 RGBA，方便直接喂 QImage
    let mut out: Vec<u8> = Vec::with_capacity((width_px * height_px * 4) as usize);
    for px in pixmap.pixels() {
        let c = px.demultiply();
        out.push(c.red());
        out.push(c.green());
        out.push(c.blue());
        out.push(c.alpha());
    }
    Ok(out.into_pyarray(py))
}

#[pymodule]
fn hn_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(simplify, m)?)?;
    m.add_function(wrap_pyfunction!(hit_test, m)?)?;
    m.add_function(wrap_pyfunction!(rasterize, m)?)?;
    Ok(())
}

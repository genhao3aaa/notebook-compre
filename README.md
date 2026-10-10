# 手写笔记本与 PDF 批注程序

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-0078d4.svg)](#系统要求)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Rust](https://img.shields.io/badge/rust-optional-orange.svg)](#rust-加速核心可选)

一款运行在 **Windows 10 / 11** 上的桌面手写程序：新建手写笔记、打开 PDF 并直接在页面上批注。
外壳使用 **PySide6**，PDF 引擎使用 **PyMuPDF**，可选接入 **Rust 渲染核心** 提升压感笔迹与命中测试性能。

> A Windows desktop app for handwriting notes and PDF annotation, built with PySide6 + PyMuPDF,
> with an optional Rust rendering core.

---

## 目录

- [功能特性](#功能特性)
- [系统要求](#系统要求)
- [安装](#安装)
- [运行](#运行)
- [使用说明](#使用说明)
- [Rust 加速核心（可选）](#rust-加速核心可选)
- [打包为可执行文件](#打包为可执行文件)
- [测试](#测试)
- [项目结构](#项目结构)
- [设计原则](#设计原则)
- [环境变量](#环境变量)
- [已知限制](#已知限制)
- [性能](#性能)
- [贡献](#贡献)
- [许可证](#许可证)

---

## 功能特性

### A. 空白手写笔记

- 新建笔记：默认 A4 纵向，可选 **网格 / 横线 / 点阵** 背景。
- **背景颜色**：8 种预设纸色（白 / 米黄 / 护眼绿 / 淡蓝 / 淡粉 / 浅灰 / 深灰 / 夜黑）+ 自定义取色；
  深色底上网格 / 横线自动变为浅色，缩略图与导出 PDF 同步生效。
- 多页管理：翻页、添加页、删除页、页码跳转。
- 画笔工具：钢笔 / **荧光笔** / 橡皮擦，颜色、笔宽、撤销 / 重做、清空当前页。
- **常用色快捷盘**：点工具栏色块弹出常用色（钢笔 12 色 / 荧光笔 8 色半透明），**单击即换色**，
  键盘 `1`~`9` 也行；自动记住「最近使用」；完整调色板（取色器 / 色轮 / 十六进制 / 透明度）
  收在弹窗里的「更多颜色…」。
- 输入方式：鼠标拖拽书写；支持数位板 / 触控笔压感（自动识别 `tabletEvent`）。
- 笔迹渲染：平滑曲线 + 抗锯齿；书写时增量绘制，单笔延迟低。
- 画布操作：`Ctrl + 滚轮` 缩放；`空格` 或 `中键` 拖拽平移。
- 保存：自定义 `.note` 格式（**v2 二进制容器**：magic + 元数据 + 列式点数据 + CRC32 + 原子写）。

### B. PDF 批注与阅读

- 打开 PDF：按页渲染为高清图像显示。
- **书签 / 目录**：侧边栏「目录」标签展示多级书签树，点击跳转；自动高亮当前页所在书签。
- **全文搜索**：侧边栏「搜索」标签，后台线程检索不卡界面；结果列表显示页码与上下文，
  支持区分大小写与全词匹配。
- **页码跳转**：页码框输入后回车跳转、`Ctrl+G` 跳转对话框、`PgUp / PgDn / ← / → / Home / End` 键盘翻页。
- **连续阅读 / 单页阅读**：工具栏最右侧按钮或 `Ctrl+Shift+L` 切换。连续模式纵向排布整篇滚动阅读。
- **阅读进度自动恢复**：关掉再打开同一份 PDF，直接回到上次读到的那一页。
- **视图模式**：适应页面（`Ctrl+0`）/ 适应宽度（`Ctrl+Shift+9`）/ 实际大小 100%（`Ctrl+Shift+1`）。
- **页面旋转**：向左 / 向右旋转 90°（`Ctrl+Shift+←/→`），批注坐标始终存原始页面坐标，旋转不影响批注位置与导出。
- **文档信息 / 复制文本**：查看标题、作者、页数、加密状态；一键复制当前页文本（`Ctrl+Shift+C`）。
- 批注层：手写批注叠加在 PDF 之上，与原文分离保存。
- 保存：默认保存为 PDF 同名的 `.annot` 文件（**v2 二进制容器** + PDF 的 MD5 校验），再次打开自动加载；
  写盘在后台线程完成，大文档不卡界面。
- 导出：将带批注的 PDF 导出为新 PDF，批注烧录到页面（基于页面坐标，位置精确）。
- 性能：PDF 页面 **异步渲染** + **渲染缓存**，避免 UI 卡顿。
- 坐标系统：批注坐标基于 **PDF points**，显示按缩放因子映射，导出无需换算。

### C. 浏览器式多标签

- **任意混开**：多个笔记、多个 PDF、笔记与 PDF 混合，全部可同时打开。
- **切换即时**：每个标签独占一个画布，切换只是把某一页显示出来（O(1)）——PDF 不重新解析，
  缩略图不重排，**页码、缩放、滚动位置、撤销栈原样保留**。
- **状态完全隔离**：A 标签的笔画、页码、缩放、背景色、未保存标记，绝不会串到 B 标签。
- **同一文件不重复开**：已经打开的 PDF / 笔记再点一次，会切到已有标签而不是开成两份。
- **标签可拖成独立窗口**：把标签拖出标签栏 → 新窗口；拖到另一个窗口的标签栏 → 合并过去。
- **会话恢复**：退出时记住打开的标签，下次启动自动恢复（文件被删则跳过，不会卡住启动）。

### D. 界面

- **左侧可折叠侧边栏**（默认 248px，可拖拽 180~460px）：新建笔记、最近文件、页面缩略图。
- **响应式顶部工具栏**（约 46px）：窗口变窄时优先级最低的按钮自动收进最右端的「»」溢出菜单。
- **工具栏下方的标签栏**：每个标签有类型图标、标题、未保存星号 `*` 与常显的 × 关闭按钮。
- **浅色 / 深色主题**：一键切换，样式由 `src/ui/theme.py` 的 QSS 统一管理。
- 支持 150% / 200% 高 DPI 缩放。
- 错误提示：文件损坏 / PDF 无法打开 / 保存失败等均有弹窗，不会崩溃。

---

## 系统要求

| 项目 | 要求 |
|---|---|
| 操作系统 | **Windows 10 / 11**（依赖 Qt 触控笔事件与 Windows 反馈 API，暂不支持其他平台） |
| Python | **3.10+**（开发与 CI 使用 3.12） |
| Rust | 可选，仅构建加速核心时需要 |

---

## 安装

```bash
git clone https://github.com/genhao3aaa/notebook-compre.git
cd notebook-compre
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt`：

```
PySide6>=6.5.0
PyMuPDF>=1.23.0
pillow>=10.0.0
numpy>=1.26
```

> 也可以直接用 [uv](https://docs.astral.sh/uv/)（更快）：
> `uv venv .venv --python 3.12 && uv pip install --python .venv\Scripts\python.exe -r requirements.txt`
>
> 国内网络安装慢时可加镜像：`pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt`

---

## 运行

```bash
python main.py
```

首次启动会进入一个空白 A4 笔记页，即可开始手写。

**命令行 / 拖放打开文件**：

```bash
python main.py "D:\书\book.pdf" "D:\笔记\笔记.note"   # 多个文件开成多个标签
```

- 把 PDF / `.note` 拖到 exe 图标上，或用资源管理器「打开方式」选择本程序，效果相同；
- PDF 会恢复到**上次读到的那一页**；无效路径与未知类型会被**忽略**（不弹错、不开空标签）。

**Windows 启动器**（可选）：仓库自带的 `run_episode2.cmd` 提供三种用法。

| 命令 | 作用 |
|---|---|
| 双击 `run_episode2.cmd` | 用 `pythonw.exe` 无控制台启动 |
| `run_episode2.cmd console` | 带控制台启动，能看到 traceback（排查启动失败用） |
| `run_episode2.cmd shortcut` | 在桌面创建 / 更新 `Episode2.lnk`，会回读字段并暂停 |

> 启动器优先使用仓库根目录的 `.venv`，因此按上文装好虚拟环境后即可直接使用。

---

## 使用说明

| 操作 | 方式 |
|------|------|
| 手写 | 鼠标左键拖拽 / 触控笔书写 |
| 橡皮擦 | 点击「橡皮」后在笔迹上拖拽（对象级擦除，可撤销） |
| 荧光笔 | 点击「荧光笔」或按 `H`，在文字上拖拽即可标记 |
| 切换颜色 | 点工具栏**颜色块** → 弹出常用色，**单击即换**（键盘 `1`~`9` 选第 N 个） |
| 切换笔宽 | 工具栏笔宽滑块 / 数字框（随当前工具切换） |
| 关闭标签 | 点标签上的 **×**、中键点击标签，或 `Ctrl+W` |
| 把标签变成独立窗口 | **把标签往标签栏外面拖**，松手即可 |
| 缩放 | `Ctrl + 滚轮`，或工具栏 / 视图菜单的放大缩小 |
| 平移 | 按住 `空格` 拖拽，或 `中键` 拖拽 |
| 连续 / 单页阅读 | 工具栏的阅读模式按钮，或 `Ctrl+Shift+L` |
| 新建笔记 | `Ctrl+N`，可先选背景 |
| 打开笔记 / PDF | `Ctrl+O` 或菜单「打开笔记… / 打开 PDF…」 |
| 保存 | `Ctrl+S`（笔记存为 `.note`，PDF 批注存为同名 `.annot`） |
| 导出 | `Ctrl+E`（笔记 / PDF 均导出为带内容的 PDF） |
| 撤销 / 重做 | `Ctrl+Z` / `Ctrl+Y` |
| 清空当前页 | `Ctrl+K` |

**标签快捷键**：`Ctrl+T` 新建、`Ctrl+W` 关闭、`Ctrl+Tab` / `Ctrl+Shift+Tab` 上一个 / 下一个、
`Ctrl+1`~`Ctrl+8` 直达第 N 个、`Ctrl+9` 最后一个、`Ctrl+Shift+T` 恢复刚关闭的标签。

PDF 批注文件默认写入原 PDF 同级目录，文件名相同、后缀为 `.annot`。再次用本程序打开该 PDF 时，
若 MD5 一致会自动加载批注。

---

## Rust 加速核心（可选）

程序把渲染与几何计算抽象成 `src/render/backend.py` 的后端契约，提供两份实现：

- **Python 参考实现**（默认，无需任何额外工具链）；
- **Rust 实现**（`rust/hn_core`，基于 tiny-skia + rstar + PyO3，走 numpy 缓冲零拷贝传参）。

两者可逐点 / 逐像素对拍。**扩展缺失或 ABI 不匹配时会自动降级到 Python 实现**并写入日志，
程序不会因为性能优化而不可用。

### 构建

```bash
# 1) 工具链
winget install Rustlang.Rustup
rustup default stable-x86_64-pc-windows-msvc   # 没有 VS Build Tools 时可用 -gnu 目标
pip install maturin

# 2) 编译并安装扩展（产出 dist_rust/hn_core-*-cp310-abi3-win_amd64.whl）
python build_rust.py
python build_rust.py --check                   # 只检查扩展能否导入
```

### 切换后端

```bash
set HN_RENDER_BACKEND=python   # 强制参考实现
set HN_RENDER_BACKEND=rust     # 强制 Rust（不可用时自动降级并记日志）
set HN_RENDER_BACKEND=auto     # 默认：能用 Rust 就用
```

架构细节与对拍数据见 [EPISODE2.md](EPISODE2.md)。

---

## 打包为可执行文件

`build.spec` 采用 **onedir（文件夹）模式**，并做 **白名单式裁剪**：不收集 PySide6 的
qml / translations / metatypes / `.pyi`，按名字丢掉用不到的原生件（WebEngine、QtQml/Quick、QtPdf 等）。

1. 安装打包工具：

   ```bash
   pip install pyinstaller
   ```

2. 在项目根目录执行：

   ```bash
   pyinstaller build.spec
   ```

   生成的程序在：

   ```
   dist\手写笔记本与PDF批注\手写笔记本与PDF批注.exe
   ```

   直接双击即可运行（目标机器无需安装 Python）。整个 `dist\手写笔记本与PDF批注\` 文件夹需一起分发。

3. **打包后必须跑一次自检**（裁剪最容易伤到平台插件与运行时才加载的 PyMuPDF / PIL）：

   ```bash
   dist\手写笔记本与PDF批注\手写笔记本与PDF批注.exe --selftest %TEMP%\hn-check
   ```

   自检会离屏跑完「创建 PDF → 打开 PDF → 读目录 → 渲染 → 手写 → 保存批注(v2) → 导出 PDF
   → 新建笔记 → 保存并读回」，成功打印 `SELFTEST OK`，失败返回非 0。
   源码环境用 `python main.py --selftest %TEMP%\hn-check` 等价。

4. 想要**单个 exe 文件**（体积更大、启动稍慢）可改用：

   ```bash
   pyinstaller -F -w --name "手写笔记本与PDF批注" ^
     --hidden-import pymupdf --hidden-import PIL main.py
   ```

   （`-F` 不会应用 `build.spec` 里的白名单裁剪，体积会回到数百 MB。）

### 关于 SmartScreen「未知发布者」提示

未签名的 exe 在别的机器上首次运行会触发 Windows SmartScreen 的声誉检查——**这不是杀毒告警**。

- 在提示里点「**更多信息**」→「**仍要运行**」即可；
- **不要**为了它关闭 SmartScreen / Defender 全局防护；
- 如果 exe 是从别处拷来的（U 盘、聊天工具、压缩包），先解除下载标记：
  `Unblock-File 'dist\手写笔记本与PDF批注\手写笔记本与PDF批注.exe'`。

根治需要代码签名证书（本仓库提供 `sign_dist.py`）：

```bash
python sign_dist.py --pfx my.pfx --password <口令>
python sign_dist.py --thumbprint <证书指纹>   # 或证书已装进证书库时
python sign_dist.py --check                   # 只看当前签名状态
```

> 自签名证书**不能**消除 SmartScreen 提示，仅适合内部机器；对外分发需 OV/EV 代码签名证书
> 或 Azure Trusted Signing。

---

## 测试

```bash
pip install pytest ruff pyright
```

```bash
pytest -q                       # 全量回归 + 性能预算
ruff check                      # 静态检查（配置在 pyproject.toml）
pyright                         # 类型检查（配置在 pyrightconfig.json，验收线为「无 error」）
```

**冒烟测试（offscreen，端到端）**：

```bash
python smoke_v4.py              # 打开 PDF / 目录 / 跳页 / 旋转 / 搜索 / 视图 / 背景色 / 存取 / 文本
python smoke_v5.py              # 多标签 / 会话恢复 / 色盘 / 圆圈反馈策略
python main.py --selftest %TEMP%\hn-check
```

**基准脚本**位于 `tests/benchmarks/`（真机整页重建 / 微基准 / 增量重绘 / 存储 / 采样简化 / 冷启动，
全部支持 `--json`），口径见该目录的 README。

CI 配置在 `.github/workflows/ci.yml`（windows-latest）：`ruff` → `pyright` → `pytest` →
基准 JSON 存档 → 源码自检；第二个 job 负责打包 + 成品自检 + 体积预算。

---

## 项目结构

```
notebook-compre/
├── main.py                    # 程序入口（高 DPI 设置 + 启动主窗口 + 命令行/拖放打开文件）
├── requirements.txt           # 运行依赖清单
├── run_episode2.cmd           # Windows 启动器（无控制台启动 / 建桌面快捷方式）
├── build.spec                 # PyInstaller 打包脚本（onedir + 白名单裁剪）
├── build_rust.py              # 一键编译并安装 Rust 核心
├── sign_dist.py               # 代码签名（消除 SmartScreen「未知发布者」）
├── env_compat.py              # 运行环境兼容层
├── conftest.py                # pytest 配置（sys.path + 环境兼容）
├── pyproject.toml             # 工具链配置（pytest / ruff）
├── pyrightconfig.json         # 类型检查配置
├── LICENSE                    # AGPL-3.0
├── CONTRIBUTING.md
├── THIRD_PARTY_NOTICES.md
├── README.md
├── CHANGELOG.md               # 版本演进历史
├── EPISODE2.md                # Rust 核心设计与对拍数据
├── ROADMAP.md / ROADMAP_REVIEW.md
├── assets/
│   ├── app.ico                # 应用图标
│   └── version_info.txt       # exe 版本资源
├── rust/
│   └── hn_core/               # ★ Rust 核心（tiny-skia + rstar + PyO3）
│       ├── Cargo.toml
│       ├── Cargo.lock
│       ├── pyproject.toml     # maturin 配置（abi3-py310）
│       └── src/lib.rs         # simplify / hit_test / rasterize
├── tests/
│   ├── test_*.py              # 单元与回归测试
│   └── benchmarks/            # 基准脚本（渲染 / 增量 / 存储 / 简化 / 冷启动 / 后端对拍）
└── src/
    ├── core/                  # 核心数据层（与 UI 完全解耦）
    │   ├── stroke.py          # Stroke、命令式撤销/重做、RDP 简化、几何工具
    │   ├── format.py          # 文档格式统一入口（v1/v2 分发 / 就地升级）
    │   ├── serializer.py      # v2 二进制容器（列式点数据 + CRC32 + 原子写）
    │   ├── note_document.py   # 笔记文档（.note，多页，背景样式+背景色）
    │   ├── annot_document.py  # 批注文档（.annot，含 PDF MD5）
    │   ├── annot_store.py     # 批注保存位置的兜底链
    │   ├── document_tab.py    # 标签级文档状态
    │   ├── colors.py          # 颜色工具（明暗判定 / RGBA 解析 / 半透明叠加）
    │   ├── hn_log.py          # 结构化日志
    │   └── settings.py        # 偏好持久化（主题/工具/笔色笔宽/最近文件/窗口几何）
    ├── tools/                 # 工具系统（可扩展）
    │   ├── base.py            # Tool 抽象基类 + ToolContext
    │   ├── pen.py             # 钢笔（压感）
    │   ├── highlighter.py     # 荧光笔（半透明等宽）
    │   ├── eraser.py          # 橡皮（对象级擦除）
    │   └── registry.py        # 工具注册表（扩展点）
    ├── render/                # 渲染 / 导出层
    │   ├── backend.py         # ★ 渲染后端契约（Python 参考实现 + Rust 实现 + 自动降级）
    │   ├── pdf_engine.py      # PDF 打开、异步渲染、搜索、导出烧录
    │   ├── stroke_painter.py  # 笔迹绘制唯一真源（平滑 + 压感）
    │   └── thumbnail_pool.py  # 有界线程池的 PDF 缩略图加载器
    └── ui/                    # 界面层
        ├── canvas.py          # 手写画布：视图状态 + 工具 + 缩放/旋转 + 编辑
        ├── canvas_render.py   # 整页重建 / 脏矩形增量重绘 / PDF 异步渲染
        ├── canvas_input.py    # 鼠标 / 触控笔 / 滚轮 / 键盘（双通道互斥）
        ├── canvas_layout.py   # 页面⇄显示坐标、连续阅读纵向布局
        ├── main_window.py     # 主窗口：属性代理 + 组装
        ├── controllers/       # 主窗口职责拆分（ui_build / tab_ops / file_ops / reading_ops …）
        ├── theme.py           # 浅/深色 QSS + 内嵌 SVG 图标
        ├── topbar.py          # 响应式顶栏
        ├── color_palette.py   # 常用色快捷盘
        ├── thumbnail_panel.py # 缩略图侧栏
        ├── outline_panel.py   # 目录（书签）树
        ├── search_panel.py    # 全文搜索面板 + 后台检索线程
        └── …
```

---

## 设计原则

1. **核心逻辑（core）与界面（ui）分离**，便于维护与测试；坐标统一使用「页面 points」，
   导出与显示都不需要坐标换算。
2. **画布与具体工具解耦**：画布只认 `Tool` 抽象，具体语义在 `src/tools` 中，新增工具不需要改画布。
3. **笔迹渲染只有一份实现**（`render/stroke_painter.py`），画布、缩略图、导出共用，保证外观一致。
4. **渲染后端可在 Python / Rust 之间切换**，且始终保留参考实现，优化不会导致程序不可用。
5. **每个文件 < 600 行**：`main_window.py` 只留属性代理与组装，交互语义按职责拆到 `ui/controllers/*`；
   画布拆成 view / render / input / layout 四块。

---

## 环境变量

| 变量 | 作用 |
|---|---|
| `HN_DEBUG=1` | 打印鼠标 / 触控笔的按下、移动、释放坐标，便于排查书写问题 |
| `HN_LOG=<level>` | 调整结构化日志级别 |
| `HN_RENDER_BACKEND=python\|rust\|auto` | 选择渲染后端（默认 `auto`） |
| `HN_ANNOT_DIR=<目录>` | 显式指定批注的兜底保存目录 |
| `HN_PRESSURE_OUTLINE=0` | 关闭压感变宽轮廓渲染，回到逐段 `drawLine` |
| `HN_SKIP_PERF=1` / `HN_PERF_SCALE=<系数>` | 关闭 / 放宽性能预算闸门 |

---

## 已知限制

- 橡皮为「对象级擦除」（整条命中笔画被删除），未实现笔画内部局部擦除；荧光笔同理。
- 笔记导出为「页面光栅化后写入 PDF」，因此导出的是图片 PDF（批注为像素）；如需矢量批注可改用
  PyMuPDF 的 `Shape` 绘制。
- 压感需要有 `QTabletEvent` 支持的设备（数位板 / 触控笔）；纯鼠标压感恒为 1.0。
- 手写时按「增量绘制」保证低延迟，抬笔后不会立即全页重绘（仅在撤销 / 重做 / 擦除 / 翻页时重绘）；
  由于增量与最终路径公式一致，外观无差异。
- 偏好设置默认存注册表 `HKCU\Software\WorkBuddy\HandwritingNotebook`；写不进去（受限环境 / 只读策略）时
  **自动改用 INI 文件**，偏好不会静默丢失。
- 批注兜底位置：PDF 旁边写不了时会退到候选链里第一个可写的目录，可用 `HN_ANNOT_DIR` 显式指定。
- 阅读位置：再次打开同一份 PDF 会回到上次读到的那一页；文件「大小 / 修改时间」变了就从头开始。
- **无头 / offscreen 自动化的坑**：批注 MD5 不匹配时的确认框是**模态**的，无头跑回归时会永久挂住脚本，
  需给提示加钩子自动确认，或每轮使用全新目录。
- `v2` 二进制文件**旧版本程序读不了**（magic 不匹配会明确报错而非画花）；需要与旧版本交换文件时
  用 `save(path, version=1)` 写回 JSON。

---

## 性能

以下为路线 A（Python 侧优化）的前后对比实测数据：

| 指标 | 改前 | 现在 | 说明 |
|---|---:|---:|---|
| 1000 笔画整页重建（dpr 1.0） | 1.042 s | **0.222 s** | 压感笔画改为变宽轮廓一次填充（4.7x） |
| 1000 笔画整页重建（dpr 1.5） | 1.33 s | **0.314 s** | 真实像素量（1330×1881） |
| 500 笔画整页重建（dpr 1.0） | 0.526 s | **0.111 s** | |
| 单笔撤销/擦除重绘（1000 笔画页） | 整页重建 103 ms | **1.04 ms** | 脏矩形增量重绘（最坏情况长笔画 20 ms） |
| 点数 / 文件体积（200 笔画页） | 90.5 点/笔 · 1.80 MB | **8.1 点/笔 · 0.19 MB** | 采样 1.2 pt + 抬笔 RDP 0.35 pt（-91%） |
| `.note` 1 页 × 1000 笔画 | 11.9 MB | **1.10 MB**（10.8x） | v2 二进制容器；save 0.430 → **0.047 s** |
| `.note` 50 页 × 200 笔画 | 118.7 MB | **11.0 MB** | save 4.78 → **0.42 s**，且写盘在后台线程 |
| `dist` 体积 | 684.8 MB / 3695 文件 | **113.1 MB / 202 文件** | 白名单裁剪 + `optimize=1`（6.1x） |
| 冷启动（新进程、热缓存） | 0.542 s | **0.393 s** | PyMuPDF 惰性导入 + 缩略图线程池惰性启动 |

Rust 核心的对拍与基准数据见 [EPISODE2.md](EPISODE2.md) 与 `bench_backend_result.json`。

---

## 贡献

欢迎提交 Issue 与 Pull Request。开发环境、构建步骤与提交规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

提交前请确保：

```bash
ruff check
pyright
pytest -q
```

---

## 许可证

本项目以 **GNU Affero General Public License v3.0（AGPL-3.0）** 发布，详见 [LICENSE](LICENSE)。

因依赖 PyMuPDF（AGPL-3.0），本项目的**任何分发与网络服务**均须遵守 AGPL-3.0 的开源义务：
若你将本项目（或其修改版）作为网络服务对外提供，也必须向使用者开放完整源码。

第三方组件许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

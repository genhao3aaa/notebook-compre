"""一键编译并安装 Rust 核心（`hn_core`）——Episode2（路线 B）的构建入口。

用法
----
    python build_rust.py            # 编译 release 版轮子并装进当前解释器
    python build_rust.py --check    # 只检查扩展能不能导入（不编译）
    python build_rust.py --wheel    # 只产出轮子，不安装

前置条件（本机已经具备，换机器照抄即可）
------------------------------------
1. **Rust 工具链**：`rustup` 装 `stable-x86_64-pc-windows-msvc`（有 VS Build Tools 时）
   或 `stable-x86_64-pc-windows-gnu`（没有 MSVC 时，本机就是这个）。
   本机实测：`winget install Rustlang.Rustup` 后
   `rustup default stable-x86_64-pc-windows-gnu`（下载约 200 MB，含 MinGW 链接器）。
2. **maturin**：`uv pip install maturin`（本机 1.15.0）。
3. **numpy**：契约要求零拷贝传数组（`requirements.txt` 已加）。

产物：`dist_rust/hn_core-<版本>-cp310-abi3-win_amd64.whl`，装进解释器后
`src/render/backend.py` 会自动选中它（`HN_RENDER_BACKEND=rust|auto`）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CRATE = os.path.join(HERE, "rust", "hn_core")
OUT = os.path.join(HERE, "dist_rust")


def cargo_bin() -> str:
    """把 `~/.cargo/bin` 加到 PATH（maturin 要求 cargo 在 PATH 里）。"""
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, ".cargo", "bin"), r"C:\Program Files\Rust\bin"):
        if os.path.isdir(cand) and cand not in os.environ.get("PATH", ""):
            os.environ["PATH"] = cand + os.pathsep + os.environ.get("PATH", "")
    return shutil.which("cargo") or ""


def check() -> int:
    try:
        import hn_core  # noqa: F401

        print(f"hn_core 可用，版本 {getattr(hn_core, 'version', lambda: '?')()}")
    except Exception as e:                       # pragma: no cover - 取决于环境
        print(f"hn_core 不可用：{type(e).__name__}: {e}")
        return 1
    return 0


def build(install: bool = True) -> int:
    cargo = cargo_bin()
    if not cargo:
        print("找不到 cargo：先装 Rust 工具链（见本文件顶部说明）")
        return 2
    maturin = shutil.which("maturin") or os.path.join(
        os.path.dirname(sys.executable), "Scripts", "maturin.exe")
    if not os.path.exists(maturin) and not shutil.which("maturin"):
        print("找不到 maturin：`uv pip install maturin`")
        return 2
    print(f"cargo   = {cargo}")
    print(f"maturin = {maturin}")

    os.makedirs(OUT, exist_ok=True)
    cmd = [maturin, "build", "--release", "--out", OUT, "--interpreter", sys.executable]
    print("运行：" + " ".join(cmd))
    rc = subprocess.call(cmd, cwd=CRATE)
    if rc != 0:
        return rc

    wheels = sorted(
        (os.path.join(OUT, f) for f in os.listdir(OUT) if f.endswith(".whl")),
        key=os.path.getmtime)
    if not wheels:
        print("maturin 没有产出轮子")
        return 3
    wheel = wheels[-1]
    print(f"产出：{wheel}")
    if not install:
        return 0

    # 安装：优先 uv（本项目的环境是 uv 管理的），否则退回 pip
    uv = shutil.which("uv")
    if uv:
        rc = subprocess.call([uv, "pip", "install", "--python", sys.executable,
                              "--force-reinstall", wheel])
    else:
        rc = subprocess.call([sys.executable, "-m", "pip", "install",
                              "--force-reinstall", wheel])
    if rc != 0:
        return rc
    return check()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="编译并安装 hn_core（Episode2 路线 B）")
    ap.add_argument("--check", action="store_true", help="只检查扩展是否可用")
    ap.add_argument("--wheel", action="store_true", help="只产轮子，不安装")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    return build(install=not args.wheel)


if __name__ == "__main__":
    raise SystemExit(main())

"""生成 `assets/app.ico`（多尺寸应用图标）。

图标只画一份：`src/ui/theme.py` 里的 `APP_ICON_SVG`——窗口/任务栏用它、打包 exe 也用它，
避免「改了一个地方忘了另一个」。PyInstaller 需要 .ico 文件，所以这里把它渲染出来。

用法：
    .venv\\Scripts\\python.exe make_icon.py          # 生成 assets/app.ico
    .venv\\Scripts\\python.exe make_icon.py --check  # 只校验（CI 用，缺文件返回非 0）
"""

from __future__ import annotations

import argparse
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

#: ICO 里包含的尺寸（Windows 会在不同位置挑不同大小）
SIZES = (256, 128, 64, 48, 32, 16)


def render_rgba(svg: str, size: int):
    """把 SVG 渲染成 Pillow 图像（BGRA → RGBA，Qt 的 ARGB32 是小端 BGRA）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication
    from PIL import Image

    QApplication.instance() or QApplication([])
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
    painter.end()
    raw = bytes(img.constBits())
    return Image.frombytes("RGBA", (size, size), raw, "raw", "BGRA")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成应用图标 assets/app.ico")
    ap.add_argument("--check", action="store_true", help="只检查 ico 是否存在且含预期尺寸")
    ap.add_argument("--out", default=os.path.join(REPO, "assets", "app.ico"))
    a = ap.parse_args(argv)

    if a.check:
        from PIL import Image

        if not os.path.isfile(a.out):
            print(f"缺少图标文件：{a.out}（先运行 make_icon.py 生成）", file=sys.stderr)
            return 1
        with Image.open(a.out) as im:
            got = sorted(im.info.get("sizes", []))
            want = {(s, s) for s in SIZES}
            missing = want - set(got)
            if missing:
                print(f"图标尺寸不全，缺：{sorted(missing)}", file=sys.stderr)
                return 1
            print(f"图标 OK：{a.out}（{len(got)} 个尺寸）")
        return 0

    from src.ui.theme import APP_ICON_SVG

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    base = render_rgba(APP_ICON_SVG, max(SIZES))
    base.save(a.out, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"已生成 {a.out}（{os.path.getsize(a.out)} 字节，尺寸 {list(SIZES)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""程序入口。

支持 Windows 10/11 高 DPI 缩放（150% / 200% 等），界面与笔迹保持清晰。

命令行参数（2026-10-01 新增）
---------------------------
- `python main.py` / 双击 exe：正常启动；
- `python main.py <文件.pdf|文件.note> [...]`：启动后直接把这些文件开成标签
  （与「把文件拖到 exe 上」/ 资源管理器右键打开一致），PDF 仍然会恢复到上次读到的那一页；
- `--selftest [工作目录]`：打包后的自检（见 src/ui/selftest.py）。
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


def _open_paths(window: "MainWindow", paths) -> None:
    """把命令行里给的文件路径开成标签（一次只开一个，多余的不是本程序格式就忽略）。"""
    for raw in paths:
        path = os.path.abspath(raw)
        if not os.path.isfile(path):
            continue
        try:
            if path.lower().endswith(".pdf"):
                window.open_pdf(path)
            elif path.lower().endswith(".note"):
                window.open_note(path)
        except Exception:                    # 单个文件打不开不该拦住启动
            continue


def main() -> int:
    # A5：打包后自检入口（`<exe> --selftest [工作目录]`）：
    # 启动 → 打开 PDF → 手写 → 保存批注 → 导出 → 保存笔记，全程离屏运行。
    # 打包裁剪最容易伤到运行时才加载的东西（平台插件 / PyMuPDF / PIL），
    # 所以发布前必须能在成品里跑这串检查。
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        from src.ui.selftest import run_selftest

        return run_selftest(sys.argv[2] if len(sys.argv) > 2 else None)

    # 高 DPI：Qt 6 默认已开启高分屏缩放，这里仅设置缩放取整策略，
    # 让 150% / 200% 等非整数比例下界面与笔迹保持清晰。
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # 先把命令行里的文件路径摘出来：Qt 会把 `sys.argv` 里它自己的参数吃掉并改写
    # 这个列表，之后再读就拿不到文件路径了（实测：打包版「拖文件到 exe 上」打不开）。
    requested = [a for a in sys.argv[1:] if not a.startswith("-")]

    app = QApplication(sys.argv)
    app.setApplicationName("手写笔记本与 PDF 批注")
    # 应用图标内嵌在 theme.py 里（与打包 exe 的 .ico 同一份来源）：
    # 任务栏 / Alt-Tab / 任务管理器都会显示它，而不是 Qt/Python 的默认图标。
    from src.ui.theme import app_icon

    app.setWindowIcon(app_icon())

    window = MainWindow()
    window.show()

    # 命令行里给的文件：等窗口显示后再开，界面才有正确的尺寸与状态栏。
    if requested:
        QTimer.singleShot(0, lambda: _open_paths(window, requested))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

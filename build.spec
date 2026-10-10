# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包脚本（onedir 模式）——A5：白名单式收集
#
# 为什么不再 `collect_data_files('PySide6')`
# ----------------------------------------
# 那一句会把 PySide6 整个目录搬进来：Qt6WebEngineCore.dll 194 MB、.pak 132 MB、
# translations 59 MB、qml 25 MB、metatypes 15 MB……而本程序只 import
# QtCore / QtGui / QtWidgets / QtSvg（唯一 QtSvg 引用在 src/ui/theme.py）。
# 实测 dist 从 684.8 MB 降到 ~90 MB 全靠这里。
#
# 现在的做法：
#   1. **不收集** PySide6 的 data（qml / translations / metatypes / .pyi / include 全不要）；
#   2. 用 `excludes` 把用不到的 Qt 模块排除掉（PyInstaller 的 PySide6 hook 会按需
#      带上被 import 的模块所需的 DLL 与插件）；
#   3. `optimize=1`：去掉 assert 与 __debug__ 分支，.pyc 更小、导入更快；
#   4. 保留 `platforms`（qwindows/qoffscreen）与 `iconengines`（SVG 图标）等必要插件——
#      裁剪最容易伤到的就是平台插件，所以打包后必须跑一次冒烟。
#
# 用法：
#   pyinstaller build.spec
#   生成 dist/手写笔记本与PDF批注/手写笔记本与PDF批注.exe
import os

# 项目根目录：从候选目录向上查找包含 main.py 的目录，避免 spec 路径解析歧义。
def _find_root():
    candidates = []
    try:
        candidates.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    try:
        candidates.append(os.path.dirname(os.path.abspath(SPECPATH)))
    except NameError:
        pass
    candidates.append(os.getcwd())
    for c in candidates:
        if os.path.isfile(os.path.join(c, 'main.py')):
            return c
    cur = candidates[0] if candidates else os.getcwd()
    for _ in range(6):
        if os.path.isfile(os.path.join(cur, 'main.py')):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return candidates[0]


ROOT = _find_root()
import sys as _sys
print("SPEC ROOT =", ROOT, file=_sys.stderr)
print("MAIN PY  =", os.path.join(ROOT, 'main.py'), file=_sys.stderr)

# A5：不收集 PySide6 的 data（见文件头说明）
datas = []

# 确保 pymupdf / PIL / PySide6 的子模块都被打包进来
# 注：PyMuPDF 1.24+ 的官方包名是 `pymupdf`（`fitz` 只是兼容别名，会打印弃用告警）。
# 源码统一惰性 `import pymupdf`，所以这里**不要**再收 'fitz'：既没用，又会在构建时
# 触发「The fitz API is deprecated」告警。
hiddenimports = [
    'pymupdf',
    'PIL',
    'PIL._imaging',
    'PIL._imagingmath',
    'PIL._imagingmorph',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtSvg',
]

# A5：明确排除用不到的 Qt 模块（少一个模块就少一批 DLL/插件被拖进来）
excludes = [
    'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuick3D', 'PySide6.QtQuickWidgets',
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
    'PySide6.QtWebChannel', 'PySide6.QtWebSockets', 'PySide6.QtWebView',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtSpatialAudio',
    'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic',
    'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras', 'PySide6.QtCharts',
    'PySide6.QtDataVisualization', 'PySide6.QtGraphs', 'PySide6.QtDesigner',
    'PySide6.QtUiTools', 'PySide6.QtHelp', 'PySide6.QtSql', 'PySide6.QtTest',
    'PySide6.QtNetwork', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
    'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtPrintSupport',
    'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning',
    'PySide6.QtSerialPort', 'PySide6.QtSerialBus', 'PySide6.QtSensors',
    'PySide6.QtTextToSpeech', 'PySide6.QtScxml', 'PySide6.QtRemoteObjects',
    'PySide6.QtStateMachine', 'PySide6.QtConcurrent', 'PySide6.QtDBus',
    'PySide6.QtLocation', 'PySide6.QtHttpServer', 'PySide6.QtSvgWidgets',
    # 标准库里明显用不到的大件
    'tkinter', 'unittest', 'pydoc_data',
    # Pillow：只用到 Image / ImageDraw / ImageChops / ImageFilter（导出时合成批注层）
    'PIL.AvifImagePlugin', 'PIL.WebPImagePlugin', 'PIL.ImageCms', 'PIL.ImageTk',
    'PIL.ImageShow', 'PIL.ImageQt',
]

# ---- A5 第二遍：按名字丢掉用不到的原生件（每条都写明理由） ----
# 出问题时把对应项从元组里删掉、重新 `pyinstaller build.spec` 即可回退。
_DROP_NATIVE = (
    'opengl32sw.dll',        # 软件 OpenGL 兜底：本程序是 QWidget 光栅渲染，不用 OpenGL
    'qt6qml', 'qt6quick',    # QtQml / QtQuick（Python 侧已 exclude，平台插件不依赖）
    'qt6pdf',                # Qt 自带 PDF 实现（我们用 PyMuPDF）
    'libcrypto-3-x64.dll',   # OpenSSL：只有 _hashlib / _ssl 需要；
    'libssl-3-x64.dll',      #   md5 走内置 _md5（同为 C 实现），程序不联网
    '_ssl.pyd', '_hashlib.pyd',
    'pil\\_avif', 'pil\\avif',          # AVIF 编解码：程序不读 AVIF
    'pil\\_webp',                       # WEBP：同上（图片来自 Qt / PyMuPDF）
    'pil\\_imagingtk',                  # tkinter 版接口（tkinter 已排除）
    'pil\\_imagingcms',                 # 颜色管理（ICC）：导出只做 alpha 合成
)


def _keep_native(dest: str) -> bool:
    low = dest.replace('/', '\\').lower()
    return not any(key in low for key in _DROP_NATIVE)


a = Analysis(
    [os.path.join(ROOT, 'main.py')],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,              # 去掉 assert / __debug__（A5）
)

# 分析完成后再按名字过滤原生件（此时 a.binaries / a.datas 才有内容）
a.binaries = [b for b in a.binaries if _keep_native(b[0])]
a.datas = [d for d in a.datas if _keep_native(d[0])]

pyz = PYZ(a.pure, a.zipped_data)

# 图标与版本资源（A5+）：没有它们，exe 在「属性 → 详细信息」里全是空白，
# 看起来就像来路不明的程序。图标由 `make_icon.py` 从 theme.py 的内嵌 SVG 生成，
# 保证「窗口图标 / 任务栏图标 / 文件图标」是同一枚。
_ICON = os.path.join(ROOT, 'assets', 'app.ico')
_VERSION = os.path.join(ROOT, 'assets', 'version_info.txt')
if not os.path.isfile(_ICON):
    print('WARN: 缺少 assets/app.ico（先运行 python make_icon.py）', file=_sys.stderr)
if not os.path.isfile(_VERSION):
    print('WARN: 缺少 assets/version_info.txt（exe 将没有版本信息）', file=_sys.stderr)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='手写笔记本与PDF批注',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # Qt 的 DLL 用 UPX 压过容易出问题；体积收益也不大
    console=False,           # -w：无控制台窗口（GUI 程序）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON if os.path.isfile(_ICON) else None,
    version=_VERSION if os.path.isfile(_VERSION) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='手写笔记本与PDF批注',
)

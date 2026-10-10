"""pytest 配置。

1. 把项目根目录加入 `sys.path`，使 `src` 包可被测试导入。
2. 修正受限文件沙箱下 `tempfile.mkdtemp()` 目录不可写的问题（详见 env_compat.py）。
3. 让每个测试使用独立的设置文件，避免读到用户机器上的真实偏好。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_compat import ensure_writable_tempdir  # noqa: E402  (需先插入 sys.path)

ensure_writable_tempdir()


@pytest.fixture(scope="session", autouse=True)
def _qt_application():
    """整个测试会话先建好一个 **QApplication**。

    有些测试（例如 test_tools 的设置用例）写的是
    `QCoreApplication.instance() or QCoreApplication([])`：**如果它先跑**，进程里就只剩
    QCoreApplication；后面创建 QWidget 的测试会直接触发 Qt fail-fast（实测退出码
    0xC0000409，且只在「单独跑这几个文件」时复现）。先建好 QApplication，
    那些 `instance() or ...` 就会拿到它，顺序就不再敏感。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _no_leaked_windows():
    """每个测试结束后清掉遗留窗口与窗口登记表。

    「多窗口」是全局状态（`_OPEN_WINDOWS`），会话保存会把**所有活窗口**的标签都记下来。
    只要前面任何一个测试留下了没关掉的窗口，后面 `test_session_saves_and_restores_open_tabs`
    就会记到多于预期的标签——实测在「只跑部分文件」时必现（完整套件因文件顺序不同才侥幸通过）。
    另外，留着窗口也会留着缩略图/保存线程，进程退出时可能触发
    `QThread: Destroyed while thread is still running`（Qt 直接 fail-fast）。
    """
    yield
    try:
        from src.ui import main_window as mw

        for w in list(mw._OPEN_WINDOWS.live()):
            try:
                for t in list(w._tabs):
                    t.dirty = False
                w._confirm_save = lambda *a, **k: True
                w.close()
            except Exception:
                pass
        mw._OPEN_WINDOWS._refs = []
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolated_app_settings(tmp_path, monkeypatch):
    """把「应用自身设置」重定向到本测试专用的 INI 文件。

    否则测试会读到用户机器上的真实偏好（主题 / 上次工具 / 阅读模式 / 最近文件），
    出现「用户改了个偏好，测试就变红」的假失败——例如把阅读模式设成「连续阅读」后，
    依赖 canvas.page_pixmap 的用例就会拿到空白页（连续模式本来就不建整页离屏缓存）。

    只拦截默认的 WorkBuddy / HandwritingNotebook 命名空间；test_tools.py 里的
    org=HNTest 命名空间保持原样（它专门验证设置读写）。
    """
    from src.core import settings as settings_mod

    ini = str(tmp_path / "app_settings.ini")
    original = settings_mod.AppSettings.__init__

    def _init(self, org: str = "WorkBuddy", app: str = "HandwritingNotebook",
              path=None):
        if path is None and app == "HandwritingNotebook":
            original(self, org, app, ini)
        else:
            original(self, org, app, path)

    monkeypatch.setattr(settings_mod.AppSettings, "__init__", _init)
    yield

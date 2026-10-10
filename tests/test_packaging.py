"""打包元数据验收（图标 + exe 版本资源 + 签名入口）。

背景：未签名的 exe 会被 SmartScreen 以「未知发布者」拦一下，而「属性 → 详细信息」
全空会让人更不放心。这里守住三件事：图标能生成、版本资源接了线、签名脚本可用。
"""

from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_version_info_has_product_fields():
    path = os.path.join(REPO, "assets", "version_info.txt")
    assert os.path.isfile(path), "缺少 assets/version_info.txt"
    text = open(path, encoding="utf-8").read()
    for field in ("ProductName", "FileDescription", "FileVersion", "ProductVersion",
                  "CompanyName", "OriginalFilename"):
        assert field in text, f"版本资源缺少 {field}"
    assert "VSVersionInfo" in text


def test_app_icon_generation():
    """图标可由 make_icon.py 生成/校验（多尺寸，Windows 各位置都能挑到合适的）。"""
    import make_icon

    rc = make_icon.main(["--check"])
    assert rc == 0, "assets/app.ico 缺失或尺寸不全（运行 python make_icon.py）"


def test_build_spec_wires_icon_and_version():
    spec = open(os.path.join(REPO, "build.spec"), encoding="utf-8").read()
    assert "app.ico" in spec and "version_info.txt" in spec
    assert "icon=" in spec and "version=" in spec


def test_app_icon_object_is_not_null(qapp):
    from src.ui.theme import app_icon

    icon = app_icon(64)
    assert not icon.isNull(), "应用图标渲染失败（SVG 可能写错了）"


def test_sign_dist_check_mode_does_not_require_certificate(tmp_path):
    """没有证书时 `--check` 只报告状态、返回 0（不阻塞打包流程）。"""
    import sign_dist

    fake = tmp_path / "dummy.exe"
    fake.write_bytes(b"MZ" + b"\x00" * 64)
    assert sign_dist.main(["--check", "--exe", str(fake)]) == 0


def test_sign_dist_without_args_returns_error(tmp_path, capsys):
    """既不 --check 也不给证书：明确报错（避免「以为签了其实没签」）。"""
    import sign_dist

    fake = tmp_path / "dummy.exe"
    fake.write_bytes(b"MZ" + b"\x00" * 64)
    assert sign_dist.main(["--exe", str(fake)]) == 3
    assert "代码签名" in capsys.readouterr().err


def test_signtool_lookup_does_not_crash():
    import sign_dist

    found = sign_dist.find_signtool()
    assert found is None or os.path.isfile(found)

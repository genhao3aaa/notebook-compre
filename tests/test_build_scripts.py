"""构建/发布脚本的回归测试：别把「正在用的东西」当成历史产物。

两条规则都是踩过坑才有的：

* `cleanup_old_versions.py` 曾经把正在使用的 `dist/` 送进回收站（用户看到的现象就是「文件被误删」）；
* `make_shortcut.py` 曾经无条件优先 `dist/`，把桌面快捷方式指到了旧版本。

因此这里锁死：**规范目录 `dist/` 是唯一交付物**，`dist_*` 只能是历史产物或临时别名。
"""

from __future__ import annotations

import os

import pytest

import make_shortcut

try:
    # send2trash 只被清理脚本用来「送进回收站」，是可选依赖：缺了就跳过对应用例，
    # 不能让它把整个测试套件卡死（requirements.txt 里本来也没有它）。
    import cleanup_old_versions as cleanup
except ModuleNotFoundError as _exc:   # pragma: no cover - 取决于环境
    cleanup = None
    _MISSING = str(_exc)
else:
    _MISSING = ""

needs_cleanup = pytest.mark.skipif(cleanup is None,
                                   reason="cleanup_old_versions 不可导入：%s" % _MISSING)

APP = "手写笔记本与PDF批注"
EXE = "手写笔记本与PDF批注.exe"


def _make_dist(root, name: str, mtime: float) -> str:
    """造一个假的构建目录 dist_x/手写笔记本与PDF批注/…exe，并指定修改时间。"""
    folder = os.path.join(str(root), name, APP)
    os.makedirs(folder, exist_ok=True)
    exe = os.path.join(folder, EXE)
    with open(exe, "wb") as f:
        f.write(b"MZ")
    os.utime(exe, (mtime, mtime))
    return exe


# ---------------------------------------------------------------------- #
# make_shortcut.find_target
# ---------------------------------------------------------------------- #
def test_find_target_prefers_newest_when_dist_is_stale(tmp_path):
    _make_dist(tmp_path, "dist", 1_000)
    newer = _make_dist(tmp_path, "dist_v5", 2_000)
    assert make_shortcut.find_target(str(tmp_path)) == newer, \
        "dist/ 是旧的时，必须选更新的 dist_*，否则快捷方式指向旧版本"


def test_find_target_prefers_canonical_dist_when_current(tmp_path):
    canonical = _make_dist(tmp_path, "dist", 2_000)
    _make_dist(tmp_path, "dist_v5", 1_500)
    _make_dist(tmp_path, "dist_tmp", 1_000)
    assert make_shortcut.find_target(str(tmp_path)) == canonical, \
        "dist/ 不比别人旧时应当直接用 dist/，避免快捷方式绕经临时别名"


def test_find_target_returns_none_without_exe(tmp_path):
    os.makedirs(os.path.join(str(tmp_path), "dist", APP))
    assert make_shortcut.find_target(str(tmp_path)) is None


def test_find_target_returns_none_for_missing_dir(tmp_path):
    assert make_shortcut.find_target(os.path.join(str(tmp_path), "没有这个目录")) is None


# ---------------------------------------------------------------------- #
# cleanup_old_versions
# ---------------------------------------------------------------------- #
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(cleanup, "BASE", str(tmp_path))
    monkeypatch.setattr(cleanup, "EXTRA", [])


@needs_cleanup
def test_cleanup_keeps_canonical_pair_and_cleans_versioned(tmp_path, monkeypatch):
    for name in ("dist", "build", "dist_v5", "build_v4"):
        os.makedirs(os.path.join(str(tmp_path), name), exist_ok=True)
    _isolate(monkeypatch, tmp_path)
    names = {os.path.basename(t) for t in cleanup.collect_targets()}
    assert names == {"dist_v5", "build_v4"}, "只清历史目录，绝不动 dist/ 与 build/"
    assert cleanup.live_build_looks_versioned(cleanup.collect_targets()) is False


@needs_cleanup
def test_cleanup_warns_when_deliverable_lives_in_versioned_dir(tmp_path, monkeypatch):
    os.makedirs(os.path.join(str(tmp_path), "dist_v5"), exist_ok=True)
    _isolate(monkeypatch, tmp_path)
    targets = cleanup.collect_targets()
    assert cleanup.live_build_looks_versioned(targets) is True, \
        "dist/ 不存在却只有 dist_* —— 那很可能就是正在用的交付物，必须拦住"


@needs_cleanup
def test_cleanup_skips_links(tmp_path, monkeypatch):
    os.makedirs(os.path.join(str(tmp_path), "dist"), exist_ok=True)
    os.makedirs(os.path.join(str(tmp_path), "build_v4"), exist_ok=True)
    _isolate(monkeypatch, tmp_path)

    linked = os.path.join(str(tmp_path), "dist_alias")
    if not _try_junction(linked, os.path.join(str(tmp_path), "dist")):
        pytest.skip("当前环境不允许创建目录联接")
    try:
        assert cleanup._is_link(linked) is True
        assert cleanup._is_link(str(tmp_path)) is False
        names = {os.path.basename(t) for t in cleanup.collect_targets()}
        assert "dist_alias" not in names, "目录联接/符号链接一律不清理（可能牵连目标）"
        assert names == {"build_v4"}
    finally:
        try:
            os.rmdir(linked)
        except OSError:
            pass


def _try_junction(link: str, target: str) -> bool:
    """尽力创建一个目录联接；环境不允许就返回 False。"""
    try:
        import _winapi
        _winapi.CreateJunction(target, link)
        return True
    except Exception:  # noqa: BLE001
        return False

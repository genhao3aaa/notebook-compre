"""A7 验收：结构化日志（`src/core/hn_log.py`）。

要求：默认安静、`HN_DEBUG=1` 兼容旧行为、`HN_LOG=<级别>` 覆盖、`HN_LOG_FILE` 落盘、
一条记录 = 一行（值里的空白/换行被转义）。
"""

from __future__ import annotations

import logging
import os

import pytest

from src.core import hn_log


@pytest.fixture(autouse=True)
def _restore_logging():
    """每个用例前后恢复日志状态（模块里有全局 _configured 与 handlers）。"""
    root = logging.getLogger("hn")
    saved = list(root.handlers), root.level, hn_log._configured
    yield
    root.handlers.clear()
    root.handlers.extend(saved[0])
    root.setLevel(saved[1])
    hn_log._configured = saved[2]


# --------------------------------------------------------------------- #
# 格式
# --------------------------------------------------------------------- #
def test_format_event_is_single_line_and_sorted():
    line = hn_log.format_event("pointer_down", y=3, x=1, tool="pen")
    assert line == "event=pointer_down tool=pen x=1 y=3"
    assert "\n" not in line


def test_format_event_escapes_whitespace_and_newlines():
    line = hn_log.format_event("oops", msg="a b\nc", path="D:\\x y")
    assert "\n" not in line
    assert "msg=a_b\\nc" in line
    assert "path=D:\\\\x_y" in line


def test_format_event_accepts_qt_enum():
    """Qt 枚举 / 标志必须能进日志字段。

    回归背景（v5.1 鼠标完全画不出字）：PySide6 6.11 起 `Qt.MouseButton` 是
    pybind11 的 flag（**不是** int 子类），`int(Qt.LeftButton)` 抛 `TypeError`。
    历史代码在 `mousePressEvent` 里写 `log_debug(..., button=int(e.button()))`，
    于是每次鼠标按下都在事件处理函数里抛异常 —— 手写笔（另一条通道）不受影响，
    症状就是「笔能写、鼠标不能」。日志层必须自己把枚举转成数字。
    """
    from PySide6.QtCore import Qt

    line = hn_log.format_event("mouse_press", button=Qt.LeftButton,
                               mods=Qt.NoModifier)
    assert line == "event=mouse_press button=1 mods=0"


def test_format_event_never_raises_on_hostile_value():
    """字段值再奇怪也只能少一条日志，绝不能把异常抛回业务代码。"""
    class _Boom:
        @property
        def value(self):
            raise RuntimeError("boom")

        def __repr__(self):
            return "<boom>"

    line = hn_log.format_event("weird", obj=_Boom(), none=None, flag=True)
    assert "event=weird" in line
    assert "obj=<boom>" in line
    assert "flag=True" in line


def test_log_event_swallows_handler_errors():
    """落盘/自定义 handler 出错时也不能影响调用方。"""
    log = hn_log.get_logger("hostile")

    class _BadHandler(logging.Handler):
        def emit(self, record):
            raise OSError("disk on fire")

    log.addHandler(_BadHandler())
    try:
        hn_log.log_warning(log, "still_ok", answer=1)     # 不应抛异常
    finally:
        log.handlers.clear()


# --------------------------------------------------------------------- #
# 级别解析（环境变量）
# --------------------------------------------------------------------- #
def test_resolve_level_precedence():
    assert hn_log.resolve_level({}) == logging.WARNING
    assert hn_log.resolve_level({"HN_DEBUG": "1"}) == logging.DEBUG
    assert hn_log.resolve_level({"HN_DEBUG": "1", "HN_LOG": "info"}) == logging.INFO
    assert hn_log.resolve_level({"HN_LOG": "DEBUG"}) == logging.DEBUG
    assert hn_log.resolve_level({"HN_LOG": "off"}) > logging.CRITICAL
    assert hn_log.resolve_level({"HN_LOG": "胡说"}) == logging.WARNING   # 非法值回退


def test_setup_respects_level_and_is_quiet_by_default(monkeypatch):
    monkeypatch.delenv("HN_LOG", raising=False)
    monkeypatch.delenv("HN_DEBUG", raising=False)
    monkeypatch.delenv("HN_LOG_FILE", raising=False)
    root = hn_log.setup(force=True, logfile="")
    assert root.level == logging.WARNING
    assert hn_log.enabled("debug") is False
    assert hn_log.enabled("warning") is True


def test_setup_debug_level_from_env(monkeypatch):
    monkeypatch.setenv("HN_DEBUG", "1")
    monkeypatch.delenv("HN_LOG", raising=False)
    root = hn_log.setup(force=True, logfile="")
    assert root.level == logging.DEBUG
    assert hn_log.enabled("debug") is True


def test_setup_writes_log_file(tmp_path, monkeypatch):
    monkeypatch.delenv("HN_DEBUG", raising=False)
    monkeypatch.delenv("HN_LOG", raising=False)
    path = tmp_path / "sub" / "hn.log"          # 目录不存在也要能建
    root = hn_log.setup(level=logging.DEBUG, logfile=str(path), force=True)
    log = hn_log.get_logger("test")
    hn_log.log_event(log, "unit_test", level="info", answer=42)
    logging.shutdown()
    text = path.read_text(encoding="utf-8")
    assert "event=unit_test answer=42" in text
    assert "hn.test" in text
    assert any(isinstance(h, logging.FileHandler) for h in root.handlers)


def test_get_logger_namespacing():
    assert hn_log.get_logger("ui.canvas").name == "hn.ui.canvas"
    assert hn_log.get_logger("hn.x").name == "hn.x"


def test_log_event_levels(monkeypatch):
    """三个级别都能被记录（注意 hn 日志器 propagate=False，所以用自定义 handler 收）。"""
    monkeypatch.delenv("HN_LOG", raising=False)
    root = hn_log.setup(level=logging.DEBUG, logfile="", force=True)
    captured = []

    class _Collect(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = _Collect()
    root.addHandler(handler)
    try:
        log = hn_log.get_logger("levels")
        hn_log.log_debug(log, "d1", k=1)
        hn_log.log_info(log, "i1", k=2)
        hn_log.log_warning(log, "w1", k=3)
    finally:
        root.removeHandler(handler)
    assert captured == ["event=d1 k=1", "event=i1 k=2", "event=w1 k=3"]


def test_default_setup_does_not_crash_without_env():
    # 不设置任何环境变量时反复调用也必须幂等
    hn_log.setup()
    hn_log.setup()
    assert hn_log.get_logger("x").name == "hn.x"

"""结构化日志（A7）：替代散落各处的 `print` / `_log`。

设计取舍
--------
- **只用标准库 `logging`**：不引入第三方依赖（打包体积已经够敏感了）；
- **默认安静**（WARNING）：日志是排查用的，不该在正常使用时往控制台刷东西；
- **两种开关**：
  * `HN_DEBUG=1` → DEBUG 级（历史上画布用它打印指针/触控笔事件，继续兼容）；
  * `HN_LOG=debug|info|warning|error` → 显式指定级别（优先于 HN_DEBUG）；
  * `HN_LOG_FILE=<路径>` → 同时落盘（默认只到 stderr）；
- **记录格式是 `事件 key=value`**：既能用 grep/awk 处理，也不影响人读。
  值里的空白与换行会转义，保证一条记录 = 一行。

用法：
    from src.core.hn_log import get_logger, log_event
    log = get_logger(__name__)
    log_event(log, "pointer_down", tool="pen", x=1.2, y=3.4)
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

_LEVELS = {
    "critical": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

_ROOT_NAME = "hn"
_configured = False


def _escape(value) -> str:
    text = str(value)
    text = text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    return text.replace(" ", "_").replace("\t", "\\t")


def _field_text(value) -> str:
    """把任意字段值变成一行文本，**保证不抛异常**。

    为什么单独抽出来：PySide6 6.11 起 `Qt.MouseButton` / `Qt.KeyboardModifier`
    这类「标志（flag）」不再是 `int` 的子类，`int(Qt.LeftButton)` 会直接抛
    `TypeError`。历史上 `log_debug(..., button=int(e.button()))` 就是这么写的，
    结果是**每次鼠标按下都在事件处理函数里抛异常**（鼠标完全画不出字），
    而手写笔走的是另一条通道所以看起来「只有鼠标坏了」。这类低级错误不该
    再有机会发生，所以：
      1. 优先取 `.value`（Qt 枚举 / 标志 → 数字，日志保持原有的数值语义）；
      2. 退回 `.name`；
      3. 再退回 `repr`；
      4. 全部包在 try 里——日志只是排查工具，绝不能反过来打断输入。
    """
    try:
        if value is None or isinstance(value, (str, int, float, bool)):
            return _escape(value)
    except Exception:                       # pragma: no cover - 兜底
        return "<unprintable>"
    for attr in ("value", "name"):
        try:
            got = getattr(value, attr, None)
        except Exception:                   # 属性本身就是个坑：试下一种写法
            continue
        if isinstance(got, (str, int, float)) and not isinstance(got, bool):
            return _escape(got)
    try:
        return _escape(repr(value))
    except Exception:                       # pragma: no cover - 兜底
        return "<unprintable>"


def format_event(event: str, **fields) -> str:
    """把一次事件格式化成 `event=名字 k=v k2=v2`（值按需转义）。"""
    parts = [f"event={_escape(event)}"]
    for key in sorted(fields):
        parts.append(f"{key}={_field_text(fields[key])}")
    return " ".join(parts)


def resolve_level(env: Optional[dict] = None) -> int:
    """按环境变量决定日志级别（HN_LOG 优先，其次 HN_DEBUG=1，默认 WARNING）。"""
    env = os.environ if env is None else env
    name = (env.get("HN_LOG") or "").strip().lower()
    if name in _LEVELS:
        return _LEVELS[name]
    if name == "off" or name == "none":
        return logging.CRITICAL + 1
    if (env.get("HN_DEBUG") or "") == "1":
        return logging.DEBUG
    return logging.WARNING


def setup(level: Optional[int] = None, logfile: Optional[str] = None,
          force: bool = False) -> logging.Logger:
    """初始化根日志器（幂等）。返回 `hn` 根日志器。"""
    global _configured
    root = logging.getLogger(_ROOT_NAME)
    if _configured and not force:
        return root

    root.handlers.clear()
    root.setLevel(resolve_level() if level is None else int(level))
    root.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname).1s %(name)s %(message)s",
                                  datefmt="%H:%M:%S")
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    path = logfile if logfile is not None else os.environ.get("HN_LOG_FILE")
    if path:
        try:
            directory = os.path.dirname(os.path.abspath(path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:                     # 落盘失败不影响程序
            pass
    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(f"{_ROOT_NAME}.{name}" if not name.startswith(_ROOT_NAME) else name)


def enabled(level: str = "debug") -> bool:
    """当前是否记录该级别（避免为了拼日志字符串而做无谓计算）。"""
    return get_logger("").isEnabledFor(_LEVELS.get(level, logging.DEBUG))


def log_event(logger: logging.Logger, event: str, level: str = "debug", **fields) -> None:
    """记录一次结构化事件。

    哪怕事件名或字段值有问题，也只是少一条日志：`logger.log` 本身也可能被
    自定义 handler 弄出异常（例如落盘的日志文件被删），这里一并吞掉。
    """
    try:
        message = format_event(event, **fields)
    except Exception:                       # pragma: no cover - 兜底
        message = f"event={event} <format_error>"
    try:
        logger.log(_LEVELS.get(level, logging.DEBUG), message)
    except Exception:                       # pragma: no cover - 兜底
        pass


def log_debug(logger: logging.Logger, event: str, **fields) -> None:
    log_event(logger, event, "debug", **fields)


def log_info(logger: logging.Logger, event: str, **fields) -> None:
    log_event(logger, event, "info", **fields)


def log_warning(logger: logging.Logger, event: str, **fields) -> None:
    log_event(logger, event, "warning", **fields)

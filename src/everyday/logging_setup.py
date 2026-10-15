"""日志配置（文档第九章）。

* 按日滚动，保留最近 7 天
* 文件内容包含时间戳、级别、消息、异常堆栈
* 禁止写入敏感信息（由调用方负责，本模块不提供脱敏以外的便利接口）
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from everyday import APP_NAME
from everyday.paths import logs_dir

#: 日志文件名（不含滚动后缀）
LOG_FILENAME = "everyday.log"

#: 保留天数（文档第九章：保留最近七天）
LOG_BACKUP_DAYS = 7

_LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False

#: 当前的按日滚动文件 handler，便于在数据目录变更时重新指向
_file_handler: logging.handlers.TimedRotatingFileHandler | None = None
_active_log_file: Path | None = None

#: 由本模块挂载的 handler，``reset_logging`` 只清理这些
_installed_handlers: list[logging.Handler] = []


def setup_logging(
    level: str = "INFO",
    *,
    log_directory: Path | None = None,
    console: bool = True,
) -> Path:
    """初始化全局日志，返回日志文件路径。

    重复调用是安全的：

    * 若目标目录与上次相同，只调整级别（避免 PyCharm 重跑或热重载时日志重复）
    * 若目标目录变了（``--data-dir`` 覆盖），把文件 handler 重新指向新目录。
      这一点很重要：启动早期会用默认目录初始化一次日志，
      随后拿到 ``--data-dir`` 时必须改写到真正的数据目录。
    """
    global _configured, _file_handler, _active_log_file

    target_dir = log_directory or logs_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    log_file = target_dir / LOG_FILENAME

    root = logging.getLogger()
    root.setLevel(_normalize_level(level))

    if _file_handler is not None and _active_log_file != log_file:
        root.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None

    if not _configured or _file_handler is None:
        formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=str(log_file),
            when="midnight",
            interval=1,
            backupCount=LOG_BACKUP_DAYS,
            encoding="utf-8",
            delay=True,
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        _installed_handlers.append(file_handler)
        _file_handler = file_handler
        _active_log_file = log_file

        if console and not any(
            isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.handlers.TimedRotatingFileHandler)
            for handler in root.handlers
        ):
            stream_handler = logging.StreamHandler(stream=sys.stderr)
            stream_handler.setFormatter(formatter)
            root.addHandler(stream_handler)
            _installed_handlers.append(stream_handler)

        # 第三方库的噪音降级
        logging.getLogger("PySide6").setLevel(logging.WARNING)
        _configured = True

    logging.getLogger(__name__).info(
        "%s 日志已就绪，级别=%s，文件=%s", APP_NAME, level, log_file
    )
    return log_file


def reset_logging() -> None:
    """拆除由本模块挂载的 handler，恢复到未初始化状态。

    存在两个理由：

    1. 集成测试需要在同一个进程里反复启动应用；每个实例都会把文件 handler
       指向自己的数据目录。Windows 上未关闭的 handler 会锁住日志文件，
       导致临时目录清理失败。
    2. 嵌入式/宿主场景下需要把日志控制权交还调用方。

    只清理 :data:`_installed_handlers` 里记录过的 handler，
    不会动别人（例如 pytest、宿主程序）挂上去的 handler。
    """
    global _configured, _file_handler, _active_log_file

    root = logging.getLogger()
    for handler in list(_installed_handlers):
        try:
            root.removeHandler(handler)
            handler.close()
        except Exception:  # pragma: no cover - 关闭失败不应影响调用方
            pass
    _installed_handlers.clear()
    _file_handler = None
    _active_log_file = None
    _configured = False


def _normalize_level(level: str | int | None) -> int:
    """把 ``"info"`` / ``"INFO"`` / 整数级别统一成 ``logging`` 常量。"""
    if isinstance(level, int):
        return level
    return getattr(logging, str(level or "INFO").upper(), logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """获取模块级 logger 的便捷函数。"""
    return logging.getLogger(name)

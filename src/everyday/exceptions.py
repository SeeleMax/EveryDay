"""异常捕获（文档第九章）。

应用注册三个层面的异常捕获：

1. **应用域异常**  —— ``sys.excepthook``，主线程未捕获异常
2. **异步任务未观察异常** —— ``threading.excepthook``，子线程异常
3. **UI 线程异常**  —— Qt 事件循环异常，由 ``everyday.ui`` 通过
   :func:`install_qt_message_handler` 所描述的 ``notify`` 钩子接入

任一层捕获异常后：写日志 + 弹出友好提示 + **程序不退出**。
"""

from __future__ import annotations

import logging
import sys
import threading
from types import TracebackType
from typing import Callable

logger = logging.getLogger(__name__)

#: 友好提示回调。签名 ``(title, message)``。由 UI 层注入；
#: 为空时只记日志（保证逻辑层可以在无 UI 环境运行）。
Notifier = Callable[[str, str], None]

#: 全局已安装标记，避免重复安装
_installed = False

#: 已上报过的异常指纹，用于抑制同一异常反复弹窗
_seen_fingerprints: set[str] = set()

#: 同一异常最多弹出提示的次数
_MAX_POPUPS_PER_FINGERPRINT = 1


def install(
    notifier: Notifier | None = None,
    *,
    install_thread_hook: bool = True,
) -> None:
    """安装全局异常钩子。

    :param notifier: 友好提示回调；``None`` 表示只记日志
    :param install_thread_hook: 是否同时接管 ``threading.excepthook``
    """
    global _installed
    _notifier = notifier

    def _excepthook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        # Ctrl+C 交回默认处理，避免打断用户主动中断
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        report_exception(exc_value, source="main", notifier=_notifier)

    sys.excepthook = _excepthook

    if install_thread_hook and hasattr(threading, "excepthook"):
        def _thread_hook(args: threading.ExceptHookArgs) -> None:
            if issubclass(args.exc_type, SystemExit):
                return
            thread_name = args.thread.name if args.thread else "unknown"
            report_exception(
                args.exc_value, source=f"thread:{thread_name}", notifier=_notifier
            )

        threading.excepthook = _thread_hook  # type: ignore[assignment]

    _installed = True
    logger.debug("全局异常钩子已安装")


def is_installed() -> bool:
    """返回全局异常钩子是否已安装（供测试断言）。"""
    return _installed


def report_exception(
    exc: BaseException,
    *,
    source: str = "unknown",
    notifier: Notifier | None = None,
    show_dialog: bool = True,
) -> None:
    """记录一个异常并按需弹出友好提示。

    本函数**自身不会抛出异常**：日志与提示失败都被吞掉并降级处理，
    否则异常处理器抛异常会造成递归崩溃。
    """
    fingerprint = f"{type(exc).__name__}:{exc}"
    try:
        logger.error("捕获到未处理异常（来源=%s）", source, exc_info=exc)
    except Exception:  # pragma: no cover - 日志系统本身故障时的兜底
        pass

    if notifier is None or not show_dialog:
        return

    if fingerprint in _seen_fingerprints:
        # 同一异常只提示一次，避免弹窗风暴
        return
    _seen_fingerprints.add(fingerprint)

    title = "EveryDay 遇到问题"
    message = (
        f"发生了一个未预期的错误，程序会继续运行。\n\n"
        f"来源：{source}\n"
        f"类型:{type(exc).__name__}\n"
        f"信息：{exc}\n\n"
        f"详细信息已写入日志文件，可在「设置」中查看日志目录。"
    )
    try:
        notifier(title, message)
    except Exception:  # pragma: no cover
        logger.exception("弹出友好提示失败")


def reset_popup_state() -> None:
    """清空「已提示异常」记录（供测试使用）。"""
    _seen_fingerprints.clear()


def guard(notifier: Notifier | None = None, source: str = "guard"):
    """装饰器：把函数体内的任何异常转为「记日志 + 提示」，不向上传播。

    适用于定时器回调、托盘菜单回调这类「抛异常就会静默失效」的入口。
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 这里是全局兜底，必须捕获全部
                report_exception(exc, source=source or func.__name__, notifier=notifier)
                return None

        wrapper.__name__ = getattr(func, "__name__", "wrapped")
        wrapper.__doc__ = getattr(func, "__doc__", None)
        return wrapper

    return decorator

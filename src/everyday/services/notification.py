"""通知端口（v1.1 P3）。

这里只定义**接口**与几个不依赖 Qt 的实现，具体怎么弹出通知由界面层决定。
这样调度逻辑可以完全脱离 Qt、脱离托盘做单元测试。

为什么不直接在服务里调 ``QSystemTrayIcon.showMessage``：

* 服务层不允许 import Qt（分层约束，见 docs/architecture.md）
* 托盘气泡在无图形会话（CI、测试）里不可用，会拖累测试
* Windows 的通知中心有更「正规」的通道（WinRT Toast），但它需要额外依赖
  与打包配置。先把调度逻辑与端口定下来，换实现只改一个类。

当前实现选择：**托盘气泡**（``QSystemTrayIcon.showMessage``）。
理由见 docs/known-issues.md 的 KI-18。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Notification:
    """一条待弹出的通知。

    ``actions`` 是「动作名 -> 回调」，界面层按自己的能力呈现
    （托盘气泡不支持按钮，只会在点击气泡时执行第一个动作）。
    """

    title: str
    message: str
    kind: str = "info"
    task_id: int | None = None


@runtime_checkable
class NotificationPort(Protocol):
    """通知端口。实现方负责真正把内容呈现给用户。"""

    def notify(self, notification: Notification) -> bool:
        """弹出一条通知，返回是否成功呈现。"""
        ...


class NullNotifier:
    """什么都不做的通知端口（无托盘、无图形会话时使用）。"""

    def notify(self, notification: Notification) -> bool:
        """记录一条调试日志后返回 False，表示未呈现。"""
        logger.debug("通知被丢弃（NullNotifier）：%s / %s", notification.title, notification.message)
        return False


class RecordingNotifier:
    """把通知记在内存里的通知端口（单元测试用）。

    ``handler`` 可选：传入一个函数可以在通知时做额外动作，
    例如模拟用户点击。
    """

    def __init__(self, handler: Callable[[Notification], None] | None = None) -> None:
        self.sent: list[Notification] = []
        self._handler = handler

    def notify(self, notification: Notification) -> bool:
        """记录通知并返回 True。"""
        self.sent.append(notification)
        if self._handler is not None:
            self._handler(notification)
        return True

    # ------------------------------------------------------------------
    @property
    def count(self) -> int:
        """已发出的通知数量。"""
        return len(self.sent)

    def titles(self) -> list[str]:
        """已发出通知的标题列表，便于断言。"""
        return [n.title for n in self.sent]

    def messages(self) -> list[str]:
        """已发出通知的正文列表。"""
        return [n.message for n in self.sent]

    def clear(self) -> None:
        """清空记录。"""
        self.sent.clear()

    def for_task(self, task_id: int) -> list[Notification]:
        """筛出某个任务的通知。"""
        return [n for n in self.sent if n.task_id == task_id]

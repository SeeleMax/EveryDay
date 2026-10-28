"""托盘气泡形式的通知实现（v1.1 P3）。

服务层只认 :class:`everyday.services.notification.NotificationPort`，
本模块负责把它落到托盘气泡上。

为什么用托盘气泡而不是 Windows 通知中心
----------------------------------------
Windows 的 Toast 需要 WinRT（``winrt-Windows.UI.Notifications``）或
未文档化的 ``Shell_NotifyIcon`` 扩展，前者要额外依赖、后者要 ctypes 拼结构，
两者都会显著增加打包复杂度（PyInstaller 还要处理 WinRT 的元数据）。

托盘气泡 ``QSystemTrayIcon.showMessage`` 的优势是**零额外依赖**、随
PySide6 一起打包、在 Windows 10/11 上表现稳定。代价是不进通知中心、
不支持按钮（点气泡等价于「打开主窗口」）。

后续若要升级到通知中心，只需另写一个实现 ``NotificationPort`` 的类，
服务层与调度逻辑一行不用改。
"""

from __future__ import annotations

import logging

from everyday.services.notification import Notification, NotificationPort

logger = logging.getLogger(__name__)

#: 气泡停留时长（毫秒）。文档没规定，取一个不碍事又能看清的值。
BALLOON_TIMEOUT_MS = 5000


class TrayNotifier(NotificationPort):
    """通过托盘气泡呈现通知。

    ``tray`` 需要提供 ``notify(title, message)`` 方法（见
    :class:`everyday.ui.tray.TrayIcon`）。没有托盘时退化为只写日志，
    绝不因为「弹不出通知」而抛异常——提醒失败不该让应用崩掉。
    """

    def __init__(self, tray=None) -> None:
        self._tray = tray
        self._suppressed = False

    def set_suppressed(self, suppressed: bool) -> None:
        """临时静音（例如专注期间的额外保护，与调度器的屏蔽互补）。"""
        self._suppressed = suppressed

    def notify(self, notification: Notification) -> bool:
        """弹出气泡；``suppressed`` 为真时直接返回 False。"""
        if self._suppressed:
            logger.debug("通知处于静音状态，丢弃：%s", notification.title)
            return False
        if self._tray is None:
            logger.info("[无托盘] 通知：%s / %s", notification.title, notification.message)
            return False
        try:
            self._tray.notify(notification.title, notification.message)
        except Exception:
            logger.exception("托盘气泡弹出失败：%s", notification.title)
            return False
        logger.info("已弹出通知：%s / %s", notification.title, notification.message)
        return True

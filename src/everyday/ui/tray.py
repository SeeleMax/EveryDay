"""系统托盘（文档第七章）。

托盘菜单：显示主窗口、显示/隐藏悬浮窗（可勾选）、开机自启开关（可勾选）、退出应用。
关闭主窗口默认最小化到托盘，只有通过托盘菜单或主窗口菜单才能退出。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from everyday import APP_DISPLAY_NAME
from everyday.ui import icons
from everyday.ui.context import AppContext

logger = logging.getLogger(__name__)


class TrayIcon(QSystemTrayIcon):
    """常驻托盘图标与右键菜单。"""

    show_main_requested = Signal()
    float_toggle_requested = Signal(bool)
    autostart_toggle_requested = Signal(bool)
    quit_requested = Signal()

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(icons.app_icon(), parent)
        self._context = context
        self.setToolTip(APP_DISPLAY_NAME)

        menu = QMenu(parent)

        self.show_action = QAction("显示主窗口", menu)
        self.show_action.triggered.connect(self.show_main_requested.emit)
        menu.addAction(self.show_action)

        menu.addSeparator()

        self.float_action = QAction("显示悬浮窗", menu)
        self.float_action.setCheckable(True)
        self.float_action.setChecked(context.settings.float_window_visible)
        self.float_action.toggled.connect(self.float_toggle_requested.emit)
        menu.addAction(self.float_action)

        self.autostart_action = QAction("开机自启", menu)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(context.settings.auto_start)
        self.autostart_action.setEnabled(context.startup_service.is_supported())
        self.autostart_action.toggled.connect(self.autostart_toggle_requested.emit)
        menu.addAction(self.autostart_action)

        menu.addSeparator()

        self.quit_action = QAction("退出应用", menu)
        self.quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(self.quit_action)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)
        self._context.settings_changed.connect(self._sync_from_settings)
        self._context.float_visibility_changed.connect(self.sync_float_visible)

    # ------------------------------------------------------------------
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """双击托盘图标显示主窗口。"""
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.show_main_requested.emit()

    def _sync_from_settings(self) -> None:
        self.sync_float_visible(self._context.settings.float_window_visible)
        self.sync_autostart(self._context.settings.auto_start)

    def sync_float_visible(self, visible: bool) -> None:
        """同步「显示悬浮窗」勾选状态。"""
        self.float_action.blockSignals(True)
        self.float_action.setChecked(visible)
        self.float_action.blockSignals(False)

    def sync_autostart(self, enabled: bool) -> None:
        """同步「开机自启」勾选状态。"""
        self.autostart_action.blockSignals(True)
        self.autostart_action.setChecked(enabled)
        self.autostart_action.blockSignals(False)

    def set_progress_text(self, text: str) -> None:
        """把今日进度写进托盘提示文本。"""
        self.setToolTip(f"{APP_DISPLAY_NAME}\n今日完成 {text}")

    def notify(self, title: str, message: str) -> None:
        """弹出气泡提示（关闭主窗口时给予反馈）。"""
        if self.supportsMessages():
            self.showMessage(title, message, icons.app_icon(), 3000)

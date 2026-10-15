"""界面上下文：把服务层与 Qt 的信号机制接起来。

所有窗口共用同一个 :class:`AppContext`：

* 窗口不直接互相调用，只发信号、听信号，避免「改了主窗口忘了刷悬浮窗」
* 服务对象集中注入，界面代码不自己 ``new`` 服务
"""

from __future__ import annotations

import logging
from datetime import date

from PySide6.QtCore import QObject, Signal

from everyday import timeutils
from everyday.models.settings import AppSettings
from everyday.services.calendar_service import CalendarService
from everyday.services.preset_service import PresetService
from everyday.services.settings_service import SettingsService
from everyday.services.startup_service import StartupService
from everyday.services.task_service import TaskProgress, TaskService

logger = logging.getLogger(__name__)


class AppContext(QObject):
    """窗口之间共享的服务容器与信号总线。"""

    #: 任务集合发生变化（增删改、完成状态切换）
    tasks_changed = Signal()
    #: 设置发生变化
    settings_changed = Signal()
    #: 日历数据发生变化
    calendar_changed = Signal()
    #: 悬浮窗显隐状态被切换（参数为是否可见）
    float_visibility_changed = Signal(bool)

    def __init__(
        self,
        *,
        task_service: TaskService,
        preset_service: PresetService,
        settings_service: SettingsService,
        calendar_service: CalendarService,
        startup_service: StartupService,
        settings: AppSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.task_service = task_service
        self.preset_service = preset_service
        self.settings_service = settings_service
        self.calendar_service = calendar_service
        self.startup_service = startup_service
        self._settings = settings
        #: 应用是否正在退出。见 :meth:`begin_shutdown` 的说明。
        self.shutting_down = False

    # ------------------------------------------------------------------
    def begin_shutdown(self) -> None:
        """标记「应用正在退出」，此后窗口必须放行关闭事件。

        为什么需要这个标志
        ------------------
        ``QApplication.quit()`` 会先给所有顶层窗口派发 close 事件。
        而主窗口与悬浮窗为了「关闭 = 最小化到托盘」都调用了
        ``event.ignore()``。只要有**任何一个**窗口拒绝关闭，
        事件循环就不会退出——表现为用户点了「退出应用」但程序纹丝不动。

        因此退出流程必须先把本标志置位，让窗口区分
        「用户点了窗口的 ×」与「应用正在退出」这两种情况。
        """
        self.shutting_down = True

    # ------------------------------------------------------------------
    @property
    def settings(self) -> AppSettings:
        """当前设置（内存副本，写入后会自动刷新）。"""
        return self._settings

    def update_settings(self, **changes) -> AppSettings:
        """更新设置并广播 ``settings_changed``。"""
        self._settings = self.settings_service.update(**changes)
        self.settings_changed.emit()
        return self._settings

    def apply_settings(self, settings: AppSettings) -> AppSettings:
        """整份保存设置并广播。"""
        self._settings = self.settings_service.save(settings)
        self.settings_changed.emit()
        return self._settings

    # ------------------------------------------------------------------
    def today(self) -> date:
        """今天的日期。"""
        return timeutils.today_local()

    def today_tasks(self) -> list:
        """今日任务列表（会顺带执行循环状态校对）。"""
        return self.task_service.refresh(self.today())

    def progress(self) -> TaskProgress:
        """今日完成进度。"""
        return self.task_service.progress_today(self.today())

    def today_events(self) -> list:
        """今日日程。"""
        return self.calendar_service.today_events(self.today())

    def notify_tasks_changed(self) -> None:
        """广播任务变化（界面改动后调用）。"""
        self.tasks_changed.emit()

    def notify_calendar_changed(self) -> None:
        """广播日历变化。"""
        self.calendar_changed.emit()

    def set_float_visible(self, visible: bool) -> None:
        """把悬浮窗显隐状态写回设置并广播。

        主窗口开关与托盘菜单共用本方法，从而实现文档 5.2 要求的
        「两处状态实时同步」。
        """
        if self._settings.float_window_visible == visible:
            self.float_visibility_changed.emit(visible)
            return
        self.update_settings(float_window_visible=visible)
        self.float_visibility_changed.emit(visible)

"""服务层：业务规则与外部资源交互。

这一层不依赖 :mod:`everyday.ui`，因此可以在无 GUI 环境下完整测试。
"""

from __future__ import annotations

from everyday.services.backup_service import (
    DEFAULT_KEEP,
    BackupService,
    backup_filename,
    create_backup,
    list_backups,
    prune,
)
from everyday.services.calendar_service import CalendarService, CalendarSnapshot
from everyday.services.dependency_service import DependencyCycleError, DependencyService
from everyday.services.ics_parser import CalendarEvent, load_events, parse_ics
from everyday.services.notification import (
    Notification,
    NotificationPort,
    NullNotifier,
    RecordingNotifier,
)
from everyday.services.pomodoro_service import (
    DEFAULT_BREAK_MINUTES,
    DEFAULT_FOCUS_MINUTES,
    PomodoroService,
    PomodoroSnapshot,
    PomodoroState,
)
from everyday.services.preset_service import PresetService
from everyday.services.reminder_service import ReminderScheduler
from everyday.services.settings_service import SettingsService
from everyday.services.startup_service import StartupService, build_command
from everyday.services.task_service import TaskProgress, TaskService

__all__ = [
    "TaskService",
    "TaskProgress",
    "CalendarService",
    "CalendarSnapshot",
    "CalendarEvent",
    "parse_ics",
    "load_events",
    "PresetService",
    "SettingsService",
    "StartupService",
    "build_command",
    "BackupService",
    "create_backup",
    "list_backups",
    "prune",
    "backup_filename",
    "DEFAULT_KEEP",
    # v1.1 P2
    "DependencyService",
    "DependencyCycleError",
    # v1.1 P3
    "Notification",
    "NotificationPort",
    "NullNotifier",
    "RecordingNotifier",
    "PomodoroService",
    "PomodoroSnapshot",
    "PomodoroState",
    "DEFAULT_FOCUS_MINUTES",
    "DEFAULT_BREAK_MINUTES",
    "ReminderScheduler",
]

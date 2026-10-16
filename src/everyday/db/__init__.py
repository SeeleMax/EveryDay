"""数据层：SQLite 连接、迁移与仓储。

    from everyday.db import open_database
    from everyday.db.repositories import TaskRepository

    db = open_database(paths.database_path())
    tasks = TaskRepository(db)
"""

from __future__ import annotations

from everyday.db.connection import MEMORY, Database, open_database
from everyday.db.migrations import (
    Migration,
    MigrationRunner,
    discover_migrations,
    split_statements,
)
from everyday.db.repositories import (
    DependencyRepository,
    FocusRepository,
    HistoryRepository,
    PresetRepository,
    ReminderRepository,
    ScreenTimeRepository,
    SettingsRepository,
    TagRepository,
    TaskRepository,
)

__all__ = [
    "Database",
    "MEMORY",
    "open_database",
    "Migration",
    "MigrationRunner",
    "discover_migrations",
    "split_statements",
    "TaskRepository",
    "PresetRepository",
    "SettingsRepository",
    # v1.1
    "TagRepository",
    "DependencyRepository",
    "HistoryRepository",
    "FocusRepository",
    "ScreenTimeRepository",
    "ReminderRepository",
]

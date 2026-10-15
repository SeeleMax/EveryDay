"""数据层：SQLite 连接、迁移与仓储。

    from everyday.db import open_database
    from everyday.db.repositories import TaskRepository

    db = open_database(paths.database_path())
    tasks = TaskRepository(db)
"""

from __future__ import annotations

from everyday.db.connection import Database, MEMORY, open_database
from everyday.db.migrations import Migration, MigrationRunner, discover_migrations
from everyday.db.repositories import PresetRepository, SettingsRepository, TaskRepository

__all__ = [
    "Database",
    "MEMORY",
    "open_database",
    "Migration",
    "MigrationRunner",
    "discover_migrations",
    "TaskRepository",
    "PresetRepository",
    "SettingsRepository",
]

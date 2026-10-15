"""测试公共夹具。

测试统一用 **内存 SQLite**（文档要求：禁止 Mock 数据库），
每个用例拿到一个已完成迁移的干净库。
"""

from __future__ import annotations

import os
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from everyday.db import MEMORY, Database, MigrationRunner  # noqa: E402
from everyday.db.repositories import (  # noqa: E402
    PresetRepository,
    SettingsRepository,
    TaskRepository,
)


class DatabaseTestCase(unittest.TestCase):
    """提供 ``self.db`` / 三个仓储的基类。"""

    def setUp(self) -> None:
        self.db = Database(MEMORY)
        MigrationRunner(self.db).run()
        self.tasks = TaskRepository(self.db)
        self.presets = PresetRepository(self.db)
        self.settings = SettingsRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

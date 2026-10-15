"""备份服务测试（文档第八章：启动备份、保留最近五份）。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from everyday.db import Database, MigrationRunner
from everyday.db.repositories import TaskRepository
from everyday.models.task import TaskItem, TaskType
from everyday.services.backup_service import (
    DEFAULT_KEEP,
    BackupService,
    backup_filename,
    create_backup,
    list_backups,
    prune,
)


class BackupFilenameTest(unittest.TestCase):
    def test_filename_contains_iso_date(self):
        self.assertEqual(backup_filename(date(2026, 10, 5)), "everyday-2026-10-05.db")


def count_tasks(database_file: Path) -> int:
    """打开备份文件统计任务数。

    注意必须显式 ``close()``：``with sqlite3.connect(...)`` 只负责
    提交/回滚，**不会关闭连接**，在 Windows 上会让文件保持被占用状态，
    导致测试结束时的临时目录清理失败。
    """
    connection = sqlite3.connect(str(database_file))
    try:
        return int(connection.execute("SELECT COUNT(*) FROM task_item").fetchone()[0])
    finally:
        connection.close()


class BackupServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.backups = self.root / "backups"
        self.source_path = self.root / "everyday.db"
        self.database = Database(self.source_path)
        MigrationRunner(self.database).run()
        self.tasks = TaskRepository(self.database)
        self.tasks.add(TaskItem(title="喝水", type=TaskType.DAILY))

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    def test_backup_creates_file(self):
        created = create_backup(self.database, directory=self.backups, day=date(2026, 10, 5))
        self.assertIsNotNone(created)
        self.assertTrue(created.is_file())
        self.assertEqual(created.name, "everyday-2026-10-05.db")

    def test_backup_preserves_committed_data(self):
        """WAL 模式下最新提交必须出现在备份里。"""
        created = create_backup(self.database, directory=self.backups, day=date(2026, 10, 5))
        self.assertEqual(count_tasks(created), 1)

    def test_same_day_backup_overwrites_instead_of_accumulating(self):
        create_backup(self.database, directory=self.backups, day=date(2026, 10, 5))
        self.tasks.add(TaskItem(title="跑步", type=TaskType.DAILY))
        create_backup(self.database, directory=self.backups, day=date(2026, 10, 5))

        files = list_backups(self.backups)
        self.assertEqual(len(files), 1)
        self.assertEqual(count_tasks(files[0]), 2)

    def test_only_five_backups_are_kept(self):
        start = date(2026, 10, 1)
        for offset in range(7):
            create_backup(
                self.database,
                directory=self.backups,
                day=start + timedelta(days=offset),
            )

        files = list_backups(self.backups)
        self.assertEqual(len(files), DEFAULT_KEEP)
        # 保留的应该是最近 5 天
        self.assertEqual(
            [f.name for f in files],
            [backup_filename(start + timedelta(days=offset)) for offset in range(2, 7)],
        )

    def test_prune_reports_removed_files(self):
        start = date(2026, 10, 1)
        for offset in range(4):
            create_backup(
                self.database, directory=self.backups, day=start + timedelta(days=offset)
            )
        removed = prune(self.backups, keep=2)
        self.assertEqual(len(removed), 2)
        self.assertEqual(len(list_backups(self.backups)), 2)

    def test_prune_is_noop_when_under_limit(self):
        create_backup(self.database, directory=self.backups, day=date(2026, 10, 5))
        self.assertEqual(prune(self.backups, keep=5), [])

    def test_missing_source_returns_none(self):
        self.assertIsNone(create_backup(self.root / "不存在.db", directory=self.backups))

    def test_unopened_database_object_returns_none_without_creating_file(self):
        """首次运行：库文件还不存在时就跳过备份，且不得顺手创建空库文件。"""
        fresh_path = self.root / "brand-new.db"
        fresh = Database(fresh_path)
        try:
            self.assertIsNone(create_backup(fresh, directory=self.backups))
            self.assertFalse(fresh_path.exists(), "不应因为备份而创建出空数据库文件")
            self.assertEqual(list_backups(self.backups), [])
        finally:
            fresh.close()

    def test_in_memory_database_is_still_backed_up(self):
        """内存库没有文件可判断，必须照常备份（否则该路径无法使用）。"""
        memory = Database(":memory:")
        try:
            MigrationRunner(memory).run()
            created = create_backup(memory, directory=self.backups, day=date(2026, 10, 5))
            self.assertIsNotNone(created)
        finally:
            memory.close()

    def test_empty_source_file_returns_none(self):
        empty = self.root / "empty.db"
        empty.write_bytes(b"")
        self.assertIsNone(create_backup(empty, directory=self.backups))

    def test_backup_from_path_works(self):
        created = create_backup(self.source_path, directory=self.backups, day=date(2026, 10, 5))
        self.assertIsNotNone(created)
        self.assertTrue(created.is_file())

    def test_backup_directory_is_created_on_demand(self):
        nested = self.root / "a" / "b"
        create_backup(self.database, directory=nested, day=date(2026, 10, 5))
        self.assertTrue(nested.is_dir())

    def test_list_backups_ignores_unrelated_files(self):
        self.backups.mkdir(parents=True, exist_ok=True)
        (self.backups / "readme.txt").write_text("x", encoding="utf-8")
        create_backup(self.database, directory=self.backups, day=date(2026, 10, 5))
        self.assertEqual(len(list_backups(self.backups)), 1)

    def test_list_backups_on_missing_directory_is_empty(self):
        self.assertEqual(list_backups(self.root / "nope"), [])

    def test_service_facade_runs_startup_backup(self):
        service = BackupService(directory=self.backups)
        created = service.run_startup_backup(self.database)
        self.assertIsNotNone(created)
        self.assertEqual(len(service.available()), 1)


if __name__ == "__main__":
    unittest.main()

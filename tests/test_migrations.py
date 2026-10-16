"""迁移与连接测试：建表、幂等、账本、WAL、版本排序。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from everyday.db import MEMORY, Database, MigrationRunner, discover_migrations
from everyday.paths import migrations_dir
from tests.base import DatabaseTestCase


def table_names(database: Database) -> set[str]:
    """返回数据库里所有用户表名。"""
    rows = database.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row["name"] for row in rows}


class MigrationDiscoveryTest(unittest.TestCase):
    def test_repository_migrations_are_discovered(self):
        found = discover_migrations(migrations_dir())
        versions = [m.version for m in found]
        self.assertIn("001", versions)

    def test_discovery_orders_numerically_not_lexically(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in ("010_c.sql", "002_a.sql", "001_z.sql"):
                (directory / name).write_text("SELECT 1;", encoding="utf-8")
            versions = [m.version for m in discover_migrations(directory)]
            self.assertEqual(versions, ["001", "002", "010"])

    def test_non_matching_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "README.md").write_text("hi", encoding="utf-8")
            (directory / "abc.sql").write_text("SELECT 1;", encoding="utf-8")
            (directory / "001_ok.sql").write_text("SELECT 1;", encoding="utf-8")
            versions = [m.version for m in discover_migrations(directory)]
            self.assertEqual(versions, ["001"])

    def test_missing_directory_returns_empty(self):
        self.assertEqual(discover_migrations(Path("Z:/definitely/not/here")), [])


class MigrationRunnerTest(DatabaseTestCase):
    def test_initial_migration_creates_all_business_tables(self):
        names = table_names(self.db)
        self.assertIn("task_item", names)
        self.assertIn("preset_template", names)
        self.assertIn("app_settings", names)
        self.assertIn("schema_migrations", names)

    def test_default_presets_are_seeded_on_first_run(self):
        self.assertGreaterEqual(self.presets.count(), 9)
        categories = set(self.presets.categories())
        self.assertTrue({"工作", "健康", "学习"}.issubset(categories))

    def test_runner_is_idempotent(self):
        runner = MigrationRunner(self.db)
        self.assertEqual(runner.run(), [])
        self.assertEqual(runner.pending(), [])

    def test_ledger_records_checksum_and_version(self):
        rows = self.db.query("SELECT version, applied_at, checksum FROM schema_migrations")
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(row["applied_at"])
            self.assertEqual(len(row["checksum"]), 64)  # SHA-256 十六进制长度

    def test_current_version_reports_latest(self):
        # v1.1 加入了迁移 002，这里跟随最新的迁移号
        self.assertEqual(MigrationRunner(self.db).current_version(), "002")

    def test_presets_are_not_duplicated_by_second_run(self):
        before = self.presets.count()
        MigrationRunner(self.db).run()
        self.assertEqual(self.presets.count(), before)

    def test_custom_directory_migration_is_applied_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "001_a.sql").write_text(
                "CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT);", encoding="utf-8"
            )
            database = Database(MEMORY)
            try:
                runner = MigrationRunner(database, directory)
                self.assertEqual(runner.run(), ["001"])
                self.assertIn("demo", table_names(database))
                self.assertEqual(runner.run(), [])
            finally:
                database.close()


class DatabaseConnectionTest(unittest.TestCase):
    def test_memory_database_is_flagged(self):
        database = Database(MEMORY)
        try:
            self.assertTrue(database.is_memory)
        finally:
            database.close()

    def test_file_database_uses_wal_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(Path(tmp) / "everyday.db")
            try:
                database.connect()
                mode = database.query_scalar("PRAGMA journal_mode")
                self.assertEqual(str(mode).lower(), "wal")
            finally:
                database.close()

    def test_transaction_rolls_back_on_error(self):
        database = Database(MEMORY)
        try:
            MigrationRunner(database).run()
            before = database.query_scalar("SELECT COUNT(*) FROM preset_template")
            with self.assertRaises(RuntimeError):
                with database.transaction() as connection:
                    connection.execute(
                        "INSERT INTO preset_template (title, type, category) "
                        "VALUES ('临时', 'daily', '测试')"
                    )
                    raise RuntimeError("模拟失败")
            after = database.query_scalar("SELECT COUNT(*) FROM preset_template")
            self.assertEqual(after, before)
        finally:
            database.close()

    def test_foreign_keys_and_busy_timeout_are_set(self):
        database = Database(MEMORY)
        try:
            database.connect()
            self.assertEqual(int(database.query_scalar("PRAGMA foreign_keys")), 1)
            self.assertEqual(int(database.query_scalar("PRAGMA busy_timeout")), 5000)
        finally:
            database.close()

    def test_close_is_idempotent(self):
        database = Database(MEMORY)
        database.connect()
        database.close()
        database.close()
        self.assertFalse(database.is_open)

    def test_title_length_constraint_is_enforced_by_schema(self):
        database = Database(MEMORY)
        try:
            MigrationRunner(database).run()
            import sqlite3

            with self.assertRaises(sqlite3.IntegrityError):
                database.execute(
                    "INSERT INTO task_item (title, type, created_at) VALUES (?, 'daily', ?)",
                    ("x" * 101, "2026-10-05T09:00:00"),
                )
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()

"""v1.1 P1 数据层测试：迁移 002、新实体、新仓储。

覆盖范围与交接文档 P1 的要求对应：

* 「实现附录的全部 Schema 变更」→ 迁移 002 的表/列是否齐全、是否幂等
* 「数据访问层单元测试」→ 六个新仓储的增删改查
* 日志框架 / 全局异常捕获 / 启动备份在 v1.0 已完成，本文件不重复
"""

from __future__ import annotations

import sqlite3
import unittest
from datetime import date, datetime, timedelta

from everyday import timeutils
from everyday.db import (
    MEMORY,
    Database,
    DependencyRepository,
    FocusRepository,
    HistoryRepository,
    MigrationRunner,
    ReminderRepository,
    ScreenTimeRepository,
    TagRepository,
    discover_migrations,
    split_statements,
)
from everyday.models.focus import FocusRecord
from everyday.models.history import HistoryAction, HistoryEntry
from everyday.models.reminder import ReminderLog
from everyday.models.screen_time import ScreenTimeRecord, format_duration
from everyday.models.task import TaskItem, TaskPriority, TaskType
from tests.base import DatabaseTestCase

MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)


# ======================================================================
# 迁移
# ======================================================================
class MigrationV11Test(DatabaseTestCase):
    """迁移 002 的结构与幂等性。"""

    V11_TABLES = {
        "task_tag",
        "task_dependency",
        "task_history",
        "focus_record",
        "screen_time_record",
        "reminder_log",
    }

    def test_discovery_finds_both_migrations_in_order(self):
        versions = [m.version for m in discover_migrations()]
        self.assertEqual(versions[:2], ["001", "002"])

    def test_current_version_is_002(self):
        self.assertEqual(MigrationRunner(self.db).current_version(), "002")

    def test_v11_tables_created(self):
        rows = self.db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
        names = {row["name"] for row in rows}
        self.assertTrue(
            self.V11_TABLES <= names, f"缺少表：{sorted(self.V11_TABLES - names)}"
        )

    def test_task_item_gained_v11_columns(self):
        columns = {row["name"] for row in self.db.query("PRAGMA table_info(task_item)")}
        self.assertTrue({"priority", "parent_task_id", "deleted_at"} <= columns)

    def test_priority_defaults_to_normal_for_existing_rows(self):
        task_id = self.tasks.add(TaskItem(title="喝水", type=TaskType.DAILY))
        raw = self.db.query_one("SELECT priority FROM task_item WHERE id = ?", (task_id,))
        self.assertEqual(int(raw["priority"]), int(TaskPriority.NORMAL))

    def test_v11_indexes_created(self):
        rows = self.db.query("SELECT name FROM sqlite_master WHERE type = 'index'")
        names = {row["name"] for row in rows}
        expected = {
            "idx_task_item_priority",
            "idx_task_item_parent",
            "idx_task_item_deleted",
            "idx_task_tag_tag",
            "idx_task_dependency_dep",
            "idx_task_history_time",
            "idx_focus_record_started",
            "idx_screen_time_day",
            "idx_reminder_log_date",
        }
        self.assertTrue(expected <= names, f"缺少索引：{sorted(expected - names)}")

    def test_rerunning_migration_002_is_safe(self):
        """D03 要求迁移幂等：把 002 的语句再执行一遍不能报错。

        ALTER TABLE ADD COLUMN 没有 IF NOT EXISTS，所以这条测试是
        「执行器逐语句容错」这一设计的回归保护。
        """
        from pathlib import Path

        from everyday.paths import migrations_dir

        sql = (Path(migrations_dir()) / "002_v11.sql").read_text(encoding="utf-8")
        connection = self.db.connect()
        tolerated = 0
        for statement in split_statements(sql):
            try:
                connection.execute(statement)
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "duplicate column name" in message or "already exists" in message:
                    tolerated += 1
                    continue
                self.fail(f"重复执行 002 出现未预期错误：{exc}\n语句：{statement[:80]}")
        self.assertEqual(tolerated, 3, "应当正好是 3 条 ALTER TABLE ADD COLUMN")

    def test_rerunning_runner_changes_nothing(self):
        self.assertEqual(MigrationRunner(self.db).run(), [])


class SplitStatementsTest(unittest.TestCase):
    """SQL 切分器：字符串里的分号不能被误切。"""

    def test_splits_simple_statements(self):
        script = "CREATE TABLE a (id INTEGER);\nCREATE TABLE b (id INTEGER);\n"
        self.assertEqual(len(split_statements(script)), 2)

    def test_ignores_semicolon_inside_string_literal(self):
        script = "INSERT INTO t (v) VALUES ('');\nINSERT INTO t (v) VALUES ('a;b');\n"
        statements = split_statements(script)
        self.assertEqual(len(statements), 2)
        self.assertIn("'a;b'", statements[1])

    def test_keeps_comment_lines_with_statement(self):
        script = "-- 注释里有个分号;\nCREATE TABLE t (id INTEGER);\n"
        statements = split_statements(script)
        self.assertEqual(len(statements), 1)
        self.assertIn("CREATE TABLE t", statements[0])

    def test_trailing_statement_without_semicolon_is_kept(self):
        self.assertEqual(len(split_statements("SELECT 1")), 1)

    def test_blank_script_yields_nothing(self):
        self.assertEqual(split_statements("\n\n-- 只有注释\n"), [])


# ======================================================================
# 实体
# ======================================================================
class TaskPriorityTest(unittest.TestCase):
    def test_numeric_order_is_urgent_first(self):
        self.assertLess(int(TaskPriority.URGENT), int(TaskPriority.LOW))

    def test_labels(self):
        self.assertEqual(TaskPriority.URGENT.label, "紧急")
        self.assertEqual(TaskPriority.NORMAL.label, "普通")

    def test_from_value_accepts_int_str_and_label(self):
        self.assertIs(TaskPriority.from_value(1), TaskPriority.HIGH)
        self.assertIs(TaskPriority.from_value("3"), TaskPriority.LOW)
        self.assertIs(TaskPriority.from_value("紧急"), TaskPriority.URGENT)

    def test_from_value_falls_back_to_normal(self):
        self.assertIs(TaskPriority.from_value(None), TaskPriority.NORMAL)
        self.assertIs(TaskPriority.from_value(99), TaskPriority.NORMAL)
        self.assertIs(TaskPriority.from_value("不存在"), TaskPriority.NORMAL)


class TaskItemV11Test(unittest.TestCase):
    def test_defaults(self):
        task = TaskItem(title="喝水", type=TaskType.DAILY)
        self.assertIs(task.priority, TaskPriority.NORMAL)
        self.assertIsNone(task.parent_task_id)
        self.assertIsNone(task.deleted_at)
        self.assertFalse(task.is_deleted)

    def test_cannot_be_its_own_parent(self):
        with self.assertRaises(ValueError):
            TaskItem(title="喝水", type=TaskType.DAILY, id=5, parent_task_id=5)

    def test_soft_delete_and_restore_round_trip(self):
        task = TaskItem(title="喝水", type=TaskType.DAILY)
        removed = task.deleted(datetime(2026, 10, 5, 9, 0, 0))
        self.assertTrue(removed.is_deleted)
        self.assertEqual(removed.deleted_at, datetime(2026, 10, 5, 9, 0, 0))
        self.assertFalse(removed.restored().is_deleted)

    def test_to_row_from_row_round_trip_with_v11_fields(self):
        original = TaskItem(
            id=7,
            title="写周报",
            type=TaskType.ONCE,
            due_date=MONDAY,
            priority=TaskPriority.HIGH,
            parent_task_id=3,
            deleted_at=datetime(2026, 10, 5, 10, 0, 0),
        )
        row = original.to_row()
        row["id"] = 7
        restored = TaskItem.from_row(row)
        self.assertIs(restored.priority, TaskPriority.HIGH)
        self.assertEqual(restored.parent_task_id, 3)
        self.assertEqual(restored.deleted_at, datetime(2026, 10, 5, 10, 0, 0))

    def test_from_row_tolerates_missing_v11_columns(self):
        """旧库快照或测试假行里没有新列时，按默认值处理而不是报错。"""
        row = {
            "id": 1,
            "title": "老任务",
            "description": None,
            "type": "daily",
            "due_date": None,
            "remind_time": None,
            "is_completed": 0,
            "last_completed_date": None,
            "created_at": "2026-10-05T09:00:00",
        }
        task = TaskItem.from_row(row)
        self.assertIs(task.priority, TaskPriority.NORMAL)
        self.assertIsNone(task.parent_task_id)
        self.assertIsNone(task.deleted_at)


class HistoryEntryTest(unittest.TestCase):
    def test_action_labels_and_fallback(self):
        self.assertEqual(HistoryAction.COMPLETED.label, "完成")
        self.assertIs(HistoryAction.from_value("乱写"), HistoryAction.UPDATED)

    def test_defaults_occurred_at_to_now(self):
        entry = HistoryEntry(task_id=1, action=HistoryAction.CREATED)
        self.assertIsNotNone(entry.occurred_at)
        self.assertEqual(entry.occurred_date, timeutils.today_local())

    def test_row_round_trip(self):
        entry = HistoryEntry(
            id=3,
            task_id=9,
            action=HistoryAction.COMPLETED,
            occurred_at=datetime(2026, 10, 5, 21, 30, 0),
            detail="从悬浮窗勾选",
        )
        restored = HistoryEntry.from_row(entry.to_row() | {"id": 3})
        self.assertEqual(restored.task_id, 9)
        self.assertIs(restored.action, HistoryAction.COMPLETED)
        self.assertEqual(restored.occurred_date, MONDAY)
        self.assertEqual(restored.detail, "从悬浮窗勾选")


class FocusRecordTest(unittest.TestCase):
    def test_rejects_out_of_range_planned_minutes(self):
        with self.assertRaises(ValueError):
            FocusRecord(planned_minutes=0)
        with self.assertRaises(ValueError):
            FocusRecord(planned_minutes=181)

    def test_actual_minutes_and_ratio(self):
        record = FocusRecord(planned_minutes=25, actual_seconds=750, completed=True)
        self.assertEqual(record.actual_minutes, 12.5)
        self.assertAlmostEqual(record.progress_ratio, 0.5)

    def test_ratio_is_capped_at_one(self):
        record = FocusRecord(planned_minutes=1, actual_seconds=600)
        self.assertEqual(record.progress_ratio, 1.0)

    def test_negative_seconds_are_clamped(self):
        self.assertEqual(FocusRecord(planned_minutes=25, actual_seconds=-5).actual_seconds, 0)


class ScreenTimeRecordTest(unittest.TestCase):
    def test_rejects_blank_app_name(self):
        with self.assertRaises(ValueError):
            ScreenTimeRecord(day=MONDAY, app_name="   ", seconds=10)

    def test_truncates_long_app_name(self):
        record = ScreenTimeRecord(day=MONDAY, app_name="x" * 500, seconds=10)
        self.assertEqual(len(record.app_name), 120)

    def test_negative_seconds_clamped(self):
        self.assertEqual(ScreenTimeRecord(day=MONDAY, app_name="a", seconds=-3).seconds, 0)

    def test_format_duration(self):
        self.assertEqual(format_duration(30), "30 秒")
        self.assertEqual(format_duration(600), "10 分")
        self.assertEqual(format_duration(3600 + 23 * 60), "1 小时 23 分")


class ReminderLogTest(unittest.TestCase):
    def test_fresh_log_is_due(self):
        log = ReminderLog(task_id=1, remind_date=MONDAY)
        self.assertTrue(log.is_due(datetime(2026, 10, 5, 9, 0)))
        self.assertFalse(log.is_notified)

    def test_notified_log_is_not_due_again(self):
        log = ReminderLog(task_id=1, remind_date=MONDAY).notified(datetime(2026, 10, 5, 9, 0))
        self.assertTrue(log.is_notified)
        self.assertFalse(log.is_due(datetime(2026, 10, 5, 9, 1)))

    def test_snooze_blocks_until_deadline_then_fires(self):
        now = datetime(2026, 10, 5, 9, 0)
        log = ReminderLog(task_id=1, remind_date=MONDAY).snoozed(5, now)
        self.assertTrue(log.is_snoozed(now))
        self.assertFalse(log.is_due(now + timedelta(minutes=4)))
        self.assertTrue(log.is_due(now + timedelta(minutes=5)))
        self.assertFalse(log.is_notified, "推迟期间不应记为已通知，否则到点弹不出来")


# ======================================================================
# 任务仓储：软删除与子任务
# ======================================================================
class TaskRepositoryV11Test(DatabaseTestCase):
    def test_list_all_excludes_deleted_by_default(self):
        task_id = self.tasks.add(TaskItem(title="喝水", type=TaskType.DAILY))
        self.assertEqual(len(self.tasks.list_all()), 1)

        self.tasks.soft_delete(task_id)
        self.assertEqual(self.tasks.list_all(), [])
        self.assertEqual(len(self.tasks.list_all(include_deleted=True)), 1)
        self.assertEqual(len(self.tasks.list_deleted()), 1)

    def test_soft_delete_is_idempotent(self):
        task_id = self.tasks.add(TaskItem(title="喝水", type=TaskType.DAILY))
        self.assertTrue(self.tasks.soft_delete(task_id))
        self.assertFalse(self.tasks.soft_delete(task_id), "重复软删除应返回 False")

    def test_restore_returns_task_to_active_list(self):
        task_id = self.tasks.add(TaskItem(title="喝水", type=TaskType.DAILY))
        self.tasks.soft_delete(task_id)
        self.assertTrue(self.tasks.restore(task_id))
        self.assertEqual(len(self.tasks.list_all()), 1)

    def test_restore_non_deleted_returns_false(self):
        task_id = self.tasks.add(TaskItem(title="喝水", type=TaskType.DAILY))
        self.assertFalse(self.tasks.restore(task_id))

    def test_list_deleted_can_filter_by_day(self):
        task_id = self.tasks.add(TaskItem(title="喝水", type=TaskType.DAILY))
        self.tasks.soft_delete(task_id, datetime(2026, 10, 5, 23, 0, 0))
        self.assertEqual(len(self.tasks.list_deleted(MONDAY)), 1)
        self.assertEqual(len(self.tasks.list_deleted(TUESDAY)), 0)

    def test_purge_deleted_removes_only_old_ones(self):
        old_id = self.tasks.add(TaskItem(title="昨天删的", type=TaskType.DAILY))
        new_id = self.tasks.add(TaskItem(title="今天删的", type=TaskType.DAILY))
        self.tasks.soft_delete(old_id, datetime(2026, 10, 4, 12, 0, 0))
        self.tasks.soft_delete(new_id, datetime(2026, 10, 5, 12, 0, 0))

        removed = self.tasks.purge_deleted(before=date(2026, 10, 4))
        self.assertEqual(removed, 1)
        remaining = self.tasks.list_deleted()
        self.assertEqual([t.id for t in remaining], [new_id])

    def test_list_children(self):
        parent_id = self.tasks.add(TaskItem(title="装修", type=TaskType.DAILY))
        child_id = self.tasks.add(
            TaskItem(title="买涂料", type=TaskType.DAILY, parent_task_id=parent_id)
        )
        self.tasks.add(TaskItem(title="无关任务", type=TaskType.DAILY))

        children = self.tasks.list_children(parent_id)
        self.assertEqual([c.id for c in children], [child_id])

        self.tasks.soft_delete(child_id)
        self.assertEqual(self.tasks.list_children(parent_id), [])

    def test_count_excludes_deleted_by_default(self):
        task_id = self.tasks.add(TaskItem(title="喝水", type=TaskType.DAILY))
        self.assertEqual(self.tasks.count(), 1)
        self.tasks.soft_delete(task_id)
        self.assertEqual(self.tasks.count(), 0)
        self.assertEqual(self.tasks.count(include_deleted=True), 1)

    def test_priority_persists(self):
        task = TaskItem(title="救火", type=TaskType.DAILY, priority=TaskPriority.URGENT)
        self.tasks.add(task)
        self.assertIs(self.tasks.get(task.id).priority, TaskPriority.URGENT)


# ======================================================================
# 标签 / 依赖
# ======================================================================
class TagRepositoryTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tags = TagRepository(self.db)
        self.task_id = self.tasks.add(TaskItem(title="写周报", type=TaskType.DAILY))

    def test_add_and_list(self):
        self.assertTrue(self.tags.add(self.task_id, "工作"))
        self.assertTrue(self.tags.add(self.task_id, "本周"))
        # SQLite 对 TEXT 按 UTF-8 字节序排序：工(U+5DE5) < 本(U+672C)
        self.assertEqual(self.tags.tags_for(self.task_id), ["工作", "本周"])

    def test_add_is_idempotent(self):
        self.tags.add(self.task_id, "工作")
        self.assertFalse(self.tags.add(self.task_id, "工作"), "重复打标签应返回 False")
        self.assertEqual(len(self.tags.tags_for(self.task_id)), 1)

    def test_blank_tag_rejected(self):
        self.assertFalse(self.tags.add(self.task_id, "   "))
        self.assertEqual(self.tags.tags_for(self.task_id), [])

    def test_remove(self):
        self.tags.add(self.task_id, "工作")
        self.assertTrue(self.tags.remove(self.task_id, "工作"))
        self.assertEqual(self.tags.tags_for(self.task_id), [])

    def test_set_tags_replaces_and_dedupes(self):
        self.tags.add(self.task_id, "旧的")
        self.tags.set_tags(self.task_id, ["A", "B", "A", "  "])
        self.assertEqual(self.tags.tags_for(self.task_id), ["A", "B"])

    def test_all_tags_and_lookup(self):
        other = self.tasks.add(TaskItem(title="跑步", type=TaskType.DAILY))
        self.tags.add(self.task_id, "工作")
        self.tags.add(other, "健康")
        self.assertEqual(self.tags.all_tags(), ["健康", "工作"])
        self.assertEqual(self.tags.task_ids_with_tag("工作"), [self.task_id])
        self.assertEqual(self.tags.count_for_tag("健康"), 1)

    def test_tags_removed_when_task_deleted_hard(self):
        """外键 ON DELETE CASCADE：真正删任务时标签跟着走。"""
        self.tags.add(self.task_id, "工作")
        self.db.execute("DELETE FROM task_item WHERE id = ?", (self.task_id,))
        self.assertEqual(self.tags.tags_for(self.task_id), [])

    def test_tags_for_many_batches(self):
        other = self.tasks.add(TaskItem(title="跑步", type=TaskType.DAILY))
        self.tags.add(self.task_id, "工作")
        self.tags.add(other, "健康")
        grouped = self.tags.tags_for_many([self.task_id, other, 99999])
        self.assertEqual(grouped[self.task_id], ["工作"])
        self.assertEqual(grouped[other], ["健康"])
        self.assertNotIn(99999, grouped)

    def test_tags_for_many_with_empty_input(self):
        self.assertEqual(self.tags.tags_for_many([]), {})


class DependencyRepositoryTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.deps = DependencyRepository(self.db)
        self.a = self.tasks.add(TaskItem(title="A", type=TaskType.DAILY))
        self.b = self.tasks.add(TaskItem(title="B", type=TaskType.DAILY))
        self.c = self.tasks.add(TaskItem(title="C", type=TaskType.DAILY))

    def test_add_and_query_both_directions(self):
        self.assertTrue(self.deps.add(self.b, self.a))  # B 依赖 A
        self.assertEqual(self.deps.dependencies_of(self.b), [self.a])
        self.assertEqual(self.deps.dependents_of(self.a), [self.b])

    def test_self_dependency_rejected(self):
        self.assertFalse(self.deps.add(self.a, self.a))
        self.assertEqual(self.deps.list_all(), [])

    def test_duplicate_add_is_idempotent(self):
        self.deps.add(self.b, self.a)
        self.assertFalse(self.deps.add(self.b, self.a))
        self.assertEqual(len(self.deps.list_all()), 1)

    def test_remove(self):
        self.deps.add(self.b, self.a)
        self.assertTrue(self.deps.remove(self.b, self.a))
        self.assertEqual(self.deps.dependencies_of(self.b), [])

    def test_set_dependencies_replaces_and_drops_self(self):
        self.deps.set_dependencies(self.c, [self.a, self.b, self.a, self.c])
        self.assertEqual(self.deps.dependencies_of(self.c), [self.a, self.b])

    def test_list_all_returns_edges(self):
        self.deps.add(self.b, self.a)
        self.deps.add(self.c, self.b)
        self.assertEqual(self.deps.list_all(), [(self.b, self.a), (self.c, self.b)])

    def test_clear_for_task_removes_both_directions(self):
        self.deps.add(self.b, self.a)
        self.deps.add(self.c, self.b)
        self.assertEqual(self.deps.clear_for_task(self.b), 2)
        self.assertEqual(self.deps.list_all(), [])

    def test_cascade_on_hard_delete(self):
        self.deps.add(self.b, self.a)
        self.db.execute("DELETE FROM task_item WHERE id = ?", (self.a,))
        self.assertEqual(self.deps.list_all(), [])


# ======================================================================
# 历史 / 专注 / 屏幕时间 / 提醒
# ======================================================================
class HistoryRepositoryTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.history = HistoryRepository(self.db)
        self.task_id = self.tasks.add(TaskItem(title="喝水", type=TaskType.DAILY))

    def test_record_and_list(self):
        self.history.record(self.task_id, HistoryAction.CREATED, moment=datetime(2026, 10, 5, 8, 0))
        self.history.record(self.task_id, HistoryAction.COMPLETED, moment=datetime(2026, 10, 5, 9, 0))

        entries = self.history.list_for_task(self.task_id)
        self.assertEqual([e.action for e in entries], [HistoryAction.COMPLETED, HistoryAction.CREATED])

    def test_list_between_filters_by_date(self):
        self.history.record(self.task_id, HistoryAction.COMPLETED, moment=datetime(2026, 10, 5, 9, 0))
        self.history.record(self.task_id, HistoryAction.COMPLETED, moment=datetime(2026, 10, 7, 9, 0))

        entries = self.history.list_between(MONDAY, TUESDAY)
        self.assertEqual(len(entries), 1)

    def test_count_actions_on(self):
        self.history.record(self.task_id, HistoryAction.COMPLETED, moment=datetime(2026, 10, 5, 9, 0))
        self.history.record(self.task_id, HistoryAction.COMPLETED, moment=datetime(2026, 10, 5, 21, 0))
        self.history.record(self.task_id, HistoryAction.CREATED, moment=datetime(2026, 10, 5, 8, 0))

        self.assertEqual(self.history.count_actions_on(MONDAY, HistoryAction.COMPLETED), 2)
        self.assertEqual(self.history.count_actions_on(MONDAY, HistoryAction.CREATED), 1)
        self.assertEqual(self.history.count_actions_on(TUESDAY, HistoryAction.COMPLETED), 0)

    def test_completion_counts_feeds_heatmap(self):
        self.history.record(self.task_id, HistoryAction.COMPLETED, moment=datetime(2026, 10, 5, 9, 0))
        self.history.record(self.task_id, HistoryAction.COMPLETED, moment=datetime(2026, 10, 5, 21, 0))
        self.history.record(self.task_id, HistoryAction.COMPLETED, moment=datetime(2026, 10, 6, 9, 0))

        counts = self.history.completion_counts(MONDAY, date(2026, 10, 11))
        self.assertEqual(counts[MONDAY], 2)
        self.assertEqual(counts[TUESDAY], 1)
        self.assertEqual(self.history.distinct_completion_days(MONDAY, TUESDAY), [MONDAY, TUESDAY])

    def test_history_survives_task_hard_delete(self):
        """历史表刻意不设外键：任务被彻底删掉后统计仍要保留。"""
        self.history.record(self.task_id, HistoryAction.COMPLETED, moment=datetime(2026, 10, 5, 9, 0))
        self.db.execute("DELETE FROM task_item WHERE id = ?", (self.task_id,))
        self.assertEqual(self.history.count_actions_on(MONDAY, HistoryAction.COMPLETED), 1)


class FocusRepositoryTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.focus = FocusRepository(self.db)
        self.task_id = self.tasks.add(TaskItem(title="写代码", type=TaskType.DAILY))

    def test_add_and_get(self):
        record = FocusRecord(
            planned_minutes=25,
            task_id=self.task_id,
            started_at=datetime(2026, 10, 5, 9, 0, 0),
            ended_at=datetime(2026, 10, 5, 9, 25, 0),
            actual_seconds=1500,
            completed=True,
        )
        self.focus.add(record)
        loaded = self.focus.get(record.id)
        self.assertEqual(loaded.planned_minutes, 25)
        self.assertEqual(loaded.actual_seconds, 1500)
        self.assertTrue(loaded.completed)

    def test_update_writes_back_actual_seconds(self):
        record = FocusRecord(
            planned_minutes=25, started_at=datetime(2026, 10, 5, 9, 0, 0)
        )
        self.focus.add(record)
        record.actual_seconds = 600
        record.completed = False
        record.ended_at = datetime(2026, 10, 5, 9, 10, 0)
        self.assertTrue(self.focus.update(record))

        self.assertEqual(self.focus.get(record.id).actual_seconds, 600)

    def test_totals_and_daily_summary(self):
        self.focus.add(
            FocusRecord(
                planned_minutes=25,
                started_at=datetime(2026, 10, 5, 9, 0, 0),
                actual_seconds=1500,
            )
        )
        self.focus.add(
            FocusRecord(
                planned_minutes=25,
                started_at=datetime(2026, 10, 5, 14, 0, 0),
                actual_seconds=900,
            )
        )
        self.focus.add(
            FocusRecord(
                planned_minutes=15,
                started_at=datetime(2026, 10, 6, 9, 0, 0),
                actual_seconds=600,
            )
        )

        self.assertEqual(self.focus.total_seconds_on(MONDAY), 2400)
        self.assertEqual(self.focus.total_seconds_between(MONDAY, TUESDAY), 3000)
        self.assertEqual(self.focus.daily_totals(MONDAY, TUESDAY), {MONDAY: 2400, TUESDAY: 600})
        self.assertEqual(self.focus.count_on(MONDAY), 2)
        self.assertEqual(len(self.focus.list_between(MONDAY, TUESDAY)), 3)

    def test_task_deletion_keeps_record_with_null_task(self):
        """外键 ON DELETE SET NULL：任务没了，专注时长仍要计入统计。"""
        record = FocusRecord(
            planned_minutes=25,
            task_id=self.task_id,
            started_at=datetime(2026, 10, 5, 9, 0, 0),
            actual_seconds=1500,
        )
        self.focus.add(record)
        self.db.execute("DELETE FROM task_item WHERE id = ?", (self.task_id,))

        self.assertIsNone(self.focus.get(record.id).task_id)
        self.assertEqual(self.focus.total_seconds_on(MONDAY), 1500)


class ScreenTimeRepositoryTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.screen = ScreenTimeRepository(self.db)

    def test_add_seconds_accumulates_same_day_and_app(self):
        """采样是累加而非插入新行——一天四万个样本不能按行存。"""
        self.screen.add_seconds(MONDAY, "Code.exe", 2)
        self.screen.add_seconds(MONDAY, "Code.exe", 2)
        self.screen.add_seconds(MONDAY, "Code.exe", 2)

        records = self.screen.list_for_day(MONDAY)
        self.assertEqual(len(records), 1, "同一应用同一天只应有一行")
        self.assertEqual(records[0].seconds, 6)
        self.assertEqual(self.screen.total_seconds_on(MONDAY), 6)

    def test_top_apps_sorted_and_limited(self):
        self.screen.add_seconds(MONDAY, "Code.exe", 100)
        self.screen.add_seconds(MONDAY, "chrome.exe", 300)
        self.screen.add_seconds(MONDAY, "explorer.exe", 200)

        top = self.screen.top_apps(MONDAY, limit=2)
        self.assertEqual([r.app_name for r in top], ["chrome.exe", "explorer.exe"])
        self.assertEqual(top[0].minutes, 5.0)

    def test_days_are_separated(self):
        self.screen.add_seconds(MONDAY, "Code.exe", 10)
        self.screen.add_seconds(TUESDAY, "Code.exe", 20)
        self.assertEqual(self.screen.total_seconds_on(MONDAY), 10)
        self.assertEqual(self.screen.distinct_days(), [MONDAY, TUESDAY])

    def test_delete_day(self):
        self.screen.add_seconds(MONDAY, "Code.exe", 10)
        self.assertEqual(self.screen.delete_day(MONDAY), 1)
        self.assertEqual(self.screen.total_seconds_on(MONDAY), 0)


class ReminderRepositoryTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.reminders = ReminderRepository(self.db)
        self.task_id = self.tasks.add(TaskItem(title="喝水", type=TaskType.DAILY))

    def test_get_returns_none_when_absent(self):
        self.assertIsNone(self.reminders.get(self.task_id, MONDAY))

    def test_get_or_create_persists_row(self):
        log = self.reminders.get_or_create(self.task_id, MONDAY)
        self.assertFalse(log.is_notified)
        self.assertIsNotNone(self.reminders.get(self.task_id, MONDAY))

    def test_mark_notified_stops_repeat(self):
        now = datetime(2026, 10, 5, 9, 0)
        self.reminders.mark_notified(self.task_id, MONDAY, now)
        log = self.reminders.get(self.task_id, MONDAY)
        self.assertTrue(log.is_notified)
        self.assertFalse(log.is_due(now + timedelta(minutes=1)))

    def test_snooze_then_fire_again(self):
        now = datetime(2026, 10, 5, 9, 0)
        self.reminders.mark_notified(self.task_id, MONDAY, now)
        self.reminders.snooze(self.task_id, MONDAY, 5, now)

        log = self.reminders.get(self.task_id, MONDAY)
        self.assertFalse(log.is_due(now + timedelta(minutes=4)))
        self.assertTrue(log.is_due(now + timedelta(minutes=5)))

    def test_list_for_day_and_purge(self):
        self.reminders.get_or_create(self.task_id, MONDAY)
        self.reminders.get_or_create(self.task_id, TUESDAY)

        self.assertEqual(len(self.reminders.list_for_day(MONDAY)), 1)
        self.assertEqual(self.reminders.purge_before(TUESDAY), 1)
        self.assertEqual(len(self.reminders.list_for_day(MONDAY)), 0)

    def test_delete_for_task(self):
        self.reminders.get_or_create(self.task_id, MONDAY)
        self.assertEqual(self.reminders.delete_for_task(self.task_id), 1)
        self.assertIsNone(self.reminders.get(self.task_id, MONDAY))

    def test_cascade_on_hard_delete(self):
        self.reminders.get_or_create(self.task_id, MONDAY)
        self.db.execute("DELETE FROM task_item WHERE id = ?", (self.task_id,))
        self.assertIsNone(self.reminders.get(self.task_id, MONDAY))


class FreshDatabaseV11Test(unittest.TestCase):
    """全新库上一次跑完两个迁移，确认 v1.1 结构可直接使用。"""

    def test_new_database_has_all_v11_structures(self):
        database = Database(MEMORY)
        try:
            runner = MigrationRunner(database)
            self.assertEqual(runner.run(), ["001", "002"])
            self.assertEqual(runner.current_version(), "002")

            # 直接往新表写一条，确认外键与 CHECK 都没问题
            task_id = database.execute(
                "INSERT INTO task_item (title, type, created_at, priority) "
                "VALUES ('新任务', 'daily', ?, 1)",
                (timeutils.format_datetime(datetime(2026, 10, 5, 9, 0)),),
            )
            database.execute(
                "INSERT INTO task_tag (task_id, tag) VALUES (?, ?)", (task_id, "工作")
            )
            database.execute(
                "INSERT INTO focus_record (planned_minutes, started_at) VALUES (?, ?)",
                (25, timeutils.format_datetime(datetime(2026, 10, 5, 9, 0))),
            )
            self.assertEqual(
                database.query_scalar("SELECT COUNT(*) FROM task_tag"), 1
            )
        finally:
            database.close()

    def test_check_constraints_reject_bad_values(self):
        database = Database(MEMORY)
        try:
            MigrationRunner(database).run()
            task_id = database.execute(
                "INSERT INTO task_item (title, type, created_at) "
                "VALUES ('任务', 'daily', '2026-10-05T09:00:00')"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                database.execute(
                    "INSERT INTO task_history (task_id, action, occurred_at) "
                    "VALUES (?, '不存在的动作', '2026-10-05T09:00:00')",
                    (task_id,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                database.execute(
                    "INSERT INTO focus_record (planned_minutes, started_at, actual_seconds) "
                    "VALUES (25, '2026-10-05T09:00:00', -1)"
                )
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()

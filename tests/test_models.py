"""实体层测试：标题校验与「今天是否可见 / 是否需要重置」两类核心规则。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, time

from everyday.models.preset import PresetTemplate
from everyday.models.task import MAX_TITLE_LENGTH, TaskItem, TaskType
from tests.base import DatabaseTestCase  # noqa: F401  (确保 src 已入 path)

# 2026-10-05 是星期一
MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)
NEXT_MONDAY = date(2026, 10, 12)
MONTH_END = date(2026, 10, 31)


class TaskTypeTest(unittest.TestCase):
    def test_from_value_accepts_enum_value_and_label(self):
        self.assertIs(TaskType.from_value("daily"), TaskType.DAILY)
        self.assertIs(TaskType.from_value("每日"), TaskType.DAILY)
        self.assertIs(TaskType.from_value(TaskType.MONTHLY), TaskType.MONTHLY)

    def test_from_value_falls_back_to_once(self):
        self.assertIs(TaskType.from_value("不存在的类型"), TaskType.ONCE)
        self.assertIs(TaskType.from_value(None), TaskType.ONCE)

    def test_is_recurring(self):
        self.assertFalse(TaskType.ONCE.is_recurring)
        self.assertTrue(TaskType.DAILY.is_recurring)
        self.assertTrue(TaskType.WEEKLY.is_recurring)
        self.assertTrue(TaskType.MONTHLY.is_recurring)


class TaskItemValidationTest(unittest.TestCase):
    def test_empty_title_rejected(self):
        with self.assertRaises(ValueError):
            TaskItem(title="   ", type=TaskType.DAILY)

    def test_title_over_limit_rejected(self):
        with self.assertRaises(ValueError):
            TaskItem(title="x" * (MAX_TITLE_LENGTH + 1), type=TaskType.DAILY)

    def test_title_at_limit_accepted(self):
        task = TaskItem(title="x" * MAX_TITLE_LENGTH, type=TaskType.DAILY)
        self.assertEqual(len(task.title), MAX_TITLE_LENGTH)

    def test_once_task_requires_due_date(self):
        with self.assertRaises(ValueError):
            TaskItem(title="交报告", type=TaskType.ONCE)

    def test_weekly_task_requires_anchor_date(self):
        with self.assertRaises(ValueError):
            TaskItem(title="周会", type=TaskType.WEEKLY)

    def test_recurring_task_without_remind_time_is_allowed(self):
        task = TaskItem(title="喝水", type=TaskType.DAILY)
        self.assertIsNone(task.remind_time)

    def test_created_at_defaults_to_now(self):
        task = TaskItem(title="喝水", type=TaskType.DAILY)
        self.assertIsInstance(task.created_at, datetime)


class TaskItemVisibilityTest(unittest.TestCase):
    def test_completed_once_task_is_hidden(self):
        task = TaskItem(
            title="交报告", type=TaskType.ONCE, due_date=MONDAY, is_completed=True
        )
        self.assertFalse(task.is_visible_on(MONDAY))

    def test_once_task_visible_on_due_date(self):
        task = TaskItem(title="交报告", type=TaskType.ONCE, due_date=MONDAY)
        self.assertTrue(task.is_visible_on(MONDAY))

    def test_once_task_stays_visible_after_due_date_until_completed(self):
        task = TaskItem(title="交报告", type=TaskType.ONCE, due_date=MONDAY)
        self.assertTrue(task.is_visible_on(TUESDAY))
        self.assertTrue(task.is_overdue_on(TUESDAY))

    def test_future_once_task_is_hidden(self):
        task = TaskItem(title="交报告", type=TaskType.ONCE, due_date=NEXT_MONDAY)
        self.assertFalse(task.is_visible_on(MONDAY))
        self.assertFalse(task.is_overdue_on(MONDAY))

    def test_daily_task_visible_every_day_even_when_completed(self):
        task = TaskItem(title="喝水", type=TaskType.DAILY, is_completed=True)
        self.assertTrue(task.is_visible_on(MONDAY))
        self.assertTrue(task.is_visible_on(TUESDAY))

    def test_weekly_task_visible_only_on_anchor_weekday(self):
        task = TaskItem(title="周会", type=TaskType.WEEKLY, due_date=MONDAY)
        self.assertTrue(task.is_visible_on(MONDAY))
        self.assertTrue(task.is_visible_on(NEXT_MONDAY))
        self.assertFalse(task.is_visible_on(TUESDAY))

    def test_monthly_task_visible_only_on_anchor_day(self):
        task = TaskItem(title="对账", type=TaskType.MONTHLY, due_date=date(2026, 9, 5))
        self.assertTrue(task.is_visible_on(MONDAY))  # 5 号
        self.assertFalse(task.is_visible_on(TUESDAY))
        self.assertFalse(task.is_visible_on(MONTH_END))


class TaskItemRolloverTest(unittest.TestCase):
    def test_rollover_needed_when_completed_on_previous_day(self):
        task = TaskItem(
            title="喝水",
            type=TaskType.DAILY,
            is_completed=True,
            last_completed_date=date(2026, 10, 4),
        )
        self.assertTrue(task.needs_rollover_on(MONDAY))
        self.assertFalse(task.rolled_over(MONDAY).is_completed)

    def test_rollover_not_needed_when_completed_today(self):
        task = TaskItem(
            title="喝水", type=TaskType.DAILY, is_completed=True, last_completed_date=MONDAY
        )
        self.assertFalse(task.needs_rollover_on(MONDAY))

    def test_rollover_keeps_last_completed_date(self):
        task = TaskItem(
            title="喝水",
            type=TaskType.DAILY,
            is_completed=True,
            last_completed_date=date(2026, 10, 4),
        )
        rolled = task.rolled_over(MONDAY)
        self.assertEqual(rolled.last_completed_date, date(2026, 10, 4))
        self.assertFalse(rolled.is_completed)

    def test_once_task_never_rolls_over(self):
        task = TaskItem(
            title="交报告",
            type=TaskType.ONCE,
            due_date=MONDAY,
            is_completed=True,
            last_completed_date=date(2026, 10, 4),
        )
        self.assertFalse(task.needs_rollover_on(MONDAY))

    def test_with_completion_sets_and_clears_date(self):
        task = TaskItem(title="喝水", type=TaskType.DAILY)
        done = task.with_completion(True, MONDAY)
        self.assertTrue(done.is_completed)
        self.assertEqual(done.last_completed_date, MONDAY)
        undone = done.with_completion(False, MONDAY)
        self.assertFalse(undone.is_completed)
        self.assertIsNone(undone.last_completed_date)


class TaskItemSortKeyTest(unittest.TestCase):
    def test_tasks_without_remind_time_sort_last(self):
        early = TaskItem(title="早", type=TaskType.DAILY, remind_time=time(8, 0))
        late = TaskItem(title="晚", type=TaskType.DAILY, remind_time=time(18, 0))
        none = TaskItem(title="无提醒", type=TaskType.DAILY)
        ordered = sorted([none, late, early], key=TaskItem.sort_key)
        self.assertEqual([t.title for t in ordered], ["早", "晚", "无提醒"])


class TaskItemStorageTest(unittest.TestCase):
    def test_to_row_from_row_roundtrip(self):
        original = TaskItem(
            id=7,
            title="  写日报  ",
            description="  总结今天  ",
            type=TaskType.ONCE,
            due_date=MONDAY,
            remind_time=time(18, 0),
            created_at=datetime(2026, 10, 5, 9, 30, 0),
        )
        row = original.to_row()
        row["id"] = 7
        restored = TaskItem.from_row(row)
        self.assertEqual(restored.id, 7)
        self.assertEqual(restored.title, "写日报")
        self.assertEqual(restored.description, "总结今天")
        self.assertEqual(restored.due_date, MONDAY)
        self.assertEqual(restored.remind_time, time(18, 0))
        self.assertEqual(restored.created_at, datetime(2026, 10, 5, 9, 30, 0))

    def test_from_row_tolerates_dirty_data(self):
        """历史脏数据（单次任务缺截止日期）读取时不应抛异常。"""
        row = {
            "id": 1,
            "title": "脏数据",
            "description": None,
            "type": "once",
            "due_date": None,
            "remind_time": None,
            "is_completed": 0,
            "last_completed_date": None,
            "created_at": "2026-10-05T09:00:00",
        }
        task = TaskItem.from_row(row)
        self.assertIsNone(task.due_date)
        self.assertEqual(task.type, TaskType.ONCE)


class PresetTemplateTest(unittest.TestCase):
    def test_create_task_uses_today_as_anchor(self):
        template = PresetTemplate(title="站会", type=TaskType.DAILY, remind_time=time(9, 30))
        task = template.create_task(MONDAY)
        self.assertEqual(task.title, "站会")
        self.assertEqual(task.due_date, MONDAY)
        self.assertEqual(task.remind_time, time(9, 30))

    def test_create_task_allows_title_override(self):
        template = PresetTemplate(title="站会", type=TaskType.DAILY)
        task = template.create_task(MONDAY, title="改过的标题")
        self.assertEqual(task.title, "改过的标题")

    def test_from_task_keeps_type_and_remind_time(self):
        task = TaskItem(
            title="跑步", type=TaskType.DAILY, remind_time=time(20, 30)
        )
        template = PresetTemplate.from_task(task, category="健康")
        self.assertEqual(template.title, "跑步")
        self.assertIs(template.type, TaskType.DAILY)
        self.assertEqual(template.remind_time, time(20, 30))
        self.assertEqual(template.category, "健康")

    def test_blank_category_falls_back_to_default(self):
        template = PresetTemplate(title="站会", type=TaskType.DAILY, category="   ")
        self.assertEqual(template.category, "其他")


if __name__ == "__main__":
    unittest.main()

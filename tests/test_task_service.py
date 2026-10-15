"""任务服务测试：循环重置、今日列表过滤、排序与进度统计。

数据库使用内存 SQLite（真实 SQLite，非 Mock）。
"""

from __future__ import annotations

import unittest
from datetime import date, time

from tests.base import DatabaseTestCase

from everyday.models.task import TaskItem, TaskType
from everyday.services.task_service import TaskService

MONDAY = date(2026, 10, 5)      # 星期一
TUESDAY = date(2026, 10, 6)
SUNDAY = date(2026, 10, 4)
NEXT_MONDAY = date(2026, 10, 12)


class TaskServiceCrudTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = TaskService(self.tasks)

    def test_create_assigns_id(self):
        task = self.service.create(TaskItem(title="喝水", type=TaskType.DAILY))
        self.assertIsNotNone(task.id)
        self.assertEqual(self.tasks.count(), 1)

    def test_save_creates_then_updates(self):
        task = self.service.save(TaskItem(title="喝水", type=TaskType.DAILY))
        task.title = "多喝水"
        self.service.save(task)
        self.assertEqual(self.tasks.count(), 1)
        self.assertEqual(self.service.get(task.id).title, "多喝水")

    def test_delete_removes_task(self):
        task = self.service.create(TaskItem(title="喝水", type=TaskType.DAILY))
        self.assertTrue(self.service.delete(task.id))
        self.assertEqual(self.tasks.count(), 0)

    def test_delete_missing_returns_false(self):
        self.assertFalse(self.service.delete(9999))

    def test_update_without_id_raises(self):
        with self.assertRaises(ValueError):
            self.service.update(TaskItem(title="喝水", type=TaskType.DAILY))


class TaskServiceRolloverTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = TaskService(self.tasks)

    def test_rollover_resets_daily_task_completed_yesterday(self):
        task = self.service.create(TaskItem(title="喝水", type=TaskType.DAILY))
        self.service.set_completed(task.id, True, SUNDAY)

        self.assertEqual(self.service.rollover(TUESDAY), 1)

        reloaded = self.service.get(task.id)
        self.assertFalse(reloaded.is_completed)
        self.assertEqual(reloaded.last_completed_date, SUNDAY)

    def test_rollover_keeps_task_completed_today(self):
        task = self.service.create(TaskItem(title="喝水", type=TaskType.DAILY))
        self.service.set_completed(task.id, True, MONDAY)

        self.assertEqual(self.service.rollover(MONDAY), 0)
        self.assertTrue(self.service.get(task.id).is_completed)

    def test_rollover_skips_once_tasks(self):
        task = self.service.create(
            TaskItem(title="交报告", type=TaskType.ONCE, due_date=MONDAY)
        )
        self.service.set_completed(task.id, True, SUNDAY)

        self.assertEqual(self.service.rollover(MONDAY), 0)
        self.assertTrue(self.service.get(task.id).is_completed)

    def test_rollover_resets_weekly_task_completed_last_week(self):
        task = self.service.create(
            TaskItem(title="周会", type=TaskType.WEEKLY, due_date=MONDAY)
        )
        self.service.set_completed(task.id, True, SUNDAY)

        self.assertEqual(self.service.rollover(NEXT_MONDAY), 1)
        self.assertFalse(self.service.get(task.id).is_completed)

    def test_rollover_if_needed_only_runs_once_per_day(self):
        task = self.service.create(TaskItem(title="喝水", type=TaskType.DAILY))
        self.service.set_completed(task.id, True, SUNDAY)

        self.assertEqual(self.service.rollover_if_needed(MONDAY), 1)
        self.assertEqual(self.service.rollover_if_needed(MONDAY), 0)
        self.assertEqual(self.service.last_rollover_day, MONDAY)

        # 跨午夜：次日再手动标记完成后，新的一天应再次触发校对
        self.service.set_completed(task.id, True, MONDAY)
        self.assertEqual(self.service.rollover_if_needed(TUESDAY), 1)
        self.assertFalse(self.service.get(task.id).is_completed)


class TaskServiceTodayListTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = TaskService(self.tasks)

    def test_completed_once_task_disappears_from_today(self):
        task = self.service.create(
            TaskItem(title="交报告", type=TaskType.ONCE, due_date=MONDAY)
        )
        self.assertEqual(len(self.service.list_today(MONDAY)), 1)

        self.service.set_completed(task.id, True, MONDAY)
        self.assertEqual(self.service.list_today(MONDAY), [])

    def test_completed_daily_task_stays_in_today_with_strikethrough(self):
        task = self.service.create(TaskItem(title="喝水", type=TaskType.DAILY))
        self.service.set_completed(task.id, True, MONDAY)

        listed = self.service.list_today(MONDAY)
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0].is_completed)

    def test_weekly_task_hidden_on_other_weekdays(self):
        self.service.create(TaskItem(title="周会", type=TaskType.WEEKLY, due_date=MONDAY))
        self.assertEqual(len(self.service.list_today(MONDAY)), 1)
        self.assertEqual(len(self.service.list_today(TUESDAY)), 0)

    def test_today_list_sorted_by_remind_time_then_created_at(self):
        self.service.create(
            TaskItem(title="晚间", type=TaskType.DAILY, remind_time=time(20, 0))
        )
        self.service.create(
            TaskItem(title="无提醒", type=TaskType.DAILY)
        )
        self.service.create(
            TaskItem(title="晨间", type=TaskType.DAILY, remind_time=time(8, 0))
        )

        titles = [t.title for t in self.service.list_today(MONDAY)]
        self.assertEqual(titles, ["晨间", "晚间", "无提醒"])

    def test_refresh_applies_rollover_before_listing(self):
        task = self.service.create(TaskItem(title="喝水", type=TaskType.DAILY))
        self.service.set_completed(task.id, True, SUNDAY)

        listed = self.service.refresh(MONDAY)
        self.assertEqual(len(listed), 1)
        self.assertFalse(listed[0].is_completed)


class TaskServiceProgressTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = TaskService(self.tasks)

    def test_progress_is_zero_when_no_tasks(self):
        progress = self.service.progress_today(MONDAY)
        self.assertEqual((progress.completed, progress.total), (0, 0))
        self.assertEqual(progress.ratio, 0.0)
        self.assertEqual(progress.label, "0/0")

    def test_completing_daily_task_increases_both_counters(self):
        daily = self.service.create(TaskItem(title="喝水", type=TaskType.DAILY))
        self.service.create(TaskItem(title="跑步", type=TaskType.DAILY))

        self.service.set_completed(daily.id, True, MONDAY)
        progress = self.service.progress_today(MONDAY)

        # 完成的每日任务仍留在列表中，因此分母不因它而额外增加
        self.assertEqual((progress.completed, progress.total), (1, 2))

    def test_completing_once_task_increases_both_counters(self):
        """单次任务完成后会隐藏；分母必须把它算进去，否则进度条会倒退。"""
        self.service.create(TaskItem(title="跑步", type=TaskType.DAILY))
        once = self.service.create(
            TaskItem(title="交报告", type=TaskType.ONCE, due_date=MONDAY)
        )
        self.service.set_completed(once.id, True, MONDAY)

        progress = self.service.progress_today(MONDAY)
        self.assertEqual((progress.completed, progress.total), (1, 2))

    def test_progress_ignores_completions_from_other_days(self):
        daily = self.service.create(TaskItem(title="喝水", type=TaskType.DAILY))
        self.service.set_completed(daily.id, True, SUNDAY)

        progress = self.service.progress_today(MONDAY)
        self.assertEqual((progress.completed, progress.total), (0, 1))

    def test_is_all_done(self):
        daily = self.service.create(TaskItem(title="喝水", type=TaskType.DAILY))
        self.service.set_completed(daily.id, True, MONDAY)
        self.assertTrue(self.service.progress_today(MONDAY).is_all_done)


class TaskServiceValidationTest(unittest.TestCase):
    def test_blank_title_rejected(self):
        self.assertIsNotNone(TaskService.validate("  ", TaskType.DAILY, None))

    def test_once_requires_due_date(self):
        self.assertIsNotNone(TaskService.validate("交报告", TaskType.ONCE, None))

    def test_valid_once_task_passes(self):
        self.assertIsNone(TaskService.validate("交报告", TaskType.ONCE, MONDAY))

    def test_valid_daily_task_passes_without_due_date(self):
        self.assertIsNone(TaskService.validate("喝水", TaskType.DAILY, None))

    def test_too_long_title_rejected(self):
        self.assertIsNotNone(TaskService.validate("x" * 101, TaskType.DAILY, None))


if __name__ == "__main__":
    unittest.main()

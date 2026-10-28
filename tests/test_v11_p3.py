"""v1.1 P3 测试：番茄钟状态机与提醒调度器。

两者都设计成不读系统时钟：番茄钟用 ``tick(seconds)`` 推进，
调度器用 ``tick(now=...)`` 注入时间。因此这里不需要 sleep，
测试是确定性的。
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, time

from everyday.db import (
    DependencyRepository,
    FocusRepository,
    ReminderRepository,
    TagRepository,
    TaskRepository,
)
from everyday.models.task import TaskItem, TaskType
from everyday.services.dependency_service import DependencyService
from everyday.services.notification import Notification, NullNotifier, RecordingNotifier
from everyday.services.pomodoro_service import (
    PomodoroService,
    PomodoroState,
)
from everyday.services.reminder_service import ReminderScheduler
from everyday.services.task_service import TaskService
from tests.base import DatabaseTestCase

MONDAY = date(2026, 10, 5)


class P3Base(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        task_repo = TaskRepository(self.db)
        self.service = TaskService(
            task_repo,
            tags=TagRepository(self.db),
            dependencies=DependencyService(DependencyRepository(self.db), task_repo),
        )
        self.focus_repo = FocusRepository(self.db)
        self.reminder_repo = ReminderRepository(self.db)
        self.notifier = RecordingNotifier()
        self.pomodoro = PomodoroService(self.focus_repo, default_minutes=25, break_minutes=5)
        self.scheduler = ReminderScheduler(
            self.service, self.reminder_repo, self.notifier, pomodoro=self.pomodoro
        )

    def add(self, title: str, **kwargs) -> TaskItem:
        payload = {"title": title, "type": TaskType.DAILY}
        payload.update(kwargs)
        task = TaskItem(**payload)
        self.service.create(task)
        return task


# ======================================================================
# 番茄钟
# ======================================================================
class PomodoroTest(P3Base):
    def test_starts_idle(self):
        snapshot = self.pomodoro.snapshot()
        self.assertIs(snapshot.state, PomodoroState.IDLE)
        self.assertEqual(snapshot.planned_seconds, 0)
        self.assertFalse(self.pomodoro.is_focusing)

    def test_start_creates_record_immediately(self):
        """一开始专注就落库：进程被强杀也不丢「今天专注了多久」。"""
        task = self.add("写代码")
        snapshot = self.pomodoro.start(task_id=task.id)

        self.assertIs(snapshot.state, PomodoroState.FOCUSING)
        self.assertEqual(snapshot.planned_seconds, 25 * 60)
        self.assertEqual(snapshot.remaining_seconds, 25 * 60)
        self.assertEqual(snapshot.remaining_label, "25:00")
        self.assertIsNotNone(snapshot.record_id)

        record = self.focus_repo.get(snapshot.record_id)
        self.assertEqual(record.task_id, task.id)
        self.assertEqual(record.actual_seconds, 0)
        self.assertFalse(record.completed)

    def test_tick_advances_and_updates_label(self):
        self.pomodoro.start(minutes=10)
        snapshot = self.pomodoro.tick(90)
        self.assertEqual(snapshot.elapsed_seconds, 90)
        self.assertEqual(snapshot.remaining_label, "08:30")
        self.assertAlmostEqual(snapshot.ratio, 0.15)

    def test_tick_to_the_end_finishes_and_persists(self):
        snapshot = self.pomodoro.start(minutes=25)
        record_id = snapshot.record_id

        result = self.pomodoro.tick(25 * 60)
        self.assertIs(result.state, PomodoroState.FINISHED)

        record = self.focus_repo.get(record_id)
        self.assertTrue(record.completed)
        self.assertEqual(record.actual_seconds, 25 * 60)
        self.assertIsNotNone(record.ended_at)
        self.assertEqual(record.actual_minutes, 25.0)

    def test_overshooting_tick_is_clamped(self):
        """定时器迟到不该把 25 分钟的番茄钟算成 40 分钟。"""
        snapshot = self.pomodoro.start(minutes=25)
        result = self.pomodoro.tick(40 * 60)
        self.assertEqual(result.elapsed_seconds, 25 * 60)
        self.assertEqual(self.focus_repo.get(snapshot.record_id).actual_seconds, 25 * 60)

    def test_tick_does_nothing_when_not_focusing(self):
        self.assertEqual(self.pomodoro.tick(60).elapsed_seconds, 0)

    def test_pause_freezes_the_clock(self):
        self.pomodoro.start(minutes=10)
        self.pomodoro.tick(60)
        paused = self.pomodoro.pause()
        self.assertIs(paused.state, PomodoroState.PAUSED)
        self.assertFalse(self.pomodoro.is_focusing, "暂停不算专注中")

        self.assertEqual(self.pomodoro.tick(300).elapsed_seconds, 60, "暂停期间不推进")
        resumed = self.pomodoro.resume()
        self.assertIs(resumed.state, PomodoroState.FOCUSING)
        self.assertEqual(self.pomodoro.tick(30).elapsed_seconds, 90)

    def test_stop_keeps_partial_time_but_marks_incomplete(self):
        snapshot = self.pomodoro.start(minutes=25)
        self.pomodoro.tick(600)
        record = self.pomodoro.stop()

        self.assertFalse(record.completed)
        self.assertEqual(record.actual_seconds, 600)
        self.assertEqual(self.focus_repo.get(snapshot.record_id).actual_seconds, 600)

    def test_invalid_transitions_raise(self):
        with self.assertRaises(ValueError):
            self.pomodoro.pause()  # 空闲时不能暂停
        with self.assertRaises(ValueError):
            self.pomodoro.resume()
        with self.assertRaises(ValueError):
            self.pomodoro.stop()
        with self.assertRaises(ValueError):
            self.pomodoro.start_break()  # 没结束不能休息

        self.pomodoro.start(minutes=5)
        with self.assertRaises(ValueError):
            self.pomodoro.start(minutes=5)  # 不能重复开始
        with self.assertRaises(ValueError):
            self.pomodoro.resume()  # 专注中不能恢复

    def test_minutes_are_clamped(self):
        service = PomodoroService(self.focus_repo, default_minutes=0)
        self.assertEqual(service.default_minutes, 1)
        service = PomodoroService(self.focus_repo, default_minutes=999)
        self.assertEqual(service.default_minutes, 180)

    def test_callback_fires_and_errors_are_swallowed(self):
        seen = []

        def boom(record):
            seen.append(record)
            raise RuntimeError("回调故意炸")

        self.pomodoro.on_finished = boom
        snapshot = self.pomodoro.start(minutes=1)
        result = self.pomodoro.tick(60)

        self.assertEqual(len(seen), 1)
        self.assertIs(result.state, PomodoroState.FINISHED, "回调异常不能影响状态机")
        self.assertIsNotNone(self.focus_repo.get(snapshot.record_id))

    def test_break_cycle_returns_to_idle(self):
        self.pomodoro.start(minutes=1)
        self.pomodoro.tick(60)
        self.assertIs(self.pomodoro.state, PomodoroState.FINISHED)

        brk = self.pomodoro.start_break()
        self.assertIs(brk.state, PomodoroState.BREAK)
        self.assertEqual(brk.planned_seconds, 5 * 60)

        done = self.pomodoro.tick_break(5 * 60)
        self.assertIs(done.state, PomodoroState.IDLE)

    def test_skip_break(self):
        self.pomodoro.start(minutes=1)
        self.pomodoro.tick(60)
        self.pomodoro.start_break()
        self.assertIs(self.pomodoro.skip_break().state, PomodoroState.IDLE)

    def test_today_total_seconds(self):
        self.pomodoro.start(minutes=25, moment=datetime(2026, 10, 5, 9, 0))
        self.pomodoro.tick(600)
        self.pomodoro.stop()
        self.assertEqual(self.focus_repo.total_seconds_on(MONDAY), 600)


# ======================================================================
# 提醒调度
# ======================================================================
class ReminderSchedulerTest(P3Base):
    def at(self, hour: int, minute: int = 0, day: date = MONDAY) -> datetime:
        return datetime.combine(day, time(hour, minute))

    def test_task_without_remind_time_never_notifies(self):
        self.add("没有提醒")
        self.assertEqual(self.scheduler.tick(self.at(23)), [])
        self.assertEqual(self.notifier.count, 0)

    def test_notifies_once_when_due(self):
        task = self.add("吃药", remind_time=time(8, 0))

        self.assertEqual(self.scheduler.tick(self.at(7, 59)), [], "未到点不该提醒")
        self.assertEqual(self.scheduler.tick(self.at(8, 0)), [task.id])
        self.assertEqual(self.notifier.count, 1)
        self.assertIn("吃药", self.notifier.sent[0].title)

        # 同一分钟内重复扫描不能重复弹
        self.assertEqual(self.scheduler.tick(self.at(8, 0)), [])
        self.assertEqual(self.scheduler.tick(self.at(8, 30)), [])
        self.assertEqual(self.notifier.count, 1)

    def test_completed_task_does_not_notify(self):
        task = self.add("吃药", remind_time=time(8, 0))
        self.service.set_completed(task.id, True, MONDAY)
        self.assertEqual(self.scheduler.tick(self.at(9)), [])
        self.assertEqual(self.notifier.count, 0)

    def test_snooze_defers_then_fires_again(self):
        task = self.add("吃药", remind_time=time(8, 0))
        self.scheduler.tick(self.at(8, 0))
        self.assertEqual(self.notifier.count, 1)

        until = self.scheduler.snooze(task.id, 5, self.at(8, 0))
        self.assertEqual(until, self.at(8, 5))

        self.assertEqual(self.scheduler.tick(self.at(8, 3)), [], "推迟未到点")
        self.assertEqual(self.scheduler.tick(self.at(8, 5)), [task.id], "到点再提醒")
        self.assertEqual(self.notifier.count, 2)

    def test_missed_reminder_is_not_dumped_on_the_user(self):
        """关了一整天再打开时，早上 9 点的提醒不该在晚上 11 点跳出来。"""
        task = self.add("吃药", remind_time=time(9, 0))
        self.assertEqual(self.scheduler.tick(self.at(23)), [], "超过补发窗口不弹")
        self.assertEqual(self.notifier.count, 0)

        # 但已标记为通知过，不会在下一次扫描又弹
        log = self.reminder_repo.get(task.id, MONDAY)
        self.assertTrue(log.is_notified)

    def test_reminder_within_catch_up_window_still_fires(self):
        task = self.add("吃药", remind_time=time(9, 0))
        self.assertEqual(self.scheduler.tick(self.at(9, 20)), [task.id])
        self.assertEqual(self.notifier.count, 1)

    def test_focus_suppresses_notifications_without_swallowing_them(self):
        """专注期间不打扰，但也不能把提醒静默吞掉——专注结束后要补上。"""
        task = self.add("吃药", remind_time=time(8, 0))
        self.pomodoro.start(minutes=25, moment=self.at(7, 55))

        self.assertEqual(self.scheduler.tick(self.at(8, 0)), [], "专注中应屏蔽")
        self.assertEqual(self.notifier.count, 0)
        # 屏蔽时连提醒记录都不该产生，这样专注结束后才会被当作「待提醒」
        self.assertIsNone(
            self.reminder_repo.get(task.id, MONDAY), "屏蔽时不应产生提醒记录"
        )

        self.pomodoro.stop(moment=self.at(8, 20))
        self.assertEqual(self.scheduler.tick(self.at(8, 21)), [task.id], "专注结束后补提醒")
        self.assertEqual(self.notifier.count, 1)

    def test_mark_done_silences_the_task(self):
        task = self.add("吃药", remind_time=time(8, 0))
        self.scheduler.mark_done(task.id, self.at(7, 0))
        self.assertEqual(self.scheduler.tick(self.at(9)), [])

    def test_pending_lists_unnotified(self):
        first = self.add("吃药", remind_time=time(8, 0))
        second = self.add("开会", remind_time=time(10, 0))
        self.assertEqual(sorted(self.scheduler.pending(self.at(7, 0))), sorted([first.id, second.id]))

        self.scheduler.tick(self.at(8, 0))
        self.assertEqual(self.scheduler.pending(self.at(8, 1)), [second.id])

    def test_notifier_failure_does_not_break_scheduling(self):
        class Boom:
            def notify(self, notification):
                raise RuntimeError("通知炸了")

        task = self.add("吃药", remind_time=time(8, 0))
        scheduler = ReminderScheduler(self.service, self.reminder_repo, Boom())
        self.assertEqual(scheduler.tick(self.at(8, 0)), [], "失败的任务不报告为已通知")
        self.assertFalse(self.reminder_repo.get(task.id, MONDAY).is_notified)

    def test_null_notifier_returns_false(self):
        self.assertFalse(NullNotifier().notify(Notification(title="t", message="m")))


class RecordingNotifierTest(unittest.TestCase):
    def test_records_and_filters(self):
        notifier = RecordingNotifier()
        notifier.notify(Notification(title="A", message="1", task_id=1))
        notifier.notify(Notification(title="B", message="2", task_id=2))

        self.assertEqual(notifier.count, 2)
        self.assertEqual(notifier.titles(), ["A", "B"])
        self.assertEqual(notifier.messages(), ["1", "2"])
        self.assertEqual(len(notifier.for_task(1)), 1)

        notifier.clear()
        self.assertEqual(notifier.count, 0)


if __name__ == "__main__":
    unittest.main()

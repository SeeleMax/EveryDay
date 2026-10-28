"""提醒调度器（v1.1 P3）。

职责：判断「现在该提醒哪些任务」，并通过 :class:`NotificationPort` 弹通知。
它**不持有定时器**——界面用 1 分钟定时器调 :meth:`ReminderScheduler.tick`，
这样调度逻辑可以脱离 Qt 测试。

三条不显然但必须处理的规则
--------------------------
1. **每天同一任务只提醒一次**：靠 ``reminder_log`` 的
   ``(task_id, remind_date)`` 主键 + ``notified_at`` 实现。
   没有它，每分钟扫到同一个任务就会反复弹窗。
2. **稍后提醒（snooze）**：推迟期间不弹，到点后重新进入待提醒集合。
   推迟期间 ``notified_at`` 必须清空，否则到点判定不出「该提醒」。
3. **错过的提醒不补轰炸**：应用关了一整天再打开时，早上 9 点的提醒
   不该在晚上 11 点跳出来。超过 ``catch_up_minutes`` 的直接标记为已通知
   （视为错过），只补最近这段窗口内的。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from everyday import timeutils
from everyday.db.repositories import ReminderRepository
from everyday.services.notification import Notification, NotificationPort
from everyday.services.pomodoro_service import PomodoroService
from everyday.services.task_service import TaskService

logger = logging.getLogger(__name__)

#: 启动后最多补提醒多久以内的（分钟）。超过视为错过，不补弹。
DEFAULT_CATCH_UP_MINUTES = 30


class ReminderScheduler:
    """根据 ``remind_time`` 与 ``reminder_log`` 决定何时弹通知。"""

    def __init__(
        self,
        tasks: TaskService,
        reminders: ReminderRepository,
        notifier: NotificationPort,
        *,
        pomodoro: PomodoroService | None = None,
        catch_up_minutes: int = DEFAULT_CATCH_UP_MINUTES,
    ) -> None:
        self._tasks = tasks
        self._reminders = reminders
        self._notifier = notifier
        self._pomodoro = pomodoro
        self._catch_up_minutes = max(0, int(catch_up_minutes))

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def tick(self, now: datetime | None = None) -> list[int]:
        """扫描一次，返回本次实际弹出的任务 id 列表。

        :param now: 注入「当前时间」，便于测试；``None`` 表示取系统时间
        """
        moment = now or timeutils.now_local()
        day = moment.date()

        # 专注期间屏蔽通知（P3 要求）。注意**不标记已通知**，
        # 这样专注结束后该提醒还能补上，而不是被静默吞掉。
        if self._is_suppressed():
            logger.debug("专注进行中，提醒已顺延")
            return []

        self._tasks.rollover_if_needed(day)
        notified: list[int] = []
        for task in self._tasks.list_today(day):
            if task.id is None or task.remind_time is None:
                continue
            if task.is_completed:
                continue

            log = self._reminders.get_or_create(task.id, day)
            due_at = datetime.combine(day, task.remind_time)

            if not self._should_notify(log, due_at, moment):
                continue

            if self._is_missed(due_at, moment):
                # 太久以前的了：标记为已通知但不打扰用户
                self._reminders.mark_notified(task.id, day, moment)
                logger.info(
                    "提醒已错过 %s（超过 %d 分钟补发窗口），不再弹出",
                    task.title,
                    self._catch_up_minutes,
                )
                continue

            if self._notify(task, due_at, snoozed=log.snooze_until is not None):
                self._reminders.mark_notified(task.id, day, moment)
                notified.append(task.id)

        return notified

    def _should_notify(self, log, due_at: datetime, now: datetime) -> bool:
        """该任务此刻是否应当提醒。"""
        if log.is_snoozed(now):
            return False  # 推迟中
        if log.notified_at is not None and log.snooze_until is None:
            return False  # 今天已经提醒过且没有推迟
        if log.snooze_until is not None and now < log.snooze_until:
            return False  # 同上，推迟未到点
        return now >= due_at

    def _is_missed(self, due_at: datetime, now: datetime) -> bool:
        """是否已经错过补发窗口。"""
        return now - due_at > timedelta(minutes=self._catch_up_minutes)

    def _notify(self, task, due_at: datetime, *, snoozed: bool = False) -> bool:
        """真正弹出通知。"""
        when = due_at.strftime("%H:%M")
        notification = Notification(
            title=f"该做「{task.title}」了",
            message=f"计划时间 {when}" + ("（已推迟）" if snoozed else ""),
            kind="reminder",
            task_id=task.id,
        )
        try:
            return self._notifier.notify(notification)
        except Exception:  # 通知失败不应该中断调度
            logger.exception("弹出提醒失败（任务 %s），已跳过", task.id)
            return False

    def _is_suppressed(self) -> bool:
        """专注期间是否应当屏蔽通知。"""
        return self._pomodoro is not None and self._pomodoro.is_focusing

    # ------------------------------------------------------------------
    # 用户动作
    # ------------------------------------------------------------------
    def snooze(
        self,
        task_id: int,
        minutes: int = 5,
        now: datetime | None = None,
    ) -> datetime:
        """推迟某个任务的提醒，返回到点时间。"""
        moment = now or timeutils.now_local()
        log = self._reminders.snooze(task_id, moment.date(), minutes, moment)
        logger.info("任务 %s 的提醒推迟 %d 分钟", task_id, minutes)
        return log.snooze_until or moment

    def mark_done(self, task_id: int, now: datetime | None = None) -> None:
        """把某个任务的提醒标为已处理（用户点「完成」时调用）。"""
        moment = now or timeutils.now_local()
        self._reminders.mark_notified(task_id, moment.date(), moment)

    def pending(self, now: datetime | None = None) -> list[int]:
        """今天还没提醒过的任务 id（调试与界面提示用）。"""
        moment = now or timeutils.now_local()
        day = moment.date()
        pending: list[int] = []
        for task in self._tasks.list_today(day):
            if task.id is None or task.remind_time is None or task.is_completed:
                continue
            log = self._reminders.get(task.id, day)
            if log is None or (log.notified_at is None and log.snooze_until is None):
                pending.append(task.id)
        return pending

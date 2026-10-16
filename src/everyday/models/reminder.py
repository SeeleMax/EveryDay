"""提醒记录实体（v1.1 P3 通知调度）。

存在的意义有两个：

1. **每天同一任务只提醒一次**——调度器每分钟扫一次，
   没有这张表就会在同一分钟里反复弹通知。
2. **稍后提醒（snooze）**——记录推迟到什么时候，
   到点后重新进入待提醒集合。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from everyday import timeutils

#: 「稍后提醒」默认推迟的分钟数（交接文档 P3 验收要求 5 分钟）
DEFAULT_SNOOZE_MINUTES = 5


@dataclass
class ReminderLog:
    """某个任务在某一天的提醒状态。"""

    task_id: int
    remind_date: date
    notified_at: datetime | None = None
    snooze_until: datetime | None = None

    @property
    def is_notified(self) -> bool:
        """今天是否已经弹过通知。"""
        return self.notified_at is not None

    def is_snoozed(self, moment: datetime | None = None) -> bool:
        """当前是否处于「稍后提醒」的等待期内。"""
        if self.snooze_until is None:
            return False
        return (moment or timeutils.now_local()) < self.snooze_until

    def is_due(self, moment: datetime | None = None) -> bool:
        """现在是否应当弹出提醒。

        规则：已经通知过且不在 snooze 中 → 不再提醒；
        否则到点即提醒。
        """
        now = moment or timeutils.now_local()
        if self.snooze_until is not None and now >= self.snooze_until:
            # snooze 到点了，视为尚未通知，重新提醒
            return True
        if self.snooze_until is not None and now < self.snooze_until:
            return False
        return not self.is_notified

    def notified(self, moment: datetime | None = None) -> ReminderLog:
        """返回「已通知」的副本，并清掉 snooze。"""
        now = moment or timeutils.now_local()
        return ReminderLog(
            task_id=self.task_id,
            remind_date=self.remind_date,
            notified_at=now,
            snooze_until=None,
        )

    def snoozed(
        self, minutes: int = DEFAULT_SNOOZE_MINUTES, moment: datetime | None = None
    ) -> ReminderLog:
        """返回「推迟到 N 分钟后」的副本。

        推迟期间 ``notified_at`` 会被清空，这样到点后能再次触发。
        """
        now = moment or timeutils.now_local()
        return ReminderLog(
            task_id=self.task_id,
            remind_date=self.remind_date,
            notified_at=None,
            snooze_until=now + timedelta(minutes=max(1, int(minutes))),
        )

    def to_row(self) -> dict[str, Any]:
        """转换为可直接用于 UPSERT 的列字典。"""
        return {
            "task_id": self.task_id,
            "remind_date": timeutils.format_date(self.remind_date),
            "notified_at": timeutils.format_datetime(self.notified_at),
            "snooze_until": timeutils.format_datetime(self.snooze_until),
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ReminderLog:
        """从数据库行构造实体。"""
        return cls(
            task_id=row["task_id"],
            remind_date=timeutils.parse_date(row["remind_date"]) or timeutils.today_local(),
            notified_at=timeutils.parse_datetime(row["notified_at"]),
            snooze_until=timeutils.parse_datetime(row["snooze_until"]),
        )

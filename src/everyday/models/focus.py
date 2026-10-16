"""专注记录实体（v1.1 P3 番茄钟 / P4 专注时长统计）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from everyday import timeutils

#: 番茄钟时长的允许范围（分钟），与迁移 002 的 CHECK 约束保持一致
MIN_PLANNED_MINUTES = 1
MAX_PLANNED_MINUTES = 180


@dataclass
class FocusRecord:
    """一段专注。

    ``task_id`` 可为空——用户可能不绑定任务直接开一个番茄钟。
    中途放弃也照样落库（``completed=False``），因为「专注了多少分钟」
    与「是否跑完整段」是两件事，统计专注时长时都要算。
    """

    planned_minutes: int
    started_at: datetime | None = None
    ended_at: datetime | None = None
    actual_seconds: int = 0
    completed: bool = False
    task_id: int | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        self.planned_minutes = int(self.planned_minutes)
        if not (MIN_PLANNED_MINUTES <= self.planned_minutes <= MAX_PLANNED_MINUTES):
            raise ValueError(
                f"计划时长必须在 {MIN_PLANNED_MINUTES}~{MAX_PLANNED_MINUTES} 分钟之间"
            )
        self.actual_seconds = max(0, int(self.actual_seconds))
        if self.started_at is None:
            self.started_at = timeutils.now_local()

    @property
    def actual_minutes(self) -> float:
        """实际专注分钟数（保留一位小数足够展示）。"""
        return round(self.actual_seconds / 60, 1)

    @property
    def started_date(self) -> date:
        """专注开始的日期（按天统计时用）。"""
        assert self.started_at is not None
        return self.started_at.date()

    @property
    def progress_ratio(self) -> float:
        """已经走过的比例，``0.0~1.0``（悬浮窗进度环用）。"""
        planned_seconds = self.planned_minutes * 60
        if planned_seconds <= 0:
            return 0.0
        return min(1.0, self.actual_seconds / planned_seconds)

    def to_row(self) -> dict[str, Any]:
        """转换为可直接用于 INSERT/UPDATE 的列字典。"""
        return {
            "task_id": self.task_id,
            "started_at": timeutils.format_datetime(self.started_at),
            "ended_at": timeutils.format_datetime(self.ended_at),
            "planned_minutes": self.planned_minutes,
            "actual_seconds": self.actual_seconds,
            "completed": 1 if self.completed else 0,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> FocusRecord:
        """从数据库行构造实体（绕开强校验，容忍历史脏数据）。"""
        return cls(
            id=row["id"],
            task_id=row["task_id"],
            started_at=timeutils.parse_datetime(row["started_at"]),
            ended_at=timeutils.parse_datetime(row["ended_at"]),
            planned_minutes=int(row["planned_minutes"] or MIN_PLANNED_MINUTES),
            actual_seconds=int(row["actual_seconds"] or 0),
            completed=bool(row["completed"]),
        )

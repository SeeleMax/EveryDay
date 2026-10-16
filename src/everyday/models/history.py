"""任务历史实体（v1.1 P4：历史统计的数据来源）。

历史表**故意不设外键**：任务被彻底清理后历史仍要保留，
否则「完成率热力图」会随着删除任务而缩水，统计就失真了。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from everyday import timeutils


class HistoryAction(str, Enum):
    """历史事件的种类，与迁移 002 的 CHECK 约束一一对应。"""

    CREATED = "created"
    UPDATED = "updated"
    COMPLETED = "completed"
    UNCOMPLETED = "uncompleted"
    DELETED = "deleted"
    RESTORED = "restored"

    @property
    def label(self) -> str:
        """面向界面的中文名称。"""
        return _ACTION_LABELS[self]

    @classmethod
    def from_value(cls, value: object) -> HistoryAction:
        """宽松解析，无法识别时按「更新」处理（不抛异常）。"""
        if isinstance(value, HistoryAction):
            return value
        text = str(value or "").strip()
        for member in cls:
            if text == member.value or text == member.label:
                return member
        return cls.UPDATED


_ACTION_LABELS: dict[HistoryAction, str] = {
    HistoryAction.CREATED: "创建",
    HistoryAction.UPDATED: "修改",
    HistoryAction.COMPLETED: "完成",
    HistoryAction.UNCOMPLETED: "取消完成",
    HistoryAction.DELETED: "删除",
    HistoryAction.RESTORED: "恢复",
}

#: 视为「完成了一次」的动作，供完成率与连续打卡统计使用
COMPLETION_ACTIONS = (HistoryAction.COMPLETED,)


@dataclass
class HistoryEntry:
    """一条历史记录。"""

    task_id: int
    action: HistoryAction
    occurred_at: datetime | None = None
    detail: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        self.action = HistoryAction.from_value(self.action)
        if self.occurred_at is None:
            self.occurred_at = timeutils.now_local()
        if self.detail is not None:
            self.detail = self.detail.strip() or None

    @property
    def occurred_date(self) -> date:
        """事件发生的日期（热力图按天聚合时用）。"""
        assert self.occurred_at is not None
        return self.occurred_at.date()

    def to_row(self) -> dict[str, Any]:
        """转换为可直接用于 INSERT 的列字典。"""
        return {
            "task_id": self.task_id,
            "action": self.action.value,
            "occurred_at": timeutils.format_datetime(self.occurred_at),
            "detail": self.detail,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> HistoryEntry:
        """从数据库行构造实体。"""
        return cls(
            id=row["id"],
            task_id=row["task_id"],
            action=HistoryAction.from_value(row["action"]),
            occurred_at=timeutils.parse_datetime(row["occurred_at"]),
            detail=row["detail"],
        )

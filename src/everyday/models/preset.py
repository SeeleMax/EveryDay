"""预设模板实体（文档 4.1 PresetTemplate、5.7 预设任务）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any, Mapping

from everyday import timeutils
from everyday.models.task import MAX_TITLE_LENGTH, TaskItem, TaskType

#: 未指定分类时使用的兜底分类
DEFAULT_CATEGORY = "其他"


@dataclass
class PresetTemplate:
    """一个可一键实例化为任务的模板。

    ``type`` 为单次时，模板本身不携带截止日期（模板是「常用任务」的抽象，
    具体日期在创建时由用户确定），因此实例化时会把当天作为截止日期。
    """

    title: str
    type: TaskType = TaskType.DAILY
    remind_time: time | None = None
    category: str = DEFAULT_CATEGORY
    id: int | None = None

    def __post_init__(self) -> None:
        self.title = (self.title or "").strip()
        if not self.title:
            raise ValueError("模板标题不能为空")
        if len(self.title) > MAX_TITLE_LENGTH:
            raise ValueError(f"模板标题不能超过 {MAX_TITLE_LENGTH} 个字符")
        self.type = TaskType.from_value(self.type)
        self.category = (self.category or "").strip() or DEFAULT_CATEGORY

    def create_task(self, day: date, title: str | None = None) -> TaskItem:
        """按模板实例化一个任务（文档 5.7 使用流程）。

        实例化后用户仍可在编辑对话框中修改，因此这里只做合理填充：
        单次任务以 ``day`` 作为截止日期，循环任务以 ``day`` 作为锚点日期。
        """
        return TaskItem(
            title=title or self.title,
            type=self.type,
            remind_time=self.remind_time,
            due_date=day,
        )

    @classmethod
    def from_task(cls, task: TaskItem, category: str = DEFAULT_CATEGORY) -> "PresetTemplate":
        """「另存为模板」（文档 5.7 模板管理）。"""
        return cls(
            title=task.title,
            type=task.type,
            remind_time=task.remind_time,
            category=category,
        )

    def to_row(self) -> dict[str, Any]:
        """转换为可直接用于 INSERT/UPDATE 的列字典。"""
        return {
            "title": self.title,
            "type": self.type.value,
            "remind_time": timeutils.format_time(self.remind_time),
            "category": self.category,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "PresetTemplate":
        """从数据库行构造实体。"""
        return cls(
            id=row["id"],
            title=str(row["title"]),
            type=TaskType.from_value(row["type"]),
            remind_time=timeutils.parse_time(row["remind_time"]),
            category=str(row["category"] or DEFAULT_CATEGORY),
        )

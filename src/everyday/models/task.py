"""任务实体与任务本身的业务规则。

本模块刻意不接触数据库与 UI：``TaskItem`` 上的判定方法都是纯函数，
输入一个 ``date``，输出布尔值或新的 ``TaskItem``。
这样「循环任务次日重置」「单次任务完成后隐藏」这类核心规则可以被直接单元测试。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from enum import Enum
from typing import Any

from everyday import timeutils

#: 文档 5.3 校验规则：标题必填且长度不超过 100 字符
MAX_TITLE_LENGTH = 100


class TaskType(str, Enum):
    """任务类型（文档 5.5）。"""

    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"

    @property
    def label(self) -> str:
        """面向界面的中文名称。"""
        return _TYPE_LABELS[self]

    @property
    def is_recurring(self) -> bool:
        """是否为循环任务（每日 / 每周 / 每月）。"""
        return self is not TaskType.ONCE

    @classmethod
    def from_value(cls, value: object) -> TaskType:
        """宽松解析：接受枚举、``once`` 这类值、或中文名称，默认单次。"""
        if isinstance(value, TaskType):
            return value
        text = str(value or "").strip()
        for member in cls:
            if text == member.value or text == member.label:
                return member
        return cls.ONCE


_TYPE_LABELS: dict[TaskType, str] = {
    TaskType.ONCE: "单次",
    TaskType.DAILY: "每日",
    TaskType.WEEKLY: "每周",
    TaskType.MONTHLY: "每月",
}


class TaskPriority(int, Enum):
    """任务优先级（v1.1 P2）。

    数值**越小越优先**（0 最紧急），因此 ``ORDER BY priority`` 天然就是
    「重要的排在前面」，排序时不必再倒序处理。
    """

    URGENT = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3

    @property
    def label(self) -> str:
        """面向界面的中文名称。"""
        return _PRIORITY_LABELS[self]

    @classmethod
    def from_value(cls, value: object) -> TaskPriority:
        """宽松解析：接受枚举、``0``~``3`` 的数字或字符串、中文名称。

        无法识别时回落到「普通」——优先级只是装饰，不该因脏数据报错。
        """
        if isinstance(value, TaskPriority):
            return value
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError:
                return cls.NORMAL
        text = str(value or "").strip()
        for member in cls:
            if text == member.label or text == str(member.value):
                return member
        return cls.NORMAL


_PRIORITY_LABELS: dict[TaskPriority, str] = {
    TaskPriority.URGENT: "紧急",
    TaskPriority.HIGH: "高",
    TaskPriority.NORMAL: "普通",
    TaskPriority.LOW: "低",
}


@dataclass
class TaskItem:
    """一条任务（文档 4.1 TaskItem 实体 + v1.1 扩展）。

    字段与文档表格一一对应；v1.1（迁移 002）追加了三个字段：

    * ``priority``       优先级，见 :class:`TaskPriority`
    * ``parent_task_id`` 子任务指向父任务；``None`` 表示顶层任务
    * ``deleted_at``     软删除时间戳（回收站）；``None`` 表示未删除

    ``id`` 为 ``None`` 表示尚未落库。
    """

    title: str
    type: TaskType = TaskType.ONCE
    description: str | None = None
    due_date: date | None = None
    remind_time: time | None = None
    is_completed: bool = False
    last_completed_date: date | None = None
    created_at: datetime | None = None
    id: int | None = None
    # ---- v1.1 新增 ----
    priority: TaskPriority = TaskPriority.NORMAL
    parent_task_id: int | None = None
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        self.title = (self.title or "").strip()
        if not self.title:
            raise ValueError("任务标题不能为空")
        if len(self.title) > MAX_TITLE_LENGTH:
            raise ValueError(f"任务标题不能超过 {MAX_TITLE_LENGTH} 个字符")
        self.type = TaskType.from_value(self.type)
        self.priority = TaskPriority.from_value(self.priority)
        if self.description is not None:
            self.description = self.description.strip() or None
        if self.created_at is None:
            self.created_at = timeutils.now_local()
        # 不能把自己当作父任务
        if self.parent_task_id is not None and self.id is not None:
            if self.parent_task_id == self.id:
                raise ValueError("任务不能作为自己的父任务")
        # 单次任务必须设置截止日期（文档 5.3 校验规则）
        if self.type is TaskType.ONCE and self.due_date is None:
            raise ValueError("单次任务必须设置截止日期")
        # 每周 / 每月任务需要一个锚点日期来确定「哪一天显示」
        if self.type in (TaskType.WEEKLY, TaskType.MONTHLY) and self.due_date is None:
            raise ValueError(f"{self.type.label}任务必须设置一个参考日期")

    # ------------------------------------------------------------------
    # 业务规则（文档 4.2 / 5.4 / 5.5）
    # ------------------------------------------------------------------
    def is_visible_on(self, day: date) -> bool:
        """判断该任务是否应出现在 ``day`` 这一天的今日列表中。

        完成的任务**不再从列表里消失**，而是留在原位变半透明并沉到底部
        （见 :meth:`sort_key` 与 ``ui.widgets.TaskRow.apply_state``）。
        对单次任务来说，只在**完成当天**保留；次日不再出现，
        否则历史任务会一天天堆积在列表里。
        """
        if self.type is TaskType.ONCE:
            if self.is_completed:
                return self.last_completed_date == day
            # 未完成：截止当日及之前显示（超期的用醒目颜色提示）
            return self.due_date is not None and self.due_date <= day
        if self.type is TaskType.DAILY:
            return True
        if self.type is TaskType.WEEKLY:
            # 以 due_date 作为锚点，取其中的星期几
            return self.due_date is not None and day.weekday() == self.due_date.weekday()
        if self.type is TaskType.MONTHLY:
            return self.due_date is not None and day.day == self.due_date.day
        return False

    def is_overdue_on(self, day: date) -> bool:
        """单次任务是否已超期未完成（界面用醒目颜色提示）。"""
        return (
            self.type is TaskType.ONCE
            and not self.is_completed
            and self.due_date is not None
            and self.due_date < day
        )

    def needs_rollover_on(self, day: date) -> bool:
        """循环任务是否需要重置完成状态（文档 4.2：上次完成日期不是今天）。"""
        return (
            self.type.is_recurring
            and self.is_completed
            and self.last_completed_date != day
        )

    def rolled_over(self, day: date) -> TaskItem:
        """返回完成状态已重置的副本。

        保留 ``last_completed_date``：它是「上次完成日期」这一语义的载体，
        与文档 5.4「用户手动取消勾选则清空完成日期」是两条不同的路径。
        """
        if not self.needs_rollover_on(day):
            return self
        return replace(self, is_completed=False)

    def with_completion(self, completed: bool, day: date) -> TaskItem:
        """返回完成状态被用户切换后的副本（文档 5.4）。"""
        if completed:
            return replace(self, is_completed=True, last_completed_date=day)
        return replace(self, is_completed=False, last_completed_date=None)

    def sort_key(self) -> tuple[int, int, str, str]:
        """列表排序键（文档 6.1 + 完成项下沉）。

        顺序：
        1. **未完成的在前，已完成的全部沉到最下面**
        2. 其余按提醒时间——未设置提醒的排在定时任务之后
        3. 最后按创建时间

        已完成任务之间不再看提醒时间，避免「上午完成的任务」夹在
        下午的待办之间。
        """
        completed = 1 if self.is_completed else 0
        has_remind = 0 if self.remind_time is not None else 1
        remind = timeutils.format_time(self.remind_time) or ""
        created = timeutils.format_datetime(self.created_at) or ""
        return (completed, has_remind, remind, created)

    # ------------------------------------------------------------------
    # 存储映射
    # ------------------------------------------------------------------
    def to_row(self) -> dict[str, Any]:
        """转换为可直接用于 INSERT/UPDATE 的列字典。"""
        return {
            "title": self.title,
            "description": self.description,
            "type": self.type.value,
            "due_date": timeutils.format_date(self.due_date),
            "remind_time": timeutils.format_time(self.remind_time),
            "is_completed": 1 if self.is_completed else 0,
            "last_completed_date": timeutils.format_date(self.last_completed_date),
            "created_at": timeutils.format_datetime(self.created_at),
            # ---- v1.1 ----
            "priority": int(self.priority),
            "parent_task_id": self.parent_task_id,
            "deleted_at": timeutils.format_datetime(self.deleted_at),
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> TaskItem:
        """从数据库行构造实体。

        这里绕开 ``__post_init__`` 的强校验路径：数据库里可能存在历史脏数据
        （例如单次任务缺少截止日期），读取时应尽量还原而不是抛异常炸掉界面。
        """
        item = cls.__new__(cls)
        item.id = row["id"]
        item.title = str(row["title"])
        item.description = row["description"]
        item.type = TaskType.from_value(row["type"])
        item.due_date = timeutils.parse_date(row["due_date"])
        item.remind_time = timeutils.parse_time(row["remind_time"])
        item.is_completed = bool(row["is_completed"])
        item.last_completed_date = timeutils.parse_date(row["last_completed_date"])
        item.created_at = timeutils.parse_datetime(row["created_at"]) or timeutils.now_local()
        # v1.1 字段用 keys() 探测：迁移 002 之前的旧库快照或测试假行里可能没有，
        # 缺失时按默认值处理，避免为了兼容而写一堆 try/except。
        keys = row.keys() if hasattr(row, "keys") else ()
        item.priority = (
            TaskPriority.from_value(row["priority"])
            if "priority" in keys
            else TaskPriority.NORMAL
        )
        item.parent_task_id = row["parent_task_id"] if "parent_task_id" in keys else None
        item.deleted_at = (
            timeutils.parse_datetime(row["deleted_at"]) if "deleted_at" in keys else None
        )
        return item

    def clone(self) -> TaskItem:
        """深拷贝一份（``replace`` 只做浅拷贝，但字段都是不可变类型）。"""
        return replace(self)

    @property
    def is_deleted(self) -> bool:
        """是否已被软删除（在回收站里）。"""
        return self.deleted_at is not None

    def deleted(self, moment: datetime | None = None) -> TaskItem:
        """返回「已软删除」的副本（P4 回收站）。"""
        return replace(self, deleted_at=moment or timeutils.now_local())

    def restored(self) -> TaskItem:
        """返回「已恢复」的副本（撤销删除）。"""
        return replace(self, deleted_at=None)

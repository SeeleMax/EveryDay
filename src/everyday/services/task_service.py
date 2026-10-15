"""任务服务（文档 5.3 自定义任务、5.4 完成状态、5.5 循环与单次）。

本模块是「任务业务规则」的唯一入口：界面与托盘都通过它读写任务，
从而不会出现「某处漏做循环重置」这类问题。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from everyday import timeutils
from everyday.db.repositories import TaskRepository
from everyday.models.task import TaskItem, TaskType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskProgress:
    """今日完成进度（文档 5.2：悬浮窗显示任务完成数量）。"""

    completed: int
    total: int

    @property
    def ratio(self) -> float:
        """完成率，``0.0~1.0``；无任务时为 0。"""
        if self.total <= 0:
            return 0.0
        return min(1.0, self.completed / self.total)

    @property
    def label(self) -> str:
        """形如 ``3/7`` 的展示文本。"""
        return f"{self.completed}/{self.total}"

    @property
    def is_all_done(self) -> bool:
        """今日任务是否已全部完成。"""
        return self.total > 0 and self.completed >= self.total


class TaskService:
    """任务的增删改查与状态流转。"""

    def __init__(self, repository: TaskRepository) -> None:
        self._repo = repository
        #: 最近一次执行循环重置的日期，用于「每日只校对一次」与跨午夜检测
        self._last_rollover_day: date | None = None

    # ------------------------------------------------------------------
    # 循环任务状态校对（文档 4.2 / 5.5）
    # ------------------------------------------------------------------
    def rollover(self, day: date | None = None) -> int:
        """把所有「上次完成日期不是今天」的循环任务重置为未完成。

        :return: 被重置的任务数量
        """
        target = day or timeutils.today_local()
        reset_count = 0
        for task in self._repo.list_all():
            if not task.needs_rollover_on(target):
                continue
            self._repo.update(task.rolled_over(target))
            reset_count += 1
        if reset_count:
            logger.info("循环任务状态校对完成：重置 %d 项（%s）", reset_count, target)
        self._last_rollover_day = target
        return reset_count

    def rollover_if_needed(self, day: date | None = None) -> int:
        """仅在「今天还没校对过」时执行校对。

        文档 5.5 要求：应用启动时、以及运行期间每日首次刷新时各校对一次。
        跨午夜场景由调用方定时传入新的 ``day`` 触发。
        """
        target = day or timeutils.today_local()
        if self._last_rollover_day == target:
            return 0
        return self.rollover(target)

    @property
    def last_rollover_day(self) -> date | None:
        """最近一次校对的日期。"""
        return self._last_rollover_day

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_today(self, day: date | None = None) -> list[TaskItem]:
        """返回今日任务列表（已按提醒时间 + 创建时间排序）。"""
        target = day or timeutils.today_local()
        today_tasks = [t for t in self._repo.list_all() if t.is_visible_on(target)]
        today_tasks.sort(key=TaskItem.sort_key)
        return today_tasks

    def refresh(self, day: date | None = None) -> list[TaskItem]:
        """校对循环状态后返回今日列表（界面刷新的统一入口）。"""
        target = day or timeutils.today_local()
        self.rollover_if_needed(target)
        return self.list_today(target)

    def list_all(self) -> list[TaskItem]:
        """返回全部任务（设置页/调试用）。"""
        return self._repo.list_all()

    def get(self, task_id: int) -> TaskItem | None:
        """按主键获取任务。"""
        return self._repo.get(task_id)

    def progress_today(self, day: date | None = None) -> TaskProgress:
        """计算今日完成进度。

        口径说明：``total`` = 今日可见任务数 + 今天已完成因而隐藏的单次任务数；
        ``completed`` = 完成日期为今天的任务数。这样「完成一个单次任务」时
        分子分母同时 +1，进度条不会因为任务消失而回退。
        """
        target = day or timeutils.today_local()
        tasks = self._repo.list_all()
        visible = [t for t in tasks if t.is_visible_on(target)]
        completed_today = [t for t in tasks if t.last_completed_date == target]
        hidden_completed_once = [
            t
            for t in completed_today
            if t.type is TaskType.ONCE and not t.is_visible_on(target)
        ]
        return TaskProgress(
            completed=len(completed_today),
            total=len(visible) + len(hidden_completed_once),
        )

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def create(self, task: TaskItem) -> TaskItem:
        """新建任务。"""
        self._repo.add(task)
        return task

    def save(self, task: TaskItem) -> TaskItem:
        """新建或更新（按 ``id`` 是否为空判断）。"""
        if task.id is None:
            return self.create(task)
        self._repo.update(task)
        return task

    def update(self, task: TaskItem) -> TaskItem:
        """更新已有任务。"""
        if task.id is None:
            raise ValueError("更新任务需要先有 id")
        self._repo.update(task)
        return task

    def delete(self, task_id: int) -> bool:
        """删除任务（文档 5.3：删除前由界面弹出确认）。"""
        return self._repo.delete(task_id)

    def set_completed(
        self, task_id: int, completed: bool, day: date | None = None
    ) -> TaskItem | None:
        """切换完成状态并持久化（文档 5.4）。"""
        target = day or timeutils.today_local()
        task = self._repo.get(task_id)
        if task is None:
            logger.warning("切换完成状态失败：任务 id=%s 不存在", task_id)
            return None
        updated = task.with_completion(completed, target)
        self._repo.update(updated)
        logger.info(
            "任务 id=%s %s（完成日期=%s）",
            task_id,
            "标记完成" if completed else "取消完成",
            timeutils.format_date(updated.last_completed_date) if completed else "-",
        )
        return updated

    def toggle(self, task_id: int, day: date | None = None) -> TaskItem | None:
        """在完成与未完成之间切换。"""
        task = self._repo.get(task_id)
        if task is None:
            return None
        return self.set_completed(task_id, not task.is_completed, day)

    # ------------------------------------------------------------------
    # 校验（文档 5.3 校验规则）
    # ------------------------------------------------------------------
    @staticmethod
    def validate(
        title: str,
        task_type: TaskType,
        due_date: date | None,
    ) -> str | None:
        """校验任务字段，合法时返回 ``None``，否则返回面向用户的错误文案。"""
        if not (title or "").strip():
            return "标题不能为空"
        if len(title.strip()) > 100:
            return "标题不能超过 100 个字符"
        if task_type is TaskType.ONCE and due_date is None:
            return "单次任务必须设置截止日期"
        if task_type in (TaskType.WEEKLY, TaskType.MONTHLY) and due_date is None:
            return f"{task_type.label}任务必须设置参考日期"
        return None

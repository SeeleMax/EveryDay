"""任务服务（文档 5.3 自定义任务、5.4 完成状态、5.5 循环与单次）。

本模块是「任务业务规则」的唯一入口：界面与托盘都通过它读写任务，
从而不会出现「某处漏做循环重置」这类问题。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date

from everyday import timeutils
from everyday.db.repositories import TagRepository, TaskRepository
from everyday.models.task import TaskItem, TaskType
from everyday.services.dependency_service import DependencyService

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

    def __init__(
        self,
        repository: TaskRepository,
        tags: TagRepository | None = None,
        dependencies: DependencyService | None = None,
    ) -> None:
        self._repo = repository
        #: v1.1 P2：标签仓储（可选，为 None 时不填充标签）
        self._tags = tags
        #: v1.1 P2：依赖服务（可选，为 None 时不计算阻塞状态）
        self._dependencies = dependencies
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
    def list_today(self, day: date | None = None, tag: str | None = None) -> list[TaskItem]:
        """返回今日任务列表。

        排序规则见 :meth:`TaskItem.sort_key`：未完成在前、优先级高的在前，
        已完成的沉到最下面。

        v1.1 P2 起会一并填充界面需要的三个展示字段（标签、子任务进度、
        被哪些前置任务阻塞），全部用**批量查询**完成，不逐行查表。

        :param tag: 只返回带该标签的任务；``None`` 表示不筛选。
        """
        target = day or timeutils.today_local()
        today_tasks = [t for t in self._repo.list_all() if t.is_visible_on(target)]
        if tag:
            # 一次批量取标签，不要在这里逐任务查（否则就是 N+1）
            ids = [t.id for t in today_tasks if t.id is not None]
            tag_map = self._tags.tags_for_many(ids) if self._tags is not None else {}
            today_tasks = [
                t for t in today_tasks if t.id is not None and tag in tag_map.get(t.id, ())
            ]
        today_tasks.sort(key=TaskItem.sort_key)
        return self._decorate_many(today_tasks)

    def _decorate(self, task: TaskItem) -> TaskItem:
        """给单个任务补上标签 / 子任务进度 / 阻塞信息。"""
        return self._decorate_many([task])[0]

    def _decorate_many(self, tasks: list[TaskItem]) -> list[TaskItem]:
        """批量补齐展示字段。

        一次查标签、一次查全部任务算子任务进度、一次查依赖算阻塞，
        复杂度与任务数量线性相关而不是乘积。
        """
        if not tasks:
            return []
        ids = [t.id for t in tasks if t.id is not None]

        tag_map: dict[int, list[str]] = {}
        if self._tags is not None and ids:
            tag_map = self._tags.tags_for_many(ids)

        # 子任务进度：把「所有任务」按 parent 聚一次，比逐个查询快得多
        child_counts: dict[int, list[int]] = {}
        for child in self._repo.list_all():
            if child.parent_task_id is None:
                continue
            bucket = child_counts.setdefault(child.parent_task_id, [0, 0])
            bucket[0] += 1
            if child.is_completed:
                bucket[1] += 1

        blocked: dict[int, list[int]] = {}
        if self._dependencies is not None and tasks:
            blocked = self._dependencies.blocked_map(tasks)

        decorated: list[TaskItem] = []
        for task in tasks:
            counts = child_counts.get(task.id, [0, 0]) if task.id is not None else [0, 0]
            decorated.append(
                replace(
                    task,
                    tags=tuple(tag_map.get(task.id, ())) if task.id is not None else (),
                    child_total=counts[0],
                    child_completed=counts[1],
                    blocked_by=tuple(blocked.get(task.id, ()))
                    if task.id is not None
                    else (),
                )
            )
        return decorated

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

        口径说明：``completed`` = 完成日期为今天的任务数；
        ``total`` = 今日可见任务数。

        已完成的任务现在**仍留在今日列表里**（半透明、沉到底部），
        因此不需要再为「已完成却被隐藏」的任务单独补一项分母——
        早期版本会补，是因为单次任务完成后会立刻从列表消失。
        """
        target = day or timeutils.today_local()
        tasks = self._repo.list_all()
        visible = [t for t in tasks if t.is_visible_on(target)]
        completed_today = [t for t in tasks if t.last_completed_date == target]
        return TaskProgress(
            completed=len(completed_today),
            total=len(visible),
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

    # ------------------------------------------------------------------
    # v1.1 P2：标签与依赖的便捷入口
    #
    # 对话框只依赖 TaskService，由它向下转发，避免界面直接摸仓储。
    # ------------------------------------------------------------------
    def tags_for(self, task_id: int) -> list[str]:
        """某个任务的标签。"""
        return self._tags.tags_for(task_id) if self._tags is not None else []

    def all_tags(self) -> list[str]:
        """所有出现过的标签（筛选下拉框用）。"""
        return self._tags.all_tags() if self._tags is not None else []

    def set_tags(self, task_id: int, tags: list[str]) -> None:
        """整份替换某个任务的标签。"""
        if self._tags is not None:
            self._tags.set_tags(task_id, tags)

    def dependencies_of(self, task_id: int) -> list[int]:
        """某个任务的前置依赖 id。"""
        return self._dependencies.dependencies_of(task_id) if self._dependencies else []

    def set_dependencies(self, task_id: int, depends_on_ids: list[int]) -> list[int]:
        """整份替换前置依赖（会做环检测，成环抛 ``DependencyCycleError``）。"""
        if self._dependencies is None:
            return []
        return self._dependencies.set_dependencies(task_id, depends_on_ids)

    def describe_blocking(self, task: TaskItem) -> str:
        """把阻塞原因翻译成可读文本（界面提示用）。"""
        if self._dependencies is None or not task.blocked_by:
            return ""
        return self._dependencies.describe_blocking(task.blocked_by)

    def save_with_details(
        self,
        task: TaskItem,
        tags: list[str] | None = None,
        depends_on: list[int] | None = None,
    ) -> TaskItem:
        """保存任务，并一并写入标签与前置依赖（对话框保存时用）。

        顺序：先落库任务（新建时才能拿到 id，依赖外键需要它），
        再写标签与依赖。依赖成环时 ``set_dependencies`` 会抛
        ``DependencyCycleError``——此时任务本身已经保存，
        界面上提示用户改依赖即可，不至于把用户填的内容全丢掉。
        """
        if task.id is None:
            self.create(task)
        else:
            self._repo.update(task)
        if depends_on is not None:
            self.set_dependencies(task.id, depends_on)
        if tags is not None:
            self.set_tags(task.id, tags)
        return self._decorate(task)

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
        """切换完成状态并持久化（文档 5.4）。

        v1.1 P2：任务被未完成的前置依赖阻塞时**拒绝勾选**，返回 ``None``。
        界面上勾选框本来就是置灰的，这里是服务层的兜底——防止
        「托盘菜单 / 快捷键」等其它入口绕过界面约束。
        """
        target = day or timeutils.today_local()
        task = self._repo.get(task_id)
        if task is None:
            logger.warning("切换完成状态失败：任务 id=%s 不存在", task_id)
            return None
        if completed and self._dependencies is not None:
            blocking = self._dependencies.blocked_map([task]).get(task_id, [])
            if blocking:
                logger.warning(
                    "拒绝勾选任务 id=%s：前置任务 %s 尚未完成", task_id, blocking
                )
                return None
        updated = task.with_completion(completed, target)
        self._repo.update(updated)
        logger.info(
            "任务 id=%s %s（完成日期=%s）",
            task_id,
            "标记完成" if completed else "取消完成",
            timeutils.format_date(updated.last_completed_date) if completed else "-",
        )
        return self._decorate(updated)

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

"""前置依赖服务（v1.1 P2）。

交接文档 P2 的要求：

* 任务可设置前置依赖（前置未完成 → 勾选框置灰并提示）
* 前置完成 → 自动恢复可勾选
* **依赖形成环时必须拒绝并给出提示**（循环依赖检测）

本模块只做「图」的事：加边、查边、检测环、拓扑排序、算阻塞。
节点的业务含义（是否完成）由调用方通过 :class:`TaskItem` 传入，
所以这里不依赖数据库以外的任何东西，也可以纯内存测试。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping

from everyday.db.repositories import DependencyRepository
from everyday.models.task import TaskItem

logger = logging.getLogger(__name__)


class DependencyCycleError(ValueError):
    """添加依赖会形成环。``path`` 是环上的任务 id 序列，便于提示用户。"""

    def __init__(self, path: list[int]) -> None:
        self.path = list(path)
        arrow = " → ".join(str(i) for i in self.path)
        super().__init__(f"该依赖会形成循环依赖：{arrow}")


class DependencyService:
    """任务前置依赖的读写与图算法。

    「A 依赖 B」记作边 ``A → B``，语义是 **B 必须先完成**。
    因此拓扑排序的结果中 B 出现在 A 之前。
    """

    def __init__(
        self,
        repository: DependencyRepository,
        task_repository=None,
    ) -> None:
        self._repo = repository
        #: 可选：用于把 id 映射成标题、判断完成状态。为 ``None`` 时只做纯图操作。
        self._tasks = task_repository

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def dependencies_of(self, task_id: int) -> list[int]:
        """任务的前置（它依赖谁）。"""
        return self._repo.dependencies_of(task_id)

    def dependents_of(self, task_id: int) -> list[int]:
        """任务的后置（谁依赖它）。"""
        return self._repo.dependents_of(task_id)

    def graph(self) -> dict[int, list[int]]:
        """把全部依赖读成邻接表 ``{任务: [前置...]}``。"""
        adjacency: dict[int, list[int]] = {}
        for task_id, depends_on in self._repo.list_all():
            adjacency.setdefault(task_id, []).append(depends_on)
        return adjacency

    # ------------------------------------------------------------------
    # 写（带环检测）
    # ------------------------------------------------------------------
    def add_dependency(self, task_id: int, depends_on_id: int) -> bool:
        """添加一条依赖。

        :raises DependencyCycleError: 该边会形成环
        :raises ValueError: 自指
        """
        if task_id == depends_on_id:
            raise ValueError("任务不能依赖自己")
        # 加边前先判断：沿着「depends_on 的前置链」走下去能否回到 task_id
        path = self._find_path(depends_on_id, task_id)
        if path is not None:
            raise DependencyCycleError([task_id, *path])
        added = self._repo.add(task_id, depends_on_id)
        if added:
            logger.info("任务 %s 增加前置依赖 %s", task_id, depends_on_id)
        return added

    def set_dependencies(self, task_id: int, depends_on_ids: Iterable[int]) -> list[int]:
        """整份替换前置依赖；只要有一条会成环就整体拒绝，不写入任何边。"""
        wanted = [i for i in dict.fromkeys(depends_on_ids) if i != task_id]

        # 先在内存里模拟：现有图去掉该任务的旧边，再把新边逐条加入并检测
        adjacency = self.graph()
        adjacency.pop(task_id, None)
        for dep in wanted:
            if self._find_path(dep, task_id, adjacency) is not None:
                raise DependencyCycleError([task_id, dep, task_id])
            adjacency.setdefault(task_id, []).append(dep)

        self._repo.set_dependencies(task_id, wanted)
        logger.info("任务 %s 的前置依赖已更新为 %s", task_id, wanted)
        return wanted

    def remove_dependency(self, task_id: int, depends_on_id: int) -> bool:
        """删除一条依赖。"""
        return self._repo.remove(task_id, depends_on_id)

    def clear_for_task(self, task_id: int) -> int:
        """清掉与某个任务相关的所有依赖边。"""
        return self._repo.clear_for_task(task_id)

    # ------------------------------------------------------------------
    # 环检测
    # ------------------------------------------------------------------
    def would_create_cycle(self, task_id: int, depends_on_id: int) -> bool:
        """判断加这条边是否成环（不写入）。"""
        if task_id == depends_on_id:
            return True
        return self._find_path(depends_on_id, task_id) is not None

    def find_cycle(self) -> list[int] | None:
        """全图体检：有环则返回环上的 id 序列，无环返回 ``None``。

        用于「历史数据已经存在环」的兜底（例如早期版本或手工改库造成）。
        """
        adjacency = self.graph()
        WHITE, GREY, BLACK = 0, 1, 2
        color: dict[int, int] = {}
        stack: list[tuple[int, int]] = []

        def visit(node: int) -> list[int] | None:
            color[node] = GREY
            stack.append((node, 0))
            while stack:
                current, index = stack[-1]
                neighbours = adjacency.get(current, [])
                if index < len(neighbours):
                    stack[-1] = (current, index + 1)
                    nxt = neighbours[index]
                    state = color.get(nxt, WHITE)
                    if state == GREY:
                        # 找到回边：截取当前栈中从 nxt 到栈顶的路径
                        cycle = [n for n, _ in stack]
                        start = cycle.index(nxt)
                        return cycle[start:] + [nxt]
                    if state == WHITE:
                        color[nxt] = GREY
                        stack.append((nxt, 0))
                else:
                    color[current] = BLACK
                    stack.pop()
            return None

        for node in adjacency:
            if color.get(node, WHITE) == WHITE:
                found = visit(node)
                if found is not None:
                    return found
        return None

    def _find_path(
        self,
        start: int,
        target: int,
        adjacency: Mapping[int, list[int]] | None = None,
    ) -> list[int] | None:
        """沿「前置」方向从 ``start`` 走到 ``target`` 的路径（DFS）。

        返回 ``[start, ..., target]``，走不到返回 ``None``。
        """
        graph = adjacency if adjacency is not None else self.graph()
        if start == target:
            return [start]
        visited: set[int] = set()
        stack: list[tuple[int, list[int]]] = [(start, [start])]
        while stack:
            node, path = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            for nxt in graph.get(node, []):
                if nxt == target:
                    return [*path, nxt]
                if nxt not in visited:
                    stack.append((nxt, [*path, nxt]))
        return None

    # ------------------------------------------------------------------
    # 拓扑排序
    # ------------------------------------------------------------------
    def topological_order(self, task_ids: Iterable[int] | None = None) -> list[int]:
        """按依赖关系排序：被依赖的在前。

        用 Kahn 算法。有环时**不会死循环**——把剩余的节点按 id 追加到末尾
        并记一条告警，保证界面永远能拿到一个完整列表。

        :param task_ids: 只排这些任务；``None`` 表示图上出现的全部任务。
        """
        subset = set(task_ids) if task_ids is not None else None
        adjacency = self.graph()

        nodes: set[int] = set(adjacency)
        for deps in adjacency.values():
            nodes.update(deps)
        if subset is not None:
            nodes &= subset

        # 入度 = 该任务还有几个「在本集合内」的前置
        indegree: dict[int, int] = {node: 0 for node in nodes}
        for task_id, deps in adjacency.items():
            if task_id not in nodes:
                continue
            for dep in deps:
                if dep in nodes:
                    indegree[task_id] += 1

        ready = sorted(node for node, degree in indegree.items() if degree == 0)
        ordered: list[int] = []
        while ready:
            node = ready.pop(0)
            ordered.append(node)
            for dependent, deps in adjacency.items():
                if dependent in indegree and node in deps:
                    indegree[dependent] -= 1
                    if indegree[dependent] == 0:
                        ready.append(dependent)
                        ready.sort()

        remaining = [node for node in indegree if node not in set(ordered)]
        if remaining:
            logger.warning("拓扑排序发现环，%d 个任务无法排序：%s", len(remaining), remaining)
            ordered.extend(sorted(remaining))
        return ordered

    # ------------------------------------------------------------------
    # 阻塞判断
    # ------------------------------------------------------------------
    def blocked_map(self, tasks: Iterable[TaskItem]) -> dict[int, list[int]]:
        """算出每个任务「尚未完成的前置」列表。

        一次读完依赖边与任务状态，避免逐任务查询（N+1）。

        ⚠️ 状态必须来自**整张任务表**，不能只看传入的这几条：
        前置任务通常不在「今日可见列表」里（它可能是已完成的历史任务、
        或属于别的日期），如果只查传入集合就会查不到，被当成「已完成」，
        阻塞判断静默失效——这是测试抓出来的真实缺陷。

        * 前置任务已被软删除 → 不算阻塞（用户看不到它了）
        * 前置任务已完成 → 不算阻塞
        * 前置是循环任务且已跨天 → rollover 已把它置为未完成，会重新阻塞
        """
        task_list = list(tasks)
        state: dict[int, bool] = {}
        if self._tasks is not None:
            for task in self._tasks.list_all(include_deleted=True):
                if task.id is None:
                    continue
                # 软删除的任务视为「不再阻塞」
                state[task.id] = True if task.is_deleted else task.is_completed
        else:
            for task in task_list:
                if task.id is not None:
                    state[task.id] = task.is_completed

        blocked: dict[int, list[int]] = {}
        for task_id, depends_on in self._repo.list_all():
            if task_id not in state:
                continue  # 不在本次要展示的任务集合里
            if state.get(depends_on, True):
                continue  # 前置已完成、已软删除，或已被彻底清理 → 不阻塞
            blocked.setdefault(task_id, []).append(depends_on)
        return {k: sorted(v) for k, v in blocked.items()}

    def is_blocked(self, task: TaskItem) -> bool:
        """单个任务是否被未完成的前置阻塞。"""
        return bool(self.blocked_map([task]).get(task.id, []))

    def describe_blocking(self, task_ids: Iterable[int]) -> str:
        """把前置任务 id 翻译成「等待：写周报、买涂料」这样的提示文本。"""
        ids = [i for i in task_ids if i is not None]
        if not ids:
            return ""
        if self._tasks is None:
            return "等待前置任务：" + "、".join(f"#{i}" for i in ids)
        titles: list[str] = []
        for task_id in ids:
            task = self._tasks.get(task_id)
            titles.append(task.title if task is not None else f"#{task_id}")
        return "等待前置任务：" + "、".join(titles)

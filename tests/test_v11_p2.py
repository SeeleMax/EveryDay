"""v1.1 P2 测试：优先级、标签、子任务、前置依赖与循环依赖检测。

对应交接文档 P2 的四条验收：

* 设置优先级为「紧急」的任务排在列表首位
* 标签可以添加、删除、按标签筛选
* 子任务可以折叠（数据层提供进度，折叠由界面实现）
* 依赖关系形成环时拒绝并提示
"""

from __future__ import annotations

import unittest
from datetime import date, time

from everyday.db import DependencyRepository, TagRepository
from everyday.models.task import TaskItem, TaskPriority, TaskType
from everyday.services.dependency_service import DependencyCycleError, DependencyService
from everyday.services.task_service import TaskService
from tests.base import DatabaseTestCase

MONDAY = date(2026, 10, 5)


class P2TestCase(DatabaseTestCase):
    """带上标签仓储与依赖服务的测试基类。"""

    def setUp(self) -> None:
        super().setUp()
        self.tag_repo = TagRepository(self.db)
        self.dep_repo = DependencyRepository(self.db)
        self.dependency_service = DependencyService(self.dep_repo, self.tasks)
        self.service = TaskService(
            self.tasks, tags=self.tag_repo, dependencies=self.dependency_service
        )

    def add(self, title: str, **kwargs) -> TaskItem:
        kwargs.setdefault("type", TaskType.DAILY)
        task = TaskItem(title=title, **kwargs)
        self.service.create(task)
        return task


# ======================================================================
# 优先级
# ======================================================================
class PriorityOrderingTest(P2TestCase):
    def test_urgent_comes_first_regardless_of_remind_time(self):
        """设置「紧急」的任务排在列表首位——P2 验收原文。"""
        self.add("下午的普通任务", remind_time=time(15, 0))
        urgent = self.add("救火", priority=TaskPriority.URGENT, remind_time=time(23, 0))
        self.add("上午的普通任务", remind_time=time(9, 0))

        titles = [t.title for t in self.service.list_today(MONDAY)]
        self.assertEqual(titles[0], urgent.title)

    def test_priority_applies_after_completion_sinking(self):
        """优先级再高，已完成的仍要沉到最后（v1.0 的行为不能被 P2 打破）。"""
        urgent_done = self.add("紧急但已完成", priority=TaskPriority.URGENT)
        self.add("低优先级未完成", priority=TaskPriority.LOW)
        self.service.set_completed(urgent_done.id, True, MONDAY)

        titles = [t.title for t in self.service.list_today(MONDAY)]
        self.assertEqual(titles, ["低优先级未完成", "紧急但已完成"])

    def test_full_priority_ladder(self):
        low = self.add("低", priority=TaskPriority.LOW)
        normal = self.add("普通", priority=TaskPriority.NORMAL)
        high = self.add("高", priority=TaskPriority.HIGH)
        urgent = self.add("紧急", priority=TaskPriority.URGENT)

        titles = [t.title for t in self.service.list_today(MONDAY)]
        self.assertEqual(titles, [urgent.title, high.title, normal.title, low.title])

    def test_priority_defaults_to_normal(self):
        task = self.add("默认")
        self.assertIs(task.priority, TaskPriority.NORMAL)


# ======================================================================
# 标签
# ======================================================================
class TagFilteringTest(P2TestCase):
    def test_tags_are_populated_on_today_list(self):
        task = self.add("写周报")
        self.tag_repo.set_tags(task.id, ["工作", "本周"])

        listed = self.service.list_today(MONDAY)
        self.assertEqual(listed[0].tags, ("工作", "本周"))

    def test_filter_by_tag(self):
        work = self.add("写周报")
        life = self.add("买菜")
        self.tag_repo.set_tags(work.id, ["工作"])
        self.tag_repo.set_tags(life.id, ["生活"])

        only_work = self.service.list_today(MONDAY, tag="工作")
        self.assertEqual([t.title for t in only_work], ["写周报"])

        self.assertEqual(self.service.list_today(MONDAY, tag="不存在"), [])
        self.assertEqual(len(self.service.list_today(MONDAY)), 2)

    def test_all_tags_feeds_filter_dropdown(self):
        self.tag_repo.add(self.add("写周报").id, "工作")
        self.tag_repo.add(self.add("买菜").id, "生活")
        # SQLite 按 UTF-8 字节序排序：工(U+5DE5) < 生(U+751F)
        self.assertEqual(self.service.all_tags(), ["工作", "生活"])

    def test_tag_helpers_delegate(self):
        task = self.add("写周报")
        self.service.set_tags(task.id, ["工作"])
        self.assertEqual(self.service.tags_for(task.id), ["工作"])

    def test_no_tag_repository_means_empty(self):
        plain = TaskService(self.tasks)
        task = self.add("写周报")
        self.assertEqual(plain.tags_for(task.id), [])
        self.assertEqual(plain.list_today(MONDAY)[0].tags, ())


# ======================================================================
# 子任务
# ======================================================================
class SubTaskProgressTest(P2TestCase):
    def test_parent_reports_child_progress(self):
        parent = self.add("装修")
        first = self.add("买涂料", parent_task_id=parent.id)
        self.add("约工人", parent_task_id=parent.id)

        listed = {t.title: t for t in self.service.list_today(MONDAY)}
        self.assertTrue(listed["装修"].has_children)
        self.assertEqual(listed["装修"].child_total, 2)
        self.assertEqual(listed["装修"].child_completed, 0)
        self.assertEqual(listed["装修"].child_progress_label, "0/2")

        self.service.set_completed(first.id, True, MONDAY)
        listed = {t.title: t for t in self.service.list_today(MONDAY)}
        self.assertEqual(listed["装修"].child_progress_label, "1/2")

    def test_task_without_children_has_empty_progress(self):
        self.add("独立任务")
        listed = self.service.list_today(MONDAY)[0]
        self.assertFalse(listed.has_children)
        self.assertEqual(listed.child_progress_label, "")

    def test_children_are_listed_separately(self):
        parent = self.add("装修")
        self.add("买涂料", parent_task_id=parent.id)
        # 子任务也各自出现在今日列表里（折叠是界面层的事）
        self.assertEqual(len(self.service.list_today(MONDAY)), 2)


# ======================================================================
# 依赖与循环检测
# ======================================================================
class DependencyServiceTest(P2TestCase):
    def test_add_and_query(self):
        a = self.add("A")
        b = self.add("B")
        self.assertTrue(self.dependency_service.add_dependency(b.id, a.id))
        self.assertEqual(self.dependency_service.dependencies_of(b.id), [a.id])
        self.assertEqual(self.dependency_service.dependents_of(a.id), [b.id])

    def test_self_dependency_rejected(self):
        a = self.add("A")
        with self.assertRaises(ValueError):
            self.dependency_service.add_dependency(a.id, a.id)

    def test_direct_cycle_rejected(self):
        """A 依赖 B 之后，再让 B 依赖 A 必须被拒绝并指出环节点。"""
        a = self.add("A")
        b = self.add("B")
        self.dependency_service.add_dependency(b.id, a.id)  # B → A

        with self.assertRaises(DependencyCycleError) as ctx:
            self.dependency_service.add_dependency(a.id, b.id)  # A → B 成环
        self.assertIn(a.id, ctx.exception.path)
        self.assertIn(b.id, ctx.exception.path)

        # 被拒绝的边不能落库
        self.assertEqual(self.dependency_service.dependencies_of(a.id), [])

    def test_indirect_cycle_rejected(self):
        """A→B→C 之后，C→A 形成三元环，也必须拒绝。"""
        a = self.add("A")
        b = self.add("B")
        c = self.add("C")
        self.dependency_service.add_dependency(a.id, b.id)  # A 依赖 B
        self.dependency_service.add_dependency(b.id, c.id)  # B 依赖 C

        with self.assertRaises(DependencyCycleError):
            self.dependency_service.add_dependency(c.id, a.id)  # C 依赖 A → 环

        self.assertEqual(self.dependency_service.dependencies_of(c.id), [])

    def test_would_create_cycle_does_not_write(self):
        a = self.add("A")
        b = self.add("B")
        self.dependency_service.add_dependency(b.id, a.id)

        self.assertTrue(self.dependency_service.would_create_cycle(a.id, b.id))
        self.assertTrue(self.dependency_service.would_create_cycle(a.id, a.id), "自指也算成环")
        self.assertEqual(self.dependency_service.dependencies_of(a.id), [])

    def test_set_dependencies_rejects_whole_batch_on_cycle(self):
        """批量设置时只要有一条成环，整批都不写入（不留半成品）。"""
        a = self.add("A")
        b = self.add("B")
        self.dependency_service.add_dependency(b.id, a.id)  # B → A

        with self.assertRaises(DependencyCycleError):
            self.dependency_service.set_dependencies(a.id, [b.id])

        self.assertEqual(self.dependency_service.dependencies_of(a.id), [])

    def test_set_dependencies_replaces_and_drops_self_reference(self):
        a = self.add("A")
        b = self.add("B")
        c = self.add("C")
        self.dependency_service.set_dependencies(c.id, [a.id, b.id, a.id, c.id])
        self.assertEqual(self.dependency_service.dependencies_of(c.id), [a.id, b.id])

    def test_find_cycle_on_clean_graph(self):
        a = self.add("A")
        b = self.add("B")
        self.dependency_service.add_dependency(b.id, a.id)
        self.assertIsNone(self.dependency_service.find_cycle())

    def test_find_cycle_detects_handcrafted_cycle(self):
        """绕过服务层直接写边造一个环，全图体检必须能发现。"""
        a = self.add("A")
        b = self.add("B")
        self.dep_repo.add(a.id, b.id)
        self.dep_repo.add(b.id, a.id)

        cycle = self.dependency_service.find_cycle()
        self.assertIsNotNone(cycle)
        self.assertIn(a.id, cycle)
        self.assertIn(b.id, cycle)

    def test_topological_order_puts_dependencies_first(self):
        a = self.add("A")
        b = self.add("B")
        c = self.add("C")
        # C 依赖 B，B 依赖 A  →  顺序应为 A, B, C
        self.dependency_service.add_dependency(b.id, a.id)
        self.dependency_service.add_dependency(c.id, b.id)

        order = self.dependency_service.topological_order()
        self.assertLess(order.index(a.id), order.index(b.id))
        self.assertLess(order.index(b.id), order.index(c.id))

    def test_topological_order_survives_a_cycle(self):
        """有环时不能死循环，必须返回完整列表（脏数据的兜底）。"""
        a = self.add("A")
        b = self.add("B")
        self.dep_repo.add(a.id, b.id)
        self.dep_repo.add(b.id, a.id)

        order = self.dependency_service.topological_order()
        self.assertEqual(sorted(order), sorted([a.id, b.id]))

    def test_clear_for_task(self):
        a = self.add("A")
        b = self.add("B")
        self.dependency_service.add_dependency(b.id, a.id)
        self.assertEqual(self.dependency_service.clear_for_task(b.id), 1)
        self.assertEqual(self.dependency_service.dependencies_of(b.id), [])


class DependencyBlockingTest(P2TestCase):
    def test_unfinished_dependency_blocks(self):
        prereq = self.add("打地基")
        house = self.add("盖房子")
        self.dependency_service.add_dependency(house.id, prereq.id)

        listed = {t.title: t for t in self.service.list_today(MONDAY)}
        self.assertTrue(listed["盖房子"].is_blocked)
        self.assertEqual(listed["盖房子"].blocked_by, (prereq.id,))
        self.assertFalse(listed["打地基"].is_blocked)

    def test_completing_prerequisite_unblocks(self):
        """前置完成 → 自动恢复可勾选（P2 验收要求）。"""
        prereq = self.add("打地基")
        house = self.add("盖房子")
        self.dependency_service.add_dependency(house.id, prereq.id)

        self.service.set_completed(prereq.id, True, MONDAY)

        listed = {t.title: t for t in self.service.list_today(MONDAY)}
        self.assertFalse(listed["盖房子"].is_blocked)

    def test_blocked_task_cannot_be_completed(self):
        """服务层兜底：被阻塞的任务即使绕过界面也勾不上。"""
        prereq = self.add("打地基")
        house = self.add("盖房子")
        self.dependency_service.add_dependency(house.id, prereq.id)

        self.assertIsNone(self.service.set_completed(house.id, True, MONDAY))
        self.assertFalse(self.tasks.get(house.id).is_completed)

    def test_deleted_prerequisite_does_not_block(self):
        """前置被软删除（进了回收站）后不应继续阻塞。"""
        prereq = self.add("打地基")
        house = self.add("盖房子")
        self.dependency_service.add_dependency(house.id, prereq.id)
        self.tasks.soft_delete(prereq.id)

        listed = {t.title: t for t in self.service.list_today(MONDAY)}
        self.assertFalse(listed["盖房子"].is_blocked)

    def test_describe_blocking_uses_titles(self):
        prereq = self.add("打地基")
        house = self.add("盖房子")
        self.dependency_service.add_dependency(house.id, prereq.id)

        listed = {t.title: t for t in self.service.list_today(MONDAY)}
        text = self.service.describe_blocking(listed["盖房子"])
        self.assertIn("打地基", text)

    def test_decorate_is_cheap_batch(self):
        """_decorate_many 应一次算完，不是逐任务查（这里只验证结果正确）。"""
        a = self.add("A")
        b = self.add("B", parent_task_id=a.id)
        self.tag_repo.add(a.id, "工作")
        self.dependency_service.add_dependency(b.id, a.id)

        listed = {t.title: t for t in self.service.list_today(MONDAY)}
        self.assertEqual(listed["A"].tags, ("工作",))
        self.assertEqual(listed["A"].child_total, 1)
        self.assertEqual(listed["B"].blocked_by, (a.id,))


class SaveWithDetailsTest(P2TestCase):
    def test_new_task_persists_tags_and_dependencies(self):
        prereq = self.add("打地基")
        task = TaskItem(title="盖房子", type=TaskType.DAILY)
        self.service.save_with_details(task, tags=["装修"], depends_on=[prereq.id])

        self.assertIsNotNone(task.id)
        self.assertEqual(self.service.tags_for(task.id), ["装修"])
        self.assertEqual(self.service.dependencies_of(task.id), [prereq.id])

    def test_existing_task_updates_details(self):
        task = self.add("盖房子")
        self.service.save_with_details(task, tags=["装修"], depends_on=[])
        self.assertEqual(self.service.tags_for(task.id), ["装修"])

    def test_task_survives_when_dependency_is_rejected(self):
        """依赖成环被拒时，任务本身仍然保存（不丢用户填的内容）。"""
        a = self.add("A")
        new_task = self.add("新任务")
        # 让 A 依赖新任务，于是「新任务依赖 A」就会成环
        self.dependency_service.add_dependency(a.id, new_task.id)

        with self.assertRaises(DependencyCycleError):
            self.service.save_with_details(new_task, depends_on=[a.id])
        # 依赖被拒，但任务本身仍在
        self.assertIsNotNone(self.tasks.get(new_task.id))
        self.assertEqual(self.service.dependencies_of(new_task.id), [])


if __name__ == "__main__":
    unittest.main()

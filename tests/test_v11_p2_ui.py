"""v1.1 P2 界面层测试：标签筛选下拉框、子任务折叠与依赖置灰。

这一层单独成文件，因为需要**带标签与依赖服务**的 AppContext——
``tests/test_ui_smoke.py`` 里的基类只构造了裸 TaskService，
装饰字段（标签、子任务进度、阻塞）会全是空的。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from PySide6.QtWidgets import QApplication

from everyday import paths
from everyday.db import (
    MEMORY,
    Database,
    DependencyRepository,
    MigrationRunner,
    PresetRepository,
    SettingsRepository,
    TagRepository,
    TaskRepository,
)
from everyday.models.task import TaskItem, TaskPriority, TaskType
from everyday.services.calendar_service import CalendarService
from everyday.services.dependency_service import DependencyService
from everyday.services.preset_service import PresetService
from everyday.services.settings_service import SettingsService
from everyday.services.task_service import TaskService
from everyday.ui.context import AppContext
from everyday.ui.main_window import MainWindow


class _NoopStartupService:
    """不碰注册表的开机自启替身。"""

    def is_enabled(self) -> bool:
        return False

    def enable(self) -> bool:
        return True

    def disable(self) -> bool:
        return True


class P2UiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._previous = os.environ.get(paths.DATA_DIR_ENV)
        os.environ[paths.DATA_DIR_ENV] = self._tmp.name

        self.db = Database(MEMORY)
        MigrationRunner(self.db).run()

        self.settings_service = SettingsService(SettingsRepository(self.db))
        self.settings_service.seed_from_template()
        self.settings = self.settings_service.load()

        task_repo = TaskRepository(self.db)
        self.tag_repo = TagRepository(self.db)
        self.dependency = DependencyService(DependencyRepository(self.db), task_repo)
        self.task_service = TaskService(
            task_repo, tags=self.tag_repo, dependencies=self.dependency
        )

        self.context = AppContext(
            task_service=self.task_service,
            preset_service=PresetService(PresetRepository(self.db)),
            settings_service=self.settings_service,
            calendar_service=CalendarService(),
            startup_service=_NoopStartupService(),
            settings=self.settings,
        )

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()
        if self._previous is None:
            os.environ.pop(paths.DATA_DIR_ENV, None)
        else:
            os.environ[paths.DATA_DIR_ENV] = self._previous

    def add(self, title: str, **kwargs) -> TaskItem:
        payload = {"title": title, "type": TaskType.DAILY}
        payload.update(kwargs)
        task = TaskItem(**payload)
        self.task_service.create(task)
        return task

    def window(self) -> MainWindow:
        window = MainWindow(self.context)
        self.addCleanup(window.deleteLater)
        return window

    @staticmethod
    def titles(window: MainWindow) -> list[str]:
        """当前列表里显示的任务标题（占位行会返回空列表）。"""
        result: list[str] = []
        for index in range(window.task_list.count()):
            row = window.task_list.itemWidget(window.task_list.item(index))
            if row is not None:
                result.append(row._task.title)
        return result


class TagFilterUiTest(P2UiBase):
    def test_combo_lists_all_tags_with_all_option_first(self):
        work = self.add("写周报")
        life = self.add("买菜")
        self.tag_repo.set_tags(work.id, ["工作"])
        self.tag_repo.set_tags(life.id, ["生活"])

        window = self.window()
        combo = window.tag_filter_combo
        self.assertEqual(combo.itemText(0), "全部标签")
        self.assertIsNone(combo.itemData(0))
        labels = [combo.itemText(i) for i in range(1, combo.count())]
        self.assertEqual(labels, ["#工作", "#生活"])

    def test_selecting_tag_filters_the_list(self):
        work = self.add("写周报")
        self.add("买菜")
        self.tag_repo.set_tags(work.id, ["工作"])

        window = self.window()
        self.assertEqual(len(self.titles(window)), 2)

        index = window.tag_filter_combo.findData("工作")
        self.assertGreaterEqual(index, 0)
        window.tag_filter_combo.setCurrentIndex(index)

        self.assertEqual(self.titles(window), ["写周报"])

    def test_back_to_all_restores_list(self):
        work = self.add("写周报")
        self.add("买菜")
        self.tag_repo.set_tags(work.id, ["工作"])

        window = self.window()
        window.tag_filter_combo.setCurrentIndex(window.tag_filter_combo.findData("工作"))
        window.tag_filter_combo.setCurrentIndex(0)
        self.assertEqual(len(self.titles(window)), 2)

    def test_empty_result_shows_hint(self):
        """标签存在、但今天没有任务带着它 → 显示专门的提示。

        注意不能用「随便编一个标签」来测：那个标签不在下拉框里，
        ``_reload_tag_filter`` 会把筛选重置回「全部」，走不到这条分支。
        """
        from datetime import timedelta

        self.add("今天的事")
        later = self.add(
            "下周的事",
            type=TaskType.ONCE,
            due_date=self.context.today() + timedelta(days=7),
        )
        self.tag_repo.set_tags(later.id, ["工作"])  # 只挂在未来任务上

        window = self.window()
        index = window.tag_filter_combo.findData("工作")
        self.assertGreaterEqual(index, 0, "标签应出现在下拉框里")
        window.tag_filter_combo.setCurrentIndex(index)

        self.assertEqual(self.titles(window), [])
        self.assertIn("没有带该标签的任务", window.task_list.item(0).text())


class SubTaskCollapseUiTest(P2UiBase):
    def test_child_hidden_until_expanded(self):
        parent = self.add("装修")
        self.add("买涂料", parent_task_id=parent.id)

        window = self.window()
        self.assertEqual(self.titles(window), ["装修"], "默认应收起子任务")

        window._on_expand_toggled(parent.id)
        self.assertEqual(self.titles(window), ["装修", "买涂料"])

        window._on_expand_toggled(parent.id)
        self.assertEqual(self.titles(window), ["装修"], "再次点击应收起")

    def test_expand_button_only_on_parents(self):
        parent = self.add("装修")
        self.add("买涂料", parent_task_id=parent.id)

        window = self.window()
        row = window.task_list.itemWidget(window.task_list.item(0))
        self.assertFalse(row.expand_button.isHidden(), "父任务应有展开按钮")
        self.assertEqual(row.expand_button.text(), "▸")

        window._on_expand_toggled(parent.id)
        parent_row = None
        for index in range(window.task_list.count()):
            candidate = window.task_list.itemWidget(window.task_list.item(index))
            if candidate is not None and candidate._task.title == "装修":
                parent_row = candidate
        self.assertIsNotNone(parent_row)
        self.assertEqual(parent_row.expand_button.text(), "▾")

    def test_orphan_child_of_collapsed_parent_stays_visible(self):
        """父任务不在今日列表时，子任务不能被无端藏起来。"""
        self.add("单独的子任务")
        window = self.window()
        self.assertEqual(self.titles(window), ["单独的子任务"])


class DependencyUiTest(P2UiBase):
    def test_blocked_row_disables_checkbox(self):
        prereq = self.add("打地基")
        house = self.add("盖房子", priority=TaskPriority.URGENT)
        self.dependency.add_dependency(house.id, prereq.id)

        window = self.window()
        rows = {}
        for index in range(window.task_list.count()):
            row = window.task_list.itemWidget(window.task_list.item(index))
            if row is not None:
                rows[row._task.title] = row

        self.assertFalse(rows["盖房子"].checkbox.isEnabled())
        self.assertIn("前置任务未完成", rows["盖房子"].checkbox.toolTip())
        self.assertTrue(rows["打地基"].checkbox.isEnabled())
        self.assertEqual(rows["盖房子"].priority_label.text(), "紧急")
        self.assertEqual(rows["盖房子"].blocked_label.text(), "待前置")

    def test_unblocking_after_completing_prerequisite(self):
        prereq = self.add("打地基")
        house = self.add("盖房子")
        self.dependency.add_dependency(house.id, prereq.id)

        window = self.window()
        self.task_service.set_completed(prereq.id, True, self.context.today())
        window.refresh_tasks()

        for index in range(window.task_list.count()):
            row = window.task_list.itemWidget(window.task_list.item(index))
            if row is not None and row._task.title == "盖房子":
                self.assertTrue(row.checkbox.isEnabled())
                return
        self.fail("没找到「盖房子」这一行")

    def test_tags_and_progress_render(self):
        parent = self.add("装修")
        self.add("买涂料", parent_task_id=parent.id)
        self.tag_repo.set_tags(parent.id, ["工作"])

        window = self.window()
        row = window.task_list.itemWidget(window.task_list.item(0))
        self.assertEqual(row.tag_label.text(), "#工作")
        self.assertEqual(row.child_label.text(), "子任务 0/1")


if __name__ == "__main__":
    unittest.main()

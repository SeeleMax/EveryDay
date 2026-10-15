"""预设模板服务测试（文档 5.7）。"""

from __future__ import annotations

import unittest
from datetime import date, time

from tests.base import DatabaseTestCase

from everyday.models.preset import PresetTemplate
from everyday.models.task import TaskItem, TaskType
from everyday.services.preset_service import PresetService

MONDAY = date(2026, 10, 5)


class PresetServiceTest(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = PresetService(self.presets)

    def test_default_templates_come_from_migration(self):
        templates = self.service.list_all()
        self.assertGreaterEqual(len(templates), 9)
        titles = {t.title for t in templates}
        self.assertIn("晨间计划", titles)
        self.assertIn("喝水提醒", titles)

    def test_categories_are_available(self):
        self.assertTrue({"工作", "健康", "学习"}.issubset(set(self.service.categories())))

    def test_create_and_delete_template(self):
        created = self.service.create(
            PresetTemplate(title="自定义模板", type=TaskType.DAILY, remind_time=time(7, 0))
        )
        self.assertIsNotNone(created.id)
        self.assertTrue(self.service.delete(created.id))
        self.assertIsNone(self.service.get(created.id))

    def test_update_template(self):
        created = self.service.create(
            PresetTemplate(title="旧标题", type=TaskType.DAILY)
        )
        created.title = "新标题"
        created.remind_time = time(6, 30)
        self.service.update(created)

        reloaded = self.service.get(created.id)
        self.assertEqual(reloaded.title, "新标题")
        self.assertEqual(reloaded.remind_time, time(6, 30))

    def test_update_without_id_raises(self):
        with self.assertRaises(ValueError):
            self.service.update(PresetTemplate(title="无 id", type=TaskType.DAILY))

    def test_instantiate_returns_draft_without_persisting(self):
        template = self.service.list_all()[0]
        task = self.service.instantiate(template.id, day=MONDAY)

        self.assertIsNotNone(task)
        self.assertIsNone(task.id)
        self.assertEqual(task.due_date, MONDAY)
        self.assertEqual(task.title, template.title)
        # 草稿不应落库
        self.assertEqual(self.tasks.count(), 0)

    def test_instantiate_allows_title_override(self):
        template = self.service.list_all()[0]
        task = self.service.instantiate(template.id, day=MONDAY, title="改过的标题")
        self.assertEqual(task.title, "改过的标题")

    def test_instantiate_missing_template_returns_none(self):
        self.assertIsNone(self.service.instantiate(999999, day=MONDAY))

    def test_save_as_template_from_task(self):
        task = TaskItem(title="跑步", type=TaskType.DAILY, remind_time=time(20, 30))
        template = self.service.save_as_template(task, category="健康")

        self.assertIsNotNone(template.id)
        stored = self.service.get(template.id)
        self.assertEqual(stored.title, "跑步")
        self.assertIs(stored.type, TaskType.DAILY)
        self.assertEqual(stored.category, "健康")

    def test_instantiated_template_can_be_saved_as_task(self):
        """端到端：模板 -> 草稿 -> 落库，验证与任务仓储的契约。"""
        template = self.service.list_all()[0]
        draft = self.service.instantiate(template.id, day=MONDAY)
        self.tasks.add(draft)

        self.assertEqual(self.tasks.count(), 1)
        self.assertIsNotNone(self.tasks.get(draft.id))


if __name__ == "__main__":
    unittest.main()

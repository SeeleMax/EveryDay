"""预设模板服务（文档 5.7 预设任务）。

默认模板由迁移脚本 ``001_initial.sql`` 在首次建库时插入，
因此本服务不需要内置模板常量，只负责读写与实例化。
"""

from __future__ import annotations

import logging
from datetime import date

from everyday import timeutils
from everyday.db.repositories import PresetRepository
from everyday.models.preset import DEFAULT_CATEGORY, PresetTemplate
from everyday.models.task import TaskItem

logger = logging.getLogger(__name__)


class PresetService:
    """模板的增删改查与「一键创建任务」。"""

    def __init__(self, repository: PresetRepository) -> None:
        self._repo = repository

    # ------------------------------------------------------------------
    def list_all(self) -> list[PresetTemplate]:
        """返回全部模板。"""
        return self._repo.list_all()

    def categories(self) -> list[str]:
        """返回已有分类。"""
        return self._repo.categories()

    def get(self, template_id: int) -> PresetTemplate | None:
        """按主键获取模板。"""
        return self._repo.get(template_id)

    def count(self) -> int:
        """模板总数。"""
        return self._repo.count()

    # ------------------------------------------------------------------
    def create(self, template: PresetTemplate) -> PresetTemplate:
        """新增模板。"""
        self._repo.add(template)
        return template

    def update(self, template: PresetTemplate) -> PresetTemplate:
        """更新模板。"""
        if template.id is None:
            raise ValueError("更新模板需要先有 id")
        self._repo.update(template)
        return template

    def delete(self, template_id: int) -> bool:
        """删除模板（文档 5.7：模板仅作参考，用户可自由删除）。"""
        return self._repo.delete(template_id)

    # ------------------------------------------------------------------
    def instantiate(
        self, template_id: int, day: date | None = None, title: str | None = None
    ) -> TaskItem | None:
        """由模板创建任务对象（**不入库**，交由界面确认后保存）。

        文档 5.7 使用流程：选择模板后自动填充，用户可在基础上修改后再保存。
        """
        template = self._repo.get(template_id)
        if template is None:
            logger.warning("由模板创建任务失败：模板 id=%s 不存在", template_id)
            return None
        task = template.create_task(day or timeutils.today_local(), title=title)
        logger.info("由模板 %s 创建任务草稿：%r", template_id, task.title)
        return task

    def save_as_template(
        self, task: TaskItem, category: str = DEFAULT_CATEGORY
    ) -> PresetTemplate:
        """把任务「另存为模板」（文档 5.7 模板管理）。"""
        template = PresetTemplate.from_task(task, category=category)
        self._repo.add(template)
        logger.info("已把任务 %r 另存为模板", task.title)
        return template

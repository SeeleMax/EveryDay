"""数据访问层：把 SQL 集中在这里，服务层只面对实体对象。

三个仓储分别对应文档 4.1 的三个实体。仓储**不做业务判断**
（例如「哪些任务今天可见」属于 :mod:`everyday.services.task_service`），
只负责「行的读写」。
"""

from __future__ import annotations

import logging
from typing import Any

from everyday import timeutils
from everyday.db.connection import Database
from everyday.models.preset import PresetTemplate
from everyday.models.settings import AppSettings
from everyday.models.task import TaskItem

logger = logging.getLogger(__name__)

_TASK_COLUMNS = (
    "id, title, description, type, due_date, remind_time, "
    "is_completed, last_completed_date, created_at"
)


class TaskRepository:
    """``task_item`` 表的读写。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def add(self, task: TaskItem) -> int:
        """插入任务，返回新主键；同时回写到 ``task.id``。"""
        row = task.to_row()
        new_id = self._db.execute(
            """
            INSERT INTO task_item
                (title, description, type, due_date, remind_time,
                 is_completed, last_completed_date, created_at)
            VALUES
                (:title, :description, :type, :due_date, :remind_time,
                 :is_completed, :last_completed_date, :created_at)
            """,
            row,
        )
        task.id = new_id
        logger.info("新增任务 id=%s title=%r type=%s", new_id, task.title, task.type.value)
        return new_id

    def update(self, task: TaskItem) -> bool:
        """按主键整行更新，返回是否有行受影响。"""
        if task.id is None:
            raise ValueError("更新任务前必须先落库（id 为空）")
        row = task.to_row()
        row["id"] = task.id
        with self._db.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE task_item SET
                    title               = :title,
                    description         = :description,
                    type                = :type,
                    due_date            = :due_date,
                    remind_time         = :remind_time,
                    is_completed        = :is_completed,
                    last_completed_date = :last_completed_date,
                    created_at          = :created_at
                WHERE id = :id
                """,
                row,
            )
        changed = cursor.rowcount > 0
        if changed:
            logger.info("更新任务 id=%s title=%r", task.id, task.title)
        else:
            logger.warning("更新任务失败：id=%s 不存在", task.id)
        return changed

    def delete(self, task_id: int) -> bool:
        """删除任务，返回是否删除成功（文档 5.3 删除任务）。"""
        with self._db.transaction() as connection:
            cursor = connection.execute("DELETE FROM task_item WHERE id = ?", (task_id,))
        deleted = cursor.rowcount > 0
        logger.info("删除任务 id=%s 结果=%s", task_id, "成功" if deleted else "未找到")
        return deleted

    def get(self, task_id: int) -> TaskItem | None:
        """按主键查询。"""
        row = self._db.query_one(
            f"SELECT {_TASK_COLUMNS} FROM task_item WHERE id = ?", (task_id,)
        )
        return TaskItem.from_row(row) if row is not None else None

    def list_all(self) -> list[TaskItem]:
        """返回全部任务。"""
        rows = self._db.query(f"SELECT {_TASK_COLUMNS} FROM task_item")
        return [TaskItem.from_row(row) for row in rows]

    def count(self) -> int:
        """任务总数。"""
        return int(self._db.query_scalar("SELECT COUNT(*) FROM task_item", default=0))

    def update_completion(self, task_id: int, is_completed: bool, day) -> bool:
        """只更新完成状态与完成日期（勾选框高频路径，避免整行写）。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE task_item SET is_completed = ?, last_completed_date = ? WHERE id = ?",
                (
                    1 if is_completed else 0,
                    timeutils.format_date(day) if is_completed else None,
                    task_id,
                ),
            )
        return cursor.rowcount > 0


class PresetRepository:
    """``preset_template`` 表的读写（文档 5.7 预设任务）。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def add(self, template: PresetTemplate) -> int:
        """新增模板。"""
        new_id = self._db.execute(
            """
            INSERT INTO preset_template (title, type, remind_time, category)
            VALUES (:title, :type, :remind_time, :category)
            """,
            template.to_row(),
        )
        template.id = new_id
        logger.info("新增模板 id=%s title=%r", new_id, template.title)
        return new_id

    def update(self, template: PresetTemplate) -> bool:
        """更新模板。"""
        if template.id is None:
            raise ValueError("更新模板前必须先落库（id 为空）")
        row = template.to_row()
        row["id"] = template.id
        with self._db.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE preset_template SET
                    title = :title, type = :type,
                    remind_time = :remind_time, category = :category
                WHERE id = :id
                """,
                row,
            )
        return cursor.rowcount > 0

    def delete(self, template_id: int) -> bool:
        """删除模板。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM preset_template WHERE id = ?", (template_id,)
            )
        return cursor.rowcount > 0

    def get(self, template_id: int) -> PresetTemplate | None:
        """按主键查询模板。"""
        row = self._db.query_one(
            "SELECT id, title, type, remind_time, category FROM preset_template WHERE id = ?",
            (template_id,),
        )
        return PresetTemplate.from_row(row) if row is not None else None

    def list_all(self) -> list[PresetTemplate]:
        """返回全部模板，按分类、标题排序。"""
        rows = self._db.query(
            "SELECT id, title, type, remind_time, category FROM preset_template "
            "ORDER BY category, title"
        )
        return [PresetTemplate.from_row(row) for row in rows]

    def categories(self) -> list[str]:
        """返回已有分类列表。"""
        rows = self._db.query(
            "SELECT DISTINCT category FROM preset_template ORDER BY category"
        )
        return [row["category"] for row in rows]

    def count(self) -> int:
        """模板总数。"""
        return int(self._db.query_scalar("SELECT COUNT(*) FROM preset_template", default=0))


class SettingsRepository:
    """``app_settings`` 键值表的读写。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def load(self) -> AppSettings:
        """读取全部设置；缺失项使用默认值。"""
        rows = self._db.query("SELECT key, value FROM app_settings")
        return AppSettings.from_kv({row["key"]: row["value"] for row in rows})

    def save(self, settings: AppSettings) -> None:
        """整份写入设置（UPSERT）。"""
        payload: list[tuple[str, str]] = list(settings.to_kv().items())
        self._db.execute_many(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            payload,
        )
        logger.debug("设置已保存，共 %d 项", len(payload))

    def get_raw(self, key: str, default: Any = None) -> Any:
        """读取单个原始值。"""
        row = self._db.query_one("SELECT value FROM app_settings WHERE key = ?", (key,))
        return row["value"] if row is not None else default

    def set_raw(self, key: str, value: str) -> None:
        """写入单个原始值。"""
        self._db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

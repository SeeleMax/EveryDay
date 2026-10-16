"""数据访问层：把 SQL 集中在这里，服务层只面对实体对象。

三个仓储分别对应文档 4.1 的三个实体。仓储**不做业务判断**
（例如「哪些任务今天可见」属于 :mod:`everyday.services.task_service`），
只负责「行的读写」。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from everyday import timeutils
from everyday.db.connection import Database
from everyday.models.focus import FocusRecord
from everyday.models.history import HistoryAction, HistoryEntry
from everyday.models.preset import PresetTemplate
from everyday.models.reminder import ReminderLog
from everyday.models.screen_time import ScreenTimeRecord
from everyday.models.settings import AppSettings
from everyday.models.task import TaskItem

logger = logging.getLogger(__name__)

_TASK_COLUMNS = (
    "id, title, description, type, due_date, remind_time, "
    "is_completed, last_completed_date, created_at, "
    "priority, parent_task_id, deleted_at"
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
                 is_completed, last_completed_date, created_at,
                 priority, parent_task_id, deleted_at)
            VALUES
                (:title, :description, :type, :due_date, :remind_time,
                 :is_completed, :last_completed_date, :created_at,
                 :priority, :parent_task_id, :deleted_at)
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
                    created_at          = :created_at,
                    priority            = :priority,
                    parent_task_id      = :parent_task_id,
                    deleted_at          = :deleted_at
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

    def list_all(self, include_deleted: bool = False) -> list[TaskItem]:
        """返回任务列表。

        :param include_deleted: 是否包含回收站里（已软删除）的任务。
            默认排除——绝大多数调用方只想看到「活着的」任务。
        """
        sql = f"SELECT {_TASK_COLUMNS} FROM task_item"
        if not include_deleted:
            sql += " WHERE deleted_at IS NULL"
        rows = self._db.query(sql)
        return [TaskItem.from_row(row) for row in rows]

    def list_children(self, parent_id: int, include_deleted: bool = False) -> list[TaskItem]:
        """返回某个任务的直接子任务（v1.1 P2 子任务）。"""
        sql = f"SELECT {_TASK_COLUMNS} FROM task_item WHERE parent_task_id = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        rows = self._db.query(sql, (parent_id,))
        return [TaskItem.from_row(row) for row in rows]

    def count(self, include_deleted: bool = False) -> int:
        """任务总数。"""
        sql = "SELECT COUNT(*) FROM task_item"
        if not include_deleted:
            sql += " WHERE deleted_at IS NULL"
        return int(self._db.query_scalar(sql, default=0))

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

    # ------------------------------------------------------------------
    # 软删除 / 回收站（v1.1 P4）
    # ------------------------------------------------------------------
    def soft_delete(self, task_id: int, moment: datetime | None = None) -> bool:
        """把任务移入回收站（打删除时间戳，不真的删行）。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE task_item SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                (timeutils.format_datetime(moment or timeutils.now_local()), task_id),
            )
        moved = cursor.rowcount > 0
        logger.info("任务 %s 移入回收站：%s", task_id, "成功" if moved else "未找到或已在回收站")
        return moved

    def restore(self, task_id: int) -> bool:
        """从回收站恢复（5 秒撤销也走这里）。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE task_item SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
                (task_id,),
            )
        restored = cursor.rowcount > 0
        logger.info("任务 %s 从回收站恢复：%s", task_id, "成功" if restored else "未在回收站")
        return restored

    def list_deleted(self, day: date | None = None) -> list[TaskItem]:
        """返回回收站内容；给了 ``day`` 则只返回那一天删除的。"""
        sql = f"SELECT {_TASK_COLUMNS} FROM task_item WHERE deleted_at IS NOT NULL"
        params: tuple = ()
        if day is not None:
            sql += " AND substr(deleted_at, 1, 10) = ?"
            params = (timeutils.format_date(day),)
        sql += " ORDER BY deleted_at DESC"
        rows = self._db.query(sql, params)
        return [TaskItem.from_row(row) for row in rows]

    def purge_deleted(self, before: date) -> int:
        """彻底删除 ``before``（含）之前移入回收站的任务（次日清理）。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM task_item WHERE deleted_at IS NOT NULL "
                "AND substr(deleted_at, 1, 10) <= ?",
                (timeutils.format_date(before),),
            )
        removed = int(cursor.rowcount or 0)
        if removed:
            logger.info("回收站清理：彻底删除 %d 个任务（%s 及之前）", removed, before)
        return removed


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


# ======================================================================
# v1.1 新增仓储
# ======================================================================
class TagRepository:
    """``task_tag`` 表的读写（v1.1 P2 标签）。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def add(self, task_id: int, tag: str) -> bool:
        """给任务打一个标签；已存在时不报错（幂等）。"""
        name = (tag or "").strip()
        if not name:
            return False
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO task_tag (task_id, tag) VALUES (?, ?)",
                (task_id, name),
            )
        return cursor.rowcount > 0

    def remove(self, task_id: int, tag: str) -> bool:
        """摘掉一个标签。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM task_tag WHERE task_id = ? AND tag = ?",
                (task_id, (tag or "").strip()),
            )
        return cursor.rowcount > 0

    def set_tags(self, task_id: int, tags: list[str]) -> None:
        """整份替换某个任务的标签（编辑对话框保存时用）。"""
        cleaned: list[str] = []
        for tag in tags:
            name = (tag or "").strip()
            if name and name not in cleaned:
                cleaned.append(name)
        with self._db.transaction() as connection:
            connection.execute("DELETE FROM task_tag WHERE task_id = ?", (task_id,))
            connection.executemany(
                "INSERT OR IGNORE INTO task_tag (task_id, tag) VALUES (?, ?)",
                [(task_id, name) for name in cleaned],
            )

    def tags_for(self, task_id: int) -> list[str]:
        """返回某个任务的标签（按字母序，便于界面稳定展示）。"""
        rows = self._db.query(
            "SELECT tag FROM task_tag WHERE task_id = ? ORDER BY tag", (task_id,)
        )
        return [row["tag"] for row in rows]

    def tags_for_many(self, task_ids: list[int]) -> dict[int, list[str]]:
        """批量取标签，避免列表刷新时逐行查询（N+1）。"""
        if not task_ids:
            return {}
        placeholders = ", ".join("?" for _ in task_ids)
        rows = self._db.query(
            f"SELECT task_id, tag FROM task_tag WHERE task_id IN ({placeholders}) "
            "ORDER BY task_id, tag",
            tuple(task_ids),
        )
        result: dict[int, list[str]] = {}
        for row in rows:
            result.setdefault(row["task_id"], []).append(row["tag"])
        return result

    def all_tags(self) -> list[str]:
        """返回所有出现过的标签。"""
        rows = self._db.query("SELECT DISTINCT tag FROM task_tag ORDER BY tag")
        return [row["tag"] for row in rows]

    def task_ids_with_tag(self, tag: str) -> list[int]:
        """返回带某个标签的任务 id。"""
        rows = self._db.query(
            "SELECT task_id FROM task_tag WHERE tag = ? ORDER BY task_id",
            ((tag or "").strip(),),
        )
        return [row["task_id"] for row in rows]

    def count_for_tag(self, tag: str) -> int:
        """某个标签被用了多少次。"""
        return int(
            self._db.query_scalar(
                "SELECT COUNT(*) FROM task_tag WHERE tag = ?",
                ((tag or "").strip(),),
                default=0,
            )
        )


class DependencyRepository:
    """``task_dependency`` 表的读写（v1.1 P2 前置依赖）。

    只负责「边」的存取；**环检测与拓扑排序属于服务层**
    （见 ``services/dependency_service.py``），仓储不做图算法。
    """

    def __init__(self, database: Database) -> None:
        self._db = database

    def add(self, task_id: int, depends_on_id: int) -> bool:
        """添加一条依赖；自指与重复都会被拒绝。"""
        if task_id == depends_on_id:
            logger.warning("拒绝自指依赖：任务 %s", task_id)
            return False
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO task_dependency (task_id, depends_on_id) "
                "VALUES (?, ?)",
                (task_id, depends_on_id),
            )
        return cursor.rowcount > 0

    def remove(self, task_id: int, depends_on_id: int) -> bool:
        """删除一条依赖。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM task_dependency WHERE task_id = ? AND depends_on_id = ?",
                (task_id, depends_on_id),
            )
        return cursor.rowcount > 0

    def set_dependencies(self, task_id: int, depends_on_ids: list[int]) -> None:
        """整份替换某个任务的前置依赖。"""
        unique = [i for i in dict.fromkeys(depends_on_ids) if i != task_id]
        with self._db.transaction() as connection:
            connection.execute(
                "DELETE FROM task_dependency WHERE task_id = ?", (task_id,)
            )
            connection.executemany(
                "INSERT OR IGNORE INTO task_dependency (task_id, depends_on_id) "
                "VALUES (?, ?)",
                [(task_id, dep) for dep in unique],
            )

    def dependencies_of(self, task_id: int) -> list[int]:
        """任务的前置（它依赖谁）。"""
        rows = self._db.query(
            "SELECT depends_on_id FROM task_dependency WHERE task_id = ? "
            "ORDER BY depends_on_id",
            (task_id,),
        )
        return [row["depends_on_id"] for row in rows]

    def dependents_of(self, task_id: int) -> list[int]:
        """任务的后置（谁依赖它）。"""
        rows = self._db.query(
            "SELECT task_id FROM task_dependency WHERE depends_on_id = ? ORDER BY task_id",
            (task_id,),
        )
        return [row["task_id"] for row in rows]

    def list_all(self) -> list[tuple[int, int]]:
        """返回全部依赖边 ``(task_id, depends_on_id)``，供拓扑排序使用。"""
        rows = self._db.query(
            "SELECT task_id, depends_on_id FROM task_dependency "
            "ORDER BY task_id, depends_on_id"
        )
        return [(row["task_id"], row["depends_on_id"]) for row in rows]

    def clear_for_task(self, task_id: int) -> int:
        """清掉与某个任务相关的所有边（它依赖别人 + 别人依赖它）。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM task_dependency WHERE task_id = ? OR depends_on_id = ?",
                (task_id, task_id),
            )
        return int(cursor.rowcount or 0)


class HistoryRepository:
    """``task_history`` 表的读写（v1.1 P4 历史统计）。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def add(self, entry: HistoryEntry) -> int:
        """写入一条历史。"""
        new_id = self._db.execute(
            """
            INSERT INTO task_history (task_id, action, occurred_at, detail)
            VALUES (:task_id, :action, :occurred_at, :detail)
            """,
            entry.to_row(),
        )
        entry.id = new_id
        return new_id

    def record(
        self,
        task_id: int,
        action: HistoryAction,
        detail: str | None = None,
        moment: datetime | None = None,
    ) -> int:
        """便捷方法：直接按字段写一条历史。"""
        return self.add(
            HistoryEntry(
                task_id=task_id, action=action, detail=detail, occurred_at=moment
            )
        )

    def list_for_task(self, task_id: int, limit: int = 50) -> list[HistoryEntry]:
        """某个任务的历史（新的在前）。"""
        rows = self._db.query(
            "SELECT id, task_id, action, occurred_at, detail FROM task_history "
            "WHERE task_id = ? ORDER BY occurred_at DESC, id DESC LIMIT ?",
            (task_id, max(1, int(limit))),
        )
        return [HistoryEntry.from_row(row) for row in rows]

    def list_between(self, start: date, end: date) -> list[HistoryEntry]:
        """时间段内的历史（含首尾）。"""
        rows = self._db.query(
            "SELECT id, task_id, action, occurred_at, detail FROM task_history "
            "WHERE substr(occurred_at, 1, 10) BETWEEN ? AND ? "
            "ORDER BY occurred_at, id",
            (timeutils.format_date(start), timeutils.format_date(end)),
        )
        return [HistoryEntry.from_row(row) for row in rows]

    def count_actions_on(self, day: date, action: HistoryAction) -> int:
        """某一天某种动作发生了多少次。"""
        return int(
            self._db.query_scalar(
                "SELECT COUNT(*) FROM task_history "
                "WHERE substr(occurred_at, 1, 10) = ? AND action = ?",
                (timeutils.format_date(day), HistoryAction.from_value(action).value),
                default=0,
            )
        )

    def completion_counts(self, start: date, end: date) -> dict[date, int]:
        """按天统计「完成」次数，直接喂给热力图（v1.1 P4）。

        一次查询搞定整段区间，避免按天循环查 84 次（12 周 × 7 天）。
        """
        rows = self._db.query(
            "SELECT substr(occurred_at, 1, 10) AS day, COUNT(*) AS total "
            "FROM task_history WHERE action = ? AND substr(occurred_at, 1, 10) "
            "BETWEEN ? AND ? GROUP BY day ORDER BY day",
            (
                HistoryAction.COMPLETED.value,
                timeutils.format_date(start),
                timeutils.format_date(end),
            ),
        )
        counts: dict[date, int] = {}
        for row in rows:
            parsed = timeutils.parse_date(row["day"])
            if parsed is not None:
                counts[parsed] = int(row["total"])
        return counts

    def distinct_completion_days(self, start: date, end: date) -> list[date]:
        """区间内「有完成任务」的日期列表（连续打卡统计用）。"""
        return sorted(self.completion_counts(start, end).keys())

    def delete_for_task(self, task_id: int) -> int:
        """删除某个任务的历史（仅用于彻底清理场景）。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM task_history WHERE task_id = ?", (task_id,)
            )
        return int(cursor.rowcount or 0)


class FocusRepository:
    """``focus_record`` 表的读写（v1.1 P3 番茄钟 / P4 专注时长）。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def add(self, record: FocusRecord) -> int:
        """写入一条专注记录。"""
        row = record.to_row()
        new_id = self._db.execute(
            """
            INSERT INTO focus_record
                (task_id, started_at, ended_at, planned_minutes,
                 actual_seconds, completed)
            VALUES
                (:task_id, :started_at, :ended_at, :planned_minutes,
                 :actual_seconds, :completed)
            """,
            row,
        )
        record.id = new_id
        logger.info(
            "专注记录 id=%s 任务=%s 计划=%d 分钟 实际=%.1f 分钟 完成=%s",
            new_id,
            record.task_id,
            record.planned_minutes,
            record.actual_minutes,
            record.completed,
        )
        return new_id

    def update(self, record: FocusRecord) -> bool:
        """更新一条专注记录（番茄钟结束时回写实际时长）。"""
        if record.id is None:
            raise ValueError("更新专注记录前必须先落库（id 为空）")
        row = record.to_row()
        row["id"] = record.id
        with self._db.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE focus_record SET
                    task_id         = :task_id,
                    started_at      = :started_at,
                    ended_at        = :ended_at,
                    planned_minutes = :planned_minutes,
                    actual_seconds  = :actual_seconds,
                    completed       = :completed
                WHERE id = :id
                """,
                row,
            )
        return cursor.rowcount > 0

    def get(self, record_id: int) -> FocusRecord | None:
        """按主键取一条专注记录。"""
        row = self._db.query_one(
            "SELECT id, task_id, started_at, ended_at, planned_minutes, "
            "actual_seconds, completed FROM focus_record WHERE id = ?",
            (record_id,),
        )
        return FocusRecord.from_row(row) if row is not None else None

    def list_between(self, start: date, end: date) -> list[FocusRecord]:
        """时间段内的专注记录（含首尾）。"""
        rows = self._db.query(
            "SELECT id, task_id, started_at, ended_at, planned_minutes, "
            "actual_seconds, completed FROM focus_record "
            "WHERE substr(started_at, 1, 10) BETWEEN ? AND ? "
            "ORDER BY started_at",
            (timeutils.format_date(start), timeutils.format_date(end)),
        )
        return [FocusRecord.from_row(row) for row in rows]

    def total_seconds_on(self, day: date) -> int:
        """某一天的总专注秒数。"""
        return int(
            self._db.query_scalar(
                "SELECT COALESCE(SUM(actual_seconds), 0) FROM focus_record "
                "WHERE substr(started_at, 1, 10) = ?",
                (timeutils.format_date(day),),
                default=0,
            )
        )

    def total_seconds_between(self, start: date, end: date) -> int:
        """时间段内的总专注秒数。"""
        return int(
            self._db.query_scalar(
                "SELECT COALESCE(SUM(actual_seconds), 0) FROM focus_record "
                "WHERE substr(started_at, 1, 10) BETWEEN ? AND ?",
                (timeutils.format_date(start), timeutils.format_date(end)),
                default=0,
            )
        )

    def daily_totals(self, start: date, end: date) -> dict[date, int]:
        """按天汇总专注秒数（一次查询，供统计面板画柱状图）。"""
        rows = self._db.query(
            "SELECT substr(started_at, 1, 10) AS day, "
            "COALESCE(SUM(actual_seconds), 0) AS total FROM focus_record "
            "WHERE substr(started_at, 1, 10) BETWEEN ? AND ? GROUP BY day",
            (timeutils.format_date(start), timeutils.format_date(end)),
        )
        result: dict[date, int] = {}
        for row in rows:
            parsed = timeutils.parse_date(row["day"])
            if parsed is not None:
                result[parsed] = int(row["total"])
        return result

    def count_on(self, day: date) -> int:
        """某一天开了几个番茄钟。"""
        return int(
            self._db.query_scalar(
                "SELECT COUNT(*) FROM focus_record WHERE substr(started_at, 1, 10) = ?",
                (timeutils.format_date(day),),
                default=0,
            )
        )


class ScreenTimeRepository:
    """``screen_time_record`` 表的读写（v1.1 P5 屏幕时间）。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def add_seconds(self, day: date, app_name: str, seconds: int) -> None:
        """把一个采样区间的秒数累加到「日期 + 应用」上。

        用 UPSERT 累加而不是插入新行：P5 每 2 秒采样一次，
        按样本存储一天就是四万行。
        """
        record = ScreenTimeRecord(day=day, app_name=app_name, seconds=seconds)
        self._db.execute(
            """
            INSERT INTO screen_time_record (day, app_name, seconds)
            VALUES (:day, :app_name, :seconds)
            ON CONFLICT(day, app_name)
            DO UPDATE SET seconds = seconds + excluded.seconds
            """,
            record.to_row(),
        )

    def list_for_day(self, day: date) -> list[ScreenTimeRecord]:
        """某一天各应用的使用时长（从多到少）。"""
        rows = self._db.query(
            "SELECT day, app_name, seconds FROM screen_time_record "
            "WHERE day = ? ORDER BY seconds DESC, app_name",
            (timeutils.format_date(day),),
        )
        return [ScreenTimeRecord.from_row(row) for row in rows]

    def top_apps(self, day: date, limit: int = 10) -> list[ScreenTimeRecord]:
        """某一天使用时长排行前 N 的应用（统计面板用）。"""
        return self.list_for_day(day)[: max(1, int(limit))]

    def total_seconds_on(self, day: date) -> int:
        """某一天的总屏幕时间秒数。"""
        return int(
            self._db.query_scalar(
                "SELECT COALESCE(SUM(seconds), 0) FROM screen_time_record WHERE day = ?",
                (timeutils.format_date(day),),
                default=0,
            )
        )

    def distinct_days(self) -> list[date]:
        """有记录的所有日期（从早到晚）。"""
        rows = self._db.query("SELECT DISTINCT day FROM screen_time_record ORDER BY day")
        days = [timeutils.parse_date(row["day"]) for row in rows]
        return [d for d in days if d is not None]

    def delete_day(self, day: date) -> int:
        """删掉某一天的记录。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM screen_time_record WHERE day = ?",
                (timeutils.format_date(day),),
            )
        return int(cursor.rowcount or 0)


class ReminderRepository:
    """``reminder_log`` 表的读写（v1.1 P3 通知调度）。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def get(self, task_id: int, day: date) -> ReminderLog | None:
        """取某个任务在某一天的提醒状态。"""
        row = self._db.query_one(
            "SELECT task_id, remind_date, notified_at, snooze_until "
            "FROM reminder_log WHERE task_id = ? AND remind_date = ?",
            (task_id, timeutils.format_date(day)),
        )
        return ReminderLog.from_row(row) if row is not None else None

    def get_or_create(self, task_id: int, day: date) -> ReminderLog:
        """取提醒状态，不存在则创建一条空记录。"""
        existing = self.get(task_id, day)
        if existing is not None:
            return existing
        log = ReminderLog(task_id=task_id, remind_date=day)
        self.upsert(log)
        return log

    def upsert(self, log: ReminderLog) -> None:
        """写入或更新提醒状态。"""
        self._db.execute(
            """
            INSERT INTO reminder_log (task_id, remind_date, notified_at, snooze_until)
            VALUES (:task_id, :remind_date, :notified_at, :snooze_until)
            ON CONFLICT(task_id, remind_date) DO UPDATE SET
                notified_at  = excluded.notified_at,
                snooze_until = excluded.snooze_until
            """,
            log.to_row(),
        )

    def mark_notified(self, task_id: int, day: date, moment: datetime | None = None) -> None:
        """标记为已通知，并清掉 snooze。"""
        self.upsert(self.get_or_create(task_id, day).notified(moment))

    def snooze(
        self,
        task_id: int,
        day: date,
        minutes: int,
        moment: datetime | None = None,
    ) -> ReminderLog:
        """推迟提醒，返回更新后的状态。"""
        log = self.get_or_create(task_id, day).snoozed(minutes, moment)
        self.upsert(log)
        return log

    def list_for_day(self, day: date) -> list[ReminderLog]:
        """某一天的全部提醒状态。"""
        rows = self._db.query(
            "SELECT task_id, remind_date, notified_at, snooze_until "
            "FROM reminder_log WHERE remind_date = ? ORDER BY task_id",
            (timeutils.format_date(day),),
        )
        return [ReminderLog.from_row(row) for row in rows]

    def delete_for_task(self, task_id: int) -> int:
        """清掉某个任务的提醒记录。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM reminder_log WHERE task_id = ?", (task_id,)
            )
        return int(cursor.rowcount or 0)

    def purge_before(self, day: date) -> int:
        """删除 ``day`` 之前的提醒记录（历史提醒没有保留价值）。"""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM reminder_log WHERE remind_date < ?",
                (timeutils.format_date(day),),
            )
        return int(cursor.rowcount or 0)


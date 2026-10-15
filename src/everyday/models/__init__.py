"""实体模型层。

只包含纯数据与纯函数式业务规则，不依赖数据库与 UI，
因此可以在没有 PySide6、没有数据库文件的环境下直接单元测试。
"""

from __future__ import annotations

from everyday.models.preset import PresetTemplate
from everyday.models.settings import AppSettings
from everyday.models.task import TaskItem, TaskType

__all__ = ["TaskItem", "TaskType", "PresetTemplate", "AppSettings"]

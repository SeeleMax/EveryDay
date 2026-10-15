"""EveryDay —— 每日计划软件。

分层结构（依赖方向自上而下，服务层不依赖表示层）：

    everyday.ui          表示层：PySide6 窗口、对话框、托盘
    everyday.services    服务层：任务、日历、自启、预设、备份
    everyday.db          数据层：SQLite 连接、迁移、仓储
    everyday.models      实体：TaskItem / PresetTemplate / AppSettings

除 ``everyday.ui`` 外，其余模块只依赖 Python 标准库。
"""

from __future__ import annotations

__all__ = ["__version__", "APP_NAME", "APP_DISPLAY_NAME"]

__version__ = "1.0.0"

#: 用于文件系统路径、注册表键名、数据库文件名（不含空格，避免转义问题）
APP_NAME = "EveryDay"

#: 用于窗口标题、托盘提示等面向用户的展示名
APP_DISPLAY_NAME = "EveryDay 每日计划"

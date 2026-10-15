"""表示层：PySide6 窗口、对话框与系统托盘。

这是唯一允许依赖 Qt 的包。其余各层（models / db / services）都只使用
Python 标准库，因此可以在没有安装 PySide6 的环境下运行单元测试。
"""

from __future__ import annotations

__all__ = ["icons", "styles"]

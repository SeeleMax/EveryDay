"""路径解析。

**唯一的路径真相来源。** 所有模块必须通过本模块取路径，
禁止在别处拼接 ``os.path.join`` 或硬编码目录名。

目录布局（文档第八章：数据库与应用安装目录分离）：

    %LOCALAPPDATA%\\EveryDay\\                 用户数据根目录
        everyday.db                             SQLite 数据库
        appsettings.json                        运行期配置
        backups\\                               数据库备份（保留 5 份）
        logs\\                                  按日滚动日志（保留 7 天）

仓库内目录：

    <repo>\\migrations\\          迁移脚本
    <repo>\\config\\              配置模板
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from everyday import APP_NAME

#: 运行期数据目录的环境变量覆盖名（便于自动化测试与便携模式）
DATA_DIR_ENV = "EVERYDAY_DATA_DIR"

_DB_FILENAME = "everyday.db"
_CONFIG_FILENAME = "appsettings.json"


def project_root() -> Path:
    """返回仓库根目录（含 ``migrations`` / ``config`` 的那一层）。

    兼容三种运行形态：源码运行（``main.py`` 在根目录）、
    ``pip install -e .`` 后的包导入、以及被 PyInstaller 打包后的可执行文件。
    """
    if getattr(sys, "frozen", False):  # PyInstaller 等打包产物
        return Path(sys.executable).resolve().parent
    # __file__ = <repo>/src/everyday/paths.py -> parents[2] = <repo>
    return Path(__file__).resolve().parents[2]


def resolve_data_dir(override: str | os.PathLike[str] | None = None) -> Path:
    """返回用户数据根目录路径，**不创建目录、不产生副作用**。

    优先级：显式参数 > 环境变量 > ``%LOCALAPPDATA%\\EveryDay`` > ``~/.everyday``。
    最后一级兜底是为了让非 Windows 环境下的单元测试也能运行。

    单独拆出这个函数，是为了让调用方在目录创建失败时能拿到**目标路径**
    去给出可读的错误提示——否则 :func:`data_dir` 抛出的异常里只有系统错误码。
    """
    if override is not None:
        return Path(override).expanduser()

    from_env = os.environ.get(DATA_DIR_ENV)
    if from_env:
        return Path(from_env).expanduser()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def data_dir(override: str | os.PathLike[str] | None = None) -> Path:
    """返回用户数据根目录，并确保它存在。

    :raises OSError: 目录无法创建（权限、只读介质、磁盘满等）。
        调用方应当捕获它并给出面向用户的提示，而不是直接崩掉。
    """
    path = resolve_data_dir(override)
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path(root: Path | None = None) -> Path:
    """返回 SQLite 数据库文件路径。"""
    return (root or data_dir()) / _DB_FILENAME


def config_path(root: Path | None = None) -> Path:
    """返回运行期配置文件路径。"""
    return (root or data_dir()) / _CONFIG_FILENAME


def backups_dir(root: Path | None = None) -> Path:
    """返回数据库备份目录，并确保它存在。"""
    path = (root or data_dir()) / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir(root: Path | None = None) -> Path:
    """返回日志目录，并确保它存在。"""
    path = (root or data_dir()) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def migrations_dir() -> Path:
    """返回迁移脚本目录。

    优先使用仓库内的 ``migrations/``；若不存在（例如已安装为 wheel），
    则回落到随包分发的 ``everyday/migrations/`` 副本。
    """
    candidates = [
        project_root() / "migrations",
        Path(__file__).resolve().parent / "migrations",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def config_template_path() -> Path:
    """返回仓库内的配置模板路径（``config/appsettings.example.json``）。"""
    return project_root() / "config" / "appsettings.example.json"

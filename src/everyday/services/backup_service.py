"""数据库备份服务（文档第八章：启动时备份，保留最近五份）。

**关键实现细节**：数据库启用了 WAL 模式，最新的写入可能还在 ``-wal``
文件里。直接用 :func:`shutil.copy2` 复制 ``everyday.db`` 会得到一个
「缺少最近若干次提交」的备份。因此这里使用 SQLite 官方的在线备份 API
（:meth:`sqlite3.Connection.backup`），它保证拿到一个一致的快照。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path

from everyday import timeutils
from everyday.db.connection import Database
from everyday.paths import backups_dir as default_backups_dir

logger = logging.getLogger(__name__)

#: 备份文件名前缀，与数据库主文件名保持一致
BACKUP_PREFIX = "everyday-"

#: 备份文件后缀
BACKUP_SUFFIX = ".db"

#: 保留份数（文档第八章：保留最近五份）
DEFAULT_KEEP = 5


def backup_filename(day: date) -> str:
    """返回某一天的备份文件名，例如 ``everyday-2026-10-05.db``。"""
    return f"{BACKUP_PREFIX}{timeutils.format_date(day)}{BACKUP_SUFFIX}"


def list_backups(directory: Path | None = None) -> list[Path]:
    """列出备份文件，按文件名（即日期）升序。"""
    target = directory or default_backups_dir()
    if not target.is_dir():
        return []
    return sorted(
        (
            entry
            for entry in target.iterdir()
            if entry.is_file()
            and entry.name.startswith(BACKUP_PREFIX)
            and entry.suffix == BACKUP_SUFFIX
        ),
        key=lambda p: p.name,
    )


def prune(directory: Path | None = None, keep: int = DEFAULT_KEEP) -> list[Path]:
    """把备份数量裁剪到 ``keep`` 份，返回被删除的文件列表。"""
    backups = list_backups(directory)
    if len(backups) <= keep:
        return []
    removed: list[Path] = []
    for stale in backups[: len(backups) - keep]:
        try:
            stale.unlink()
            removed.append(stale)
        except OSError as exc:
            logger.warning("删除过期备份失败 %s：%s", stale, exc)
    if removed:
        logger.info("已清理 %d 个过期备份", len(removed))
    return removed


def create_backup(
    source: Database | str | Path,
    directory: Path | None = None,
    day: date | None = None,
    keep: int = DEFAULT_KEEP,
) -> Path | None:
    """创建一次数据库备份。

    :param source: 已打开的 :class:`Database`，或数据库文件路径
    :param directory: 备份目录，默认为 ``%LOCALAPPDATA%\\EveryDay\\backups``
    :param day: 备份日期，默认今天；同一天重复备份会覆盖同名文件
    :param keep: 保留份数
    :return: 备份文件路径；源数据库不存在或为空时返回 ``None``
    """
    target_dir = directory or default_backups_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / backup_filename(day or timeutils.today_local())

    source_database: Database | None = None
    if isinstance(source, Database):
        source_database = source
        # 首次运行时数据库文件尚不存在。注意此时**不能**先 connect()：
        # sqlite3.connect 会顺手创建一个空库文件，于是我们会备份出一个
        # 毫无意义的空数据库，还会占掉当天的备份名额。
        if not source.is_memory and not Path(source.path).is_file():
            logger.info("源数据库尚未创建，跳过备份：%s", source.path)
            return None
        source_connection = source.connect()
    else:
        source_path = Path(source)
        if not source_path.is_file() or source_path.stat().st_size == 0:
            logger.info("源数据库不存在或为空，跳过备份：%s", source_path)
            return None
        source_connection = sqlite3.connect(str(source_path))

    try:
        # 覆盖同一天的旧备份：目标必须先删除，backup() 要求目标为空库
        if target_path.exists():
            target_path.unlink()
        destination_connection = sqlite3.connect(str(target_path))
        try:
            source_connection.backup(destination_connection)
        finally:
            # 必须显式关闭：在 Windows 上未关闭的连接会锁住备份文件，
            # 导致后续的 prune() 删除失败。
            destination_connection.close()
    except sqlite3.Error as exc:
        logger.error("数据库备份失败：%s", exc)
        return None
    finally:
        if source_database is None:
            source_connection.close()

    logger.info("数据库已备份到 %s", target_path)
    prune(target_dir, keep=keep)
    return target_path


class BackupService:
    """面向应用的备份门面。"""

    def __init__(self, directory: Path | None = None, keep: int = DEFAULT_KEEP) -> None:
        self._directory = directory
        self._keep = keep

    @property
    def directory(self) -> Path:
        """备份目录。"""
        return self._directory or default_backups_dir()

    @property
    def keep(self) -> int:
        """保留份数。"""
        return self._keep

    def run_startup_backup(self, source: Database | str | Path) -> Path | None:
        """执行一次启动备份（文档第八章）。"""
        return create_backup(
            source, directory=self.directory, keep=self._keep
        )

    def available(self) -> list[Path]:
        """列出当前可用的备份。"""
        return list_backups(self.directory)

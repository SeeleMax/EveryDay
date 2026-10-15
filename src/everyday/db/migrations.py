"""数据库迁移执行器（交付物 D2/D3：顺序编号、幂等）。

约定：

* 文件名 ``<版本号>_<描述>.sql``，例如 ``001_initial.sql``
* 版本号按字符串升序执行（因此编号请保持零填充的等宽数字）
* 已执行的版本记入 ``schema_migrations`` 账本，重复运行不会重复执行
* 账本额外记录脚本 SHA-256；**已执行的脚本被修改**时只告警不失败，
  因为开发期调整迁移是常态，而直接失败会让应用无法启动
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from everyday.db.connection import Database
from everyday.paths import migrations_dir

logger = logging.getLogger(__name__)

#: 迁移文件名模式：001_initial.sql
MIGRATION_PATTERN = re.compile(r"^(?P<version>\d+)_(?P<name>.+)\.sql$", re.IGNORECASE)

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    checksum   TEXT NULL
);
"""


@dataclass(frozen=True)
class Migration:
    """一个待执行（或已执行）的迁移脚本。"""

    version: str
    name: str
    path: Path

    @property
    def sql(self) -> str:
        """脚本内容。"""
        return self.path.read_text(encoding="utf-8")

    def checksum(self) -> str:
        """脚本内容的 SHA-256，用于检测「已执行的迁移被改动」。"""
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    """扫描目录，返回按版本号升序排列的迁移列表。"""
    target = directory or migrations_dir()
    if not target.is_dir():
        logger.warning("迁移目录不存在：%s", target)
        return []

    found: list[Migration] = []
    for entry in sorted(target.iterdir()):
        match = MIGRATION_PATTERN.match(entry.name)
        if match and entry.is_file():
            found.append(
                Migration(
                    version=match.group("version"),
                    name=match.group("name"),
                    path=entry,
                )
            )
    # 按版本号排序：不同位数的编号会被字符串顺序坑到，故显式按数值排
    found.sort(key=lambda m: (len(m.version), m.version))
    return found


class MigrationRunner:
    """在给定 :class:`Database` 上执行迁移。"""

    def __init__(self, database: Database, directory: Path | None = None) -> None:
        self._database = database
        self._directory = directory

    # ------------------------------------------------------------------
    def applied_versions(self) -> dict[str, str | None]:
        """返回已执行迁移的 ``{版本: 校验和}``。"""
        self._database.executescript(_LEDGER_DDL)
        rows = self._database.query("SELECT version, checksum FROM schema_migrations")
        return {row["version"]: row["checksum"] for row in rows}

    def pending(self) -> list[Migration]:
        """返回尚未执行的迁移。"""
        applied = self.applied_versions()
        return [m for m in discover_migrations(self._directory) if m.version not in applied]

    def run(self) -> list[str]:
        """执行全部待执行迁移，返回本次实际执行的版本号列表。"""
        applied = self.applied_versions()
        executed: list[str] = []

        for migration in discover_migrations(self._directory):
            if migration.version in applied:
                self._warn_if_modified(migration, applied[migration.version])
                continue
            self._apply(migration)
            executed.append(migration.version)

        if executed:
            logger.info("已执行 %d 个数据库迁移：%s", len(executed), ", ".join(executed))
        else:
            logger.debug("数据库结构已是最新，无需迁移")
        return executed

    # ------------------------------------------------------------------
    def _apply(self, migration: Migration) -> None:
        """执行单个迁移并记账。"""
        logger.info("执行迁移 %s_%s", migration.version, migration.name)
        try:
            self._database.executescript(migration.sql)
        except Exception:
            logger.exception("迁移 %s 执行失败", migration.version)
            raise
        self._database.execute(
            "INSERT OR REPLACE INTO schema_migrations (version, applied_at, checksum) "
            "VALUES (?, ?, ?)",
            (migration.version, datetime.now().isoformat(timespec="seconds"), migration.checksum()),
        )

    @staticmethod
    def _warn_if_modified(migration: Migration, recorded: str | None) -> None:
        """已执行脚本被改动时告警（不失败）。"""
        if recorded and recorded != migration.checksum():
            logger.warning(
                "迁移 %s 在已执行后被修改，校验和不一致。"
                "数据库不会被重新迁移；如需生效请新增一个迁移脚本。",
                migration.version,
            )

    def current_version(self) -> str | None:
        """返回当前数据库结构版本（已执行迁移中的最大版本号）。"""
        applied = self.applied_versions()
        if not applied:
            return None
        return max(applied, key=lambda v: (len(v), v))

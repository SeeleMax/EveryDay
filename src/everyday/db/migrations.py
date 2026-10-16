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
import sqlite3
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

#: 出现这些字样的 ``OperationalError`` 表示「该对象已经生效过」，
#: 重复执行迁移时应当跳过而不是中断。
#: 覆盖：ALTER TABLE ADD COLUMN 的 duplicate column name、
#: CREATE TABLE / INDEX 的 already exists、以及列已存在时的重复索引。
_TOLERATED_ERRORS = (
    "duplicate column name",
    "already exists",
)


_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _has_sql(statement: str) -> bool:
    """语句里是否含有真正的 SQL（而不是只有注释与空白）。

    SQLite 对「只有注释」的字符串执行 prepare 会拿到空语句，
    Python 侧会抛 ProgrammingError，所以这类片段必须丢掉。
    """
    without_comments = _LINE_COMMENT_RE.sub("", statement)
    return bool(without_comments.strip())


def split_statements(script: str) -> list[str]:
    """把 SQL 脚本切成一条条完整语句。

    用 :func:`sqlite3.complete_statement` 判断语句是否收尾，因此
    **字符串字面量里的分号不会被误切**（这是简单按 ``;`` 分割的常见坑）。
    行注释会随语句一起保留（交给 SQLite 忽略），但**只含注释的片段会被丢弃**。
    """
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            text = buffer.strip()
            if text and _has_sql(text):
                statements.append(text)
            buffer = ""
    tail = buffer.strip()
    if tail and _has_sql(tail):
        # 最后一条没有以分号结尾也照常执行
        statements.append(tail)
    return statements


def _first_line(statement: str) -> str:
    """取语句首行，用于日志里标识是哪一条被跳过。"""
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return stripped[:90]
    return statement.strip()[:90]


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
        """逐语句执行单个迁移，并与账本写入放在同一个事务里。

        为什么不用 ``executescript``
        ---------------------------
        SQLite 的 ``ALTER TABLE ... ADD COLUMN`` 与 ``CREATE INDEX`` 都**没有**
        ``IF NOT EXISTS``，整脚本一把执行时只要有一条重复就会中断，后面
        还没执行的语句全部丢掉，迁移文件也就谈不上幂等（D03 要求幂等）。
        因此这里按语句切分执行，并把「该对象已经存在」这类错误视为已生效、
        跳过继续——这样重复执行同一个脚本是安全的。
        """
        logger.info("执行迁移 %s_%s", migration.version, migration.name)
        statements = split_statements(migration.sql)
        try:
            with self._database.transaction() as connection:
                for statement in statements:
                    self._execute_statement(connection, statement, migration)
                connection.execute(
                    "INSERT OR REPLACE INTO schema_migrations "
                    "(version, applied_at, checksum) VALUES (?, ?, ?)",
                    (
                        migration.version,
                        datetime.now().isoformat(timespec="seconds"),
                        migration.checksum(),
                    ),
                )
        except Exception:
            logger.exception("迁移 %s 执行失败", migration.version)
            raise
        logger.debug("迁移 %s 共执行 %d 条语句", migration.version, len(statements))

    @staticmethod
    def _execute_statement(
        connection: sqlite3.Connection, statement: str, migration: Migration
    ) -> None:
        """执行一条语句；「已经存在」类错误跳过，其余抛出。"""
        try:
            connection.execute(statement)
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if any(token in message for token in _TOLERATED_ERRORS):
                logger.debug(
                    "迁移 %s 跳过已生效的语句（%s）：%s",
                    migration.version,
                    exc,
                    _first_line(statement),
                )
                return
            raise

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

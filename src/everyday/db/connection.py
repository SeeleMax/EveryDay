"""SQLite 连接管理。

设计要点：

* **单一长连接**：桌面单机应用，单进程单用户，长连接最简单也最快，
  同时避免了 ``:memory:`` 模式下每次连接都是新空库的陷阱。
* **WAL 模式**（文档技术选型要求）：读写不互相阻塞，崩溃后可恢复。
  内存库不支持 WAL，此处静默跳过。
* **显式事务**：通过 :meth:`Database.transaction` 上下文管理器，
  异常自动回滚。
* **线程安全**：所有语句经同一把可重入锁串行化，允许 UI 线程与
  后台刷新线程共用连接。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 内存库标识（单元测试用）
MEMORY = ":memory:"


class Database:
    """对 :mod:`sqlite3` 的一层薄封装。"""

    def __init__(self, path: str | Path, *, timeout: float = 10.0) -> None:
        self._path = str(path)
        self._timeout = timeout
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    @property
    def path(self) -> str:
        """数据库文件路径，内存库返回 ``:memory:``。"""
        return self._path

    @property
    def is_memory(self) -> bool:
        """是否为内存数据库。"""
        return self._path == MEMORY or self._path.startswith("file::memory:")

    @property
    def is_open(self) -> bool:
        """连接是否已建立。"""
        return self._connection is not None

    def connect(self) -> sqlite3.Connection:
        """返回（必要时创建）数据库连接。"""
        with self._lock:
            if self._connection is None:
                if not self.is_memory:
                    Path(self._path).parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(
                    self._path,
                    timeout=self._timeout,
                    check_same_thread=False,
                )
                connection.row_factory = sqlite3.Row
                self._configure(connection)
                self._connection = connection
                logger.debug("已连接数据库 %s", self._path)
            return self._connection

    def _configure(self, connection: sqlite3.Connection) -> None:
        """设置连接级 PRAGMA。"""
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if not self.is_memory:
            try:
                mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                logger.debug("journal_mode = %s", mode)
            except sqlite3.DatabaseError as exc:  # pragma: no cover - 只读介质等极端情况
                logger.warning("启用 WAL 失败，回落为默认日志模式：%s", exc)
            # WAL 下同步级别设为 NORMAL 是官方推荐，兼顾安全与性能
            connection.execute("PRAGMA synchronous = NORMAL")

    def close(self) -> None:
        """关闭连接（幂等）。"""
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                finally:
                    self._connection = None
                    logger.debug("数据库连接已关闭")

    # ------------------------------------------------------------------
    # 语句执行
    # ------------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """事务上下文：正常退出提交，抛异常回滚。"""
        connection = self.connect()
        with self._lock:
            try:
                yield connection
            except Exception:
                connection.rollback()
                logger.debug("事务已回滚")
                raise
            else:
                connection.commit()

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> int:
        """执行一条写语句，返回 ``lastrowid``。"""
        with self.transaction() as connection:
            cursor = connection.execute(sql, params)
            return int(cursor.lastrowid or 0)

    def execute_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        """批量执行写语句，返回受影响行数。"""
        with self.transaction() as connection:
            cursor = connection.executemany(sql, list(rows))
            return int(cursor.rowcount or 0)

    def executescript(self, script: str) -> None:
        """执行多语句脚本（迁移用）。"""
        connection = self.connect()
        with self._lock:
            connection.executescript(script)
            connection.commit()

    def query(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[sqlite3.Row]:
        """执行查询，返回全部行。"""
        connection = self.connect()
        with self._lock:
            return list(connection.execute(sql, params).fetchall())

    def query_one(
        self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()
    ) -> sqlite3.Row | None:
        """执行查询，返回首行或 ``None``。"""
        connection = self.connect()
        with self._lock:
            return connection.execute(sql, params).fetchone()

    def query_scalar(
        self, sql: str, params: Sequence[Any] | Mapping[str, Any] = (), default: Any = None
    ) -> Any:
        """执行查询并返回第一行第一列。"""
        row = self.query_one(sql, params)
        if row is None:
            return default
        return row[0]

    # ------------------------------------------------------------------
    # 上下文管理器支持（``with Database(...) as db:``）
    # ------------------------------------------------------------------
    def __enter__(self) -> Database:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def open_database(path: str | Path) -> Database:
    """打开数据库并确保结构已就绪的便捷函数。"""
    from everyday.db.migrations import MigrationRunner

    database = Database(path)
    database.connect()
    MigrationRunner(database).run()
    return database

"""应用装配的集成测试。

这个文件真实地跑一遍 :func:`everyday.app.main`：日志 → 数据库 → 迁移 →
备份 → 服务 → 界面 → 托盘 → 事件循环 → 退出清理。

为什么必须有它：纯粹靠静态审查完全无法发现下面这类问题——

* ``QApplication.quit()`` 会先给顶层窗口派发 close 事件，而主窗口与悬浮窗
  为「关闭 = 最小化到托盘」都调用了 ``event.ignore()``，于是**退出被卡死**
* ``QApplication.exec`` 在 PySide6 里是静态方法，补丁写错就会在启动阶段炸掉
* 首次运行时数据库文件还不存在，备份逻辑会顺手造出一个空库

只有真的把应用启起来再退出去，这些问题才会暴露。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from tests.base import DatabaseTestCase  # noqa: F401  (确保 src 已入 path)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PySide6  # noqa: F401
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAS_QT = True
except ImportError:  # pragma: no cover - 无 Qt 环境
    HAS_QT = False
    QApplication = None  # type: ignore[assignment]

#: 事件循环存活时长（毫秒）
RUN_MILLISECONDS = 250

#: 运行期间被自动消解掉的模态提示框
_DISMISSED_DIALOGS: list[str] = []


@unittest.skipUnless(HAS_QT, "需要 PySide6")
class ApplicationStartupTest(unittest.TestCase):
    """端到端启动/退出集成测试。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "data"
        _DISMISSED_DIALOGS.clear()
        # Application.run() 会把 --data-dir 写进 EVERYDAY_DATA_DIR（进程级），
        # 这里保存原值，避免污染同一进程里的其它测试
        from everyday import paths

        self._previous_data_dir = os.environ.get(paths.DATA_DIR_ENV)

    def tearDown(self) -> None:
        # 让日志 handler 释放日志文件，否则 Windows 上临时目录删不掉
        from everyday import paths
        from everyday.logging_setup import reset_logging

        reset_logging()
        if self._previous_data_dir is None:
            os.environ.pop(paths.DATA_DIR_ENV, None)
        else:
            os.environ[paths.DATA_DIR_ENV] = self._previous_data_dir
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    def _run_application(self, *, minimized: bool = False) -> int:
        """启动应用，到点后走真实退出路径，返回进程退出码。"""
        from everyday.app import Application, main
        from everyday import exceptions

        holder: dict[str, object] = {}
        existing_app = QApplication.instance()

        original_run = Application.run
        original_create = Application._create_qt_application
        original_exec = QApplication.exec
        original_messagebox_exec = QMessageBox.exec

        def capturing_run(self):  # noqa: ANN001, ANN202 - 补丁
            holder["app"] = self
            return original_run(self)

        def create_or_reuse(self):  # noqa: ANN001, ANN202 - 补丁
            if existing_app is None:
                return original_create(self)
            # 同一进程内已经有 QApplication（其它 UI 测试先跑过），
            # Qt 不允许创建第二个，因此复用并把退出清理接回来。
            existing_app.aboutToQuit.connect(self._on_about_to_quit)
            return existing_app

        def timed_exec(self):  # noqa: ANN001, ANN202 - 补丁
            def request_quit() -> None:
                application = holder.get("app")
                if application is not None:
                    application._quit()  # noqa: SLF001 - 刻意走真实退出路径

            QTimer.singleShot(RUN_MILLISECONDS, request_quit)
            return original_exec()

        def dismiss_messagebox(self) -> int:  # noqa: ANN001 - 补丁
            # 异常处理器会弹模态框，模态框会把事件循环永久卡住。
            # 这里改成「记录 + 立即关闭」，于是异常不再隐藏。
            _DISMISSED_DIALOGS.append(self.text() or self.windowTitle())
            return int(QMessageBox.Ok)

        Application.run = capturing_run
        Application._create_qt_application = create_or_reuse
        QApplication.exec = timed_exec
        QMessageBox.exec = dismiss_messagebox
        try:
            argv = ["EveryDay", "--data-dir", str(self.data_dir), "--log-level", "DEBUG"]
            if minimized:
                argv.append("--minimized")
            return int(main(argv))
        finally:
            Application.run = original_run
            Application._create_qt_application = original_create
            QApplication.exec = original_exec
            QMessageBox.exec = original_messagebox_exec
            exceptions.reset_popup_state()

    # ------------------------------------------------------------------
    def _database_tables(self) -> set[str]:
        connection = sqlite3.connect(str(self.data_dir / "everyday.db"))
        try:
            return {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()

    # ------------------------------------------------------------------
    def test_first_run_creates_database_and_exits_cleanly(self):
        exit_code = self._run_application()

        self.assertEqual(exit_code, 0, f"启动失败，弹出的提示框：{_DISMISSED_DIALOGS}")
        self.assertEqual(_DISMISSED_DIALOGS, [], "启动过程中不应出现异常提示框")
        self.assertTrue((self.data_dir / "everyday.db").is_file())
        self.assertTrue(
            {"task_item", "preset_template", "app_settings", "schema_migrations"}.issubset(
                self._database_tables()
            )
        )

    def test_startup_writes_log_with_clean_shutdown_marker(self):
        self._run_application()

        log_text = (self.data_dir / "logs" / "everyday.log").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("启动中", log_text)
        self.assertIn("初始化完成，进入事件循环", log_text)
        # 「已退出」由 aboutToQuit → Application._on_about_to_quit 写入，
        # 是退出清理真的跑完了的证据
        self.assertIn("已退出", log_text)
        self.assertNotIn("Traceback", log_text)

    def test_minimized_start_also_exits_cleanly(self):
        """``--minimized`` 是开机自启使用的路径，必须同样能正常退出。"""
        exit_code = self._run_application(minimized=True)

        self.assertEqual(exit_code, 0, f"弹出的提示框：{_DISMISSED_DIALOGS}")
        log_text = (self.data_dir / "logs" / "everyday.log").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("以最小化模式启动", log_text)

    def test_first_run_does_not_create_empty_backup(self):
        """首次运行时库文件原本不存在，不应产生毫无意义的空库备份。"""
        self._run_application()

        backups_dir = self.data_dir / "backups"
        backups = sorted(backups_dir.glob("everyday-*.db")) if backups_dir.is_dir() else []
        self.assertEqual(backups, [], f"不应生成备份：{[p.name for p in backups]}")

    def test_second_run_creates_startup_backup(self):
        """文档第八章：每次启动都备份，保留最近五份。"""
        self._run_application()
        exit_code = self._run_application()

        self.assertEqual(exit_code, 0, f"弹出的提示框：{_DISMISSED_DIALOGS}")
        backups = sorted((self.data_dir / "backups").glob("everyday-*.db"))
        self.assertEqual(len(backups), 1, f"实际备份：{[p.name for p in backups]}")
        self.assertRegex(backups[0].name, r"^everyday-\d{4}-\d{2}-\d{2}\.db$")

        connection = sqlite3.connect(str(backups[0]))
        try:
            preset_count = connection.execute(
                "SELECT COUNT(*) FROM preset_template"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertGreaterEqual(preset_count, 10, "备份里应当包含已迁移的数据")

    def test_startup_is_idempotent_for_presets(self):
        """重复启动不应重复播种默认模板。"""
        self._run_application()
        self._run_application()

        connection = sqlite3.connect(str(self.data_dir / "everyday.db"))
        try:
            preset_count = connection.execute(
                "SELECT COUNT(*) FROM preset_template"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(preset_count, 10)

    def test_data_dir_override_never_touches_default_location(self):
        """``--data-dir`` 必须完全隔离，连启动早期的日志也不能写到默认目录。

        回归背景：早期实现在解析参数之后、应用数据目录之前就调用了
        ``setup_logging()``，于是走默认的 ``%LOCALAPPDATA%\\EveryDay\\logs``，
        即使传了 ``--data-dir`` 也会污染真实数据目录。这里把 LOCALAPPDATA
        指向临时目录，从而能在不碰用户真实数据的前提下验证隔离性。
        """
        fake_local_app_data = Path(self._tmp.name) / "fake-localappdata"
        fake_local_app_data.mkdir()
        previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(fake_local_app_data)
        try:
            exit_code = self._run_application()
        finally:
            if previous is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous

        self.assertEqual(exit_code, 0, f"弹出的提示框：{_DISMISSED_DIALOGS}")
        self.assertFalse(
            (fake_local_app_data / "EveryDay").exists(),
            "传入 --data-dir 时不得在默认数据目录下创建任何文件",
        )


    def test_status_bar_shows_actual_database_path(self):
        """``--data-dir`` 必须成为进程级数据根目录，界面才显示正确路径。

        回归背景：``MainWindow`` 状态栏与设置页调用不带参数的
        ``paths.database_path()``，而 ``--data-dir`` 原先只传给了
        ``Application`` 内部，导致界面显示并「打开数据目录」时指向默认的
        ``%LOCALAPPDATA%\\EveryDay``——真实数据其实在别处。
        """
        from everyday import paths

        self._run_application()

        self.assertEqual(
            paths.database_path(),
            self.data_dir / "everyday.db",
            "启动后不带参数的 paths.database_path() 必须指向 --data-dir 指定的目录",
        )
        self.assertEqual(paths.data_dir(), self.data_dir)
        self.assertEqual(paths.logs_dir(), self.data_dir / "logs")
        self.assertEqual(paths.backups_dir(), self.data_dir / "backups")


if __name__ == "__main__":
    unittest.main()

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
        from everyday import exceptions
        from everyday.app import Application, main

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


class DataDirectoryResolutionTest(unittest.TestCase):
    """数据目录解析与失败提示。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_resolve_data_dir_has_no_side_effects(self):
        """``resolve_data_dir`` 只算路径，不建目录。

        这样调用方才能在创建失败时拿到目标路径去给出可读提示。
        """
        from everyday import paths

        target = Path(self._tmp.name) / "not-created" / "deeper"
        self.assertEqual(paths.resolve_data_dir(str(target)), target)
        self.assertFalse(target.exists(), "resolve_data_dir 不应创建任何东西")

    def test_data_dir_creates_directory(self):
        from everyday import paths

        target = Path(self._tmp.name) / "a" / "b" / "c"
        created = paths.data_dir(str(target))
        self.assertTrue(created.is_dir())

    def test_data_dir_raises_oserror_when_parent_is_a_file(self):
        from everyday import paths

        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("我是文件，不是目录", encoding="utf-8")
        with self.assertRaises(OSError):
            paths.data_dir(str(blocker / "sub"))

    def test_project_root_finds_bundled_resources_for_frozen_builds(self):
        """打包后必须能在 ``sys._MEIPASS`` 里找到 ``migrations/``。

        回归背景：``--onefile`` 模式下 ``--add-data`` 附带的数据被解包到
        ``sys._MEIPASS`` 临时目录，而不是 exe 旁边。早期实现只看
        ``sys.executable`` 的父目录，于是打包后找不到迁移脚本、数据库建不起来。
        """
        import sys

        from everyday import paths

        bundled = Path(self._tmp.name) / "_MEI12345"
        (bundled / "migrations").mkdir(parents=True)

        original_frozen = getattr(sys, "frozen", None)
        original_meipass = getattr(sys, "_MEIPASS", None)
        try:
            sys.frozen = True  # type: ignore[attr-defined]
            sys._MEIPASS = str(bundled)  # type: ignore[attr-defined]
            self.assertEqual(paths.project_root(), bundled)
            self.assertEqual(paths.migrations_dir(), bundled / "migrations")
        finally:
            if original_frozen is None:
                del sys.frozen  # type: ignore[attr-defined]
            else:
                sys.frozen = original_frozen  # type: ignore[attr-defined]
            if original_meipass is None:
                del sys._MEIPASS  # type: ignore[attr-defined]
            else:
                sys._MEIPASS = original_meipass  # type: ignore[attr-defined]

    def test_project_root_falls_back_to_executable_dir_when_no_bundle(self):
        """目录模式（``--onedir``）或没有 _MEIPASS 时应落在 exe 所在目录。"""
        import sys

        from everyday import paths

        original_frozen = getattr(sys, "frozen", None)
        original_meipass = getattr(sys, "_MEIPASS", None)
        try:
            sys.frozen = True  # type: ignore[attr-defined]
            if original_meipass is not None:
                del sys._MEIPASS  # type: ignore[attr-defined]
            self.assertEqual(paths.project_root(), Path(sys.executable).resolve().parent)
        finally:
            if original_frozen is None:
                del sys.frozen  # type: ignore[attr-defined]
            else:
                sys.frozen = original_frozen  # type: ignore[attr-defined]
            if original_meipass is not None:
                sys._MEIPASS = original_meipass  # type: ignore[attr-defined]

    def test_unwritable_data_dir_reports_clear_error_and_exits_2(self):
        """数据目录建不出来时必须给出可读提示并以 2 退出，而不是抛裸异常。

        回归背景：这一步发生在日志初始化之前，因此拿不到日志；若直接抛
        ``PermissionError``，用 ``pythonw.exe`` 启动（双击、开机自启）时
        用户什么都看不到，程序就无声无息地没了。
        """
        from everyday import app as app_module

        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("我是文件，不是目录", encoding="utf-8")
        bad_dir = blocker / "sub"

        captured: list[tuple[Path, OSError]] = []
        dialogs: list[tuple[str, str]] = []

        original_report = app_module._report_fatal_startup_error
        original_dialog = app_module._show_native_error_dialog
        app_module._report_fatal_startup_error = lambda target, exc: captured.append(
            (target, exc)
        )
        # 屏蔽原生消息框：否则测试会被模态框阻塞（且本机曾出现 _ctypes 不可用）
        app_module._show_native_error_dialog = lambda title, message: dialogs.append(
            (title, message)
        )
        try:
            exit_code = app_module.main(["EveryDay", "--data-dir", str(bad_dir)])
        finally:
            app_module._report_fatal_startup_error = original_report
            app_module._show_native_error_dialog = original_dialog

        self.assertEqual(exit_code, 2)
        self.assertEqual(len(captured), 1, "必须调用一次失败提示")
        self.assertEqual(captured[0][0], bad_dir, "提示里必须给出目标路径")
        self.assertIsInstance(captured[0][1], OSError)

    def test_native_error_dialog_swallows_ctypes_failure(self):
        """原生提示框必须「尽力而为」：ctypes 不可用时也不能抛出去。

        本机真的遇到过某个 venv 里 ``import ctypes`` 直接报
        ``DLL load failed while importing _ctypes``，因此这条不是假想。
        """

        from everyday import app as app_module

        class BrokenCtypes:
            def __getattr__(self, name: str):
                raise RuntimeError("模拟 _ctypes 不可用")

        original = sys.modules.get("ctypes")
        sys.modules["ctypes"] = BrokenCtypes()  # type: ignore[assignment]
        try:
            # 不应抛出任何异常
            app_module._show_native_error_dialog("测试标题", "测试内容")
        finally:
            if original is None:
                sys.modules.pop("ctypes", None)
            else:
                sys.modules["ctypes"] = original


if __name__ == "__main__":
    unittest.main()

"""应用装配与启动流程（文档 3.3）。

启动顺序：解析参数 → 初始化日志 → 打开数据库 → 迁移 → 备份 →
初始化服务 → 读取配置 → 建立界面 → 挂载托盘 → 判断启动参数显示窗口。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from everyday import APP_NAME, __version__, exceptions, paths
from everyday.db import Database, MigrationRunner
from everyday.db.repositories import (
    DependencyRepository,
    FocusRepository,
    PresetRepository,
    ReminderRepository,
    SettingsRepository,
    TagRepository,
    TaskRepository,
)
from everyday.logging_setup import setup_logging
from everyday.services.backup_service import BackupService
from everyday.services.calendar_service import CalendarService
from everyday.services.dependency_service import DependencyService
from everyday.services.pomodoro_service import PomodoroService
from everyday.services.preset_service import PresetService
from everyday.services.reminder_service import ReminderScheduler
from everyday.services.settings_service import SettingsService
from everyday.services.startup_service import StartupService
from everyday.services.task_service import TaskService

logger = logging.getLogger(__name__)

#: 跨午夜检查间隔（文档 5.5：每小时检查一次系统日期）
DATE_CHECK_INTERVAL_MS = 60 * 60 * 1000

#: v1.1 P3：提醒扫描间隔。提醒粒度是分钟（``remind_time`` 精确到分），
#: 扫得更密只会白白唤醒 CPU。
REMINDER_SCAN_INTERVAL_MS = 60 * 1000


def _raw_int(repository, key: str, default: int) -> int:
    """从 app_settings 读一个整数配置，缺失或非法时用默认值。"""
    try:
        return int(repository.get_raw(key, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Options:
    """命令行选项。"""

    minimized: bool = False
    data_dir: str | None = None
    log_level: str | None = None


def parse_args(argv: list[str]) -> Options:
    """解析命令行参数。

    ``--minimized`` 由开机自启写入注册表时附带（文档 5.1）。
    """
    parser = argparse.ArgumentParser(
        prog=APP_NAME, description="EveryDay 每日计划软件"
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="静默启动：不显示主窗口，仅驻留托盘与悬浮窗（开机自启使用）",
    )
    parser.add_argument(
        "--data-dir", default=None, help="覆盖数据目录（调试用）"
    )
    parser.add_argument(
        "--log-level", default=None, help="覆盖日志级别：DEBUG/INFO/WARNING/ERROR"
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    namespace = parser.parse_args(argv[1:])
    return Options(
        minimized=namespace.minimized,
        data_dir=namespace.data_dir,
        log_level=namespace.log_level,
    )


class Application:
    """把各层对象装配起来并驱动 Qt 事件循环。"""

    def __init__(self, options: Options) -> None:
        self._options = options
        self._root: Path | None = None
        self._database: Database | None = None
        self._qt_app = None
        self._window = None
        self._float_window = None
        self._tray = None
        self._context = None
        self._settings_service: SettingsService | None = None
        self._date_timer = None
        self._calendar_timer = None
        self._current_day = None
        # ---- v1.1 P3:番茄钟与提醒 ----
        # 控制器与通知端口来自 ui 层（控制器依赖 Qt），故**不加类型注解**：
        # 模块级导入它们会破坏「没有 Qt 也能 import everyday.app」的约束。
        self._pomodoro_service: PomodoroService | None = None
        self._pomodoro_controller = None
        self._reminder_scheduler: ReminderScheduler | None = None
        self._reminder_timer = None
        self._notifier = None

    # ==================================================================
    def run(self) -> int:
        """启动应用，返回进程退出码。"""
        options = self._options

        # 1) 路径与日志：必须在最早期就绪，后续任何失败都能留痕
        #
        # 把 --data-dir 登记为**进程级**数据根目录。界面层（主窗口状态栏、
        # 设置页的「数据库文件」「打开数据目录」）调用的是不带参数的
        # paths.database_path() / paths.data_dir()，如果这里不做登记，
        # 它们会解析到默认的 %LOCALAPPDATA%\EveryDay，于是显示并打开一个
        # 完全错误的目录——而真实数据其实在 --data-dir 指定的位置。
        if options.data_dir:
            os.environ[paths.DATA_DIR_ENV] = str(Path(options.data_dir).expanduser())
        self._root = paths.data_dir(options.data_dir)
        log_file = setup_logging(
            options.log_level or "INFO", log_directory=paths.logs_dir(self._root)
        )
        logger.info("=" * 68)
        logger.info("%s v%s 启动中；数据目录=%s", APP_NAME, __version__, self._root)
        logger.info("日志文件=%s", log_file)

        # 2) 数据库：先备份再做迁移，这样迁移出问题时仍有回滚依据
        self._database = Database(paths.database_path(self._root))
        self._startup_backup()
        MigrationRunner(self._database).run()

        # 3) 服务
        # v1.1 P2：标签与依赖需要各自的仓储，依赖服务还要读任务表来判断阻塞
        task_repository = TaskRepository(self._database)
        dependency_service = DependencyService(
            DependencyRepository(self._database), task_repository
        )
        task_service = TaskService(
            task_repository,
            tags=TagRepository(self._database),
            dependencies=dependency_service,
        )
        preset_service = PresetService(PresetRepository(self._database))
        settings_repo = SettingsRepository(self._database)
        self._settings_service = SettingsService(settings_repo)
        calendar_service = CalendarService()
        startup_service = StartupService()

        self._settings_service.seed_from_template()
        settings = self._settings_service.load()
        if options.log_level:
            settings = settings.merged(log_level=options.log_level.upper())
            self._settings_service.save(settings)

        # 4) 开机自启与注册表对账（文档 5.1）
        actual_autostart = self._reconcile_autostart(startup_service, settings.auto_start)
        if actual_autostart != settings.auto_start:
            settings = self._settings_service.save(
                settings.merged(auto_start=actual_autostart)
            )

        # 4.5) v1.1 P3：番茄钟服务。
        # 时长从 app_settings 键值表读，缺键时用默认值——
        # 这正是当初选键值存储的收益：新增配置项不需要迁移。
        self._pomodoro_service = PomodoroService(
            FocusRepository(self._database),
            default_minutes=_raw_int(settings_repo, "pomodoro_minutes", 25),
            break_minutes=_raw_int(settings_repo, "pomodoro_break_minutes", 5),
        )

        # 5) 界面
        from everyday.ui.context import AppContext
        from everyday.ui.float_window import FloatWindow
        from everyday.ui.main_window import MainWindow
        from everyday.ui.tray import TrayIcon

        self._qt_app = self._create_qt_application()
        self._install_exception_handlers()

        self._context = AppContext(
            task_service=task_service,
            preset_service=preset_service,
            settings_service=self._settings_service,
            calendar_service=calendar_service,
            startup_service=startup_service,
            settings=settings,
        )
        self._current_day = self._context.today()

        # 启动时执行一次循环任务状态校对（文档 5.5）
        reset_count = task_service.rollover_if_needed(self._current_day)
        if reset_count:
            logger.info("启动校对重置了 %d 项循环任务", reset_count)

        # 启动时加载一次日历（文档 5.6）
        calendar_service.refresh(settings.calendar_ics_path, day=self._current_day)

        self._window = MainWindow(self._context)
        self._float_window = FloatWindow(self._context)
        self._tray = TrayIcon(self._context, self._window)

        self._wire_signals(startup_service)
        self._start_timers()

        self._tray.show()
        self._tray.set_progress_text(self._context.progress().label)

        if not options.minimized:
            self._window.show()
        else:
            logger.info("以最小化模式启动：仅显示托盘与悬浮窗")

        if settings.float_window_visible:
            self._float_window.show()

        logger.info("初始化完成，进入事件循环")
        return int(self._qt_app.exec())

    # ==================================================================
    def _startup_backup(self) -> None:
        """启动备份（文档第八章）。"""
        assert self._database is not None
        assert self._root is not None
        service = BackupService(directory=paths.backups_dir(self._root))
        created = service.run_startup_backup(self._database)
        if created is not None:
            logger.info("启动备份完成：%s（保留最近 %d 份）", created.name, service.keep)

    def _reconcile_autostart(self, startup: StartupService, desired: bool) -> bool:
        """让注册表与配置一致，返回对账后的真实状态。"""
        if not startup.is_supported():
            return desired
        actual = startup.is_enabled()
        if desired and not actual:
            logger.info("配置要求开机自启，正在写入注册表")
            startup.enable()
            return startup.is_enabled()
        if not desired and actual:
            logger.info("配置已关闭开机自启，正在清理注册表")
            startup.disable()
            return startup.is_enabled()
        return actual

    def _create_qt_application(self):
        """创建 QApplication 子类实例（含 Qt 事件异常兜底）。"""
        from everyday.ui import icons, styles

        app = _make_application_class()(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setApplicationDisplayName(APP_NAME)
        app.setApplicationVersion(__version__)
        app.setOrganizationName(APP_NAME)
        app.setWindowIcon(icons.app_icon())
        app.setStyleSheet(styles.MAIN_STYLE)
        app.setQuitOnLastWindowClosed(False)
        app.aboutToQuit.connect(self._on_about_to_quit)
        return app

    def _install_exception_handlers(self) -> None:
        """安装三层异常捕获（文档第九章），提示走 Qt 消息框。"""
        from PySide6.QtWidgets import QMessageBox

        def notifier(title: str, message: str) -> None:
            box = QMessageBox()
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(title)
            box.setText(message)
            box.setStandardButtons(QMessageBox.Ok)
            box.exec()

        exceptions.install(notifier)

    def _wire_signals(self, startup_service: StartupService) -> None:
        """连接窗口、托盘与上下文之间的信号。"""
        assert self._window is not None
        assert self._float_window is not None
        assert self._tray is not None
        assert self._context is not None

        self._tray.show_main_requested.connect(self._show_main_window)
        self._tray.float_toggle_requested.connect(self._toggle_float)
        self._tray.autostart_toggle_requested.connect(self._toggle_autostart)
        self._tray.quit_requested.connect(self._quit)

        self._window.float_toggle_requested.connect(self._toggle_float)
        self._float_window.visibility_change_requested.connect(self._on_float_hidden)
        # 关闭主窗口时用托盘气泡提示「程序还在运行」，避免用户以为已经退出
        self._window.attach_tray(self._tray)

        self._context.tasks_changed.connect(self._update_tray_progress)

        # ---- v1.1 P3：番茄钟与提醒调度 ----
        # 这些类在 run() 里是懒导入的，本方法是另一个作用域，必须自己导入一次
        from everyday.ui.notifier import TrayNotifier
        from everyday.ui.pomodoro_controller import PomodoroController

        # 通知端口与调度器必须在托盘建好之后创建（端口要包着托盘）
        self._notifier = TrayNotifier(self._tray)
        if self._pomodoro_service is None:  # pragma: no cover - 正常路径已创建
            self._pomodoro_service = PomodoroService(FocusRepository(self._database))
        self._reminder_scheduler = ReminderScheduler(
            self._context.task_service,
            ReminderRepository(self._database),
            self._notifier,
            pomodoro=self._pomodoro_service,
        )
        self._pomodoro_controller = PomodoroController(
            self._pomodoro_service,
            tray=self._tray,
            float_window=self._float_window,
            notifier=self._notifier,
            # 注意 parent 必须是 QObject：Application 是普通类，传进来会 TypeError。
            # 引用保存在 self 上，不会被 GC 回收。
            parent=None,
        )
        self._tray.pomodoro_start_requested.connect(self._pomodoro_controller.start)
        self._tray.pomodoro_pause_requested.connect(self._pomodoro_controller.pause)
        self._tray.pomodoro_resume_requested.connect(self._pomodoro_controller.resume)
        self._tray.pomodoro_stop_requested.connect(self._pomodoro_controller.stop)

    def _start_timers(self) -> None:
        """启动跨午夜检查与日历自动刷新定时器。"""
        from PySide6.QtCore import QTimer

        assert self._context is not None

        self._date_timer = QTimer()
        self._date_timer.setInterval(DATE_CHECK_INTERVAL_MS)
        self._date_timer.timeout.connect(self._check_date_change)
        self._date_timer.start()

        self._calendar_timer = QTimer()
        self._calendar_timer.setInterval(
            max(1, self._context.settings.calendar_refresh_minutes) * 60 * 1000
        )
        self._calendar_timer.timeout.connect(self._refresh_calendar)
        self._calendar_timer.start()

        # v1.1 P3：提醒调度器每分钟扫一次。
        # 频率取 1 分钟是因为提醒的粒度就是分钟（remind_time 精确到分），
        # 扫得更密只会白白唤醒 CPU。
        self._reminder_timer = QTimer()
        self._reminder_timer.setInterval(REMINDER_SCAN_INTERVAL_MS)
        self._reminder_timer.timeout.connect(self._scan_reminders)
        self._reminder_timer.start()

    def _scan_reminders(self) -> None:
        """扫描一次待提醒任务（失败不能影响主循环）。"""
        if self._reminder_scheduler is None:
            return
        try:
            notified = self._reminder_scheduler.tick()
        except Exception:
            logger.exception("提醒扫描失败，本轮跳过")
            return
        if notified:
            logger.info("本次提醒了 %d 个任务：%s", len(notified), notified)

    # ==================================================================
    def _check_date_change(self) -> None:
        """跨午夜检测（文档 5.5）。"""
        assert self._context is not None
        today = self._context.today()
        if today == self._current_day:
            return
        logger.info("检测到日期变更：%s -> %s，执行循环任务校对", self._current_day, today)
        self._current_day = today
        self._context.task_service.rollover(today)
        self._context.notify_tasks_changed()

    def _refresh_calendar(self) -> None:
        """定时刷新日历（文档 5.6）。"""
        assert self._context is not None
        self._context.calendar_service.refresh(
            self._context.settings.calendar_ics_path, day=self._context.today()
        )
        self._context.notify_calendar_changed()

    def _update_tray_progress(self) -> None:
        assert self._context is not None
        assert self._tray is not None
        self._tray.set_progress_text(self._context.progress().label)

    def _show_main_window(self) -> None:
        assert self._window is not None
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def _toggle_float(self, visible: bool) -> None:
        assert self._float_window is not None
        assert self._context is not None
        if visible:
            self._float_window.show()
            self._float_window.raise_()
        else:
            self._float_window.hide()
        self._context.set_float_visible(visible)

    def _on_float_hidden(self, visible: bool) -> None:
        assert self._context is not None
        self._context.set_float_visible(visible)

    def _toggle_autostart(self, enabled: bool) -> None:
        assert self._context is not None
        startup: StartupService = self._context.startup_service
        if not startup.is_supported():
            return
        ok = startup.apply(enabled)
        if not ok and enabled:
            logger.warning("开机自启写入失败")
        actual = startup.is_enabled()
        self._context.update_settings(auto_start=actual)
        if self._tray is not None:
            self._tray.sync_autostart(actual)

    def _quit(self) -> None:
        """退出应用（文档第七章：只有明确操作才退出）。

        必须先置「正在退出」标志再调 ``quit()``：主窗口与悬浮窗在
        ``closeEvent`` 里默认拒绝关闭（关闭 = 最小化到托盘），
        而 ``quit()`` 会先派发 close 事件——只要有一个窗口拒绝，
        事件循环就不会结束，用户会看到「点了退出但程序还在」。
        """
        logger.info("收到退出请求")
        assert self._qt_app is not None
        if self._context is not None:
            self._context.begin_shutdown()
        self._qt_app.quit()

    def _on_about_to_quit(self) -> None:
        """退出前保存悬浮窗位置并断开数据库。"""
        try:
            # 兜底：若退出并非经由 _quit（例如系统注销），窗口也需要放行
            if self._context is not None:
                self._context.begin_shutdown()
            if self._float_window is not None:
                self._float_window.persist_position()
            if self._tray is not None:
                self._tray.hide()
            if self._date_timer is not None:
                self._date_timer.stop()
            if self._calendar_timer is not None:
                self._calendar_timer.stop()
            # v1.1 P3：必须停掉番茄钟与提醒定时器，否则窗口销毁后
            # 定时器仍可能被触发，访问已释放控件直接崩溃
            if self._reminder_timer is not None:
                self._reminder_timer.stop()
            if self._pomodoro_controller is not None:
                self._pomodoro_controller.shutdown()
        finally:
            if self._database is not None:
                self._database.close()
            logger.info("%s 已退出", APP_NAME)


def _make_application_class():
    """构造 QApplication 子类，避免在没有 Qt 的环境下导入即失败。"""
    from PySide6.QtWidgets import QApplication

    class EveryDayApplication(QApplication):
        """在 Qt 事件分发层兜住异常，保证界面不会因单个槽函数出错而崩溃。"""

        def notify(self, receiver, event):  # noqa: D102 - Qt 覆写
            try:
                return super().notify(receiver, event)
            except Exception as exc:  # noqa: BLE001 - 全局兜底
                exceptions.report_exception(exc, source="qt-event")
                return False

    return EveryDayApplication


def _show_native_error_dialog(title: str, message: str) -> None:
    """用 Windows 原生消息框提示致命错误。

    在 ``pythonw.exe`` 下没有控制台，这是唯一能让用户看到信息的办法。
    刻意不依赖 Qt（此时还没有 QApplication），并且**吞掉所有失败**——
    这一层只做「尽力而为」的提示。注意某些 Python 发行版的 ``_ctypes``
    可能不可用（本机就遇到过一个 venv 里 ``import ctypes`` 直接报
    `DLL load failed while importing _ctypes`），此时 stderr 已经有信息了。
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        # MB_ICONERROR = 0x10
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:  # noqa: BLE001 - 提示失败不影响主流程
        logger.debug("原生消息框不可用，仅输出到 stderr", exc_info=True)


def _report_fatal_startup_error(target: Path, exc: OSError) -> None:
    """启动早期无法建立数据目录时，给出人能看懂的提示。

    为什么需要单独处理：这一步发生在日志初始化**之前**，因此拿不到日志文件。
    如果只用 ``print``，那么用 ``pythonw.exe`` 启动（开机自启、双击快捷方式）
    时用户**什么都看不到**——程序就这样无声无息地没了。
    """
    message = (
        f"EveryDay 无法建立数据目录：\n\n{target}\n\n"
        f"系统报告的错误：{exc}\n\n"
        "可能的原因：\n"
        "  · 该目录被安全软件或组策略禁止写入\n"
        "  · 该目录正处于被删除的中间状态（稍等片刻后重试）\n"
        "  · 磁盘已满或处于只读状态\n\n"
        "解决办法：用 --data-dir 指定另一个可写目录，例如\n"
        f'  python main.py --data-dir "%USERPROFILE%\\EveryDay"'
    )
    print(message, file=sys.stderr, flush=True)
    _show_native_error_dialog(f"{APP_NAME} 启动失败", message)


def main(argv: list[str] | None = None) -> int:
    """程序入口。"""
    arguments = list(sys.argv if argv is None else argv)
    options = parse_args(arguments)

    # 启动早期的日志也必须落在**正确的**数据目录。
    # 早期实现直接调用 setup_logging() 走默认目录，结果即使传了 --data-dir，
    # 仍然会在 %LOCALAPPDATA%\EveryDay\logs 下创建（并长期持有）一个日志文件，
    # 破坏 --data-dir 的隔离语义，也让便携/调试场景互相干扰。
    #
    # 这里必须显式捕获 OSError：数据目录建不出来时，与其抛一个裸 traceback
    # （pythonw 启动下用户看不到任何东西），不如给出可操作的提示。
    try:
        early_root = paths.data_dir(options.data_dir)
    except OSError as exc:
        _report_fatal_startup_error(paths.resolve_data_dir(options.data_dir), exc)
        return 2

    setup_logging(
        options.log_level or "INFO", log_directory=paths.logs_dir(early_root)
    )

    try:
        return Application(options).run()
    except Exception as exc:  # noqa: BLE001 - 启动阶段的最终兜底
        exceptions.report_exception(exc, source="startup")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

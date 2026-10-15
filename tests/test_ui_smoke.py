"""UI 层冒烟测试（需要 PySide6，未安装时整体跳过）。

这里**不测像素**，只测最容易出错、也最值得自动化守护的东西：

1. 全部模块能被导入（语法错误 / 名字写错 / 循环导入）
2. 所有窗口与对话框能被构造出来（信号连接、控件属性真实存在）
3. 界面上的操作能正确落库（点勾选框 → 数据库真的改了）
4. 悬浮窗位置记忆的读写往返
5. 图标能用 QPainter 真正渲染出来

渲染走 Qt 的 ``offscreen`` 平台插件，因此无需真实桌面会话。
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from tests.base import DatabaseTestCase  # noqa: F401  (确保 src 已入 path)

# 必须在导入任何 Qt 模块之前指定平台插件
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PySide6  # noqa: F401
    from PySide6.QtWidgets import QApplication

    HAS_QT = True
except ImportError:  # pragma: no cover - 无 Qt 环境
    HAS_QT = False
    QApplication = None  # type: ignore[assignment]

import everyday  # noqa: E402
from everyday import paths  # noqa: E402
from everyday.db import MEMORY, Database, MigrationRunner  # noqa: E402
from everyday.db.repositories import (  # noqa: E402
    PresetRepository,
    SettingsRepository,
    TaskRepository,
)
from everyday.models.task import TaskItem, TaskType  # noqa: E402
from everyday.services.calendar_service import CalendarService  # noqa: E402
from everyday.services.preset_service import PresetService  # noqa: E402
from everyday.services.settings_service import SettingsService  # noqa: E402
from everyday.services.startup_service import StartupService  # noqa: E402
from everyday.services.task_service import TaskService  # noqa: E402

MONDAY = date(2026, 10, 5)


class NoopStartupService(StartupService):
    """UI 测试专用的自启服务桩。

    真实的 :meth:`StartupService.apply` 会写当前用户的注册表启动项。
    测试绝不允许产生这种副作用，因此这里声明「平台不支持」，
    让所有注册表操作退化为空操作。
    """

    @staticmethod
    def is_supported() -> bool:
        return False


class ModuleImportTest(unittest.TestCase):
    """不依赖 Qt 的静态导入检查：所有模块都必须能被导入。"""

    def test_every_module_imports(self):
        failures: list[str] = []
        for module_info in pkgutil.walk_packages(
            everyday.__path__, prefix="everyday."
        ):
            try:
                importlib.import_module(module_info.name)
            except Exception as exc:  # noqa: BLE001 - 收集全部失败再统一断言
                failures.append(f"{module_info.name}: {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], "以下模块导入失败：\n" + "\n".join(failures))


@unittest.skipUnless(HAS_QT, "未安装 PySide6，跳过 UI 冒烟测试")
class UiBaseTestCase(unittest.TestCase):
    """提供 offscreen ``QApplication`` 与一套装配好的服务。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        # 把数据目录指到临时目录，避免碰到真实用户数据
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_data_dir = os.environ.get(paths.DATA_DIR_ENV)
        os.environ[paths.DATA_DIR_ENV] = self._tmp.name

        self.db = Database(MEMORY)
        MigrationRunner(self.db).run()

        self.settings_service = SettingsService(SettingsRepository(self.db))
        self.settings_service.seed_from_template()
        self.settings = self.settings_service.load()

        self.task_service = TaskService(TaskRepository(self.db))
        self.preset_service = PresetService(PresetRepository(self.db))
        self.calendar_service = CalendarService()
        self.startup_service = NoopStartupService()

        from everyday.ui.context import AppContext

        self.context = AppContext(
            task_service=self.task_service,
            preset_service=self.preset_service,
            settings_service=self.settings_service,
            calendar_service=self.calendar_service,
            startup_service=self.startup_service,
            settings=self.settings,
        )

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()
        if self._previous_data_dir is None:
            os.environ.pop(paths.DATA_DIR_ENV, None)
        else:
            os.environ[paths.DATA_DIR_ENV] = self._previous_data_dir

    def _add_task(self, **kwargs) -> TaskItem:
        payload = {"title": "喝水", "type": TaskType.DAILY}
        payload.update(kwargs)
        task = TaskItem(**payload)
        self.task_service.create(task)
        return task


class IconRenderTest(UiBaseTestCase):
    def test_app_icon_renders(self):
        from everyday.ui import icons

        icon = icons.app_icon(64)
        self.assertFalse(icon.isNull())
        self.assertFalse(icon.pixmap(64, 64).isNull())

    def test_progress_icon_renders_for_all_ratios(self):
        from everyday.ui import icons

        for ratio in (0.0, 0.25, 0.5, 1.0):
            self.assertFalse(icons.progress_icon(32, ratio).isNull())

    def test_icon_cache_is_reused(self):
        from everyday.ui import icons

        first = icons.app_icon(48)
        second = icons.app_icon(48)
        self.assertIs(first, second)

    def test_style_sheets_are_non_empty(self):
        from everyday.ui import styles

        self.assertIn("QListWidget", styles.MAIN_STYLE)
        self.assertIn("floatRoot", styles.FLOAT_STYLE)


class MainWindowTest(UiBaseTestCase):
    def _window(self):
        from everyday.ui.main_window import MainWindow

        return MainWindow(self.context)

    def test_window_constructs_with_empty_task_list(self):
        window = self._window()
        try:
            self.assertEqual(window.task_list.count(), 1)  # 占位提示行
            self.assertIn("0/0", window.task_title.text())
        finally:
            window.deleteLater()

    def test_window_lists_tasks(self):
        self._add_task(title="喝水")
        self._add_task(title="跑步")
        window = self._window()
        try:
            self.assertEqual(window.task_list.count(), 2)
            self.assertIn("0/2", window.task_title.text())
        finally:
            window.deleteLater()

    def test_checkbox_toggles_completion_in_database(self):
        task = self._add_task(title="喝水")
        window = self._window()
        try:
            row = window.task_list.itemWidget(window.task_list.item(0))
            row.checkbox.setChecked(True)
            reloaded = self.task_service.get(task.id)
            self.assertTrue(reloaded.is_completed)
            self.assertEqual(reloaded.last_completed_date, self.context.today())
        finally:
            window.deleteLater()

    def test_completed_once_task_disappears_from_list(self):
        task = self._add_task(
            title="交报告", type=TaskType.ONCE, due_date=self.context.today()
        )
        window = self._window()
        try:
            self.assertEqual(window.task_list.count(), 1)
            row = window.task_list.itemWidget(window.task_list.item(0))
            row.checkbox.setChecked(True)
            # 单次任务完成后隐藏，因此列表只剩占位行
            self.assertEqual(window.task_list.count(), 1)
            self.assertIn("今天还没有任务", window.task_list.item(0).text())
            self.assertEqual(self.task_service.get(task.id).is_completed, True)
        finally:
            window.deleteLater()

    def test_progress_bar_reflects_completion(self):
        task = self._add_task(title="喝水")
        window = self._window()
        try:
            self.task_service.set_completed(task.id, True, self.context.today())
            window.refresh_tasks()
            self.assertEqual(window.progress_bar.value(), 1)
            self.assertEqual(window.progress_bar.maximum(), 1)
        finally:
            window.deleteLater()

    def test_float_action_syncs_with_context(self):
        """文档 5.2：主窗口开关与托盘菜单的状态必须实时同步。"""
        window = self._window()
        try:
            # 从主窗口取消勾选 → 立刻写回设置
            window.float_action.setChecked(False)
            self.assertFalse(self.context.settings.float_window_visible)
            self.assertFalse(window.float_action.isChecked())

            # 从外部（等价于托盘菜单）打开 → 主窗口开关自动跟随
            self.context.set_float_visible(True)
            self.assertTrue(self.context.settings.float_window_visible)
            self.assertTrue(window.float_action.isChecked())

            # 外部关闭 → 主窗口开关同样跟随
            self.context.set_float_visible(False)
            self.assertFalse(window.float_action.isChecked())
            self.assertFalse(self.context.settings.float_window_visible)
        finally:
            window.deleteLater()

    def test_status_bar_shows_current_data_directory(self):
        """状态栏显示的数据库路径必须与当前数据目录一致（不能是默认目录）。"""
        window = self._window()
        try:
            self.assertIn(str(paths.data_dir()), window.db_label.text())
        finally:
            window.deleteLater()

    def test_calendar_sidebar_shows_events(self):
        from everyday.services.ics_parser import CalendarEvent
        from datetime import datetime

        today = self.context.today()
        self.calendar_service.replace_events(
            [
                CalendarEvent(
                    uid="e1",
                    summary="团队周会",
                    start=datetime(today.year, today.month, today.day, 9, 0),
                    end=datetime(today.year, today.month, today.day, 10, 0),
                )
            ]
        )
        window = self._window()
        try:
            self.assertEqual(window.event_list.count(), 1)
            self.assertIn("团队周会", window.event_list.item(0).text())
            self.assertIn("09:00-10:00", window.event_list.item(0).text())
        finally:
            window.deleteLater()

    def test_close_event_hides_instead_of_quitting(self):
        from PySide6.QtGui import QCloseEvent

        window = self._window()
        try:
            window.show()
            event = QCloseEvent()
            window.closeEvent(event)
            self.assertFalse(event.isAccepted())  # 被拦截
            self.assertFalse(window.isVisible())
        finally:
            window.deleteLater()


class FloatWindowTest(UiBaseTestCase):
    def _window(self):
        from everyday.ui.float_window import FloatWindow

        return FloatWindow(self.context)

    def test_window_constructs_and_lists_tasks(self):
        self._add_task(title="喝水")
        window = self._window()
        try:
            self.assertEqual(window.task_list.count(), 1)
            self.assertIn("1", window.progress_label.text())
        finally:
            window.deleteLater()

    def test_empty_state_placeholder(self):
        window = self._window()
        try:
            self.assertEqual(window.task_list.count(), 1)
            self.assertIn("今天没有任务", window.task_list.item(0).text())
        finally:
            window.deleteLater()

    def test_window_is_frameless_and_always_on_top(self):
        from PySide6.QtCore import Qt

        window = self._window()
        try:
            flags = window.windowFlags()
            self.assertTrue(flags & Qt.FramelessWindowHint)
            self.assertTrue(flags & Qt.WindowStaysOnTopHint)
            self.assertTrue(flags & Qt.Tool)  # 不占任务栏
        finally:
            window.deleteLater()

    def test_position_is_restored_from_settings(self):
        self.context.update_settings(float_window_left=120.0, float_window_top=130.0)
        window = self._window()
        try:
            self.assertEqual((window.pos().x(), window.pos().y()), (120, 130))
        finally:
            window.deleteLater()

    def test_position_roundtrip_persists(self):
        window = self._window()
        try:
            window.move(200, 210)
            window.persist_position()
            self.assertEqual(self.context.settings.float_window_left, 200.0)
            self.assertEqual(self.context.settings.float_window_top, 210.0)
        finally:
            window.deleteLater()

    def test_offscreen_position_falls_back_to_primary_screen(self):
        """记忆坐标落在所有屏幕之外时必须回退，而不是把窗口丢到看不见的地方。"""
        self.context.update_settings(float_window_left=999999.0, float_window_top=999999.0)
        window = self._window()
        try:
            self.assertNotEqual(window.pos().x(), 999999)
            self.assertTrue(window._is_on_any_screen(window.pos()))
        finally:
            window.deleteLater()

    def test_checkbox_toggles_completion(self):
        task = self._add_task(title="喝水")
        window = self._window()
        try:
            row = window.task_list.itemWidget(window.task_list.item(0))
            row.checkbox.setChecked(True)
            self.assertTrue(self.task_service.get(task.id).is_completed)
        finally:
            window.deleteLater()

    def test_close_button_hides_and_updates_setting(self):
        window = self._window()
        try:
            window.show()
            window._on_close_clicked()
            self.assertFalse(window.isVisible())
            self.assertFalse(self.context.settings.float_window_visible)
        finally:
            window.deleteLater()

    def test_calendar_toggle_collapses_footer(self):
        window = self._window()
        try:
            self.assertTrue(window.calendar_label.isVisibleTo(window))
            window._toggle_calendar()
            self.assertFalse(window.calendar_label.isVisible())
            window._toggle_calendar()
            self.assertTrue(window.calendar_label.isVisibleTo(window))
        finally:
            window.deleteLater()


class DialogTest(UiBaseTestCase):
    def test_new_task_dialog_uses_default_date(self):
        """回归测试：新建任务时日期控件必须采用调用方传入的 default_date。

        曾经的缺陷：新建分支提前 return，导致日期被静默忽略、
        落到系统当天（``QDate.currentDate()``）。
        """
        from everyday.ui.task_dialog import TaskDialog

        dialog = TaskDialog(None, default_date=MONDAY)
        try:
            self.assertEqual(dialog.selected_due_date(), MONDAY)
            dialog.title_edit.setText("交报告")
            self.assertEqual(dialog.result_task().due_date, MONDAY)
        finally:
            dialog.deleteLater()

    def test_result_task_requires_title(self):
        """空标题必须在实体层就被拒绝（文档 5.3 校验规则）。"""
        from everyday.ui.task_dialog import TaskDialog

        dialog = TaskDialog(None, default_date=MONDAY)
        try:
            with self.assertRaises(ValueError):
                dialog.result_task()
        finally:
            dialog.deleteLater()

    def test_new_task_dialog_without_default_date_uses_today(self):
        from everyday.ui.task_dialog import TaskDialog

        dialog = TaskDialog(None)
        try:
            self.assertEqual(dialog.selected_due_date(), date.today())
        finally:
            dialog.deleteLater()

    def test_edit_dialog_prefers_task_date_over_default(self):
        from everyday.ui.task_dialog import TaskDialog

        task = TaskItem(title="喝水", type=TaskType.DAILY, due_date=MONDAY)
        dialog = TaskDialog(None, task=task, default_date=date(2026, 12, 31))
        try:
            self.assertEqual(dialog.selected_due_date(), MONDAY)
        finally:
            dialog.deleteLater()

    def test_task_dialog_creates_task_from_fields(self):
        from PySide6.QtCore import QTime

        from everyday.ui.task_dialog import TaskDialog

        dialog = TaskDialog(None, default_date=MONDAY)
        try:
            dialog.title_edit.setText("写周报")
            dialog.description_edit.setPlainText("总结本周")
            dialog.remind_check.setChecked(True)
            dialog.remind_time_edit.setTime(QTime(18, 0))

            task = dialog.result_task()
            self.assertEqual(task.title, "写周报")
            self.assertEqual(task.description, "总结本周")
            self.assertIs(task.type, TaskType.ONCE)
            self.assertEqual(task.due_date, MONDAY)
            self.assertEqual(task.remind_time, time(18, 0))
        finally:
            dialog.deleteLater()

    def test_task_dialog_type_change_updates_date_label(self):
        from everyday.ui.task_dialog import TaskDialog
        from everyday.models.task import TaskType as TT

        dialog = TaskDialog(None, default_date=MONDAY)
        try:
            index = dialog.type_combo.findData(TT.DAILY.value)
            dialog.type_combo.setCurrentIndex(index)
            self.assertIn("参考日期", dialog.due_date_label.text())

            index = dialog.type_combo.findData(TT.ONCE.value)
            dialog.type_combo.setCurrentIndex(index)
            self.assertIn("截止日期", dialog.due_date_label.text())
        finally:
            dialog.deleteLater()

    def test_task_dialog_prefills_when_editing(self):
        from everyday.ui.task_dialog import TaskDialog

        task = TaskItem(
            title="喝水",
            type=TaskType.DAILY,
            remind_time=time(15, 30),
            due_date=MONDAY,
        )
        dialog = TaskDialog(None, task=task)
        try:
            self.assertEqual(dialog.title_edit.text(), "喝水")
            self.assertTrue(dialog.remind_check.isChecked())
            self.assertEqual(dialog.selected_remind_time(), time(15, 30))
        finally:
            dialog.deleteLater()

    def test_task_dialog_rejects_blank_title(self):
        from everyday.ui.task_dialog import TaskDialog

        dialog = TaskDialog(None, default_date=MONDAY)
        try:
            dialog.title_edit.setText("   ")
            self.assertIsNotNone(
                TaskService.validate(dialog.title_edit.text(), dialog.selected_type(), MONDAY)
            )
        finally:
            dialog.deleteLater()

    def test_task_dialog_remind_time_can_be_disabled(self):
        from everyday.ui.task_dialog import TaskDialog

        dialog = TaskDialog(None, default_date=MONDAY)
        try:
            self.assertFalse(dialog.remind_check.isChecked())
            self.assertIsNone(dialog.selected_remind_time())
            dialog.remind_check.setChecked(True)
            self.assertTrue(dialog.remind_time_edit.isEnabled())
        finally:
            dialog.deleteLater()

    def test_preset_dialog_lists_seeded_templates(self):
        from everyday.ui.preset_dialog import PresetDialog

        dialog = PresetDialog(None, service=self.preset_service)
        try:
            self.assertGreaterEqual(dialog.list_widget.count(), 10)
            self.assertIn("共", dialog.hint.text())
        finally:
            dialog.deleteLater()

    def test_preset_dialog_returns_chosen_template(self):
        from everyday.ui.preset_dialog import TEMPLATE_ROLE, PresetDialog

        dialog = PresetDialog(None, service=self.preset_service)
        try:
            # 找到第一个真正的模板行（跳过分类标题行）
            target = None
            for index in range(dialog.list_widget.count()):
                item = dialog.list_widget.item(index)
                if item.data(TEMPLATE_ROLE) is not None:
                    target = item
                    break
            self.assertIsNotNone(target)
            dialog.list_widget.setCurrentItem(target)
            dialog._use_selected()
            self.assertIsNotNone(dialog.chosen_template())
        finally:
            dialog.deleteLater()

    def test_preset_edit_dialog_builds_template(self):
        from everyday.ui.preset_dialog import PresetEditDialog

        dialog = PresetEditDialog(None)
        try:
            dialog.title_edit.setText("晨跑")
            dialog.category_edit.setText("健康")
            dialog.remind_check.setChecked(True)
            template = dialog.result_template()
            self.assertEqual(template.title, "晨跑")
            self.assertEqual(template.category, "健康")
            self.assertIsNotNone(template.remind_time)
        finally:
            dialog.deleteLater()

    def test_settings_dialog_constructs_and_loads_values(self):
        from everyday.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            None,
            context=self.context,
            preset_service=self.preset_service,
            settings_service=self.settings_service,
            calendar_service=self.calendar_service,
            startup_service=self.startup_service,
        )
        try:
            self.assertEqual(dialog.ics_edit.text(), "")
            self.assertEqual(dialog.refresh_spin.value(), 30)
            self.assertEqual(dialog.log_level_combo.currentText(), "INFO")
            self.assertIn("everyday.db", dialog.db_path_label.text())
            self.assertIn("10", dialog.preset_count_label.text())
        finally:
            dialog.deleteLater()

    def test_settings_dialog_saves_changes(self):
        from PySide6.QtWidgets import QDialog

        from everyday.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            None,
            context=self.context,
            preset_service=self.preset_service,
            settings_service=self.settings_service,
            calendar_service=self.calendar_service,
            startup_service=self.startup_service,
        )
        try:
            dialog.ics_edit.setText("D:/cal/work.ics")
            dialog.refresh_spin.setValue(45)
            dialog.float_visible_check.setChecked(False)
            dialog._on_accept()

            stored = self.settings_service.load()
            self.assertEqual(stored.calendar_ics_path, "D:/cal/work.ics")
            self.assertEqual(stored.calendar_refresh_minutes, 45)
            self.assertFalse(stored.float_window_visible)
            self.assertEqual(dialog.result(), QDialog.Accepted)
        finally:
            dialog.deleteLater()


class TrayIconTest(UiBaseTestCase):
    def test_tray_constructs_with_menu_actions(self):
        from everyday.ui.main_window import MainWindow
        from everyday.ui.tray import TrayIcon

        window = MainWindow(self.context)
        tray = TrayIcon(self.context, window)
        try:
            menu = tray.contextMenu()
            self.assertIsNotNone(menu)
            labels = [action.text() for action in menu.actions() if action.text()]
            self.assertIn("显示主窗口", labels)
            self.assertIn("显示悬浮窗", labels)
            self.assertIn("开机自启", labels)
            self.assertIn("退出应用", labels)
        finally:
            tray.hide()
            tray.deleteLater()
            window.deleteLater()

    def test_tray_checkbox_syncs_both_ways(self):
        from everyday.ui.tray import TrayIcon

        tray = TrayIcon(self.context, None)
        try:
            tray.sync_float_visible(False)
            self.assertFalse(tray.float_action.isChecked())
            tray.float_action.setChecked(True)
            self.assertTrue(self.context.settings.float_window_visible)
        finally:
            tray.hide()
            tray.deleteLater()

    def test_tray_progress_tooltip(self):
        from everyday.ui.tray import TrayIcon

        tray = TrayIcon(self.context, None)
        try:
            tray.set_progress_text("3/7")
            self.assertIn("3/7", tray.toolTip())
        finally:
            tray.hide()
            tray.deleteLater()


class ShutdownBehaviourTest(UiBaseTestCase):
    """回归测试：关闭窗口 vs 退出应用必须被区分开。

    曾经的缺陷：主窗口与悬浮窗都在 ``closeEvent`` 里 ``event.ignore()``
    （关闭 = 最小化到托盘）。而 ``QApplication.quit()`` 会先给顶层窗口派发
    close 事件——只要有一个窗口拒绝关闭，事件循环就永不退出，
    用户点托盘「退出应用」时程序纹丝不动。

    这个缺陷无法靠静态审查发现，是靠 ``tools/smoke_startup.py``
    真实跑一遍启动/退出流程才暴露出来的。
    """

    def _windows(self):
        from everyday.ui.float_window import FloatWindow
        from everyday.ui.main_window import MainWindow

        return MainWindow(self.context), FloatWindow(self.context)

    def test_close_is_ignored_during_normal_use(self):
        from PySide6.QtGui import QCloseEvent

        window, float_window = self._windows()
        try:
            for target in (window, float_window):
                event = QCloseEvent()
                target.closeEvent(event)
                self.assertFalse(event.isAccepted(), f"{type(target).__name__} 应拦截关闭")
        finally:
            window.deleteLater()
            float_window.deleteLater()

    def test_close_is_accepted_while_shutting_down(self):
        from PySide6.QtGui import QCloseEvent

        window, float_window = self._windows()
        try:
            self.context.begin_shutdown()
            for target in (window, float_window):
                event = QCloseEvent()
                target.closeEvent(event)
                self.assertTrue(
                    event.isAccepted(),
                    f"退出过程中 {type(target).__name__} 必须放行关闭，否则退出会被卡住",
                )
        finally:
            window.deleteLater()
            float_window.deleteLater()

    def test_shutting_down_flag_becomes_true(self):
        self.assertFalse(self.context.shutting_down)
        self.context.begin_shutdown()
        self.assertTrue(self.context.shutting_down)

    def test_application_quit_sets_shutting_down_flag(self):
        """``Application._quit`` 必须先置标志再退出，否则窗口会拒绝关闭。"""
        from everyday.app import Application, Options

        application = Application(Options())
        application._qt_app = self.qt_app
        application._context = self.context
        try:
            self.assertFalse(self.context.shutting_down)
            application._quit()
            self.assertTrue(self.context.shutting_down)
        finally:
            # 事件循环此时并未运行，quit() 不会真的结束任何东西
            pass


class AppliedStyleTest(UiBaseTestCase):
    def test_main_style_applies_without_error(self):
        from everyday.ui import styles

        self.qt_app.setStyleSheet(styles.MAIN_STYLE)
        self.assertIsNotNone(self.qt_app.styleSheet())


if __name__ == "__main__":
    unittest.main()

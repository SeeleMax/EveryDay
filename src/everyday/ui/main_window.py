"""主窗口（文档 6.1）。

三个区域：顶部工具栏、中部任务列表、右侧日历侧栏。
关闭窗口默认最小化到托盘，不退出应用（文档第七章）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from everyday import APP_DISPLAY_NAME, __version__, paths
from everyday.models.task import TaskItem, TaskType
from everyday.services.dependency_service import DependencyCycleError
from everyday.ui import icons
from everyday.ui.context import AppContext
from everyday.ui.preset_dialog import PresetDialog
from everyday.ui.settings_dialog import SettingsDialog
from everyday.ui.task_dialog import TaskDialog
from everyday.ui.widgets import TaskRow

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """应用主窗口。"""

    #: 请求托盘/悬浮窗显隐同步
    float_toggle_requested = Signal(bool)

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        #: 由 :meth:`attach_tray` 注入，关闭时用于气泡提示
        self._tray = None

        self.setWindowTitle(f"{APP_DISPLAY_NAME} v{__version__}")
        self.setWindowIcon(icons.app_icon())
        self.resize(880, 560)
        self.setMinimumSize(680, 420)

        self._build_toolbar()
        self._build_central()
        self._build_statusbar()
        self._connect_context()
        self.refresh_all()

    # ==================================================================
    # 界面构建
    # ==================================================================
    def _build_toolbar(self) -> None:
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.new_action = QAction("新建任务", self)
        self.new_action.setShortcut("Ctrl+N")
        self.new_action.triggered.connect(self.create_task)
        toolbar.addAction(self.new_action)

        self.preset_action = QAction("从模板新建", self)
        self.preset_action.triggered.connect(self.create_from_template)
        toolbar.addAction(self.preset_action)

        toolbar.addSeparator()

        self.float_action = QAction("悬浮窗", self)
        self.float_action.setCheckable(True)
        self.float_action.setChecked(self._context.settings.float_window_visible)
        self.float_action.toggled.connect(self._on_float_toggled)
        toolbar.addAction(self.float_action)

        self.settings_action = QAction("设置", self)
        self.settings_action.triggered.connect(self.open_settings)
        toolbar.addAction(self.settings_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        toolbar.addSeparator()
        self.refresh_action = QAction("刷新", self)
        self.refresh_action.setShortcut("F5")
        self.refresh_action.triggered.connect(self.refresh_all)
        toolbar.addAction(self.refresh_action)

    def _build_central(self) -> None:
        splitter = QSplitter(Qt.Horizontal)

        # ---- 左：今日任务 ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 6, 12)
        left_layout.setSpacing(8)

        self.task_title = QLabel("今日任务")
        self.task_title.setObjectName("sectionTitle")
        left_layout.addWidget(self.task_title)

        self.task_list = QListWidget()
        self.task_list.setSelectionMode(QListWidget.NoSelection)
        self.task_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        left_layout.addWidget(self.task_list, 1)
        splitter.addWidget(left)

        # ---- 右：今日日程 ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 12, 12, 12)
        right_layout.setSpacing(8)

        self.calendar_title = QLabel("今日日程")
        self.calendar_title.setObjectName("sectionTitle")
        right_layout.addWidget(self.calendar_title)

        self.event_list = QListWidget()
        self.event_list.setSelectionMode(QListWidget.NoSelection)
        right_layout.addWidget(self.event_list, 1)

        self.calendar_hint = QLabel("")
        self.calendar_hint.setObjectName("hint")
        self.calendar_hint.setWordWrap(True)
        right_layout.addWidget(self.calendar_hint)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)

    def _build_statusbar(self) -> None:
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(200)
        self.progress_bar.setTextVisible(True)
        self.statusBar().addPermanentWidget(self.progress_bar)

        self.db_label = QLabel(str(paths.database_path()))
        self.db_label.setObjectName("hint")
        self.db_label.setToolTip("数据库文件位置（数据与应用安装目录分离）")
        self.statusBar().addPermanentWidget(self.db_label)

        self.statusBar().showMessage("就绪")

    def _connect_context(self) -> None:
        self._context.tasks_changed.connect(self.refresh_tasks)
        self._context.calendar_changed.connect(self.refresh_calendar)
        self._context.settings_changed.connect(self._on_settings_changed)
        self._context.float_visibility_changed.connect(self._sync_float_action)

    # ==================================================================
    # 刷新
    # ==================================================================
    def refresh_all(self) -> None:
        """刷新任务列表、日程与进度。"""
        self.refresh_tasks()
        self.refresh_calendar()

    def refresh_tasks(self) -> None:
        """重建任务列表。"""
        day = self._context.today()
        tasks = self._context.today_tasks()
        progress = self._context.progress()

        self.task_list.clear()
        if not tasks:
            placeholder = QListWidgetItem("今天还没有任务，点击工具栏「新建任务」开始吧")
            placeholder.setFlags(Qt.NoItemFlags)
            self.task_list.addItem(placeholder)
        else:
            for task in tasks:
                self._append_task_row(task, day)

        self.task_title.setText(f"今日任务（{progress.label}）")
        self._apply_progress(progress)
        self.statusBar().showMessage(f"共 {len(tasks)} 项今日任务", 4000)

    def _append_task_row(self, task: TaskItem, day) -> None:
        item = QListWidgetItem()
        row = TaskRow(task, day, parent=self.task_list)
        row.completion_changed.connect(self.on_completion_changed)
        row.edit_requested.connect(self.edit_task)
        row.delete_requested.connect(self.delete_task)
        item.setSizeHint(row.sizeHint())
        self.task_list.addItem(item)
        self.task_list.setItemWidget(item, row)

    def refresh_calendar(self) -> None:
        """重建今日日程列表。"""
        events = self._context.today_events()
        self.event_list.clear()

        if not events:
            self.calendar_title.setText("今日日程")
            if self._context.calendar_service.last_error:
                self.calendar_hint.setText(
                    f"数据源加载失败：{self._context.calendar_service.last_error}"
                )
            elif not self._context.settings.calendar_ics_path:
                self.calendar_hint.setText("尚未配置日历数据源，可在「设置」中填入 .ics 文件或订阅地址。")
            else:
                self.calendar_hint.setText("今天没有日程。")
            return

        self.calendar_title.setText(f"今日日程（{len(events)}）")
        for event in events:
            label = f"{event.time_label}   {event.summary}"
            if event.location:
                label += f"   @ {event.location}"
            item = QListWidgetItem(label)
            item.setToolTip(event.description or event.summary)
            self.event_list.addItem(item)
        self.calendar_hint.setText("数据源：实时日历（自动刷新）")

    def _apply_progress(self, progress) -> None:
        self.progress_bar.setMaximum(max(1, progress.total))
        self.progress_bar.setValue(min(progress.completed, max(1, progress.total)))
        self.progress_bar.setFormat(f"今日完成 {progress.label}")

    def _on_settings_changed(self) -> None:
        self._sync_float_action(self._context.settings.float_window_visible)
        self.refresh_all()

    def _sync_float_action(self, visible: bool) -> None:
        self.float_action.blockSignals(True)
        self.float_action.setChecked(visible)
        self.float_action.blockSignals(False)

    # ==================================================================
    # 操作
    # ==================================================================
    def create_task(self) -> None:
        """新建任务。"""
        dialog = TaskDialog(self, default_date=self._context.today(), service=self._context.task_service)
        if dialog.exec() != TaskDialog.Accepted:
            return
        try:
            task = dialog.result_task()
            # v1.1 P2：标签与前置依赖一并写入
            self._context.task_service.save_with_details(
                task,
                tags=dialog.selected_tags(),
                depends_on=dialog.selected_dependencies(),
            )
        except DependencyCycleError as exc:
            QMessageBox.warning(self, "依赖冲突", str(exc))
            return
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self._context.notify_tasks_changed()

    def create_from_template(self) -> None:
        """从模板新建任务（文档 5.7）。"""
        picker = PresetDialog(self, service=self._context.preset_service)
        if picker.exec() != PresetDialog.Accepted:
            return
        template = picker.chosen_template()
        if template is None:
            return

        draft = self._context.preset_service.instantiate(
            template.id, day=self._context.today()
        )
        if draft is None:
            QMessageBox.warning(self, "无法创建", "模板已不存在，请刷新后重试。")
            return

        dialog = TaskDialog(self, task=draft, service=self._context.task_service)
        if dialog.exec() != TaskDialog.Accepted:
            return
        try:
            task = dialog.result_task(draft)
            self._context.task_service.save_with_details(
                task,
                tags=dialog.selected_tags(),
                depends_on=dialog.selected_dependencies(),
            )
        except DependencyCycleError as exc:
            QMessageBox.warning(self, "依赖冲突", str(exc))
            return
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self._context.notify_tasks_changed()

    def edit_task(self, task_id: int) -> None:
        """编辑任务（文档 5.3）。"""
        task = self._context.task_service.get(task_id)
        if task is None:
            QMessageBox.warning(self, "无法编辑", "任务已不存在，请刷新后重试。")
            self.refresh_tasks()
            return

        dialog = TaskDialog(self, task=task, service=self._context.task_service)
        if dialog.exec() != TaskDialog.Accepted:
            return
        try:
            dialog.result_task(task)
            # v1.1 P2：标签与前置依赖一并保存
            self._context.task_service.save_with_details(
                task,
                tags=dialog.selected_tags(),
                depends_on=dialog.selected_dependencies(),
            )
        except DependencyCycleError as exc:
            QMessageBox.warning(self, "依赖冲突", str(exc))
            return
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self._context.notify_tasks_changed()

    def delete_task(self, task_id: int) -> None:
        """删除任务，删除前确认（文档 5.3）。"""
        task = self._context.task_service.get(task_id)
        if task is None:
            return
        confirmed = QMessageBox.question(
            self,
            "删除任务",
            f"确定要删除任务「{task.title}」吗？\n\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmed != QMessageBox.Yes:
            return
        self._context.task_service.delete(task_id)
        self._context.notify_tasks_changed()

    def on_completion_changed(self, task_id: int, completed: bool) -> None:
        """勾选框切换（文档 5.4）。"""
        task = self._context.task_service.get(task_id)
        if task is None:
            return
        self._context.task_service.set_completed(task_id, completed, self._context.today())
        if task.type is TaskType.ONCE and completed:
            self.statusBar().showMessage(f"已完成「{task.title}」", 4000)
        self._context.notify_tasks_changed()

    def open_settings(self) -> None:
        """打开设置页。"""
        dialog = SettingsDialog(
            self,
            context=self._context,
            preset_service=self._context.preset_service,
            settings_service=self._context.settings_service,
            calendar_service=self._context.calendar_service,
            startup_service=self._context.startup_service,
        )
        if dialog.exec() == SettingsDialog.Accepted:
            self.refresh_all()

    def _on_float_toggled(self, visible: bool) -> None:
        self._context.set_float_visible(visible)
        self.float_toggle_requested.emit(visible)

    # ==================================================================
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt 命名约定
        """关闭主窗口时最小化到托盘，而不是退出应用。

        例外：应用正在退出（托盘「退出应用」或系统注销/关机）时必须放行，
        否则 ``QApplication.quit()`` 会被这个被拒绝的 close 事件卡住，
        用户会发现程序退不掉。
        """
        if self._context.shutting_down or QApplication.closingDown():
            event.accept()
            return

        event.ignore()
        self.hide()
        logger.info("主窗口已最小化到托盘")
        if self._tray is not None:
            self._tray.notify("EveryDay 仍在运行", "已最小化到托盘，双击托盘图标可重新打开")
        self.statusBar().showMessage("已最小化到托盘，双击托盘图标可重新打开", 3000)

    def attach_tray(self, tray) -> None:
        """注入托盘图标，用于关闭时给出气泡反馈。"""
        self._tray = tray

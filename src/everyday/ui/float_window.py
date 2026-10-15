"""桌面悬浮窗（文档 5.2、6.2）。

窗口属性：无边框、透明背景、始终置顶、不显示在任务栏、不可调整大小。
外观：圆角矩形半透明背景，顶部为日期与完成进度，中部为任务列表，
底部为可折叠的日历区。
拖动：在空白处按住左键拖动整个窗口；位置在移动结束时记录，下次启动恢复。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from everyday.ui import icons, styles
from everyday.ui.context import AppContext
from everyday.ui.widgets import TaskRow

logger = logging.getLogger(__name__)

#: 悬浮窗默认尺寸
DEFAULT_WIDTH = 300
DEFAULT_HEIGHT = 380


class FloatWindow(QWidget):
    """桌面置顶的任务悬浮窗。"""

    #: 用户点击关闭按钮（与主窗口开关、托盘菜单联动）
    visibility_change_requested = Signal(bool)

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._drag_offset: QPoint | None = None
        self._calendar_expanded = True

        self.setWindowTitle("EveryDay 悬浮窗")
        self.setWindowIcon(icons.app_icon())
        # Qt.Tool 让窗口不出现在任务栏与 Alt+Tab 中
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(DEFAULT_WIDTH)

        self._build_ui()
        self._connect_context()
        self.restore_position()
        self.refresh_all()

    # ==================================================================
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        root = QFrame()
        root.setObjectName("floatRoot")
        root.setStyleSheet(styles.FLOAT_STYLE)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # ---- 头部：日期 + 进度 + 关闭 ----
        header = QHBoxLayout()
        self.date_label = QLabel("")
        self.date_label.setObjectName("floatHeader")
        header.addWidget(self.date_label)
        header.addStretch(1)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("floatProgress")
        header.addWidget(self.progress_label)

        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("floatClose")
        self.close_button.setFixedSize(20, 20)
        self.close_button.setToolTip("隐藏悬浮窗（主窗口仍可用）")
        self.close_button.clicked.connect(self._on_close_clicked)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        # ---- 中部：任务列表 ----
        self.task_list = QListWidget()
        self.task_list.setObjectName("floatList")
        self.task_list.setSelectionMode(QListWidget.NoSelection)
        self.task_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.task_list.setMinimumHeight(140)
        layout.addWidget(self.task_list, 1)

        # ---- 底部：日历区（可折叠）----
        self.calendar_toggle = QPushButton("日程 ▾")
        self.calendar_toggle.setObjectName("floatClose")
        self.calendar_toggle.setFixedHeight(20)
        self.calendar_toggle.clicked.connect(self._toggle_calendar)
        layout.addWidget(self.calendar_toggle)

        self.calendar_label = QLabel("")
        self.calendar_label.setObjectName("floatFooter")
        self.calendar_label.setWordWrap(True)
        layout.addWidget(self.calendar_label)

    def _connect_context(self) -> None:
        self._context.tasks_changed.connect(self.refresh_tasks)
        self._context.calendar_changed.connect(self.refresh_calendar)
        self._context.settings_changed.connect(self.refresh_all)

    # ==================================================================
    # 刷新
    # ==================================================================
    def refresh_all(self) -> None:
        """刷新日期、任务与日程。"""
        day = self._context.today()
        self.date_label.setText(f"{day.month} 月 {day.day} 日")
        self.refresh_tasks()
        self.refresh_calendar()

    def refresh_tasks(self) -> None:
        """重建紧凑任务列表。"""
        day = self._context.today()
        tasks = self._context.today_tasks()
        progress = self._context.progress()

        self.task_list.clear()
        if not tasks:
            placeholder = QListWidgetItem("今天没有任务")
            placeholder.setFlags(Qt.NoItemFlags)
            self.task_list.addItem(placeholder)
        else:
            for task in tasks:
                item = QListWidgetItem()
                row = TaskRow(task, day, compact=True, parent=self.task_list)
                row.completion_changed.connect(self._on_completion_changed)
                item.setSizeHint(row.sizeHint())
                self.task_list.addItem(item)
                self.task_list.setItemWidget(item, row)

        self.progress_label.setText(f" {progress.label} ")
        self.adjustSize()

    def refresh_calendar(self) -> None:
        """刷新底部日程区（文档 5.2：可选显示最近一条日程）。"""
        if not self._calendar_expanded:
            self.calendar_label.setVisible(False)
            return
        self.calendar_label.setVisible(True)

        event = self._context.calendar_service.next_event()
        if event is None:
            if self._context.calendar_service.last_error:
                self.calendar_label.setText("日程加载失败，已保留原有数据")
            else:
                self.calendar_label.setText("今日暂无日程")
            return
        prefix = "今天" if event.start_date == self._context.today() else event.start_date.isoformat()
        self.calendar_label.setText(f"{prefix} {event.time_label} {event.summary}")

    def _toggle_calendar(self) -> None:
        self._calendar_expanded = not self._calendar_expanded
        self.calendar_toggle.setText("日程 ▾" if self._calendar_expanded else "日程 ▸")
        self.refresh_calendar()
        self.adjustSize()

    def _on_completion_changed(self, task_id: int, completed: bool) -> None:
        self._context.task_service.set_completed(
            task_id, completed, self._context.today()
        )
        self._context.notify_tasks_changed()

    # ==================================================================
    # 位置记忆与跨屏回退（文档 5.2）
    # ==================================================================
    def restore_position(self) -> None:
        """恢复上次位置；若坐标已不在任何屏幕内则回退到主屏。"""
        settings = self._context.settings
        candidate = QPoint(int(settings.float_window_left), int(settings.float_window_top))
        if self._is_on_any_screen(candidate):
            self.move(candidate)
            return
        logger.info("悬浮窗记忆位置已不在任何屏幕内，回退到主屏")
        self._move_to_primary_screen()

    def _is_on_any_screen(self, point: QPoint) -> bool:
        """判断窗口左上角是否落在某个屏幕的可用区域内。"""
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().contains(point):
                return True
        return False

    def _move_to_primary_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.left() + 40, area.top() + 40)

    def _persist_position(self) -> None:
        """把当前位置写回设置。"""
        position = self.pos()
        settings = self._context.settings
        if (
            int(settings.float_window_left) == position.x()
            and int(settings.float_window_top) == position.y()
        ):
            return
        self._context.update_settings(
            float_window_left=float(position.x()), float_window_top=float(position.y())
        )

    def persist_position(self) -> None:
        """对外暴露的位置持久化入口（退出流程使用）。"""
        self._persist_position()

    # ==================================================================
    # 拖动
    # ==================================================================
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """按住左键开始拖动。"""
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """拖动窗口。"""
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """结束拖动并记录位置。"""
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self._persist_position()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ==================================================================
    def _on_close_clicked(self) -> None:
        """关闭按钮 = 隐藏悬浮窗（文档 6.2：无标题栏，仅保留关闭按钮）。"""
        self.hide()
        self._persist_position()
        self._context.set_float_visible(False)
        self.visibility_change_requested.emit(False)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """拦截关闭事件，改为隐藏，避免悬浮窗被销毁后无法再次显示。

        例外：应用正在退出时必须放行——否则 ``QApplication.quit()``
        会因为存在拒绝关闭的顶层窗口而无法结束事件循环。
        """
        if self._context.shutting_down or QApplication.closingDown():
            event.accept()
            return
        event.ignore()
        self._on_close_clicked()

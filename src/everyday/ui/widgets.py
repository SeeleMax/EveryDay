"""可复用的列表行控件。

主窗口与悬浮窗共用 :class:`TaskRow`：主窗口显示完整信息与操作按钮，
悬浮窗使用 ``compact=True`` 的紧凑形态（只有勾选框与标题）。
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from everyday.models.task import TaskItem
from everyday.ui import styles


class TaskRow(QWidget):
    """一行任务。

    发出的信号都带任务 ``id``，由父窗口去调用服务层，
    控件本身不碰数据库。
    """

    completion_changed = Signal(int, bool)
    edit_requested = Signal(int)
    delete_requested = Signal(int)

    def __init__(
        self,
        task: TaskItem,
        day: date,
        *,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._task = task
        self._compact = compact
        self._syncing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6 if compact else 8, 4, 6 if compact else 8, 4)
        layout.setSpacing(8)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(task.is_completed)
        self.checkbox.toggled.connect(self._on_toggled)
        layout.addWidget(self.checkbox)

        self.title_label = QLabel(task.title)
        self.title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.title_label.setToolTip(self._tooltip_text(task, day))
        layout.addWidget(self.title_label, 1)

        if task.remind_time is not None:
            time_label = QLabel(task.remind_time.strftime("%H:%M"))
            time_label.setObjectName("hint")
            layout.addWidget(time_label)

        if not compact:
            self.edit_button = QPushButton("编辑")
            self.edit_button.setFixedWidth(56)
            self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self._task.id))
            layout.addWidget(self.edit_button)

            self.delete_button = QPushButton("删除")
            self.delete_button.setObjectName("dangerButton")
            self.delete_button.setFixedWidth(56)
            self.delete_button.clicked.connect(
                lambda: self.delete_requested.emit(self._task.id)
            )
            layout.addWidget(self.delete_button)

        self.apply_state(task, day)

    # ------------------------------------------------------------------
    @property
    def task_id(self) -> int:
        """本行对应的任务主键。"""
        return self._task.id

    def apply_state(self, task: TaskItem, day: date) -> None:
        """按任务当前状态刷新视觉（删除线、超期警示）。"""
        self._task = task
        font = QFont(self.title_label.font())
        font.setStrikeOut(task.is_completed)
        self.title_label.setFont(font)

        if task.is_overdue_on(day):
            self.title_label.setStyleSheet(f"color: {styles.OVERDUE};")
        elif task.is_completed:
            self.title_label.setStyleSheet(styles.DISABLED_TEXT)
        else:
            self.title_label.setStyleSheet("")

        self.title_label.setToolTip(self._tooltip_text(task, day))

        # 程序化回写勾选状态时不要触发业务回调
        self._syncing = True
        self.checkbox.setChecked(task.is_completed)
        self._syncing = False

    def set_completion_enabled(self, enabled: bool, reason: str = "") -> None:
        """控制勾选框可用性（v1.1 的前置任务未完成置灰会用到）。"""
        self.checkbox.setEnabled(enabled)
        self.checkbox.setToolTip(reason)

    @staticmethod
    def _tooltip_text(task: TaskItem, day: date) -> str:
        parts = [f"类型：{task.type.label}"]
        if task.due_date is not None:
            parts.append(f"日期:{task.due_date.isoformat()}")
        if task.is_overdue_on(day):
            parts.append("状态：已超期")
        elif task.is_completed:
            parts.append("状态：已完成")
        if task.description:
            parts.append(f"描述：{task.description}")
        return "\n".join(parts)

    def _on_toggled(self, checked: bool) -> None:
        if self._syncing or self._task.id is None:
            return
        self.completion_changed.emit(self._task.id, checked)

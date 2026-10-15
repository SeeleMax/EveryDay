"""任务编辑对话框（文档 6.3）。

字段：标题、描述、任务类型、截止/参考日期、提醒时间。
创建与编辑共用同一个对话框，编辑时字段预填充。
"""

from __future__ import annotations

from datetime import date, time

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from everyday.models.task import MAX_TITLE_LENGTH, TaskItem, TaskType
from everyday.services.task_service import TaskService

#: 默认提醒时间
DEFAULT_REMIND_TIME = time(9, 0)


class TaskDialog(QDialog):
    """新建/编辑任务的模态对话框。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        task: TaskItem | None = None,
        default_date: date | None = None,
    ) -> None:
        super().__init__(parent)
        self._editing = task is not None
        self.setWindowTitle("编辑任务" if self._editing else "新建任务")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._build_ui()
        self._populate(task, default_date)
        self._sync_type_visibility()

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(10)

        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(MAX_TITLE_LENGTH)
        self.title_edit.setPlaceholderText("例如：写工作日报")
        form.addRow("标题 *", self.title_edit)

        self.description_edit = QTextEdit()
        self.description_edit.setPlaceholderText("可留空")
        self.description_edit.setFixedHeight(70)
        form.addRow("描述", self.description_edit)

        self.type_combo = QComboBox()
        for task_type in TaskType:
            self.type_combo.addItem(task_type.label, task_type.value)
        self.type_combo.currentIndexChanged.connect(self._sync_type_visibility)
        form.addRow("任务类型", self.type_combo)

        self.due_date_label = QLabel("截止日期 *")
        self.due_date_edit = QDateEdit()
        self.due_date_edit.setCalendarPopup(True)
        self.due_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.due_date_edit.setDate(QDate.currentDate())
        form.addRow(self.due_date_label, self.due_date_edit)

        remind_row = QHBoxLayout()
        self.remind_check = QCheckBox("提醒")
        self.remind_check.toggled.connect(self._sync_remind_enabled)
        self.remind_time_edit = QTimeEdit()
        self.remind_time_edit.setDisplayFormat("HH:mm")
        self.remind_time_edit.setTime(QTime(DEFAULT_REMIND_TIME.hour, DEFAULT_REMIND_TIME.minute))
        remind_row.addWidget(self.remind_check)
        remind_row.addWidget(self.remind_time_edit, 1)
        remind_container = QWidget()
        remind_container.setLayout(remind_row)
        form.addRow("提醒时间", remind_container)

        layout.addLayout(form)

        hint = QLabel(
            "单次任务必须设置截止日期；每周/每月任务需要参考日期以确定在哪一天显示。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # 数据填充与联动
    # ------------------------------------------------------------------
    def _populate(self, task: TaskItem | None, default_date: date | None) -> None:
        """填充字段。

        ``task`` 为 ``None`` 表示新建，此时日期取 ``default_date``（缺省为今天）。
        注意日期必须先无条件写入：早期的实现在新建分支里提前返回，
        导致调用方传入的 ``default_date`` 被静默忽略。
        """
        if task is not None and task.due_date is not None:
            target_date = task.due_date
        else:
            target_date = default_date or date.today()
        self.due_date_edit.setDate(
            QDate(target_date.year, target_date.month, target_date.day)
        )

        if task is None:
            self._sync_remind_enabled(False)
            return

        self.title_edit.setText(task.title)
        self.description_edit.setPlainText(task.description or "")
        index = self.type_combo.findData(task.type.value)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)
        if task.remind_time is not None:
            self.remind_check.setChecked(True)
            self.remind_time_edit.setTime(
                QTime(task.remind_time.hour, task.remind_time.minute)
            )
        else:
            self._sync_remind_enabled(False)

    def _sync_remind_enabled(self, enabled: bool) -> None:
        self.remind_time_edit.setEnabled(enabled)

    def _sync_type_visibility(self) -> None:
        """四类任务都需要一个日期，但语义不同，切换类型时同步标签文案。"""
        task_type = self.selected_type()
        self.due_date_label.setText(
            "截止日期 *" if task_type is TaskType.ONCE else "参考日期 *"
        )

    # ------------------------------------------------------------------
    # 取值
    # ------------------------------------------------------------------
    def selected_type(self) -> TaskType:
        """当前选中的任务类型。"""
        return TaskType.from_value(self.type_combo.currentData())

    def selected_due_date(self) -> date:
        """当前选中的日期（``QDate`` 转 :class:`datetime.date`）。"""
        return self.due_date_edit.date().toPython()

    def selected_remind_time(self) -> time | None:
        """当前选中的提醒时间；未勾选时为 ``None``。"""
        if not self.remind_check.isChecked():
            return None
        return self.remind_time_edit.time().toPython()

    def result_task(self, base: TaskItem | None = None) -> TaskItem:
        """把对话框内容写回 :class:`TaskItem`。

        ``base`` 为 ``None`` 时新建对象，否则就地更新已有对象并重跑校验。
        """
        values = {
            "title": self.title_edit.text().strip(),
            "description": self.description_edit.toPlainText().strip() or None,
            "type": self.selected_type(),
            "due_date": self.selected_due_date(),
            "remind_time": self.selected_remind_time(),
        }
        if base is None:
            return TaskItem(**values)
        for key, value in values.items():
            setattr(base, key, value)
        base.__post_init__()
        return base

    # ------------------------------------------------------------------
    def _on_accept(self) -> None:
        task_type = self.selected_type()
        title = self.title_edit.text().strip()
        problem = TaskService.validate(title, task_type, self.selected_due_date())
        if problem:
            QMessageBox.warning(self, "无法保存", problem)
            self.title_edit.setFocus()
            return
        self.accept()

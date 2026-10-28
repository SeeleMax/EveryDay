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
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from everyday.models.task import MAX_TITLE_LENGTH, TaskItem, TaskPriority, TaskType
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
        service: TaskService | None = None,
    ) -> None:
        super().__init__(parent)
        self._editing = task is not None
        #: v1.1 P2：用于取标签、父任务候选与前置依赖候选。
        #: 为 ``None`` 时 P2 的那几个字段留空（单元测试可以不传）。
        self._service = service
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

        # ---- v1.1 P2：优先级 / 标签 / 父任务 / 前置依赖 ----
        self.priority_combo = QComboBox()
        for priority in TaskPriority:
            self.priority_combo.addItem(priority.label, int(priority))
        form.addRow("优先级", self.priority_combo)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("用逗号或空格分隔，例如：工作, 本周")
        form.addRow("标签", self.tags_edit)

        self.parent_combo = QComboBox()
        form.addRow("父任务", self.parent_combo)

        self.dependency_list = QListWidget()
        self.dependency_list.setMaximumHeight(110)
        form.addRow("前置依赖", self.dependency_list)

        layout.addLayout(form)

        hint = QLabel(
            "单次任务必须设置截止日期；每周/每月任务需要参考日期以确定在哪一天显示。\n"
            "勾选「前置依赖」后，这些任务未完成时当前任务的勾选框会置灰。"
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
            self._populate_v11(None)
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
        self._populate_v11(task)

    # ------------------------------------------------------------------
    # v1.1 P2：优先级 / 标签 / 父任务 / 前置依赖
    # ------------------------------------------------------------------
    def _populate_v11(self, task: TaskItem | None) -> None:
        """填充 P2 新增的四个字段。

        「父任务」与「前置依赖」的候选来自 ``service.list_all()``：
        排除自己（不能当自己的父任务或前置），前置依赖另外排除
        已软删除的任务。
        """
        priority = task.priority if task is not None else TaskPriority.NORMAL
        index = self.priority_combo.findData(int(priority))
        if index >= 0:
            self.priority_combo.setCurrentIndex(index)

        self.parent_combo.clear()
        self.parent_combo.addItem("（无，作为顶层任务）", None)
        self.dependency_list.clear()

        if self._service is None:
            # 没有服务时只留「无父任务」，标签/依赖字段不可用
            self.tags_edit.setEnabled(False)
            self.parent_combo.setEnabled(False)
            self.dependency_list.setEnabled(False)
            return

        tags = (
            self._service.tags_for(task.id) if task is not None and task.id else []
        )
        self.tags_edit.setText(", ".join(tags))

        candidates = [t for t in self._service.list_all() if t.id is not None]
        if task is not None and task.id is not None:
            candidates = [t for t in candidates if t.id != task.id]

        for candidate in candidates:
            self.parent_combo.addItem(candidate.title, candidate.id)
        if task is not None and task.parent_task_id is not None:
            found = self.parent_combo.findData(task.parent_task_id)
            if found >= 0:
                self.parent_combo.setCurrentIndex(found)

        selected = (
            set(self._service.dependencies_of(task.id))
            if task is not None and task.id
            else set()
        )
        for candidate in candidates:
            item = QListWidgetItem(candidate.title)
            item.setData(Qt.UserRole, candidate.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if candidate.id in selected else Qt.Unchecked)
            self.dependency_list.addItem(item)

    def selected_priority(self) -> TaskPriority:
        """当前选中的优先级。"""
        return TaskPriority.from_value(self.priority_combo.currentData())

    def selected_tags(self) -> list[str]:
        """解析标签输入框（逗号、顿号、空格都当分隔符，去重保序）。"""
        raw = self.tags_edit.text().replace("、", ",").replace("，", ",")
        for separator in (" ", "\t"):
            raw = raw.replace(separator, ",")
        tags: list[str] = []
        for piece in raw.split(","):
            name = piece.strip()
            if name and name not in tags:
                tags.append(name)
        return tags

    def selected_parent_id(self) -> int | None:
        """选中的父任务 id；顶层任务为 ``None``。"""
        return self.parent_combo.currentData()

    def selected_dependencies(self) -> list[int]:
        """勾选的前置依赖 id 列表。"""
        chosen: list[int] = []
        for row in range(self.dependency_list.count()):
            item = self.dependency_list.item(row)
            if item.checkState() == Qt.Checked:
                chosen.append(int(item.data(Qt.UserRole)))
        return chosen

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
            # v1.1 P2
            "priority": self.selected_priority(),
            "parent_task_id": self.selected_parent_id(),
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

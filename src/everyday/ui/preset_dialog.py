"""预设模板对话框（文档 5.7）。

同时承担两个职责，因为它们本质是同一份列表的两种用法：

* 「从模板新建」——选择模板一键填充
* 「模板管理」——在设置页新增、编辑、删除模板
"""

from __future__ import annotations

from datetime import time

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from everyday.models.preset import DEFAULT_CATEGORY, PresetTemplate
from everyday.models.task import MAX_TITLE_LENGTH, TaskType
from everyday.services.preset_service import PresetService

#: 用于在列表项上挂载模板对象
TEMPLATE_ROLE = Qt.UserRole + 1


class PresetEditDialog(QDialog):
    """新增/编辑单个模板。"""

    def __init__(
        self, parent: QWidget | None = None, template: PresetTemplate | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑模板" if template else "新建模板")
        self.setModal(True)
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        form = QFormLayout()
        form.setSpacing(10)

        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(MAX_TITLE_LENGTH)
        form.addRow("标题 *", self.title_edit)

        self.type_combo = QComboBox()
        for task_type in TaskType:
            self.type_combo.addItem(task_type.label, task_type.value)
        form.addRow("任务类型", self.type_combo)

        remind_row = QHBoxLayout()
        self.remind_check = QCheckBox("默认提醒")
        self.remind_time_edit = QTimeEdit()
        self.remind_time_edit.setDisplayFormat("HH:mm")
        self.remind_time_edit.setTime(QTime(9, 0))
        self.remind_check.toggled.connect(self.remind_time_edit.setEnabled)
        remind_row.addWidget(self.remind_check)
        remind_row.addWidget(self.remind_time_edit, 1)
        container = QWidget()
        container.setLayout(remind_row)
        form.addRow("提醒时间", container)

        self.category_edit = QLineEdit()
        self.category_edit.setPlaceholderText(DEFAULT_CATEGORY)
        form.addRow("分类", self.category_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if template is not None:
            self.title_edit.setText(template.title)
            index = self.type_combo.findData(template.type.value)
            if index >= 0:
                self.type_combo.setCurrentIndex(index)
            if template.remind_time is not None:
                self.remind_check.setChecked(True)
                self.remind_time_edit.setTime(
                    QTime(template.remind_time.hour, template.remind_time.minute)
                )
            self.category_edit.setText(template.category)
        else:
            self.remind_time_edit.setEnabled(False)

    def result_template(self, base: PresetTemplate | None = None) -> PresetTemplate:
        """把对话框内容写成模板对象。"""
        remind: time | None = None
        if self.remind_check.isChecked():
            remind = self.remind_time_edit.time().toPython()
        values = {
            "title": self.title_edit.text().strip(),
            "type": TaskType.from_value(self.type_combo.currentData()),
            "remind_time": remind,
            "category": self.category_edit.text().strip() or DEFAULT_CATEGORY,
        }
        if base is None:
            return PresetTemplate(**values)
        for key, value in values.items():
            setattr(base, key, value)
        base.__post_init__()
        return base

    def _on_accept(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "无法保存", "模板标题不能为空")
            return
        self.accept()


class PresetDialog(QDialog):
    """模板列表 + 使用/管理。"""

    def __init__(self, parent: QWidget | None = None, *, service: PresetService) -> None:
        super().__init__(parent)
        self._service = service
        self._chosen: PresetTemplate | None = None

        self.setWindowTitle("预设任务模板")
        self.setModal(True)
        self.setMinimumSize(460, 400)
        self._build_ui()
        self.reload()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("选择一个模板，即可预填标题、类型与提醒时间")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._use_selected())
        layout.addWidget(self.list_widget, 1)

        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        layout.addWidget(self.hint)

        actions = QHBoxLayout()
        self.new_button = QPushButton("新建模板")
        self.new_button.clicked.connect(self._create_template)
        self.edit_button = QPushButton("编辑")
        self.edit_button.clicked.connect(self._edit_selected)
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self._delete_selected)
        actions.addWidget(self.new_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("使用选中模板")
        buttons.button(QDialogButtonBox.Cancel).setText("关闭")
        buttons.accepted.connect(self._use_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    def reload(self) -> None:
        """重新加载模板列表。"""
        self.list_widget.clear()
        templates = self._service.list_all()
        current_category = None
        for template in templates:
            if template.category != current_category:
                current_category = template.category
                header = QListWidgetItem(f"—— {current_category} ——")
                header.setFlags(Qt.NoItemFlags)
                self.list_widget.addItem(header)
            label = template.title
            if template.remind_time is not None:
                label += f"    {template.remind_time.strftime('%H:%M')}"
            label += f"    [{template.type.label}]"
            item = QListWidgetItem(label)
            item.setData(TEMPLATE_ROLE, template)
            self.list_widget.addItem(item)

        self.hint.setText(f"共 {len(templates)} 个模板。模板仅作参考，可自由增删改。")
        has_selection = self._selected() is not None
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    def _selected(self) -> PresetTemplate | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(TEMPLATE_ROLE)

    # ------------------------------------------------------------------
    def _use_selected(self) -> None:
        template = self._selected()
        if template is None:
            QMessageBox.information(self, "提示", "请先选择一个模板")
            return
        self._chosen = template
        self.accept()

    def chosen_template(self) -> PresetTemplate | None:
        """用户选择的模板；取消时为 ``None``。"""
        return self._chosen

    def _create_template(self) -> None:
        dialog = PresetEditDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self._service.create(dialog.result_template())
            self.reload()

    def _edit_selected(self) -> None:
        template = self._selected()
        if template is None:
            return
        dialog = PresetEditDialog(self, template)
        if dialog.exec() == QDialog.Accepted:
            self._service.update(dialog.result_template(template))
            self.reload()

    def _delete_selected(self) -> None:
        template = self._selected()
        if template is None:
            return
        confirmed = QMessageBox.question(
            self,
            "删除模板",
            f"确定要删除模板「{template.title}」吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmed == QMessageBox.Yes:
            self._service.delete(template.id)
            self.reload()

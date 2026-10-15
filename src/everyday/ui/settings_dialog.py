"""设置页（文档 6.4）。

包含：开机自启开关、悬浮窗默认显示开关、日历数据源配置、
预设模板管理入口、数据库文件位置展示。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from everyday import paths
from everyday.services.calendar_service import CalendarService
from everyday.services.preset_service import PresetService
from everyday.services.settings_service import SettingsService
from everyday.services.startup_service import StartupService
from everyday.ui.context import AppContext
from everyday.ui.preset_dialog import PresetDialog

logger = logging.getLogger(__name__)

#: 可选日志级别
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class SettingsDialog(QDialog):
    """应用设置对话框。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        context: AppContext,
        preset_service: PresetService,
        settings_service: SettingsService,
        calendar_service: CalendarService,
        startup_service: StartupService,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._presets = preset_service
        self._settings_service = settings_service
        self._calendar = calendar_service
        self._startup = startup_service

        self.setWindowTitle("设置")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        # ---- 启动与窗口 ----
        startup_group = QGroupBox("启动与窗口")
        startup_form = QFormLayout(startup_group)
        startup_form.setSpacing(10)

        self.autostart_check = QCheckBox("登录系统后自动启动（写入当前用户注册表）")
        startup_form.addRow("开机自启", self.autostart_check)

        self.float_visible_check = QCheckBox("启动后显示桌面悬浮窗")
        startup_form.addRow("悬浮窗", self.float_visible_check)

        if not self._startup.is_supported():
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip("当前平台不支持注册表开机自启")
        layout.addWidget(startup_group)

        # ---- 日历 ----
        calendar_group = QGroupBox("实时日历")
        calendar_form = QFormLayout(calendar_group)
        calendar_form.setSpacing(10)

        path_row = QHBoxLayout()
        self.ics_edit = QLineEdit()
        self.ics_edit.setPlaceholderText("本地 .ics 路径，或 https:// 订阅地址；留空表示不加载")
        browse_button = QPushButton("浏览…")
        browse_button.clicked.connect(self._browse_ics)
        path_row.addWidget(self.ics_edit, 1)
        path_row.addWidget(browse_button)
        path_container = QWidget()
        path_container.setLayout(path_row)
        calendar_form.addRow("数据源", path_container)

        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(1, 1440)
        self.refresh_spin.setSuffix(" 分钟")
        calendar_form.addRow("自动刷新间隔", self.refresh_spin)

        refresh_row = QHBoxLayout()
        self.refresh_now_button = QPushButton("立即刷新")
        self.refresh_now_button.clicked.connect(self._refresh_calendar_now)
        self.calendar_status = QLabel("")
        self.calendar_status.setObjectName("hint")
        refresh_row.addWidget(self.refresh_now_button)
        refresh_row.addWidget(self.calendar_status, 1)
        refresh_container = QWidget()
        refresh_container.setLayout(refresh_row)
        calendar_form.addRow("", refresh_container)
        layout.addWidget(calendar_group)

        # ---- 模板 ----
        preset_group = QGroupBox("预设模板")
        preset_row = QHBoxLayout(preset_group)
        self.preset_count_label = QLabel("")
        self.preset_count_label.setObjectName("hint")
        manage_button = QPushButton("管理模板…")
        manage_button.clicked.connect(self._manage_presets)
        preset_row.addWidget(self.preset_count_label, 1)
        preset_row.addWidget(manage_button)
        layout.addWidget(preset_group)

        # ---- 数据与日志 ----
        data_group = QGroupBox("数据与日志")
        data_form = QFormLayout(data_group)
        data_form.setSpacing(10)

        self.db_path_label = QLabel("")
        self.db_path_label.setWordWrap(True)
        self.db_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        data_form.addRow("数据库文件", self.db_path_label)

        open_row = QHBoxLayout()
        open_data_button = QPushButton("打开数据目录")
        open_data_button.clicked.connect(
            lambda: self._open_path(paths.data_dir())
        )
        open_logs_button = QPushButton("打开日志目录")
        open_logs_button.clicked.connect(lambda: self._open_path(paths.logs_dir()))
        export_button = QPushButton("导出配置")
        export_button.clicked.connect(self._export_settings)
        open_row.addWidget(open_data_button)
        open_row.addWidget(open_logs_button)
        open_row.addWidget(export_button)
        open_row.addStretch(1)
        open_container = QWidget()
        open_container.setLayout(open_row)
        data_form.addRow("", open_container)

        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(LOG_LEVELS)
        data_form.addRow("日志级别", self.log_level_combo)

        self.backup_check = QCheckBox("启动时自动备份数据库（保留最近 5 份）")
        data_form.addRow("备份", self.backup_check)
        layout.addWidget(data_group)

        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    def _load(self) -> None:
        settings = self._settings_service.load()
        self.autostart_check.setChecked(settings.auto_start)
        self.float_visible_check.setChecked(settings.float_window_visible)
        self.ics_edit.setText(settings.calendar_ics_path)
        self.refresh_spin.setValue(settings.calendar_refresh_minutes)
        self.log_level_combo.setCurrentText(
            settings.log_level if settings.log_level in LOG_LEVELS else "INFO"
        )
        self.backup_check.setChecked(settings.startup_backup_enabled)
        self.db_path_label.setText(str(paths.database_path()))
        self._refresh_status()

    def _refresh_status(self) -> None:
        self.preset_count_label.setText(f"当前共有 {self._presets.count()} 个模板")
        loaded_at = self._calendar.last_loaded_at
        if self._calendar.last_error:
            self.calendar_status.setText(f"上次加载失败：{self._calendar.last_error}")
        elif loaded_at is not None:
            self.calendar_status.setText(
                f"上次成功加载：{loaded_at.strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            self.calendar_status.setText("尚未加载")

    # ------------------------------------------------------------------
    def _browse_ics(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择日历文件", "", "iCalendar 文件 (*.ics);;所有文件 (*)"
        )
        if selected:
            self.ics_edit.setText(selected)

    def _open_path(self, path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _manage_presets(self) -> None:
        PresetDialog(self, service=self._presets).exec()
        self._refresh_status()

    def _refresh_calendar_now(self) -> None:
        """立刻重新加载日历（文档 5.6：设置页提供立即刷新按钮）。"""
        source = self.ics_edit.text().strip()
        ok = self._calendar.refresh(source, day=self._context.today())
        self._context.notify_calendar_changed()
        self._refresh_status()
        if ok:
            QMessageBox.information(
                self, "刷新完成", f"已加载 {len(self._calendar.upcoming())} 条日程"
            )
        else:
            QMessageBox.warning(
                self,
                "刷新失败",
                f"无法加载日历数据源，已保留原有日程。\n\n{self._calendar.last_error}",
            )

    def _export_settings(self) -> None:
        target = self._settings_service.export_to_json()
        QMessageBox.information(self, "导出完成", f"配置已导出到：\n{target}")

    # ------------------------------------------------------------------
    def _on_accept(self) -> None:
        """保存设置；开机自启需要同步落到注册表。"""
        settings = self._settings_service.load()
        want_autostart = self.autostart_check.isChecked()

        self._settings_service.save(
            settings.merged(
                auto_start=want_autostart,
                float_window_visible=self.float_visible_check.isChecked(),
                calendar_ics_path=self.ics_edit.text().strip(),
                calendar_refresh_minutes=self.refresh_spin.value(),
                log_level=self.log_level_combo.currentText(),
                startup_backup_enabled=self.backup_check.isChecked(),
            )
        )

        if self._startup.is_supported():
            if not self._startup.apply(want_autostart) and want_autostart:
                QMessageBox.warning(
                    self,
                    "开机自启未生效",
                    "写入注册表失败，请检查权限。其余设置已保存。",
                )

        # 让主窗口与悬浮窗感知设置变化
        self._context.update_settings()
        logging.getLogger().setLevel(
            getattr(logging, self.log_level_combo.currentText(), logging.INFO)
        )
        self.accept()

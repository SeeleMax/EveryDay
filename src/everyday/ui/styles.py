"""界面样式（QSS）。

集中在这里，避免在窗口代码里散落颜色与字号字面量。
"""

from __future__ import annotations

#: 主色调
PRIMARY = "#2D7FF9"
PRIMARY_HOVER = "#1F6FE0"
DANGER = "#E5484D"
OVERDUE = "#C4314B"
TEXT_PRIMARY = "#1F2328"
TEXT_SECONDARY = "#6E7781"
BORDER = "#E3E6EA"
SURFACE = "#FFFFFF"
BACKGROUND = "#F6F7F9"

#: 悬浮窗背景（半透明）
FLOAT_BACKGROUND = "rgba(255, 255, 255, 235)"
FLOAT_BORDER = "rgba(0, 0, 0, 30)"

MAIN_STYLE = f"""
QWidget {{
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
    color: {TEXT_PRIMARY};
}}
QMainWindow, QDialog {{
    background: {BACKGROUND};
}}
QToolBar {{
    background: {SURFACE};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    spacing: 6px;
}}
QToolButton, QPushButton {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 12px;
    color: {TEXT_PRIMARY};
}}
QToolButton:hover, QPushButton:hover {{
    border-color: {PRIMARY};
    color: {PRIMARY};
}}
QToolButton:checked {{
    background: {PRIMARY};
    border-color: {PRIMARY};
    color: #FFFFFF;
}}
QPushButton#primaryButton {{
    background: {PRIMARY};
    border-color: {PRIMARY};
    color: #FFFFFF;
}}
QPushButton#primaryButton:hover {{
    background: {PRIMARY_HOVER};
}}
QPushButton#dangerButton:hover {{
    border-color: {DANGER};
    color: {DANGER};
}}
QListWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    border-bottom: 1px solid {BORDER};
    padding: 2px;
}}
QListWidget::item:selected {{
    background: rgba(45, 127, 249, 28);
}}
QLineEdit, QTextEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
}}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus,
QDateEdit:focus, QTimeEdit:focus, QSpinBox:focus {{
    border-color: {PRIMARY};
}}
QLabel#sectionTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}
QLabel#hint {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
}}
QLabel#eventTime {{
    color: {PRIMARY};
    font-weight: 600;
}}
QStatusBar {{
    background: {SURFACE};
    border-top: 1px solid {BORDER};
    color: {TEXT_SECONDARY};
}}
QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    background: {SURFACE};
    text-align: center;
    height: 16px;
}}
QProgressBar::chunk {{
    background: {PRIMARY};
    border-radius: 5px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #C9CED6;
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""

FLOAT_STYLE = f"""
#floatRoot {{
    background: {FLOAT_BACKGROUND};
    border: 1px solid {FLOAT_BORDER};
    border-radius: 14px;
}}
#floatHeader {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 600;
}}
#floatProgress {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
}}
#floatFooter {{
    color: {TEXT_SECONDARY};
    font-size: 11px;
}}
QListWidget#floatList {{
    background: transparent;
    border: none;
}}
QListWidget#floatList::item {{
    border: none;
    padding: 1px 0;
}}
QPushButton#floatClose {{
    background: transparent;
    border: none;
    color: {TEXT_SECONDARY};
    font-size: 14px;
    padding: 0px 4px;
}}
QPushButton#floatClose:hover {{
    color: {DANGER};
}}
"""

#: 置灰（前置任务未完成时）使用的样式片段
DISABLED_TEXT = f"color: {TEXT_SECONDARY};"

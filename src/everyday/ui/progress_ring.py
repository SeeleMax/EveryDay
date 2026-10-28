"""番茄钟进度环（v1.1 P3）。

悬浮窗头部的一个小圆环，显示当前专注的完成比例。
用 ``QPainter`` 画两段圆弧——比塞一个 QProgressBar 更符合「进度环」的原义，
而且不增加任何依赖。

设计取舍：
* 环的半径随控件大小自适应，最小 28px（再小就看不清了）
* 没有进行中的专注时**整体隐藏**，避免悬浮窗顶部常驻一个空环
* 剩余时间显示在环中央（``mm:ss``），一眼能看到还剩多久
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

#: 环的直径（像素）
RING_SIZE = 34

#: 环的线宽（像素）
RING_WIDTH = 4

#: 底环颜色（未完成部分）
TRACK_COLOR = "#E3E6EA"

#: 进行中与暂停时的环颜色
RUNNING_COLOR = "#2D7FF9"
PAUSED_COLOR = "#E8710A"

#: 中央文字字号（pt）
TEXT_POINT_SIZE = 8


class ProgressRing(QWidget):
    """显示比例与中央文字的圆形进度指示器。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ratio = 0.0
        self._text = ""
        self._paused = False
        self.setFixedSize(RING_SIZE, RING_SIZE)
        self.setToolTip("番茄钟")

    # ------------------------------------------------------------------
    def set_progress(self, ratio: float, text: str = "", *, paused: bool = False) -> None:
        """更新进度。

        :param ratio: ``0.0~1.0``，超出范围会被夹住
        :param text: 环中央的文字（通常是 ``mm:ss``）
        :param paused: 暂停时用另一种颜色，让状态一眼可辨
        """
        clamped = max(0.0, min(1.0, float(ratio)))
        if clamped == self._ratio and text == self._text and paused == self._paused:
            return  # 没变化就不重绘，1 秒一次的重绘没有必要
        self._ratio = clamped
        self._text = text
        self._paused = paused
        self.update()

    @property
    def ratio(self) -> float:
        """当前比例。"""
        return self._ratio

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """画底环 + 进度弧 + 中央文字。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        margin = RING_WIDTH / 2 + 1
        rect = QRectF(
            margin, margin, self.width() - 2 * margin, self.height() - 2 * margin
        )

        # 底环
        track_pen = QPen(QColor(TRACK_COLOR), RING_WIDTH)
        track_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(track_pen)
        painter.drawEllipse(rect)

        # 进度弧：从 12 点方向顺时针
        if self._ratio > 0:
            color = QColor(PAUSED_COLOR if self._paused else RUNNING_COLOR)
            arc_pen = QPen(color, RING_WIDTH)
            arc_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(arc_pen)
            span = int(-360 * 16 * self._ratio)  # Qt 的角度单位是 1/16 度，顺时针为负
            painter.drawArc(rect, 90 * 16, span)

        # 中央文字
        if self._text:
            font = QFont(self.font())
            font.setPointSize(TEXT_POINT_SIZE)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#1F2328"))
            painter.drawText(self.rect(), Qt.AlignCenter, self._text)

        painter.end()

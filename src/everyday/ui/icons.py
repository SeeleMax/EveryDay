"""图标由代码绘制，仓库里不放二进制资源。

好处：没有 .ico/.png 资产需要维护，任意 DPI 下都能重新按需渲染出清晰图标，
也让 Git 仓库保持纯文本。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

#: 品牌主色
BRAND_COLOR = "#2D7FF9"

#: 绘制结果的缓存：``(尺寸, 颜色) -> QIcon``
_cache: dict[tuple[int, str], QIcon] = {}


def app_icon(size: int = 64, color: str = BRAND_COLOR) -> QIcon:
    """返回应用图标（圆角方块 + 对勾）。"""
    key = (size, color)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    margin = max(1.0, size * 0.06)
    radius = size * 0.26
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(
        QRectF(margin, margin, size - margin * 2, size - margin * 2), radius, radius
    )

    pen = QPen(QColor("#FFFFFF"))
    pen.setWidthF(max(1.5, size * 0.10))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline(
        QPolygonF(
            [
                QPointF(size * 0.28, size * 0.52),
                QPointF(size * 0.44, size * 0.68),
                QPointF(size * 0.73, size * 0.34),
            ]
        )
    )
    painter.end()

    icon = QIcon(pixmap)
    _cache[key] = icon
    return icon


def progress_icon(size: int = 32, ratio: float = 0.0, color: str = BRAND_COLOR) -> QIcon:
    """按完成率绘制托盘进度图标（托盘图标随进度变化）。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    clamped = max(0.0, min(1.0, ratio))
    inset = max(1.0, size * 0.08)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 255, 255, 40))
    painter.drawEllipse(QRectF(inset, inset, size - inset * 2, size - inset * 2))

    painter.setBrush(QColor(color))
    painter.drawPie(
        QRectF(inset, inset, size - inset * 2, size - inset * 2),
        90 * 16,
        int(-360 * 16 * clamped),
    )
    painter.end()

    return QIcon(pixmap)


def clear_cache() -> None:
    """清空图标缓存（供测试与主题切换使用）。"""
    _cache.clear()

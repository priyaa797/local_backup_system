"""Generate application icon asset using PySide6 QPainter."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)


def generate_icon() -> None:
    # Ensure headless QGuiApplication
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QGuiApplication([])

    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    size = 256
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # Background rounded square with deep blue gradient
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#1e3a8a"))  # Deep blue
    gradient.setColorAt(1.0, QColor("#0284c7"))  # Sky blue

    path = QPainterPath()
    path.addRoundedRect(QRectF(16, 16, 224, 224), 48, 48)
    painter.fillPath(path, QBrush(gradient))

    # Outer border
    painter.setPen(QPen(QColor(255, 255, 255, 60), 4))
    painter.drawPath(path)

    # Draw SSD device shape (silver metallic with subtle gradient)
    ssd_rect = QRectF(50, 70, 156, 116)
    ssd_path = QPainterPath()
    ssd_path.addRoundedRect(ssd_rect, 14, 14)

    ssd_grad = QLinearGradient(50, 70, 206, 186)
    ssd_grad.setColorAt(0.0, QColor("#f8fafc"))
    ssd_grad.setColorAt(1.0, QColor("#cbd5e1"))
    painter.fillPath(ssd_path, QBrush(ssd_grad))
    painter.setPen(QPen(QColor("#64748b"), 3))
    painter.drawPath(ssd_path)

    # SSD LED indicator
    painter.setBrush(QBrush(QColor("#10b981")))  # Emerald green
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(QRectF(64, 82, 12, 12))

    # SSD label text
    painter.setPen(QPen(QColor("#1e293b")))
    font = QFont("Arial", 16, QFont.Bold)
    painter.setFont(font)
    painter.drawText(QRectF(50, 100, 156, 40), Qt.AlignCenter, "BACKUP")

    font_sub = QFont("Arial", 10, QFont.Bold)
    painter.setFont(font_sub)
    painter.setPen(QPen(QColor("#64748b")))
    painter.drawText(QRectF(50, 134, 156, 25), Qt.AlignCenter, "SSD SYSTEM")

    # Sync arrows / Cloud icon on bottom right
    badge_center = QPointF(185, 175)
    painter.setBrush(QBrush(QColor("#2563eb")))
    painter.setPen(QPen(QColor("#ffffff"), 4))
    painter.drawEllipse(badge_center, 30, 30)

    # Arrow inside badge
    painter.setPen(QPen(QColor("#ffffff"), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    # Down arrow
    painter.drawLine(QPointF(185, 160), QPointF(185, 185))
    painter.drawLine(QPointF(175, 175), QPointF(185, 185))
    painter.drawLine(QPointF(195, 175), QPointF(185, 185))

    painter.end()

    png_path = assets_dir / "icon.png"
    pixmap.save(str(png_path), "PNG")
    print(f"Icon generated at: {png_path}")

    # Also save .ico
    ico_path = assets_dir / "icon.ico"
    pixmap.save(str(ico_path), "ICO")
    print(f"Icon ICO generated at: {ico_path}")


if __name__ == "__main__":
    generate_icon()

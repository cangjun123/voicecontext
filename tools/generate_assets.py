from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def draw_icon(size: int = 256) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = size * 0.08
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#176b52"))
    painter.drawRoundedRect(QRectF(margin, margin, size - margin * 2, size - margin * 2), size * 0.18, size * 0.18)

    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(QRectF(size * 0.39, size * 0.22, size * 0.22, size * 0.36), size * 0.11, size * 0.11)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(QColor("#ffffff"), size * 0.055, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    path = QPainterPath()
    path.moveTo(size * 0.30, size * 0.47)
    path.cubicTo(size * 0.30, size * 0.70, size * 0.70, size * 0.70, size * 0.70, size * 0.47)
    painter.drawPath(path)
    painter.drawLine(size * 0.50, size * 0.68, size * 0.50, size * 0.80)
    painter.drawLine(size * 0.37, size * 0.80, size * 0.63, size * 0.80)
    painter.end()
    return image


def main() -> None:
    _app = QApplication.instance() or QApplication([])
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = draw_icon()
    if not image.save(str(ASSETS / "icon.png"), "PNG"):
        raise RuntimeError("Could not write icon.png")
    if not image.save(str(ASSETS / "icon.ico"), "ICO"):
        raise RuntimeError("Could not write icon.ico")


if __name__ == "__main__":
    main()

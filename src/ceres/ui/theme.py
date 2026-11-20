"""Qt 6 dark stylesheet and vector icon drawing utilities for Ceres."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

APP_QSS = """
QMainWindow, QWidget#CentralRoot {
    background-color: #0D1117;
    color: #F1F5F9;
    font-family: "Segoe UI";
    font-size: 13px;
}

QFrame#Card {
    background-color: #151B24;
    border: 1px solid #263142;
    border-radius: 12px;
}

QFrame#SubCard {
    background-color: #1B2330;
    border: 1px solid #2B374B;
    border-radius: 10px;
}

QLabel#AppTitle {
    font-size: 21px;
    font-weight: 700;
    color: #F8FAFC;
}

QLabel#HardwareBadge {
    font-size: 12px;
    color: #38BDF8;
    font-weight: 600;
}

QLabel#SectionTitle {
    font-size: 14px;
    font-weight: 700;
    color: #F8FAFC;
}

QLabel#MutedText {
    color: #94A3B8;
    font-size: 12px;
}

QLabel#ShortcutPill {
    background-color: #1E2736;
    border: 1px solid #303E54;
    border-radius: 8px;
    padding: 5px 10px;
    color: #E2E8F0;
    font-weight: 600;
    font-size: 12px;
}

QLabel#MetaBadge {
    background-color: #202B3B;
    border-radius: 5px;
    padding: 2px 7px;
    color: #93C5FD;
    font-size: 11px;
    font-weight: 600;
}

QPushButton {
    background-color: #242F40;
    border: 1px solid #334155;
    border-radius: 8px;
    color: #F1F5F9;
    padding: 7px 14px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #2E3B50;
    border-color: #475569;
}

QPushButton:pressed {
    background-color: #1E2836;
}

QPushButton#PrimaryBtn {
    background-color: #2563EB;
    border: 1px solid #3B82F6;
    color: #FFFFFF;
}

QPushButton#PrimaryBtn:hover {
    background-color: #1D4ED8;
}

QPushButton#DangerBtn {
    background-color: #DC2626;
    border: 1px solid #EF4444;
    color: #FFFFFF;
}

QPushButton#DangerBtn:hover {
    background-color: #B91C1C;
}

QPushButton#SubtleDangerBtn {
    background-color: #361F25;
    border: 1px solid #5B2B35;
    color: #FCA5A5;
}

QPushButton#SubtleDangerBtn:hover {
    background-color: #4C252E;
}

QPushButton#SegmentBtn {
    background-color: #1A2230;
    border: 1px solid #2C384C;
    border-radius: 7px;
    padding: 6px 12px;
    color: #94A3B8;
}

QPushButton#SegmentBtn:checked {
    background-color: #2563EB;
    border-color: #3B82F6;
    color: #FFFFFF;
}

QPushButton#SmallBtn {
    padding: 4px 10px;
    font-size: 11px;
    border-radius: 6px;
}

QLineEdit, QPlainTextEdit, QComboBox {
    background-color: #0E131B;
    border: 1px solid #2B374B;
    border-radius: 8px;
    color: #F1F5F9;
    padding: 6px 10px;
    selection-background-color: #2563EB;
}

QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border-color: #3B82F6;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QTabWidget::pane {
    border: 1px solid #263142;
    border-radius: 12px;
    background-color: #151B24;
    top: -1px;
}

QTabBar::tab {
    background-color: #11161F;
    border: 1px solid #263142;
    border-bottom: none;
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
    padding: 8px 18px;
    margin-right: 4px;
    color: #94A3B8;
    font-weight: 600;
}

QTabBar::tab:selected {
    background-color: #151B24;
    color: #F8FAFC;
    border-bottom: 2px solid #3B82F6;
}

QCheckBox {
    spacing: 8px;
    color: #E2E8F0;
}

QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border-radius: 4px;
    border: 1px solid #475569;
    background-color: #0E131B;
}

QCheckBox::indicator:checked {
    background-color: #2563EB;
    border-color: #3B82F6;
}

QProgressBar {
    background-color: #0E131B;
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
}

QProgressBar::chunk {
    background-color: #3B82F6;
    border-radius: 3px;
}

QScrollArea {
    border: none;
    background-color: transparent;
}

QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #334155;
    border-radius: 4px;
    min-height: 28px;
}

QScrollBar::handle:vertical:hover {
    background: #475569;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
"""


def create_mic_qicon(state: str = "idle") -> QIcon:
    """Draw a sharp vector microphone icon onto a 64x64 QPixmap for tray and window icons."""
    bg_hex, ring_hex = {
        "idle": ("#1E293B", "#38BDF8"),
        "listening": ("#16A34A", "#86EFAC"),
        "busy": ("#D97706", "#FDE68A"),
        "error": ("#DC2626", "#FCA5A5"),
    }.get(state, ("#1E293B", "#38BDF8"))

    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    painter.setBrush(QColor(bg_hex))
    painter.setPen(QPen(QColor(ring_hex), 3.0))
    painter.drawRoundedRect(QRectF(4, 4, 56, 56), 16, 16)

    painter.setBrush(QColor("#F8FAFC"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(25, 13, 14, 23), 7, 7)

    arc_pen = QPen(QColor("#F8FAFC"), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(arc_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(19, 19, 26, 23), 180 * 16, 180 * 16)
    painter.drawLine(32, 42, 32, 50)
    painter.drawLine(24, 50, 40, 50)

    painter.end()
    return QIcon(pixmap)

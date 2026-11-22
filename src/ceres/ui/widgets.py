"""Windows 11 Fluent / WinUI 3 native PySide6 widgets with live Light/Dark theming."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QCheckBox, QComboBox, QPushButton, QWidget

from ceres.ui.theme import DARK_THEME, FLUENT_ICON_FONT, FONT_FAMILIES, ThemeTokens


class FluentNavButton(QPushButton):
    """WinUI 3 NavigationViewItem with left 3px accent pill, Fluent glyph, and hover fill."""

    _HEIGHT = 38

    def __init__(
        self,
        glyph: str,
        label: str,
        tokens: ThemeTokens = DARK_THEME,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("", parent)
        self._glyph = glyph
        self._label = label
        self._tokens = tokens
        self._hovered = False

        self.setCheckable(True)
        self.setFixedHeight(self._HEIGHT)
        self.setAccessibleName(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_theme(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        font = QFont()
        font.setFamilies(FONT_FAMILIES)
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold)
        text_w = QFontMetrics(font).horizontalAdvance(self._label)
        return QSize(max(180, 44 + text_w + 14), self._HEIGHT)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        checked = self.isChecked()
        bg_rect = QRectF(0.0, 1.0, float(self.width()), float(self.height() - 2))
        if checked:
            painter.setBrush(QColor(self._tokens.nav_active))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bg_rect, 4.0, 4.0)
        elif self._hovered:
            painter.setBrush(QColor(self._tokens.nav_hover))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bg_rect, 4.0, 4.0)

        if checked:
            pill_y = (self.height() - 16.0) / 2.0
            pill_rect = QRectF(2.0, pill_y, 3.0, 16.0)
            painter.setBrush(QColor(self._tokens.accent))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(pill_rect, 1.5, 1.5)

        icon_font = QFont(FLUENT_ICON_FONT)
        icon_font.setPixelSize(15)
        painter.setFont(icon_font)
        painter.setPen(QColor(self._tokens.text_primary if self.isEnabled() else self._tokens.text_disabled))
        painter.drawText(
            QRectF(12.0, 0.0, 20.0, float(self.height())),
            Qt.AlignmentFlag.AlignCenter,
            self._glyph,
        )

        label_font = QFont()
        label_font.setFamilies(FONT_FAMILIES)
        label_font.setPixelSize(14)
        label_font.setWeight(QFont.Weight.DemiBold if checked else QFont.Weight.Normal)
        painter.setFont(label_font)
        painter.drawText(
            QRectF(40.0, 0.0, max(0.0, self.width() - 48.0), float(self.height())),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )

        painter.end()


class FluentToggleSwitch(QCheckBox):
    """WinUI 3 ToggleSwitch supporting inline 'On'/'Off' state labels or side labels."""

    _TRACK_W = 40
    _TRACK_H = 20
    _LABEL_GAP = 12

    def __init__(
        self,
        text: str = "",
        tokens: ThemeTokens = DARK_THEME,
        *,
        show_state_label: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._tokens = tokens
        self._show_state_label = show_state_label
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_theme(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def _state_label_width(self) -> int:
        fm = self.fontMetrics()
        return max(fm.horizontalAdvance("On"), fm.horizontalAdvance("Off"))

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        if self._show_state_label:
            width = self._state_label_width() + self._LABEL_GAP + self._TRACK_W + 2
        else:
            text_w = fm.horizontalAdvance(self.text()) if self.text() else 0
            width = self._TRACK_W + (self._LABEL_GAP + text_w + 8 if text_w else 2)
        height = max(26, fm.height() + 6)
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def hitButton(self, pos: QPoint) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        enabled = self.isEnabled()
        checked = self.isChecked()
        track_y = (self.height() - self._TRACK_H) / 2.0
        track_x = float(self.width() - self._TRACK_W - 1) if self._show_state_label else 0.0

        text_color = QColor(self._tokens.text_primary if enabled else self._tokens.text_disabled)
        knob_diameter = 14.0 if self._hovered and enabled else 12.0

        if not enabled:
            track_fill: QColor | None = QColor(self._tokens.bg_subtle) if checked else None
            track_border: QColor | None = None if checked else QColor(self._tokens.control_border)
            knob_color = QColor(self._tokens.text_disabled)
        elif checked:
            track_fill = QColor(self._tokens.accent_hover if self._hovered else self._tokens.accent)
            track_border = None
            knob_color = QColor(self._tokens.text_on_accent)
        else:
            track_fill = QColor(self._tokens.toggle_off_hover_bg) if self._hovered else None
            track_border = QColor(self._tokens.toggle_off_border)
            knob_color = QColor(self._tokens.toggle_off_knob)

        track_rect = QRectF(track_x, track_y, float(self._TRACK_W), float(self._TRACK_H))
        painter.setBrush(track_fill if track_fill is not None else Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(track_border, 1.2) if track_border is not None else Qt.PenStyle.NoPen)
        painter.drawRoundedRect(track_rect, self._TRACK_H / 2.0, self._TRACK_H / 2.0)

        knob_cx = (track_x + self._TRACK_W - 10.0) if checked else (track_x + 10.0)
        knob_cy = track_y + self._TRACK_H / 2.0
        knob_r = knob_diameter / 2.0
        painter.setBrush(knob_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(knob_cx - knob_r, knob_cy - knob_r, knob_diameter, knob_diameter))

        painter.setPen(text_color)
        if self._show_state_label:
            label_text = "On" if checked else "Off"
            state_rect = QRectF(
                0.0,
                0.0,
                max(0.0, track_x - self._LABEL_GAP),
                float(self.height()),
            )
            painter.drawText(
                state_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                label_text,
            )
        elif self.text():
            text_x = float(self._TRACK_W + self._LABEL_GAP)
            text_rect = QRectF(text_x, 0.0, max(0.0, self.width() - text_x), float(self.height()))
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )

        painter.end()


class CleanComboBox(QComboBox):
    """WinUI 3 ComboBox painting a crisp vector chevron and fitting long items cleanly."""

    def __init__(self, tokens: ThemeTokens = DARK_THEME, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(16)

    def set_theme(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def showPopup(self) -> None:  # noqa: N802
        view = self.view()
        if view is not None:
            hint_w = view.sizeHintForColumn(0) + 32
            view.setMinimumWidth(max(self.width(), min(680, hint_w)))
        super().showPopup()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cx = self.width() - 18.0
        cy = self.height() / 2.0
        chevron = QPainterPath()
        chevron.moveTo(cx - 4.0, cy - 2.0)
        chevron.lineTo(cx, cy + 2.0)
        chevron.lineTo(cx + 4.0, cy - 2.0)

        pen_color = QColor(self._tokens.text_secondary if self.isEnabled() else self._tokens.text_disabled)
        pen = QPen(pen_color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(chevron)
        painter.end()

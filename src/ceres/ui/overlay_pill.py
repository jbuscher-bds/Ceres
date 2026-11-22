"""HiDPI-aware, multi-monitor, non-focus-stealing Qt 6 floating microphone pill."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import math
import time
from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPen,
    QScreen,
)
from PySide6.QtWidgets import QWidget

from ceres.ui.theme import (
    DARK_THEME,
    FONT_FAMILIES,
    ThemeTokens,
)

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = -1
MONITOR_DEFAULTTONEAREST = 2
WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.MonitorFromWindow.restype = wintypes.HMONITOR
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFOEXW)]
_user32.GetMonitorInfoW.restype = wintypes.BOOL


def _active_screen() -> QScreen | None:
    """Resolve the QScreen hosting the current foreground window across multi-monitor DPI setups."""
    screens = QGuiApplication.screens()
    if not screens:
        return None
    try:
        fg_hwnd = _user32.GetForegroundWindow()
        if fg_hwnd:
            hmon = _user32.MonitorFromWindow(fg_hwnd, MONITOR_DEFAULTTONEAREST)
            if hmon:
                info = _MONITORINFOEXW()
                info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
                if _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                    dev_name = info.szDevice.strip().lower()
                    for scr in screens:
                        if scr.name().strip().lower() == dev_name:
                            return scr
    except Exception:
        pass
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


class FloatingMicPill(QWidget):
    """Smooth 60 FPS HiDPI status & waveform overlay that never steals focus."""

    LOGICAL_WIDTH = 360
    LOGICAL_HEIGHT = 58
    BAR_COUNT = 14

    def __init__(
        self,
        *,
        saved_position: list[int] | None = None,
        on_position_changed: Callable[[list[int] | None], None] | None = None,
        theme: ThemeTokens = DARK_THEME,
    ) -> None:
        super().__init__(None)
        self._saved_position: list[int] | None = list(saved_position) if saved_position else None
        self._on_position_changed = on_position_changed
        self._tokens = theme

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.LOGICAL_WIDTH, self.LOGICAL_HEIGHT)

        self._state = "idle"
        self._message = "Ready"
        self._badge = ""
        self._target_level = 0.0
        self._smooth_level = 0.0
        self._elapsed_s = 0.0
        self._bars: list[float] = [0.15] * self.BAR_COUNT

        self._drag_offset: QPoint | None = None

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._on_anim_tick)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        # Force native HWND creation immediately so WS_EX_NOACTIVATE can be verified before first show.
        self._apply_noactivate_style()
        self._reposition()

    def logical_size(self) -> tuple[int, int]:
        return (self.width(), self.height())

    def has_noactivate_style(self) -> bool:
        hwnd = int(self.winId())
        ex_style = int(_user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
        return (ex_style & WS_EX_NOACTIVATE) != 0

    def _apply_noactivate_style(self) -> None:
        hwnd = int(self.winId())
        ex_style = int(_user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
        new_style = ex_style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
        _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new_style)
        _user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )

    def _is_position_visible(self, pos: list[int]) -> bool:
        pill_rect = QRect(pos[0], pos[1], self.width(), self.height())
        return any(scr.availableGeometry().intersects(pill_rect) for scr in QGuiApplication.screens())

    def _reposition(self) -> None:
        if self._saved_position and self._is_position_visible(self._saved_position):
            self.move(self._saved_position[0], self._saved_position[1])
            return
        screen = _active_screen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        x = avail.left() + (avail.width() - self.width()) // 2
        y = max(avail.top() + 12, avail.bottom() - self.height() - 24)
        self.move(x, y)

    def set_theme(self, tokens: ThemeTokens) -> None:
        """Apply updated Light/Dark WinUI 3 tokens and repaint."""
        self._tokens = tokens
        self.update()

    def set_audio_level(self, level: float, elapsed_s: float) -> None:
        """Update target waveform energy & elapsed seconds at up to 30 Hz cheaply."""
        self._target_level = max(0.0, min(1.0, float(level)))
        self._elapsed_s = max(0.0, float(elapsed_s))

    def show_state(
        self,
        state: str,
        message: str,
        *,
        action_type: str = "dictate",
    ) -> None:
        """Transition pill visual state without activating or stealing keyboard focus."""
        self._hide_timer.stop()
        self._state = state
        self._message = message
        self._badge = "EDIT" if action_type == "edit" else ""

        if state in ("idle", "unloaded"):
            self.hide()
            return

        if not self.isVisible():
            self._elapsed_s = 0.0
            self._target_level = 0.0
            self._reposition()
            self._apply_noactivate_style()
            self.show()
            self._apply_noactivate_style()

        if not self._anim_timer.isActive():
            self._anim_timer.start()

        if state in ("done", "ready", "error"):
            delay_ms = 2500 if state == "error" else 1300
            self._hide_timer.start(delay_ms)

        self.update()

    def nativeEvent(self, event_type, message):  # noqa: N802, ANN001, ANN201
        """Reject Win32 mouse activation so clicking or dragging the pill never steals focus."""
        try:
            if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_MOUSEACTIVATE:
                    return True, MA_NOACTIVATE
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    def hideEvent(self, event) -> None:  # noqa: N802, ANN001
        self._anim_timer.stop()
        super().hideEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            target = event.globalPosition().toPoint() - self._drag_offset
            self.move(target)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            pos = [int(self.x()), int(self.y())]
            self._saved_position = pos
            if self._on_position_changed:
                self._on_position_changed(pos)
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._saved_position = None
            self._reposition()
            if self._on_position_changed:
                self._on_position_changed(None)
            event.accept()

    def _on_anim_tick(self) -> None:
        now = time.monotonic()
        self._smooth_level = 0.72 * self._smooth_level + 0.28 * self._target_level
        for idx in range(self.BAR_COUNT):
            if self._state == "listening":
                wave = 0.5 + 0.5 * math.sin(now * 9.5 + idx * 0.65)
                target = max(0.14, min(1.0, self._smooth_level * (0.45 + 0.78 * wave) + 0.10 * wave))
            elif self._state in ("transcribing", "rewriting"):
                pulse = 0.5 + 0.5 * math.sin(now * 6.5 - idx * 0.5)
                target = 0.16 + 0.45 * pulse
            elif self._state == "done":
                target = 0.26
            else:
                target = 0.14
            self._bars[idx] = 0.60 * self._bars[idx] + 0.40 * target
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802, ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._tokens

        accent_hex = {
            "listening": t.success,
            "transcribing": t.caution,
            "rewriting": t.caution,
            "done": t.success,
            "error": t.error_text,
        }.get(self._state, t.accent)
        accent = QColor(accent_hex)

        # Windows 11 frosted surface matching active Light/Dark theme
        rect = QRectF(2.0, 2.0, self.width() - 4.0, self.height() - 4.0)
        radius = rect.height() / 2.0

        painter.setBrush(QColor(*t.pill_surface_rgba))
        if self._state == "listening":
            border_col = QColor(accent)
            border_col.setAlpha(180 if t.is_dark else 160)
        else:
            border_col = QColor(t.pill_border_idle)
        painter.setPen(QPen(border_col, 1.4))
        painter.drawRoundedRect(rect, radius, radius)

        # Pulsing status dot
        dot_center_x = 24.0
        dot_center_y = self.height() / 2.0
        if self._state == "listening":
            halo = QColor(accent)
            halo.setAlpha(60 if t.is_dark else 45)
            painter.setBrush(halo)
            painter.setPen(Qt.PenStyle.NoPen)
            halo_r = 8.0 + 4.0 * self._smooth_level
            painter.drawEllipse(QRectF(dot_center_x - halo_r, dot_center_y - halo_r, halo_r * 2, halo_r * 2))

        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(dot_center_x - 5.5, dot_center_y - 5.5, 11.0, 11.0))

        # Status text
        if self._state == "listening":
            mins, secs = divmod(int(self._elapsed_s), 60)
            status_text = f"Listening  {mins}:{secs:02d}"
        else:
            status_text = self._message

        text_font = QFont()
        text_font.setFamilies(FONT_FAMILIES)
        text_font.setPointSize(10)
        text_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(text_font)
        painter.setPen(QColor(t.text_primary))

        bars_total_w = self.BAR_COUNT * 6 - 3
        bars_left = self.width() - 20.0 - bars_total_w

        text_left = 40.0
        badge_font = QFont()
        badge_font.setFamilies(FONT_FAMILIES)
        badge_font.setPointSize(8)
        badge_font.setWeight(QFont.Weight.Bold)
        badge_w = 0.0
        if self._badge:
            fm_badge = QFontMetrics(badge_font)
            badge_w = float(fm_badge.horizontalAdvance(self._badge) + 14)

        max_text_w = max(40.0, bars_left - text_left - badge_w - 14.0)
        elided = QFontMetrics(text_font).elidedText(status_text, Qt.TextElideMode.ElideRight, int(max_text_w))
        text_rect = QRectF(text_left, 0.0, max_text_w, float(self.height()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

        if self._badge:
            painter.setFont(badge_font)
            text_used = float(QFontMetrics(text_font).horizontalAdvance(elided))
            bx = text_left + text_used + 8.0
            bh = 20.0
            by = (self.height() - bh) / 2.0
            b_rect = QRectF(bx, by, badge_w, bh)
            if self._badge == "EDIT":
                painter.setBrush(QColor(*t.pill_edit_bg_rgba))
                painter.setPen(QPen(QColor(t.caution), 1.0))
                painter.drawRoundedRect(b_rect, 6.0, 6.0)
                painter.setPen(QColor(t.caution))
            else:
                painter.setBrush(QColor(*t.pill_badge_bg_rgba))
                painter.setPen(QPen(QColor(t.pill_badge_border), 1.0))
                painter.drawRoundedRect(b_rect, 6.0, 6.0)
                painter.setPen(QColor(t.text_primary))
            painter.drawText(b_rect, Qt.AlignmentFlag.AlignCenter, self._badge)

        # Animated equalizer bars
        mid_y = self.height() / 2.0
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        for i, amp in enumerate(self._bars):
            x = bars_left + i * 6.0
            half_h = max(2.0, amp * 14.0)
            painter.drawRoundedRect(QRectF(x, mid_y - half_h, 3.0, half_h * 2.0), 1.5, 1.5)

        painter.end()

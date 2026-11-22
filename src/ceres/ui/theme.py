"""Windows 11 Fluent / WinUI 3 color tokens, dynamic Light/Dark theming, and DWM helpers."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wintypes
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap

from ceres import APP_ICON_ICO, APP_ICON_PNG

FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI"]
FLUENT_ICON_FONT = "Segoe Fluent Icons"


@dataclass(frozen=True)
class ThemeTokens:
    """Semantic Windows 11 Fluent / WinUI 3 color tokens for a theme variant."""

    is_dark: bool
    name: str

    bg_base: str
    bg_card: str
    bg_card_hover: str
    bg_subtle: str
    nav_hover: str
    nav_active: str

    control_bg: str
    control_hover: str
    control_pressed: str
    control_border: str
    control_bottom_border: str
    card_border: str
    divider: str

    input_bg: str
    input_focus_bg: str
    input_border: str
    input_bottom_border: str

    text_primary: str
    text_secondary: str
    text_tertiary: str
    text_disabled: str

    accent: str
    accent_hover: str
    accent_pressed: str
    text_on_accent: str

    toggle_off_border: str
    toggle_off_knob: str
    toggle_off_hover_bg: str

    success: str
    success_bg: str
    caution: str
    caution_bg: str
    danger: str
    danger_hover: str
    subtle_danger_text: str
    subtle_danger_hover_bg: str
    error_text: str

    pill_surface_rgba: tuple[int, int, int, int]
    pill_border_idle: str
    pill_badge_bg_rgba: tuple[int, int, int, int]
    pill_badge_border: str
    pill_edit_bg_rgba: tuple[int, int, int, int]

    scrollbar_handle: str
    scrollbar_hover: str
    menu_bg: str
    menu_border: str

    dwm_caption_color: int
    dwm_text_color: int


DARK_THEME = ThemeTokens(
    is_dark=True,
    name="dark",
    bg_base="#202020",
    bg_card="#2B2B2B",
    bg_card_hover="#323232",
    bg_subtle="#292929",
    nav_hover="#2D2D2D",
    nav_active="#2D2D2D",
    control_bg="#343434",
    control_hover="#3C3C3C",
    control_pressed="#2E2E2E",
    control_border="#454545",
    control_bottom_border="#454545",
    card_border="#383838",
    divider="#383838",
    input_bg="#2B2B2B",
    input_focus_bg="#1F1F1F",
    input_border="#454545",
    input_bottom_border="#9A9A9A",
    text_primary="#FFFFFF",
    text_secondary="#CFCFCF",
    text_tertiary="#9A9A9A",
    text_disabled="#707070",
    accent="#FF7A00",
    accent_hover="#FF8E24",
    accent_pressed="#E06B00",
    text_on_accent="#000000",
    toggle_off_border="#9A9A9A",
    toggle_off_knob="#CFCFCF",
    toggle_off_hover_bg="#2D2D2D",
    success="#6CCB5F",
    success_bg="#253524",
    caution="#F7B955",
    caution_bg="#3A2F1C",
    danger="#C42B1C",
    danger_hover="#B12518",
    subtle_danger_text="#FF99A4",
    subtle_danger_hover_bg="#3D2225",
    error_text="#FF99A4",
    pill_surface_rgba=(44, 44, 44, 244),
    pill_border_idle="#454545",
    pill_badge_bg_rgba=(255, 255, 255, 20),
    pill_badge_border="#555555",
    pill_edit_bg_rgba=(247, 185, 85, 38),
    scrollbar_handle="#555555",
    scrollbar_hover="#787878",
    menu_bg="#2C2C2C",
    menu_border="#3D3D3D",
    dwm_caption_color=0x00202020,
    dwm_text_color=0x00FFFFFF,
)


LIGHT_THEME = ThemeTokens(
    is_dark=False,
    name="light",
    bg_base="#F3F3F3",
    bg_card="#FFFFFF",
    bg_card_hover="#FAFAFA",
    bg_subtle="#EAEAEA",
    nav_hover="#EAEAEA",
    nav_active="#EAEAEA",
    control_bg="#FFFFFF",
    control_hover="#F9F9F9",
    control_pressed="#F3F3F3",
    control_border="#DCDCDC",
    control_bottom_border="#CCCCCC",
    card_border="#E5E5E5",
    divider="#E5E5E5",
    input_bg="#FFFFFF",
    input_focus_bg="#FFFFFF",
    input_border="#DCDCDC",
    input_bottom_border="#868686",
    text_primary="#1A1A1A",
    text_secondary="#5D5D5D",
    text_tertiary="#767676",
    text_disabled="#A0A0A0",
    accent="#1E2B37",
    accent_hover="#2B3D4E",
    accent_pressed="#141D26",
    text_on_accent="#FFFFFF",
    toggle_off_border="#868686",
    toggle_off_knob="#5D5D5D",
    toggle_off_hover_bg="#EAEAEA",
    success="#0F7B0F",
    success_bg="#DFF6DD",
    caution="#9D5D00",
    caution_bg="#FFF4CE",
    danger="#C42B1C",
    danger_hover="#A82316",
    subtle_danger_text="#C42B1C",
    subtle_danger_hover_bg="#FDE7E9",
    error_text="#C42B1C",
    pill_surface_rgba=(255, 255, 255, 248),
    pill_border_idle="#D5D5D5",
    pill_badge_bg_rgba=(0, 0, 0, 14),
    pill_badge_border="#D0D0D0",
    pill_edit_bg_rgba=(255, 244, 206, 230),
    scrollbar_handle="#BDBDBD",
    scrollbar_hover="#8A8A8A",
    menu_bg="#FFFFFF",
    menu_border="#E5E5E5",
    dwm_caption_color=0x00F3F3F3,
    dwm_text_color=0x001A1A1A,
)


def detect_system_theme() -> str:
    """Return 'dark' or 'light' matching Windows 11 system settings."""
    with contextlib.suppress(Exception):
        if QGuiApplication.instance() is not None:
            scheme = QGuiApplication.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Light:
                return "light"
            if scheme == Qt.ColorScheme.Dark:
                return "dark"

    with contextlib.suppress(Exception):
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(val) == 1 else "dark"

    return "dark"


def resolve_theme_mode(preference: str) -> str:
    """Resolve user preference ('system', 'light', 'dark') into concrete 'light' or 'dark'."""
    norm = (preference or "system").strip().lower()
    if norm == "light":
        return "light"
    if norm == "dark":
        return "dark"
    return detect_system_theme()


def get_theme_tokens(preference: str = "system") -> ThemeTokens:
    """Return active `ThemeTokens` matching the resolved preference."""
    return LIGHT_THEME if resolve_theme_mode(preference) == "light" else DARK_THEME


def apply_windows_titlebar_style(hwnd: int, *, dark: bool) -> None:
    """Match native Windows 11 DWM titlebar colors to the active WinUI 3 surface."""
    if not hwnd:
        return
    tokens = DARK_THEME if dark else LIGHT_THEME
    with contextlib.suppress(Exception):
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

        dark_mode = ctypes.c_int(1 if dark else 0)
        caption_color = wintypes.DWORD(tokens.dwm_caption_color)
        text_color = wintypes.DWORD(tokens.dwm_text_color)

        for attr, val in (
            (20, dark_mode),  # DWMWA_USE_IMMERSIVE_DARK_MODE
            (35, caption_color),  # DWMWA_CAPTION_COLOR
            (36, text_color),  # DWMWA_TEXT_COLOR
        ):
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(attr),
                ctypes.byref(val),
                ctypes.sizeof(val),
            )


def build_app_qss(t: ThemeTokens) -> str:
    """Build the complete Windows 11 Fluent / WinUI 3 QSS for the active `ThemeTokens`."""
    return f"""
QMainWindow, QWidget#CentralRoot, QWidget#ScrollContent, QFrame#Sidebar {{
    background-color: {t.bg_base};
    color: {t.text_primary};
    font-family: "Segoe UI Variable Text", "Segoe UI";
    font-size: 13px;
    border: none;
}}

QFrame#StatusBox {{
    background-color: {t.bg_subtle};
    border: 1px solid {t.card_border};
    border-radius: 4px;
}}

QFrame#Card, QFrame#HistoryCard, QFrame#SettingsCard {{
    background-color: {t.bg_card};
    border: 1px solid {t.card_border};
    border-radius: 4px;
}}

QFrame#HistoryCard:hover {{
    border-color: {t.control_border};
}}

QFrame#Divider {{
    background-color: {t.divider};
    border: none;
    min-height: 1px;
    max-height: 1px;
}}

/* Typography hierarchy */
QLabel#BrandTitle {{
    font-size: 16px;
    font-weight: 600;
    color: {t.text_primary};
}}

QLabel#BrandVersion {{
    font-size: 11px;
    font-weight: 400;
    color: {t.text_tertiary};
}}

QLabel#PageTitle {{
    font-size: 24px;
    font-weight: 600;
    color: {t.text_primary};
}}

QLabel#PageSubtitle {{
    font-size: 13px;
    color: {t.text_secondary};
}}

QLabel#SectionTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {t.text_primary};
}}

QLabel#CardTitle {{
    font-size: 14px;
    font-weight: 500;
    color: {t.text_primary};
}}

QLabel#CardDesc {{
    font-size: 12px;
    color: {t.text_secondary};
}}

QLabel#MutedText {{
    font-size: 12px;
    color: {t.text_secondary};
}}

QLabel#HardwareMeta {{
    font-size: 11px;
    color: {t.text_tertiary};
}}

QLabel#ShortcutHint {{
    font-size: 11px;
    color: {t.text_tertiary};
}}

QLabel#BodyText {{
    font-size: 14px;
    color: {t.text_primary};
}}

QLabel#RawQuote {{
    font-size: 12px;
    color: {t.text_secondary};
    border-left: 2px solid {t.divider};
    padding-left: 10px;
}}

/* Tags */
QLabel#TagNeutral {{
    background-color: {t.bg_subtle};
    color: {t.text_secondary};
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 11px;
    font-weight: 600;
}}

QLabel#TagEdit {{
    background-color: {t.caution_bg};
    color: {t.caution};
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 11px;
    font-weight: 600;
}}

/* Buttons (WinUI 3 4px geometry) */
QPushButton {{
    background-color: {t.control_bg};
    border: 1px solid {t.control_border};
    border-bottom-color: {t.control_bottom_border};
    border-radius: 4px;
    color: {t.text_primary};
    padding: 6px 14px;
    font-weight: 600;
}}

QPushButton:hover {{
    background-color: {t.control_hover};
}}

QPushButton:pressed {{
    background-color: {t.control_pressed};
}}

QPushButton:disabled {{
    background-color: {t.bg_subtle};
    border-color: {t.card_border};
    color: {t.text_disabled};
}}

QPushButton#PrimaryBtn {{
    background-color: {t.accent};
    border: 1px solid {t.accent};
    border-radius: 4px;
    color: {t.text_on_accent};
    padding: 7px 14px;
    font-weight: 600;
}}

QPushButton#PrimaryBtn:hover {{
    background-color: {t.accent_hover};
    border-color: {t.accent_hover};
    color: {t.text_on_accent};
}}

QPushButton#PrimaryBtn:pressed {{
    background-color: {t.accent_pressed};
    border-color: {t.accent_pressed};
    color: {t.text_on_accent};
}}

QPushButton#PrimaryBtn:disabled {{
    background-color: {t.bg_subtle};
    border-color: {t.card_border};
    color: {t.text_disabled};
}}

QPushButton#DangerBtn {{
    background-color: {t.danger};
    border: 1px solid {t.danger};
    border-radius: 4px;
    color: #FFFFFF;
    padding: 7px 14px;
    font-weight: 600;
}}

QPushButton#DangerBtn:hover {{
    background-color: {t.danger_hover};
    border-color: {t.danger_hover};
    color: #FFFFFF;
}}

QPushButton#SubtleDangerBtn {{
    background-color: {t.control_bg};
    border: 1px solid {t.control_border};
    border-radius: 4px;
    color: {t.subtle_danger_text};
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 600;
}}

QPushButton#SubtleDangerBtn:hover {{
    background-color: {t.subtle_danger_hover_bg};
    border-color: {t.danger};
    color: {t.subtle_danger_text};
}}

QPushButton#SmallBtn {{
    background-color: {t.control_bg};
    border: 1px solid {t.control_border};
    border-radius: 4px;
    color: {t.text_secondary};
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 600;
}}

QPushButton#SmallBtn:hover {{
    background-color: {t.control_hover};
    color: {t.text_primary};
}}

QPushButton#HotkeyBtn {{
    background-color: {t.control_bg};
    border: 1px solid {t.control_border};
    border-bottom-color: {t.control_bottom_border};
    border-radius: 4px;
    color: {t.text_primary};
    font-weight: 600;
    text-align: left;
    padding: 6px 12px;
}}

QPushButton#HotkeyBtn:hover {{
    background-color: {t.control_hover};
    border-color: {t.accent};
}}

QPushButton#SegmentBtn {{
    background-color: {t.control_bg};
    border: 1px solid {t.control_border};
    border-bottom-color: {t.control_bottom_border};
    border-radius: 4px;
    padding: 6px 14px;
    color: {t.text_secondary};
    font-weight: 600;
}}

QPushButton#SegmentBtn:hover {{
    background-color: {t.control_hover};
    color: {t.text_primary};
}}

QPushButton#SegmentBtn:checked {{
    background-color: {t.accent};
    border-color: {t.accent};
    color: {t.text_on_accent};
    font-weight: 600;
}}

/* Inputs & ComboBoxes */
QLineEdit, QPlainTextEdit, QComboBox {{
    background-color: {t.input_bg};
    border: 1px solid {t.input_border};
    border-bottom-color: {t.input_bottom_border};
    border-radius: 4px;
    color: {t.text_primary};
    padding: 6px 12px;
    selection-background-color: {t.accent};
    selection-color: {t.text_on_accent};
}}

QComboBox {{
    background-color: {t.control_bg};
    padding: 6px 32px 6px 12px;
}}

QComboBox:hover {{
    background-color: {t.control_hover};
}}

QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    background-color: {t.input_focus_bg};
    border: 1px solid {t.input_border};
    border-bottom: 2px solid {t.accent};
}}

QComboBox::drop-down {{
    border: none;
    width: 28px;
    background: transparent;
}}

QComboBox::down-arrow {{
    image: none;
    width: 0px;
    height: 0px;
}}

QComboBox QAbstractItemView {{
    background-color: {t.menu_bg};
    border: 1px solid {t.menu_border};
    border-radius: 8px;
    color: {t.text_primary};
    selection-background-color: {t.nav_active};
    selection-color: {t.text_primary};
    outline: none;
    padding: 4px 0px;
}}

QComboBox QAbstractItemView::item {{
    padding: 7px 12px;
    min-height: 22px;
}}

/* Progress Bar */
QProgressBar {{
    background-color: {t.control_border};
    border: none;
    border-radius: 2px;
    min-height: 4px;
    max-height: 4px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {t.accent};
    border-radius: 2px;
}}

/* Scroll Areas & Thin Fluent Scrollbars */
QScrollArea {{
    border: none;
    background-color: transparent;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 7px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {t.scrollbar_handle};
    border-radius: 3px;
    min-height: 28px;
}}

QScrollBar::handle:vertical:hover {{
    background: {t.scrollbar_hover};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

/* Menus, Tooltips, Dialogs (8px overlays) */
QMenu {{
    background-color: {t.menu_bg};
    border: 1px solid {t.menu_border};
    border-radius: 8px;
    padding: 4px;
    color: {t.text_primary};
}}

QMenu::item {{
    padding: 6px 20px;
    border-radius: 4px;
}}

QMenu::item:selected {{
    background-color: {t.nav_hover};
    color: {t.text_primary};
}}

QMenu::separator {{
    height: 1px;
    background-color: {t.divider};
    margin: 4px 8px;
}}

QToolTip {{
    background-color: {t.menu_bg};
    color: {t.text_primary};
    border: 1px solid {t.menu_border};
    border-radius: 4px;
    padding: 5px 9px;
    font-size: 12px;
}}

QMessageBox {{
    background-color: {t.bg_card};
    color: {t.text_primary};
}}
"""


APP_QSS = build_app_qss(DARK_THEME)


def get_app_qicon() -> QIcon:
    """Return the multi-resolution Windows ICO / PNG icon for window and taskbar."""
    if APP_ICON_ICO.exists():
        icon = QIcon(str(APP_ICON_ICO))
        if not icon.isNull():
            return icon
    if APP_ICON_PNG.exists():
        icon = QIcon(str(APP_ICON_PNG))
        if not icon.isNull():
            return icon
    return create_mic_qicon("idle")


def create_mic_qicon(state: str = "idle") -> QIcon:
    """Return the branded tray icon with a live WinUI 3 status dot, or vector fallback."""
    if APP_ICON_PNG.exists():
        base = QPixmap(str(APP_ICON_PNG))
        if not base.isNull():
            pixmap = base.scaled(
                64,
                64,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            badge_hex = {
                "listening": DARK_THEME.success,
                "busy": DARK_THEME.caution,
                "error": DARK_THEME.danger,
            }.get(state)
            if badge_hex:
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setBrush(QColor(badge_hex))
                painter.setPen(QPen(QColor(LIGHT_THEME.bg_base), 3.0))
                painter.drawEllipse(QRectF(40, 40, 20, 20))
                painter.end()
            return QIcon(pixmap)

    bg_hex, ring_hex = {
        "idle": ("#1E2B37", "#FF7A00"),
        "listening": ("#0F7B0F", "#6CCB5F"),
        "busy": ("#9D5D00", "#F7B955"),
        "error": ("#C42B1C", "#FF99A4"),
    }.get(state, ("#1E2B37", "#FF7A00"))

    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    painter.setBrush(QColor(bg_hex))
    painter.setPen(QPen(QColor(ring_hex), 3.0))
    painter.drawRoundedRect(QRectF(4, 4, 56, 56), 16, 16)

    painter.setBrush(QColor("#FFFFFF"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(25, 13, 14, 23), 7, 7)

    arc_pen = QPen(QColor("#FFFFFF"), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(arc_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(19, 19, 26, 23), 180 * 16, 180 * 16)
    painter.drawLine(32, 42, 32, 50)
    painter.drawLine(24, 50, 40, 50)

    painter.end()
    return QIcon(pixmap)

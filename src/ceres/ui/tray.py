"""Native Qt 6 System Tray integration for Ceres."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from ceres.config import ConfigManager
from ceres.ui.theme import create_mic_qicon


class TrayManager:
    """System tray icon with quick mode/language switches and zero redundant redraws."""

    def __init__(
        self,
        parent: QWidget,
        config_manager: ConfigManager,
        *,
        on_open_window: Callable[[], None],
        on_toggle_dictation: Callable[[], None],
        on_start_edit: Callable[[], None],
        on_change_rewrite_mode: Callable[[str], None],
        on_change_language: Callable[[str], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._config_manager = config_manager
        self._icons = {state: create_mic_qicon(state) for state in ("idle", "listening", "busy", "error")}
        self._tray = QSystemTrayIcon(self._icons["idle"], parent)
        self._tray.setToolTip("Ceres — Local NPU Voice Dictation")

        self._menu = QMenu(parent)
        open_act = self._menu.addAction("Open Ceres")
        open_act.triggered.connect(on_open_window)

        dictate_act = self._menu.addAction("Start / Stop Dictation")
        dictate_act.triggered.connect(on_toggle_dictation)

        edit_act = self._menu.addAction("Voice Edit Selection")
        edit_act.triggered.connect(on_start_edit)

        self._menu.addSeparator()

        # Rewrite Style submenu
        rw_menu = self._menu.addMenu("Rewrite Style")
        self._rw_group = QActionGroup(rw_menu)
        self._rw_group.setExclusive(True)
        self._rw_actions: dict[str, QAction] = {}
        for mode_id, label in (
            ("fluent", "Fluent Rewrite"),
            ("light", "Light Cleanup"),
            ("verbatim", "Verbatim (Whisper Only)"),
        ):
            act = rw_menu.addAction(label)
            act.setCheckable(True)
            act.triggered.connect(lambda _checked=False, m=mode_id: on_change_rewrite_mode(m))
            self._rw_group.addAction(act)
            self._rw_actions[mode_id] = act

        # Language submenu
        lang_menu = self._menu.addMenu("Spoken Language")
        self._lang_group = QActionGroup(lang_menu)
        self._lang_group.setExclusive(True)
        self._lang_actions: dict[str, QAction] = {}
        for lang_id, label in (
            ("auto", "Auto (EN / NL / DE)"),
            ("en", "English"),
            ("nl", "Nederlands"),
            ("de", "Deutsch"),
        ):
            act = lang_menu.addAction(label)
            act.setCheckable(True)
            act.triggered.connect(lambda _checked=False, lg=lang_id: on_change_language(lg))
            self._lang_group.addAction(act)
            self._lang_actions[lang_id] = act

        self._menu.addSeparator()
        quit_act = self._menu.addAction("Quit Ceres")
        quit_act.triggered.connect(on_quit)

        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(
            lambda reason: (
                on_open_window() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None
            )
        )

        self._last_icon_key = "idle"
        self._last_tooltip = ""
        self.sync_from_config()

    def start(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

    def sync_from_config(self) -> None:
        cfg = self._config_manager.config
        if cfg.rewrite_mode in self._rw_actions:
            self._rw_actions[cfg.rewrite_mode].setChecked(True)
        if cfg.language in self._lang_actions:
            self._lang_actions[cfg.language].setChecked(True)

    def set_state(self, state: str, tooltip: str) -> None:
        icon_key = (
            "listening"
            if state == "listening"
            else (
                "busy"
                if state in ("downloading", "compiling", "transcribing", "rewriting")
                else ("error" if state == "error" else "idle")
            )
        )
        if icon_key != self._last_icon_key:
            self._last_icon_key = icon_key
            self._tray.setIcon(self._icons[icon_key])
        short_tip = tooltip[:120]
        if short_tip != self._last_tooltip:
            self._last_tooltip = short_tip
            self._tray.setToolTip(short_tip)

    def stop(self) -> None:
        self._tray.hide()

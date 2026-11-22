"""Windows 11 Fluent / WinUI 3 PySide6 interface with automatic Light/Dark theme sync."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ceres import APP_ICON_ICO, APP_ICON_PNG, MODELS_DIR, PROJECT_ROOT, __version__
from ceres.config import ConfigManager
from ceres.engine.model_manager import LLM_CATALOG, WHISPER_CATALOG
from ceres.engine.pipeline import DictationController, PipelineEvent
from ceres.history import HistoryEntry, HistoryStore, export_markdown
from ceres.system.audio import list_input_devices
from ceres.system.autostart import set_autostart_enabled
from ceres.system.hotkey import GlobalHotkeyManager, format_hotkey_display
from ceres.system.injector import set_clipboard_text
from ceres.ui.overlay_pill import FloatingMicPill
from ceres.ui.theme import (
    FLUENT_ICON_FONT,
    apply_windows_titlebar_style,
    build_app_qss,
    get_app_qicon,
    get_theme_tokens,
)
from ceres.ui.tray import TrayManager
from ceres.ui.widgets import CleanComboBox, FluentNavButton, FluentToggleSwitch

logger = logging.getLogger(__name__)

_BRAND_NOISE_RE = re.compile(r"\((?:R|TM)\)", re.IGNORECASE)


class _SignalBridge(QObject):
    pipeline_event = Signal(object)
    audio_level = Signal(float, float)
    hotkey_start = Signal(str)
    hotkey_stop = Signal()
    hotkey_cancel = Signal()
    hotkey_captured = Signal(str, str)


class CeresWindow(QMainWindow):
    """Main HiDPI Windows 11 Fluent window coordinating NPU engines, tray, hotkeys, and pill."""

    def __init__(self, *, minimized: bool = False, skip_model_load: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("Ceres — Local NPU Voice Dictation")
        self.setWindowIcon(get_app_qicon())
        self.resize(980, 680)
        self.setMinimumSize(820, 560)

        self._closing = False
        self._dwm_styled = False
        self._capturing_target: str | None = None
        self._last_pipeline_state = "compiling"

        self._nav_buttons: list[FluentNavButton] = []
        self._toggle_switches: list[FluentToggleSwitch] = []
        self._combo_boxes: list[CleanComboBox] = []
        self._card_icons: list[QLabel] = []

        self.config_manager = ConfigManager()
        self.history_store = HistoryStore()
        self._tokens = get_theme_tokens(self.config_manager.config.theme)
        self._bridge = _SignalBridge(self)

        self.controller = DictationController(
            self.config_manager,
            self.history_store,
            on_event=lambda ev: self._bridge.pipeline_event.emit(ev),
            on_level=lambda lvl, secs: self._bridge.audio_level.emit(lvl, secs),
        )

        self.pill = FloatingMicPill(
            saved_position=self.config_manager.config.pill_position,
            on_position_changed=self._on_pill_position_changed,
            theme=self._tokens,
        )

        cfg = self.config_manager.config
        self.hotkey_manager = GlobalHotkeyManager(
            hotkey=cfg.hotkey,
            edit_hotkey=cfg.edit_hotkey,
            trigger_mode=cfg.trigger_mode,
            on_start=lambda action: self._bridge.hotkey_start.emit(action),
            on_stop=lambda: self._bridge.hotkey_stop.emit(),
            on_cancel=lambda: self._bridge.hotkey_cancel.emit(),
        )
        self.tray = TrayManager(
            self,
            self.config_manager,
            on_open_window=self.show_window,
            on_toggle_dictation=self._toggle_manual_dictation,
            on_start_edit=lambda: self._handle_hotkey_start("edit"),
            on_change_rewrite_mode=self._set_rewrite_mode,
            on_change_language=self._set_language,
            on_quit=self.shutdown,
        )

        self._bridge.pipeline_event.connect(self._on_pipeline_event)
        self._bridge.audio_level.connect(self.pill.set_audio_level)
        self._bridge.hotkey_start.connect(self._handle_hotkey_start)
        self._bridge.hotkey_stop.connect(self._handle_hotkey_stop)
        self._bridge.hotkey_cancel.connect(self._handle_hotkey_cancel)
        self._bridge.hotkey_captured.connect(self._finish_hotkey_capture)

        with contextlib.suppress(Exception):
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_color_scheme_changed)

        self._mic_devices = list_input_devices()
        self._build_ui()
        self._apply_theme()
        self._sync_ui_from_config()
        self._refresh_history()

        self.hotkey_manager.start()
        self.tray.start()

        if not minimized:
            self.show()

        if not skip_model_load:
            self.controller.ensure_ready_async()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._dwm_styled:
            self._dwm_styled = True
            apply_windows_titlebar_style(int(self.winId()), dark=self._tokens.is_dark)

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        if self._closing:
            event.accept()
            return
        event.ignore()
        self.hide()

    def _on_system_color_scheme_changed(self, _scheme: object = None) -> None:
        if self.config_manager.config.theme == "system":
            self._apply_theme()

    def _set_theme(self, mode: str) -> None:
        self.config_manager.update(theme=mode)
        self._apply_theme()
        self._sync_ui_from_config()

    def _apply_theme(self) -> None:
        self._tokens = get_theme_tokens(self.config_manager.config.theme)
        self.setStyleSheet(build_app_qss(self._tokens))
        apply_windows_titlebar_style(int(self.winId()), dark=self._tokens.is_dark)

        for nav in self._nav_buttons:
            nav.set_theme(self._tokens)
        for sw in self._toggle_switches:
            sw.set_theme(self._tokens)
        for cb in self._combo_boxes:
            cb.set_theme(self._tokens)
        for icon_lbl in self._card_icons:
            icon_lbl.setStyleSheet(f"color: {self._tokens.text_secondary};")

        self.pill.set_theme(self._tokens)
        self._update_status_appearance(self._last_pipeline_state)
        self._refresh_history()

    def _update_status_appearance(self, state: str) -> None:
        t = self._tokens
        color_map = {
            "ready": t.success,
            "done": t.success,
            "listening": t.success,
            "downloading": t.caution,
            "compiling": t.caution,
            "transcribing": t.caution,
            "rewriting": t.caution,
            "error": t.error_text,
        }
        dot_color = color_map.get(state, t.text_tertiary)
        self._status_dot.setStyleSheet(f"color: {dot_color}; font-size: 11px;")
        self._status_label.setStyleSheet(f"color: {t.text_primary}; font-size: 12px;")

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("CentralRoot")
        self.setCentralWidget(root)

        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addWidget(self._build_sidebar())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_history_page())
        self._stack.addWidget(self._build_voice_page())
        self._stack.addWidget(self._build_shortcuts_system_page())
        self._stack.addWidget(self._build_engine_page())
        main_layout.addWidget(self._stack, 1)

        self._nav_group.idClicked.connect(self._stack.setCurrentIndex)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(232)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 18, 12, 16)
        layout.setSpacing(14)

        # Brand identity row
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        if APP_ICON_PNG.exists():
            pix = QPixmap(str(APP_ICON_PNG))
            if not pix.isNull():
                icon_lbl = QLabel()
                icon_lbl.setFixedSize(26, 26)
                icon_lbl.setPixmap(
                    pix.scaled(
                        26,
                        26,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                brand_row.addWidget(icon_lbl)

        brand_title = QLabel("Ceres")
        brand_title.setObjectName("BrandTitle")
        brand_ver = QLabel(f"v{__version__}")
        brand_ver.setObjectName("BrandVersion")
        brand_row.addWidget(brand_title)
        brand_row.addWidget(brand_ver)
        brand_row.addStretch(1)
        layout.addLayout(brand_row)

        # Primary dictation CTA & compact shortcut hint
        cta_box = QVBoxLayout()
        cta_box.setSpacing(6)
        self._dictate_btn = QPushButton("Start Dictation")
        self._dictate_btn.setObjectName("PrimaryBtn")
        self._dictate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dictate_btn.clicked.connect(self._toggle_manual_dictation)
        cta_box.addWidget(self._dictate_btn)

        self._shortcuts_hint = QLabel()
        self._shortcuts_hint.setObjectName("ShortcutHint")
        self._shortcuts_hint.setWordWrap(True)
        cta_box.addWidget(self._shortcuts_hint)
        layout.addLayout(cta_box)

        # WinUI 3 NavigationView items
        nav_box = QVBoxLayout()
        nav_box.setSpacing(2)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        nav_items = (
            (0, "\ue720", "Dictate & History"),
            (1, "\ue774", "Voice & Language"),
            (2, "\ue765", "Shortcuts & System"),
            (3, "\ue950", "Engine & Models"),
        )
        for idx, glyph, caption in nav_items:
            btn = FluentNavButton(glyph, caption, tokens=self._tokens)
            if idx == 0:
                btn.setChecked(True)
            self._nav_group.addButton(btn, idx)
            self._nav_buttons.append(btn)
            nav_box.addWidget(btn)
        layout.addLayout(nav_box)

        layout.addStretch(1)

        # Quiet bottom status & hardware box
        status_box = QFrame()
        status_box.setObjectName("StatusBox")
        st_layout = QVBoxLayout(status_box)
        st_layout.setContentsMargins(12, 10, 12, 10)
        st_layout.setSpacing(6)

        st_top = QHBoxLayout()
        st_top.setSpacing(8)
        self._status_dot = QLabel("●")
        self._status_dot.setFixedWidth(12)
        self._status_dot.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self._status_label = QLabel("Initializing local OpenVINO engines...")
        self._status_label.setWordWrap(True)
        st_top.addWidget(self._status_dot)
        st_top.addWidget(self._status_label, 1)
        st_layout.addLayout(st_top)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(5)
        self._progress_bar.setTextVisible(False)
        st_layout.addWidget(self._progress_bar)

        self._hw_badge = QLabel(self._format_hardware_badge())
        self._hw_badge.setObjectName("HardwareMeta")
        self._hw_badge.setWordWrap(True)
        st_layout.addWidget(self._hw_badge)

        layout.addWidget(status_box)
        return sidebar

    def _build_history_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("ScrollContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        layout.addLayout(
            self._make_page_header(
                "Dictate & History",
                "Dictate into any app using your global shortcut, or test below.",
            )
        )

        # Scratchpad card
        pad_card = QFrame()
        pad_card.setObjectName("Card")
        pad_layout = QVBoxLayout(pad_card)
        pad_layout.setContentsMargins(18, 14, 18, 14)
        pad_layout.setSpacing(8)

        pad_hdr = QHBoxLayout()
        pad_title = QLabel("Scratchpad")
        pad_title.setObjectName("SectionTitle")
        clear_pad_btn = QPushButton("Clear")
        clear_pad_btn.setObjectName("SmallBtn")
        clear_pad_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pad_hdr.addWidget(pad_title)
        pad_hdr.addStretch(1)
        pad_hdr.addWidget(clear_pad_btn)
        pad_layout.addLayout(pad_hdr)

        self._test_pad = QPlainTextEdit()
        self._test_pad.setFixedHeight(86)
        self._test_pad.setPlaceholderText(
            "Click inside and press your Dictate shortcut for English, Dutch, or German.\n"
            "Or highlight text, press Voice Edit, and speak an instruction such as 'Make this concise'."
        )
        clear_pad_btn.clicked.connect(self._test_pad.clear)
        pad_layout.addWidget(self._test_pad)
        layout.addWidget(pad_card)

        # History toolbar
        hist_hdr = QHBoxLayout()
        hist_hdr.setSpacing(10)
        hist_lbl = QLabel("Recent Dictations")
        hist_lbl.setObjectName("SectionTitle")

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search transcripts...")
        self._search_input.textChanged.connect(lambda _t: self._refresh_history())

        export_btn = QPushButton("Export .md")
        export_btn.setObjectName("SmallBtn")
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        export_btn.clicked.connect(self._export_history_markdown)

        clear_hist_btn = QPushButton("Clear All")
        clear_hist_btn.setObjectName("SubtleDangerBtn")
        clear_hist_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_hist_btn.clicked.connect(self._clear_history)

        hist_hdr.addWidget(hist_lbl)
        hist_hdr.addWidget(self._search_input, 1)
        hist_hdr.addWidget(export_btn)
        hist_hdr.addWidget(clear_hist_btn)
        layout.addLayout(hist_hdr)

        # Scrollable History Feed
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._history_container = QWidget()
        self._history_container.setObjectName("ScrollContent")
        self._history_layout = QVBoxLayout(self._history_container)
        self._history_layout.setContentsMargins(0, 0, 6, 0)
        self._history_layout.setSpacing(8)
        self._history_layout.addStretch(1)
        scroll.setWidget(self._history_container)
        layout.addWidget(scroll, 1)
        return page

    def _build_voice_page(self) -> QWidget:
        scroll, layout = self._make_scrollable_page(
            "Voice & Language",
            "Configure rewrite style, supported languages, and domain vocabulary.",
        )

        cards_col = QVBoxLayout()
        cards_col.setSpacing(6)

        self._rewrite_group, row_rw = self._make_segmented_group(
            [("fluent", "Fluent"), ("light", "Light"), ("verbatim", "Verbatim")],
            self._set_rewrite_mode,
        )
        rw_card, _ = self._make_settings_card(
            "\ue70f",
            "Rewrite style",
            "How strongly Qwen3 polishes grammar and filler words.",
            row_rw,
        )
        cards_col.addWidget(rw_card)

        self._lang_group, row_lang = self._make_segmented_group(
            [("auto", "Auto (EN/NL/DE)"), ("en", "English"), ("nl", "Nederlands"), ("de", "Deutsch")],
            self._set_language,
        )
        lang_card, _ = self._make_settings_card(
            "\ue774",
            "Spoken language",
            "Choose auto-detection or lock speech recognition to one language.",
            row_lang,
            stacked=True,
        )
        cards_col.addWidget(lang_card)

        vocab_box = QVBoxLayout()
        vocab_box.setSpacing(10)
        self._vocab_edit = QPlainTextEdit()
        self._vocab_edit.setFixedHeight(76)
        self._vocab_edit.setPlaceholderText("e.g. OpenVINO, Kubernetes, DSGVO, Ceres, jbuscher-bds")
        vocab_actions = QHBoxLayout()
        vocab_actions.addStretch(1)
        save_vocab_btn = QPushButton("Save Vocabulary")
        save_vocab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_vocab_btn.clicked.connect(self._save_vocabulary)
        vocab_actions.addWidget(save_vocab_btn)
        vocab_box.addWidget(self._vocab_edit)
        vocab_box.addLayout(vocab_actions)

        vocab_card, _ = self._make_settings_card(
            "\ue765",
            "Custom vocabulary",
            "Domain terms, names, or acronyms (comma or newline separated).",
            vocab_box,
            stacked=True,
        )
        cards_col.addWidget(vocab_card)

        layout.addLayout(cards_col)
        layout.addStretch(1)
        return scroll

    def _build_shortcuts_system_page(self) -> QWidget:
        scroll, layout = self._make_scrollable_page(
            "Shortcuts & System",
            "Manage appearance, global hotkeys, microphone selection, and Windows integration.",
        )

        # Appearance section
        app_hdr = QLabel("Appearance")
        app_hdr.setObjectName("SectionTitle")
        layout.addWidget(app_hdr)

        app_cards = QVBoxLayout()
        app_cards.setSpacing(6)
        self._theme_group, row_theme = self._make_segmented_group(
            [("system", "System"), ("light", "Light"), ("dark", "Dark")],
            self._set_theme,
        )
        theme_card, _ = self._make_settings_card(
            "\ue790",
            "App theme",
            "Follow Windows 11 Light/Dark settings automatically or lock a mode.",
            row_theme,
        )
        app_cards.addWidget(theme_card)
        layout.addLayout(app_cards)

        # Shortcuts section
        hk_hdr = QLabel("Shortcuts")
        hk_hdr.setObjectName("SectionTitle")
        layout.addWidget(hk_hdr)

        hk_cards = QVBoxLayout()
        hk_cards.setSpacing(6)

        self._hk_record_btn = QPushButton()
        self._hk_record_btn.setObjectName("HotkeyBtn")
        self._hk_record_btn.setFixedWidth(190)
        self._hk_record_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hk_record_btn.clicked.connect(lambda: self._start_hotkey_capture("dictate"))
        dict_hk_card, _ = self._make_settings_card(
            "\ue765",
            "Dictation shortcut",
            "Global shortcut to start or stop speech dictation (Esc cancels).",
            self._hk_record_btn,
        )
        hk_cards.addWidget(dict_hk_card)

        self._edit_hk_btn = QPushButton()
        self._edit_hk_btn.setObjectName("HotkeyBtn")
        self._edit_hk_btn.setFixedWidth(190)
        self._edit_hk_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_hk_btn.clicked.connect(lambda: self._start_hotkey_capture("edit"))
        edit_hk_card, _ = self._make_settings_card(
            "\ue70f",
            "Voice Edit shortcut",
            "Highlight text in any window, press hotkey, and speak an edit command.",
            self._edit_hk_btn,
        )
        hk_cards.addWidget(edit_hk_card)

        self._trigger_group, row_trig = self._make_segmented_group(
            [("hybrid", "Hybrid"), ("toggle", "Toggle"), ("hold", "Hold")],
            self._on_trigger_selected,
        )
        trig_card, _ = self._make_settings_card(
            "\ue718",
            "Trigger behavior",
            "Tap to toggle or hold push-to-talk when using your dictation hotkey.",
            row_trig,
        )
        hk_cards.addWidget(trig_card)
        layout.addLayout(hk_cards)

        # Audio & Windows section
        sys_hdr = QLabel("Audio & Windows")
        sys_hdr.setObjectName("SectionTitle")
        layout.addWidget(sys_hdr)

        sys_cards = QVBoxLayout()
        sys_cards.setSpacing(6)

        self._mic_combo = CleanComboBox(tokens=self._tokens)
        self._mic_combo.addItem("System Default", None)
        for dev in self._mic_devices:
            self._mic_combo.addItem(str(dev["name"]), int(dev["index"]))
        self._mic_combo.currentIndexChanged.connect(self._on_mic_selected)
        self._combo_boxes.append(self._mic_combo)
        mic_card, _ = self._make_settings_card(
            "\ue720",
            "Input microphone",
            "Audio capture device used for 16 kHz local transcription.",
            self._mic_combo,
            stacked=True,
        )
        sys_cards.addWidget(mic_card)

        self._auto_paste_chk = FluentToggleSwitch("", tokens=self._tokens, show_state_label=True)
        self._preserve_clip_chk = FluentToggleSwitch("", tokens=self._tokens, show_state_label=True)
        self._play_chime_chk = FluentToggleSwitch("", tokens=self._tokens, show_state_label=True)
        self._autostart_chk = FluentToggleSwitch("", tokens=self._tokens, show_state_label=True)

        for chk in (
            self._auto_paste_chk,
            self._preserve_clip_chk,
            self._play_chime_chk,
            self._autostart_chk,
        ):
            self._toggle_switches.append(chk)

        for chk in (self._auto_paste_chk, self._preserve_clip_chk, self._play_chime_chk):
            chk.toggled.connect(self._save_checkbox_settings)
        self._autostart_chk.toggled.connect(self._on_autostart_toggled)

        toggle_rows = (
            (
                "\ue77f",
                "Auto-paste",
                "Automatically paste dictated text into the active window.",
                self._auto_paste_chk,
            ),
            (
                "\ue8c8",
                "Restore clipboard",
                "Preserve and restore your previous clipboard text after pasting.",
                self._preserve_clip_chk,
            ),
            ("\ue767", "Sound feedback", "Play subtle start and stop audio cues.", self._play_chime_chk),
            (
                "\ue770",
                "Start with Windows",
                "Launch Ceres minimized in the system tray at login.",
                self._autostart_chk,
            ),
        )
        for glyph, title, desc, chk in toggle_rows:
            card, _ = self._make_settings_card(glyph, title, desc, chk)
            sys_cards.addWidget(card)

        layout.addLayout(sys_cards)
        layout.addStretch(1)
        return scroll

    def _build_engine_page(self) -> QWidget:
        scroll, layout = self._make_scrollable_page(
            "Engine & Models",
            "Select OpenVINO NPU/GPU accelerators, local speech & rewriter models, and desktop helpers.",
        )

        rt_hdr = QLabel("Local AI Runtime")
        rt_hdr.setObjectName("SectionTitle")
        layout.addWidget(rt_hdr)

        rt_cards = QVBoxLayout()
        rt_cards.setSpacing(6)

        self._device_group, row_dev = self._make_segmented_group(
            [("NPU", "NPU"), ("GPU", "GPU"), ("CPU", "CPU")],
            lambda _dev: None,
        )
        dev_card, _ = self._make_settings_card(
            "\ue950",
            "AI accelerator",
            "Preferred OpenVINO device for local inference and model compilation.",
            row_dev,
        )
        rt_cards.addWidget(dev_card)

        self._whisper_combo = CleanComboBox(tokens=self._tokens)
        for idx, spec in enumerate(WHISPER_CATALOG):
            self._whisper_combo.addItem(spec.display_name, spec.repo_id)
            self._whisper_combo.setItemData(idx, spec.repo_id, Qt.ItemDataRole.ToolTipRole)
        self._combo_boxes.append(self._whisper_combo)
        whisper_card, _ = self._make_settings_card(
            "\ue720",
            "Speech model",
            "Local OpenVINO Whisper speech recognition checkpoint.",
            self._whisper_combo,
            stacked=True,
        )
        rt_cards.addWidget(whisper_card)

        self._llm_combo = CleanComboBox(tokens=self._tokens)
        for idx, spec in enumerate(LLM_CATALOG):
            self._llm_combo.addItem(spec.display_name, spec.repo_id)
            self._llm_combo.setItemData(idx, spec.repo_id, Qt.ItemDataRole.ToolTipRole)
        self._combo_boxes.append(self._llm_combo)
        llm_card, _ = self._make_settings_card(
            "\ue950",
            "Rewriter model",
            "Local OpenVINO Qwen3 model used for polishing and voice edits.",
            self._llm_combo,
            stacked=True,
        )
        rt_cards.addWidget(llm_card)

        layout.addLayout(rt_cards)

        apply_row = QHBoxLayout()
        apply_btn = QPushButton("Apply && Reload Models")
        apply_btn.setObjectName("PrimaryBtn")
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.clicked.connect(self._apply_engine_settings)
        apply_row.addWidget(apply_btn)
        apply_row.addStretch(1)
        layout.addLayout(apply_row)

        util_hdr = QLabel("Desktop Utilities")
        util_hdr.setObjectName("SectionTitle")
        layout.addWidget(util_hdr)

        util_card = QFrame()
        util_card.setObjectName("SettingsCard")
        util_layout = QHBoxLayout(util_card)
        util_layout.setContentsMargins(18, 14, 18, 14)
        util_layout.setSpacing(10)

        shortcut_btn = QPushButton("Create Desktop Shortcut")
        shortcut_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        shortcut_btn.clicked.connect(self._create_desktop_shortcut)

        models_btn = QPushButton("Open Models Folder")
        models_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        models_btn.clicked.connect(lambda: os.startfile(str(MODELS_DIR)))

        reset_pill_btn = QPushButton("Reset Pill Position")
        reset_pill_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_pill_btn.clicked.connect(self._reset_pill_position)

        util_layout.addWidget(shortcut_btn)
        util_layout.addWidget(models_btn)
        util_layout.addWidget(reset_pill_btn)
        util_layout.addStretch(1)
        layout.addWidget(util_card)

        layout.addStretch(1)
        return scroll

    def _make_scrollable_page(self, title: str, subtitle: str) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("ScrollContent")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addLayout(self._make_page_header(title, subtitle))
        scroll.setWidget(inner)
        return scroll, layout

    @staticmethod
    def _make_page_header(title: str, subtitle: str) -> QVBoxLayout:
        hdr = QVBoxLayout()
        hdr.setSpacing(3)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("PageTitle")
        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName("PageSubtitle")
        sub_lbl.setWordWrap(True)
        hdr.addWidget(title_lbl)
        hdr.addWidget(sub_lbl)
        return hdr

    def _make_settings_card(
        self,
        icon_glyph: str,
        title: str,
        description: str,
        control: QWidget | QLayout | None = None,
        *,
        stacked: bool = False,
    ) -> tuple[QFrame, QVBoxLayout]:
        """Build a WinUI 3 SettingsCard row with icon, title/description, and inline or stacked control."""
        card = QFrame()
        card.setObjectName("SettingsCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(12 if stacked else 0)

        top = QHBoxLayout()
        top.setSpacing(14)

        icon_lbl = QLabel(icon_glyph)
        icon_font = QFont(FLUENT_ICON_FONT)
        icon_font.setPixelSize(16)
        icon_lbl.setFont(icon_font)
        icon_lbl.setFixedWidth(22)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(f"color: {self._tokens.text_secondary};")
        self._card_icons.append(icon_lbl)
        top.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        t_lbl = QLabel(title)
        t_lbl.setObjectName("CardTitle")
        d_lbl = QLabel(description)
        d_lbl.setObjectName("CardDesc")
        d_lbl.setWordWrap(True)
        text_col.addWidget(t_lbl)
        text_col.addWidget(d_lbl)
        top.addLayout(text_col, 1)

        if not stacked and control is not None:
            if isinstance(control, QWidget):
                top.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
            else:
                top.addLayout(control)

        outer.addLayout(top)

        if stacked and control is not None:
            body_row = QHBoxLayout()
            body_row.setContentsMargins(36, 0, 0, 0)
            if isinstance(control, QWidget):
                body_row.addWidget(control, 1)
            else:
                body_row.addLayout(control, 1)
            outer.addLayout(body_row)

        return card, outer

    def _make_segmented_group(
        self,
        options: list[tuple[str, str]],
        on_select,
    ) -> tuple[QButtonGroup, QHBoxLayout]:
        row = QHBoxLayout()
        row.setSpacing(6)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for idx, (val, caption) in enumerate(options):
            btn = QPushButton(caption)
            btn.setObjectName("SegmentBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("seg_value", val)
            group.addButton(btn, idx)
            row.addWidget(btn)

        def _clicked(button: QPushButton) -> None:
            on_select(str(button.property("seg_value")))

        group.buttonClicked.connect(_clicked)
        return group, row

    @staticmethod
    def _set_segment_value(group: QButtonGroup, value: str) -> None:
        for btn in group.buttons():
            if btn.property("seg_value") == value:
                btn.setChecked(True)
                return

    @staticmethod
    def _get_segment_value(group: QButtonGroup, default: str) -> str:
        checked = group.checkedButton()
        return str(checked.property("seg_value")) if checked is not None else default

    @staticmethod
    def _clean_device_name(name: str) -> str:
        cleaned = _BRAND_NOISE_RE.sub("", name)
        return " ".join(cleaned.split())

    def _format_hardware_badge(self) -> str:
        devs = self.controller.available_devices
        npu = self._clean_device_name(devs["NPU"]) if devs.get("NPU") else ""
        gpu = self._clean_device_name(devs["GPU"]) if devs.get("GPU") else ""
        if npu and gpu:
            return f"NPU: {npu}\nGPU: {gpu}"
        if npu:
            return f"NPU: {npu}"
        if gpu:
            return f"GPU: {gpu}"
        return f"Device: {self._clean_device_name(devs.get('CPU', 'CPU'))}"

    def _sync_ui_from_config(self) -> None:
        cfg = self.config_manager.config
        hk_disp = format_hotkey_display(cfg.hotkey)
        edit_disp = format_hotkey_display(cfg.edit_hotkey)
        trig_disp = {"hybrid": "Hybrid", "toggle": "Toggle", "hold": "Hold"}.get(cfg.trigger_mode, "Hybrid")

        self._shortcuts_hint.setText(f"Dictate: {hk_disp} ({trig_disp})\nEdit: {edit_disp}")
        self._hk_record_btn.setText(hk_disp)
        self._edit_hk_btn.setText(edit_disp)

        self._set_segment_value(self._theme_group, cfg.theme)
        self._set_segment_value(self._trigger_group, cfg.trigger_mode)
        self._set_segment_value(self._rewrite_group, cfg.rewrite_mode)
        self._set_segment_value(self._lang_group, cfg.language)
        self._set_segment_value(self._device_group, cfg.preferred_device)

        self._vocab_edit.setPlainText(", ".join(cfg.custom_vocabulary))

        for widget, val in (
            (self._auto_paste_chk, cfg.auto_paste),
            (self._preserve_clip_chk, cfg.preserve_clipboard),
            (self._play_chime_chk, cfg.play_chime),
            (self._autostart_chk, cfg.start_with_windows),
        ):
            widget.blockSignals(True)
            widget.setChecked(val)
            widget.blockSignals(False)

        self._mic_combo.blockSignals(True)
        mic_idx = self._mic_combo.findData(cfg.audio_device)
        self._mic_combo.setCurrentIndex(max(0, mic_idx))
        self._mic_combo.blockSignals(False)

        w_idx = self._whisper_combo.findData(cfg.whisper_model_id)
        if w_idx >= 0:
            self._whisper_combo.setCurrentIndex(w_idx)
        l_idx = self._llm_combo.findData(cfg.llm_model_id)
        if l_idx >= 0:
            self._llm_combo.setCurrentIndex(l_idx)

        self.tray.sync_from_config()

    def _on_pill_position_changed(self, pos: list[int] | None) -> None:
        self.config_manager.update(pill_position=pos)

    def _reset_pill_position(self) -> None:
        self.config_manager.update(pill_position=None)
        self.pill._saved_position = None
        self.pill._reposition()
        self._status_label.setText("Floating pill reset to active monitor bottom-center.")

    def _handle_hotkey_start(self, action: str = "dictate") -> None:
        if self.controller.start_listening(action=action):
            self._dictate_btn.setText("Stop && Process")
            self._dictate_btn.setObjectName("DangerBtn")
            self._dictate_btn.style().unpolish(self._dictate_btn)
            self._dictate_btn.style().polish(self._dictate_btn)

    def _handle_hotkey_stop(self) -> None:
        self.controller.stop_listening_and_process()
        self._restore_dictate_btn()

    def _handle_hotkey_cancel(self) -> None:
        self.controller.cancel_listening()
        self._restore_dictate_btn()

    def _restore_dictate_btn(self) -> None:
        self._dictate_btn.setText("Start Dictation")
        self._dictate_btn.setObjectName("PrimaryBtn")
        self._dictate_btn.style().unpolish(self._dictate_btn)
        self._dictate_btn.style().polish(self._dictate_btn)

    def _toggle_manual_dictation(self) -> None:
        if self.controller.is_listening:
            self.hotkey_manager.reset_recording_state()
            self._handle_hotkey_stop()
        else:
            self._handle_hotkey_start("dictate")

    def _on_pipeline_event(self, ev: PipelineEvent) -> None:
        self._last_pipeline_state = ev.state
        if ev.state in ("listening", "transcribing", "rewriting", "done", "error") or (
            ev.state == "ready" and self.pill.isVisible()
        ):
            self.pill.show_state(
                ev.state,
                ev.message,
                action_type=ev.action_type,
            )
        self.tray.set_state(ev.state, f"Ceres — {ev.message}")
        self._update_status_appearance(ev.state)

        if ev.state in ("downloading", "compiling"):
            self._progress_bar.show()
            self._progress_bar.setValue(max(2, min(100, int(ev.progress * 100))))
            self._status_label.setText(ev.message)
        else:
            self._progress_bar.hide()
            if ev.state in ("ready", "done"):
                summary = f" ({ev.device_summary})" if ev.device_summary else ""
                self._status_label.setText(f"{ev.message}{summary}")
                self._restore_dictate_btn()
            elif ev.state == "listening":
                self._status_label.setText(f"{ev.message} (Esc to cancel)")
            else:
                self._status_label.setText(ev.message)
                if ev.state == "error":
                    self._restore_dictate_btn()

        if ev.entry is not None:
            self._refresh_history()

    def _refresh_history(self) -> None:
        while self._history_layout.count() > 1:
            item = self._history_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        query = self._search_input.text().strip() if hasattr(self, "_search_input") else ""
        entries = (
            self.history_store.search(query, limit=40) if query else self.history_store.list_recent(limit=40)
        )

        if not entries:
            empty_msg = (
                "No matching entries." if query else "No dictations yet. Press Ctrl + Shift + Space anywhere."
            )
            empty_lbl = QLabel(empty_msg)
            empty_lbl.setObjectName("MutedText")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setContentsMargins(0, 32, 0, 32)
            self._history_layout.insertWidget(0, empty_lbl)
            return

        for idx, entry in enumerate(entries):
            self._history_layout.insertWidget(idx, self._create_history_card(entry))

    def _create_history_card(self, entry: HistoryEntry) -> QFrame:
        card = QFrame()
        card.setObjectName("HistoryCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)

        if entry.action_type == "edit":
            tag_lbl = QLabel("Voice Edit")
            tag_lbl.setObjectName("TagEdit")
        else:
            lang_disp = (entry.language or "auto").upper()
            mode_disp = entry.rewrite_mode.title()
            tag_lbl = QLabel(f"{lang_disp} · {mode_disp}")
            tag_lbl.setObjectName("TagNeutral")
        meta_row.addWidget(tag_lbl)

        ts_short = entry.created_at.replace("T", " ")[:16]
        total_ms = entry.stt_ms + entry.llm_ms
        meta_lbl = QLabel(f"{ts_short} · {total_ms}ms")
        meta_lbl.setObjectName("MutedText")
        meta_lbl.setToolTip(f"STT {entry.stt_ms}ms / LLM {entry.llm_ms}ms")
        meta_row.addWidget(meta_lbl, 1)

        if entry.action_type != "edit" and entry.raw_text.strip():
            next_mode = "light" if entry.rewrite_mode == "fluent" else "fluent"
            repolish_btn = QPushButton("Re-polish")
            repolish_btn.setObjectName("SmallBtn")
            repolish_btn.setToolTip(f"Re-polish as {next_mode.title()}")
            repolish_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            repolish_btn.clicked.connect(
                lambda _checked=False, e=entry, m=next_mode: self._repolish_history_entry(e, m)
            )
            meta_row.addWidget(repolish_btn)

        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("SmallBtn")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(lambda _checked=False, t=entry.polished_text: set_clipboard_text(t))
        meta_row.addWidget(copy_btn)
        v.addLayout(meta_row)

        polished_lbl = QLabel(entry.polished_text)
        polished_lbl.setObjectName("BodyText")
        polished_lbl.setWordWrap(True)
        polished_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(polished_lbl)

        if entry.raw_text and entry.raw_text.strip() != entry.polished_text.strip():
            raw_lbl = QLabel(f"Raw: {entry.raw_text}")
            raw_lbl.setObjectName("RawQuote")
            raw_lbl.setWordWrap(True)
            raw_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.addWidget(raw_lbl)

        return card

    def _repolish_history_entry(self, entry: HistoryEntry, target_mode: str) -> None:
        if not self.controller.is_ready:
            self._status_label.setText("Models are still loading — please wait.")
            return

        def _worker() -> None:
            try:
                self.controller.repolish_entry(entry, target_mode)
            except Exception as exc:
                logger.warning("Re-polish failed: %s", exc)

        threading.Thread(target=_worker, daemon=True).start()

    def _export_history_markdown(self) -> None:
        entries = self.history_store.list_recent(limit=200)
        if not entries:
            QMessageBox.information(self, "Ceres", "No history entries to export yet.")
            return
        default_path = str(Path.home() / "Documents" / "ceres-history.md")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Dictation History",
            default_path,
            "Markdown Files (*.md);;All Files (*.*)",
        )
        if not file_path:
            return
        export_markdown(entries, Path(file_path))
        self._status_label.setText(f"Exported {len(entries)} entries to {Path(file_path).name}")

    def _clear_history(self) -> None:
        self.history_store.clear_all()
        self._refresh_history()

    def _start_hotkey_capture(self, target: str) -> None:
        if self._capturing_target is not None:
            self.hotkey_manager.cancel_recording_hotkey()
            self._capturing_target = None
            for b in (self._hk_record_btn, self._edit_hk_btn):
                b.setStyleSheet("")
            self._sync_ui_from_config()
            return
        self._capturing_target = target
        btn = self._hk_record_btn if target == "dictate" else self._edit_hk_btn
        btn.setText("Press keys... (Esc cancels)")
        btn.setStyleSheet(
            f"background-color: {self._tokens.caution_bg}; border-color: {self._tokens.caution}; "
            f"color: {self._tokens.caution}; font-weight: 600;"
        )

        def _on_captured(spec: str) -> None:
            self._bridge.hotkey_captured.emit(target, spec)

        self.hotkey_manager.start_recording_hotkey(_on_captured)

    def _finish_hotkey_capture(self, target: str, spec: str) -> None:
        self._capturing_target = None
        for btn in (self._hk_record_btn, self._edit_hk_btn):
            btn.setStyleSheet("")
        if spec:
            if target == "edit":
                self.config_manager.update(edit_hotkey=spec)
                self.hotkey_manager.update_settings(edit_hotkey=spec)
            else:
                self.config_manager.update(hotkey=spec)
                self.hotkey_manager.update_settings(hotkey=spec)
        self._sync_ui_from_config()

    def _on_trigger_selected(self, mode: str) -> None:
        self.config_manager.update(trigger_mode=mode)
        self.hotkey_manager.update_settings(trigger_mode=mode)
        self._sync_ui_from_config()

    def _set_rewrite_mode(self, mode: str) -> None:
        self.config_manager.update(rewrite_mode=mode)
        self._sync_ui_from_config()

    def _set_language(self, lang: str) -> None:
        self.config_manager.update(language=lang)
        self._sync_ui_from_config()

    def _save_vocabulary(self) -> None:
        raw = self._vocab_edit.toPlainText()
        terms = [part.strip() for chunk in raw.splitlines() for part in chunk.split(",") if part.strip()]
        cfg = self.config_manager.update(custom_vocabulary=terms)
        self._vocab_edit.setPlainText(", ".join(cfg.custom_vocabulary))
        self._status_label.setText(f"Saved {len(cfg.custom_vocabulary)} custom vocabulary terms.")

    def _on_mic_selected(self, _index: int) -> None:
        dev_idx = self._mic_combo.currentData()
        self.config_manager.update(audio_device=dev_idx)

    def _save_checkbox_settings(self) -> None:
        self.config_manager.update(
            auto_paste=self._auto_paste_chk.isChecked(),
            preserve_clipboard=self._preserve_clip_chk.isChecked(),
            play_chime=self._play_chime_chk.isChecked(),
        )

    def _on_autostart_toggled(self, enabled: bool) -> None:
        ok = set_autostart_enabled(enabled, PROJECT_ROOT)
        if ok:
            self.config_manager.update(start_with_windows=enabled)
            self._status_label.setText(
                "Windows login autostart enabled." if enabled else "Windows login autostart disabled."
            )
        else:
            self._autostart_chk.blockSignals(True)
            self._autostart_chk.setChecked(not enabled)
            self._autostart_chk.blockSignals(False)
            self._status_label.setText("Unable to update Windows autostart registry key.")

    def _apply_engine_settings(self) -> None:
        self._save_vocabulary()
        self.config_manager.update(
            preferred_device=self._get_segment_value(self._device_group, "NPU"),
            whisper_model_id=str(self._whisper_combo.currentData()),
            llm_model_id=str(self._llm_combo.currentData()),
        )
        self.controller.ensure_ready_async(force_reload=True)

    def _create_desktop_shortcut(self) -> None:
        vbs_path = Path(PROJECT_ROOT) / "launch_ceres.vbs"
        desktop_lnk = Path.home() / "Desktop" / "Ceres.lnk"
        icon_line = f"$sc.IconLocation = '{APP_ICON_ICO}'; " if APP_ICON_ICO.exists() else ""
        ps_script = (
            "$ws = New-Object -ComObject WScript.Shell; "
            f"$sc = $ws.CreateShortcut('{desktop_lnk}'); "
            "$sc.TargetPath = 'wscript.exe'; "
            f"$sc.Arguments = '\"{vbs_path}\"'; "
            f"$sc.WorkingDirectory = '{PROJECT_ROOT}'; "
            f"{icon_line}"
            "$sc.Description = 'Ceres — Local Intel NPU Voice Dictation'; "
            "$sc.Save()"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._status_label.setText(f"Desktop shortcut created at {desktop_lnk}")
        except Exception as exc:
            self._status_label.setText(f"Shortcut creation failed: {exc}")

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self.controller.is_listening:
            self.controller.cancel_listening()
        self.hotkey_manager.stop()
        self.tray.stop()
        self.pill.close()
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

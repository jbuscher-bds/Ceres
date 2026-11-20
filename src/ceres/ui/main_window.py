"""Native PySide6 (Qt 6) HiDPI dashboard, History & Voice Edit UI, and Settings."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ceres import MODELS_DIR, PROJECT_ROOT, __version__
from ceres.config import ConfigManager
from ceres.engine.model_manager import LLM_CATALOG, WHISPER_CATALOG
from ceres.engine.pipeline import DictationController, PipelineEvent
from ceres.history import HistoryEntry, HistoryStore, export_markdown
from ceres.system.audio import list_input_devices
from ceres.system.autostart import set_autostart_enabled
from ceres.system.hotkey import GlobalHotkeyManager, format_hotkey_display
from ceres.system.injector import set_clipboard_text
from ceres.ui.overlay_pill import FloatingMicPill
from ceres.ui.theme import APP_QSS, create_mic_qicon
from ceres.ui.tray import TrayManager

logger = logging.getLogger(__name__)


class _SignalBridge(QObject):
    pipeline_event = Signal(object)
    audio_level = Signal(float, float)
    hotkey_start = Signal(str)
    hotkey_stop = Signal()
    hotkey_cancel = Signal()
    hotkey_captured = Signal(str, str)


class CeresWindow(QMainWindow):
    """Main HiDPI Qt 6 window coordinating NPU engines, tray, hotkeys, and pill."""

    def __init__(self, *, minimized: bool = False, skip_model_load: bool = False) -> None:
        super().__init__()
        self.setWindowTitle(f"Ceres {__version__} — Intel AI Boost NPU Voice Dictation")
        self.setWindowIcon(create_mic_qicon("idle"))
        self.resize(960, 720)
        self.setMinimumSize(780, 580)
        self.setStyleSheet(APP_QSS)

        self._closing = False
        self._capturing_target: str | None = None

        self.config_manager = ConfigManager()
        self.history_store = HistoryStore()
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

        self._mic_devices = list_input_devices()
        self._build_ui()
        self._sync_ui_from_config()
        self._refresh_history()

        self.hotkey_manager.start()
        self.tray.start()

        if not minimized:
            self.show()

        if not skip_model_load:
            self.controller.ensure_ready_async()

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        if self._closing:
            event.accept()
            return
        event.ignore()
        self.hide()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("CentralRoot")
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(10)

        # Header Card
        header = QFrame()
        header.setObjectName("Card")
        hdr_layout = QHBoxLayout(header)
        hdr_layout.setContentsMargins(16, 12, 16, 12)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_lbl = QLabel(f"Ceres {__version__}")
        title_lbl.setObjectName("AppTitle")
        self._hw_badge = QLabel(self._format_hardware_badge())
        self._hw_badge.setObjectName("HardwareBadge")
        title_col.addWidget(title_lbl)
        title_col.addWidget(self._hw_badge)
        hdr_layout.addLayout(title_col, 1)

        self._dictate_pill_lbl = QLabel()
        self._dictate_pill_lbl.setObjectName("ShortcutPill")
        self._edit_pill_lbl = QLabel()
        self._edit_pill_lbl.setObjectName("ShortcutPill")
        hdr_layout.addWidget(self._dictate_pill_lbl)
        hdr_layout.addWidget(self._edit_pill_lbl)

        self._dictate_btn = QPushButton("Start Dictation")
        self._dictate_btn.setObjectName("PrimaryBtn")
        self._dictate_btn.setMinimumWidth(144)
        self._dictate_btn.clicked.connect(self._toggle_manual_dictation)
        hdr_layout.addWidget(self._dictate_btn)
        main_layout.addWidget(header)

        # Compact Status & Progress Strip
        status_card = QFrame()
        status_card.setObjectName("Card")
        st_layout = QVBoxLayout(status_card)
        st_layout.setContentsMargins(14, 9, 14, 9)
        st_layout.setSpacing(6)

        st_top = QHBoxLayout()
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet("color: #F59E0B; font-size: 14px;")
        self._status_label = QLabel("Initializing local OpenVINO NPU engines...")
        st_top.addWidget(self._status_dot)
        st_top.addWidget(self._status_label, 1)
        st_layout.addLayout(st_top)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(5)
        st_layout.addWidget(self._progress_bar)
        main_layout.addWidget(status_card)

        # Tab Widget
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_history_tab(), "Dictate && History")
        self._tabs.addTab(self._build_settings_tab(), "Settings")
        main_layout.addWidget(self._tabs, 1)

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Test Scratchpad Card
        pad_card = QFrame()
        pad_card.setObjectName("SubCard")
        pad_layout = QVBoxLayout(pad_card)
        pad_layout.setContentsMargins(12, 10, 12, 10)
        pad_layout.setSpacing(6)

        self._test_pad = QPlainTextEdit()
        self._test_pad.setFixedHeight(100)
        self._test_pad.setPlaceholderText(
            "Click inside and press Ctrl + Shift + Space to dictate in English, Dutch, or German.\n"
            "Or select any text here, press Ctrl + Shift + E, and speak an edit like 'Make this more polite'."
        )

        pad_hdr = QHBoxLayout()
        pad_title = QLabel("Quick Test Scratchpad — dictate here or highlight text and press Voice Edit")
        pad_title.setObjectName("MutedText")
        clear_pad_btn = QPushButton("Clear Pad")
        clear_pad_btn.setObjectName("SmallBtn")
        clear_pad_btn.clicked.connect(self._test_pad.clear)
        pad_hdr.addWidget(pad_title, 1)
        pad_hdr.addWidget(clear_pad_btn)
        pad_layout.addLayout(pad_hdr)
        pad_layout.addWidget(self._test_pad)
        layout.addWidget(pad_card)

        # Search & History Controls
        hist_hdr = QHBoxLayout()
        hist_lbl = QLabel("History")
        hist_lbl.setObjectName("SectionTitle")
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search transcripts, language, or context...")
        self._search_input.textChanged.connect(lambda _t: self._refresh_history())

        export_btn = QPushButton("Export Markdown")
        export_btn.setObjectName("SmallBtn")
        export_btn.clicked.connect(self._export_history_markdown)

        clear_hist_btn = QPushButton("Clear All")
        clear_hist_btn.setObjectName("SubtleDangerBtn")
        clear_hist_btn.clicked.connect(self._clear_history)

        hist_hdr.addWidget(hist_lbl)
        hist_hdr.addWidget(self._search_input, 1)
        hist_hdr.addWidget(export_btn)
        hist_hdr.addWidget(clear_hist_btn)
        layout.addLayout(hist_hdr)

        # Scrollable History Cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._history_container = QWidget()
        self._history_layout = QVBoxLayout(self._history_container)
        self._history_layout.setContentsMargins(0, 0, 6, 0)
        self._history_layout.setSpacing(8)
        self._history_layout.addStretch(1)
        scroll.setWidget(self._history_container)
        layout.addWidget(scroll, 1)
        return page

    def _build_settings_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # 1. Shortcuts & Trigger
        sec1 = self._make_section(layout, "Keyboard Shortcuts && Trigger")
        row_hk = QHBoxLayout()
        row_hk.addWidget(self._field_label("Dictation Shortcut:"))
        self._hk_record_btn = QPushButton()
        self._hk_record_btn.setMinimumWidth(210)
        self._hk_record_btn.clicked.connect(lambda: self._start_hotkey_capture("dictate"))
        row_hk.addWidget(self._hk_record_btn)
        hint1 = QLabel("Click and press keys (Esc cancels)")
        hint1.setObjectName("MutedText")
        row_hk.addWidget(hint1, 1)
        sec1.addLayout(row_hk)

        row_edit_hk = QHBoxLayout()
        row_edit_hk.addWidget(self._field_label("Voice Edit Shortcut:"))
        self._edit_hk_btn = QPushButton()
        self._edit_hk_btn.setMinimumWidth(210)
        self._edit_hk_btn.clicked.connect(lambda: self._start_hotkey_capture("edit"))
        row_edit_hk.addWidget(self._edit_hk_btn)
        hint2 = QLabel("Select text, press shortcut, speak edit command")
        hint2.setObjectName("MutedText")
        row_edit_hk.addWidget(hint2, 1)
        sec1.addLayout(row_edit_hk)

        self._trigger_group, row_trig = self._make_segmented_row(
            "Trigger Behavior:",
            [("hybrid", "Hybrid (Tap / Hold)"), ("toggle", "Toggle Only"), ("hold", "Hold Only")],
            self._on_trigger_selected,
        )
        sec1.addLayout(row_trig)

        # 2. Language, Context & Custom Vocabulary
        sec2 = self._make_section(layout, "Language, Context && Custom Vocabulary")
        self._rewrite_group, row_rw = self._make_segmented_row(
            "Rewrite Style:",
            [("fluent", "Fluent Rewrite"), ("light", "Light Cleanup"), ("verbatim", "Verbatim")],
            self._set_rewrite_mode,
        )
        sec2.addLayout(row_rw)

        self._lang_group, row_lang = self._make_segmented_row(
            "Spoken Language:",
            [("auto", "Auto (EN/NL/DE)"), ("en", "English"), ("nl", "Nederlands"), ("de", "Deutsch")],
            self._set_language,
        )
        sec2.addLayout(row_lang)

        self._context_style_chk = QCheckBox(
            "App-aware tone (automatically adapts for Email, Chat, Code, Docs)"
        )
        self._context_style_chk.toggled.connect(self._save_checkbox_settings)
        sec2.addWidget(self._context_style_chk)

        vocab_lbl = QLabel("Custom Vocabulary (domain terms, names, acronyms — comma or newline separated):")
        vocab_lbl.setObjectName("MutedText")
        sec2.addWidget(vocab_lbl)
        row_vocab = QHBoxLayout()
        self._vocab_edit = QPlainTextEdit()
        self._vocab_edit.setFixedHeight(64)
        self._vocab_edit.setPlaceholderText("e.g. OpenVINO, Kubernetes, DSGVO, Ceres, jbuscher-bds")
        save_vocab_btn = QPushButton("Save Vocabulary")
        save_vocab_btn.clicked.connect(self._save_vocabulary)
        row_vocab.addWidget(self._vocab_edit, 1)
        row_vocab.addWidget(save_vocab_btn)
        sec2.addLayout(row_vocab)

        # 3. Audio, Auto-Paste & Windows Integration
        sec3 = self._make_section(layout, "Microphone, Auto-Paste && Windows")
        row_mic = QHBoxLayout()
        row_mic.addWidget(self._field_label("Input Microphone:"))
        self._mic_combo = QComboBox()
        self._mic_combo.addItem("System Default", None)
        for dev in self._mic_devices:
            self._mic_combo.addItem(f"[{dev['index']}] {dev['name']}", int(dev["index"]))
        self._mic_combo.currentIndexChanged.connect(self._on_mic_selected)
        row_mic.addWidget(self._mic_combo, 1)
        sec3.addLayout(row_mic)

        self._auto_paste_chk = QCheckBox("Auto-paste directly into active window")
        self._preserve_clip_chk = QCheckBox("Restore previous clipboard text after pasting")
        self._play_chime_chk = QCheckBox("Play subtle start / stop sound cue")
        self._autostart_chk = QCheckBox("Start Ceres minimized with Windows")

        for chk in (self._auto_paste_chk, self._preserve_clip_chk, self._play_chime_chk):
            chk.toggled.connect(self._save_checkbox_settings)
            sec3.addWidget(chk)
        self._autostart_chk.toggled.connect(self._on_autostart_toggled)
        sec3.addWidget(self._autostart_chk)

        # 4. Intel AI Boost NPU & Models
        sec4 = self._make_section(layout, "Intel AI Boost NPU Engine && Models")
        self._device_group, row_dev = self._make_segmented_row(
            "AI Accelerator:",
            [("NPU", "NPU"), ("GPU", "GPU"), ("CPU", "CPU")],
            lambda _dev: None,
        )
        sec4.addLayout(row_dev)

        row_whisper = QHBoxLayout()
        row_whisper.addWidget(self._field_label("Speech Model:"))
        self._whisper_combo = QComboBox()
        for spec in WHISPER_CATALOG:
            self._whisper_combo.addItem(f"{spec.display_name} ({spec.repo_id})", spec.repo_id)
        row_whisper.addWidget(self._whisper_combo, 1)
        sec4.addLayout(row_whisper)

        row_llm = QHBoxLayout()
        row_llm.addWidget(self._field_label("Rewriter Model:"))
        self._llm_combo = QComboBox()
        for spec in LLM_CATALOG:
            self._llm_combo.addItem(f"{spec.display_name} ({spec.repo_id})", spec.repo_id)
        row_llm.addWidget(self._llm_combo, 1)
        sec4.addLayout(row_llm)

        row_actions = QHBoxLayout()
        apply_btn = QPushButton("Apply && Reload Models")
        apply_btn.setObjectName("PrimaryBtn")
        apply_btn.clicked.connect(self._apply_engine_settings)
        shortcut_btn = QPushButton("Create Desktop Shortcut")
        shortcut_btn.clicked.connect(self._create_desktop_shortcut)
        models_btn = QPushButton("Open Models Folder")
        models_btn.clicked.connect(lambda: os.startfile(str(MODELS_DIR)))
        reset_pill_btn = QPushButton("Reset Pill Position")
        reset_pill_btn.clicked.connect(self._reset_pill_position)
        row_actions.addWidget(apply_btn)
        row_actions.addWidget(shortcut_btn)
        row_actions.addWidget(models_btn)
        row_actions.addWidget(reset_pill_btn)
        row_actions.addStretch(1)
        sec4.addLayout(row_actions)

        layout.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    @staticmethod
    def _field_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedWidth(155)
        return lbl

    @staticmethod
    def _make_section(parent_layout: QVBoxLayout, title: str) -> QVBoxLayout:
        card = QFrame()
        card.setObjectName("SubCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        hdr = QLabel(title)
        hdr.setObjectName("SectionTitle")
        v.addWidget(hdr)
        parent_layout.addWidget(card)
        return v

    def _make_segmented_row(
        self,
        label_text: str,
        options: list[tuple[str, str]],
        on_select,
    ) -> tuple[QButtonGroup, QHBoxLayout]:
        row = QHBoxLayout()
        row.addWidget(self._field_label(label_text))
        group = QButtonGroup(self)
        group.setExclusive(True)
        for idx, (val, caption) in enumerate(options):
            btn = QPushButton(caption)
            btn.setObjectName("SegmentBtn")
            btn.setCheckable(True)
            btn.setProperty("seg_value", val)
            group.addButton(btn, idx)
            row.addWidget(btn)
        row.addStretch(1)

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

    def _format_hardware_badge(self) -> str:
        devs = self.controller.available_devices
        npu = devs.get("NPU")
        gpu = devs.get("GPU")
        if npu and gpu:
            return f"NPU: {npu}  |  GPU: {gpu}"
        if npu:
            return f"NPU Active: {npu}"
        if gpu:
            return f"GPU Active: {gpu}"
        return f"Device: {devs.get('CPU', 'CPU')}"

    def _sync_ui_from_config(self) -> None:
        cfg = self.config_manager.config
        hk_disp = format_hotkey_display(cfg.hotkey)
        edit_disp = format_hotkey_display(cfg.edit_hotkey)
        trig_disp = {"hybrid": "Hybrid", "toggle": "Toggle", "hold": "Hold"}.get(cfg.trigger_mode, "Hybrid")

        self._dictate_pill_lbl.setText(f"Dictate: {hk_disp} ({trig_disp})")
        self._edit_pill_lbl.setText(f"Voice Edit: {edit_disp}")
        self._hk_record_btn.setText(hk_disp)
        self._edit_hk_btn.setText(edit_disp)

        self._set_segment_value(self._trigger_group, cfg.trigger_mode)
        self._set_segment_value(self._rewrite_group, cfg.rewrite_mode)
        self._set_segment_value(self._lang_group, cfg.language)
        self._set_segment_value(self._device_group, cfg.preferred_device)

        self._vocab_edit.setPlainText(", ".join(cfg.custom_vocabulary))

        for widget, val in (
            (self._context_style_chk, cfg.context_aware_style),
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
        self._status_label.setText("Floating pill position reset to active monitor bottom-center.")

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
        self.pill.show_state(
            ev.state,
            ev.message,
            app_context=ev.app_context,
            action_type=ev.action_type,
        )
        self.tray.set_state(ev.state, f"Ceres — {ev.message}")

        color_map = {
            "ready": "#22C55E",
            "done": "#22C55E",
            "listening": "#22C55E",
            "downloading": "#F59E0B",
            "compiling": "#F59E0B",
            "transcribing": "#F59E0B",
            "rewriting": "#F59E0B",
            "error": "#EF4444",
        }
        self._status_dot.setStyleSheet(f"color: {color_map.get(ev.state, '#94A3B8')}; font-size: 14px;")

        if ev.state in ("downloading", "compiling"):
            self._progress_bar.show()
            self._progress_bar.setValue(max(2, min(100, int(ev.progress * 100))))
            self._status_label.setText(ev.message)
        else:
            self._progress_bar.hide()
            if ev.state in ("ready", "done"):
                self._status_label.setText(f"{ev.message}  ({ev.device_summary})")
                self._restore_dictate_btn()
            elif ev.state == "listening":
                ctx_tag = (
                    f" • {ev.app_context.upper()}" if ev.app_context and ev.app_context != "general" else ""
                )
                self._status_label.setText(f"{ev.message}{ctx_tag} (Esc to cancel)")
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
                widget.deleteLater()

        query = self._search_input.text().strip() if hasattr(self, "_search_input") else ""
        entries = (
            self.history_store.search(query, limit=40) if query else self.history_store.list_recent(limit=40)
        )

        if not entries:
            empty_msg = (
                "No matching entries." if query else "No dictations yet. Press Ctrl + Shift + Space anywhere!"
            )
            empty_lbl = QLabel(empty_msg)
            empty_lbl.setObjectName("MutedText")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setContentsMargins(0, 28, 0, 28)
            self._history_layout.insertWidget(0, empty_lbl)
            return

        for idx, entry in enumerate(entries):
            self._history_layout.insertWidget(idx, self._create_history_card(entry))

    def _create_history_card(self, entry: HistoryEntry) -> QFrame:
        card = QFrame()
        card.setObjectName("SubCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(6)

        meta_row = QHBoxLayout()
        ts_short = entry.created_at.replace("T", " ")[:19]
        badges = [
            entry.action_type.upper(),
            (entry.language or "auto").upper(),
            entry.app_context.upper(),
            entry.rewrite_mode.title(),
        ]
        for b in badges:
            lbl = QLabel(b)
            lbl.setObjectName("MetaBadge")
            meta_row.addWidget(lbl)

        timing = QLabel(f"{ts_short}  •  STT {entry.stt_ms}ms / LLM {entry.llm_ms}ms")
        timing.setObjectName("MutedText")
        meta_row.addWidget(timing, 1)

        if entry.action_type != "edit" and entry.raw_text.strip():
            next_mode = "light" if entry.rewrite_mode == "fluent" else "fluent"
            repolish_btn = QPushButton(f"Re-polish ({next_mode.title()})")
            repolish_btn.setObjectName("SmallBtn")
            repolish_btn.clicked.connect(
                lambda _checked=False, e=entry, m=next_mode: self._repolish_history_entry(e, m)
            )
            meta_row.addWidget(repolish_btn)

        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("SmallBtn")
        copy_btn.clicked.connect(lambda _checked=False, t=entry.polished_text: set_clipboard_text(t))
        meta_row.addWidget(copy_btn)
        v.addLayout(meta_row)

        polished_lbl = QLabel(entry.polished_text)
        polished_lbl.setWordWrap(True)
        polished_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(polished_lbl)

        if entry.raw_text and entry.raw_text.strip() != entry.polished_text.strip():
            raw_lbl = QLabel(f"Raw: {entry.raw_text}")
            raw_lbl.setObjectName("MutedText")
            raw_lbl.setWordWrap(True)
            raw_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.addWidget(raw_lbl)

        return card

    def _repolish_history_entry(self, entry: HistoryEntry, target_mode: str) -> None:
        if not self.controller.is_ready:
            self._status_label.setText("Models are still loading — please wait.")
            return
        import threading

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
        self._status_label.setText(f"Exported {len(entries)} entries to {file_path}")

    def _clear_history(self) -> None:
        self.history_store.clear_all()
        self._refresh_history()

    def _start_hotkey_capture(self, target: str) -> None:
        if self._capturing_target is not None:
            self.hotkey_manager.cancel_recording_hotkey()
            self._capturing_target = None
            self._sync_ui_from_config()
            return
        self._capturing_target = target
        btn = self._hk_record_btn if target == "dictate" else self._edit_hk_btn
        btn.setText("Press keys now... (Esc cancels)")
        btn.setStyleSheet("background-color: #F59E0B; color: #0D1117;")

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
            context_aware_style=self._context_style_chk.isChecked(),
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
        ps_script = (
            "$ws = New-Object -ComObject WScript.Shell; "
            f"$sc = $ws.CreateShortcut('{desktop_lnk}'); "
            "$sc.TargetPath = 'wscript.exe'; "
            f"$sc.Arguments = '\"{vbs_path}\"'; "
            f"$sc.WorkingDirectory = '{PROJECT_ROOT}'; "
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

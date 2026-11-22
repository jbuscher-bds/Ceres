"""End-to-end orchestration connecting hotkeys, audio capture, NPU AI models, and history."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ceres import MODELS_DIR, OV_CACHE_DIR, ensure_directories
from ceres.config import ConfigManager
from ceres.engine.model_manager import download_model, get_device_info, is_model_downloaded
from ceres.engine.rewriter import SentenceRewriter
from ceres.engine.transcriber import WhisperTranscriber
from ceres.history import HistoryEntry, HistoryStore
from ceres.system.audio import AudioRecorder, play_cue, trim_silence
from ceres.system.injector import TextInjector, set_clipboard_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineEvent:
    """Discrete state transition pushed to the main dashboard, tray, and pill."""

    state: str  # unloaded | downloading | compiling | ready | listening | transcribing | rewriting | done | error
    message: str
    progress: float = 0.0
    entry: HistoryEntry | None = None
    device_summary: str = ""
    action_type: str = "dictate"


class DictationController:
    """Thread-safe coordinator for model lifecycle, dictation, and voice-edit workflows."""

    def __init__(
        self,
        config_manager: ConfigManager,
        history_store: HistoryStore,
        on_event: Callable[[PipelineEvent], None] | None = None,
        on_level: Callable[[float, float], None] | None = None,
    ) -> None:
        ensure_directories()
        self.config_manager = config_manager
        self.history_store = history_store
        self._on_event = on_event
        self._on_level = on_level

        self.recorder = AudioRecorder(on_level=self._handle_audio_level)
        self.transcriber = WhisperTranscriber()
        self.rewriter = SentenceRewriter()
        self.injector = TextInjector()

        self._engine_lock = threading.RLock()
        self._workflow_lock = threading.RLock()
        self._ready = False
        self._loading = False
        self._state = "unloaded"
        self._device_info = get_device_info()

        self._pending_action: str = "dictate"
        self._pending_selected_text: str | None = None
        self._pending_prior_clipboard: str | None = None

    @property
    def is_ready(self) -> bool:
        with self._engine_lock:
            return self._ready

    @property
    def is_listening(self) -> bool:
        return self.recorder.is_recording

    @property
    def device_summary(self) -> str:
        return f"Whisper: {self.transcriber.active_device} • Rewriter: {self.rewriter.active_device}"

    @property
    def available_devices(self) -> dict[str, str]:
        return dict(self._device_info)

    def models_present(self) -> bool:
        cfg = self.config_manager.config
        return is_model_downloaded(cfg.whisper_model_id, MODELS_DIR) and (
            cfg.rewrite_mode == "verbatim" or is_model_downloaded(cfg.llm_model_id, MODELS_DIR)
        )

    def _emit(
        self,
        state: str,
        message: str,
        *,
        progress: float = 0.0,
        entry: HistoryEntry | None = None,
        action_type: str = "dictate",
    ) -> None:
        self._state = state
        if self._on_event is None:
            return
        with contextlib.suppress(Exception):
            self._on_event(
                PipelineEvent(
                    state=state,
                    message=message,
                    progress=progress,
                    entry=entry,
                    device_summary=self.device_summary,
                    action_type=action_type,
                )
            )

    def _handle_audio_level(self, level: float) -> None:
        """Forward smooth RMS levels strictly to the pill animation without emitting tray/UI events."""
        if self.recorder.is_recording and self._on_level is not None:
            with contextlib.suppress(Exception):
                self._on_level(level, self.recorder.elapsed_seconds)

    def ensure_ready_async(self, force_reload: bool = False) -> None:
        threading.Thread(
            target=self.ensure_ready,
            kwargs={"force_reload": force_reload},
            name="CeresModelLoader",
            daemon=True,
        ).start()

    def ensure_ready(self, force_reload: bool = False) -> bool:
        """Download any missing models and compile OpenVINO pipelines on target device."""
        with self._engine_lock:
            if self._loading:
                return False
            if self._ready and not force_reload:
                return True
            self._loading = True
            self._ready = False

        try:
            cfg = self.config_manager.config
            self._device_info = get_device_info()

            def _whisper_progress(frac: float, msg: str) -> None:
                self._emit("downloading", msg, progress=frac * 0.35)

            whisper_dir = download_model(cfg.whisper_model_id, MODELS_DIR, progress_cb=_whisper_progress)

            def _llm_progress(frac: float, msg: str) -> None:
                self._emit("downloading", msg, progress=0.35 + frac * 0.40)

            llm_dir = download_model(cfg.llm_model_id, MODELS_DIR, progress_cb=_llm_progress)

            self._emit(
                "compiling", f"Compiling sentence rewriter on {cfg.preferred_device}...", progress=0.78
            )
            llm_dev = self.rewriter.load(
                llm_dir,
                model_id=cfg.llm_model_id,
                preferred_device=cfg.preferred_device,
                cache_dir=OV_CACHE_DIR,
            )

            self._emit("compiling", f"Compiling speech engine on {cfg.preferred_device}...", progress=0.90)
            whisper_dev = self.transcriber.load(
                whisper_dir,
                preferred_device=cfg.preferred_device,
                cache_dir=OV_CACHE_DIR,
            )

            with self._engine_lock:
                self._ready = True
            self._emit("ready", f"Ready ({whisper_dev} / {llm_dev})", progress=1.0)
            return True
        except Exception as exc:
            logger.exception("Engine initialization failed: %s", exc)
            self._emit("error", f"Model setup error: {exc}", progress=0.0)
            return False
        finally:
            with self._engine_lock:
                self._loading = False

    def start_listening(self, action: str = "dictate") -> bool:
        """Capture selected text for Voice Edit (if requested) and start microphone capture."""
        with self._workflow_lock:
            if self.recorder.is_recording:
                return True
            if not self.is_ready:
                self._emit("error", "AI models are still loading — please wait a moment.")
                return False

            cfg = self.config_manager.config

            selected_text: str | None = None
            prior_clip: str | None = None
            if action == "edit":
                selected_text, prior_clip = self.injector.capture_selected_text()
                if not selected_text:
                    self._emit("error", "Select text first, then press Edit shortcut", action_type="edit")
                    return False

            self._pending_action = action
            self._pending_selected_text = selected_text
            self._pending_prior_clipboard = prior_clip

            try:
                self.recorder.start(device=cfg.audio_device)
            except Exception as exc:
                logger.exception("Failed to start audio capture: %s", exc)
                if prior_clip is not None:
                    set_clipboard_text(prior_clip)
                self._emit("error", f"Microphone error: {exc}")
                return False

            if cfg.play_chime:
                play_cue("start")
            msg = "Speak edit instruction..." if action == "edit" else "Listening..."
            self._emit("listening", msg, action_type=action)
            return True

    def cancel_listening(self) -> None:
        """Abort current recording without transcribing or pasting."""
        with self._workflow_lock:
            if not self.recorder.is_recording:
                return
            self.recorder.stop()
            prior_clip = self._pending_prior_clipboard
            self._pending_selected_text = None
            self._pending_prior_clipboard = None
            self._pending_action = "dictate"
            if prior_clip is not None:
                set_clipboard_text(prior_clip)
            if self.config_manager.config.play_chime:
                play_cue("cancel")
            self._emit("ready", "Cancelled")

    def stop_listening_and_process(self) -> None:
        """Stop microphone recording and process transcription + rewrite/edit on background thread."""
        with self._workflow_lock:
            if not self.recorder.is_recording:
                return
            audio, total_duration = self.recorder.stop()
            action = self._pending_action
            selected_text = self._pending_selected_text
            prior_clip = self._pending_prior_clipboard
            self._pending_action = "dictate"
            self._pending_selected_text = None
            self._pending_prior_clipboard = None

            if self.config_manager.config.play_chime:
                play_cue("stop")

        threading.Thread(
            target=self._process_audio,
            args=(audio, total_duration, action, selected_text, prior_clip),
            name="CeresInferenceWorker",
            daemon=True,
        ).start()

    def _process_audio(
        self,
        audio: np.ndarray,
        total_duration: float,
        action: str,
        selected_text: str | None,
        prior_clip: str | None,
    ) -> None:
        with self._workflow_lock:
            cfg = self.config_manager.config
            trimmed, has_speech = trim_silence(audio)
            if not has_speech:
                if prior_clip is not None:
                    set_clipboard_text(prior_clip)
                self._emit("ready", "No speech detected", action_type=action)
                return

            try:
                self._emit(
                    "transcribing",
                    f"Transcribing on {self.transcriber.active_device}...",
                    action_type=action,
                )
                t0 = time.perf_counter()
                raw_text, detected_lang = self.transcriber.transcribe(trimmed, language=cfg.language)
                stt_ms = int((time.perf_counter() - t0) * 1000)

                if not raw_text:
                    if prior_clip is not None:
                        set_clipboard_text(prior_clip)
                    self._emit("ready", "No speech recognized", action_type=action)
                    return

                llm_ms = 0
                if action == "edit" and selected_text:
                    self._emit(
                        "rewriting",
                        f"Editing text on {self.rewriter.active_device}...",
                        action_type="edit",
                    )
                    t1 = time.perf_counter()
                    polished_text = self.rewriter.edit_text(
                        selected_text,
                        raw_text,
                        vocabulary=cfg.custom_vocabulary,
                    )
                    llm_ms = int((time.perf_counter() - t1) * 1000)
                    final_text = polished_text.strip() or selected_text.strip()
                elif cfg.rewrite_mode == "verbatim":
                    final_text = raw_text.strip()
                else:
                    self._emit(
                        "rewriting",
                        f"Polishing text on {self.rewriter.active_device}...",
                        action_type="dictate",
                    )
                    t1 = time.perf_counter()
                    lang_hint = detected_lang if detected_lang in ("en", "nl", "de") else cfg.language
                    polished_text = self.rewriter.rewrite(
                        raw_text,
                        mode=cfg.rewrite_mode,
                        language_hint=lang_hint,
                        vocabulary=cfg.custom_vocabulary,
                    )
                    llm_ms = int((time.perf_counter() - t1) * 1000)
                    final_text = polished_text.strip() or raw_text.strip()

                self.injector.inject(
                    final_text,
                    auto_paste=cfg.auto_paste,
                    preserve_clipboard=cfg.preserve_clipboard,
                    restore_text=prior_clip,
                )

                entry = self.history_store.add_entry(
                    language=detected_lang or cfg.language,
                    rewrite_mode="edit" if action == "edit" else cfg.rewrite_mode,
                    raw_text=raw_text,
                    polished_text=final_text,
                    audio_seconds=round(total_duration, 2),
                    stt_ms=stt_ms,
                    llm_ms=llm_ms,
                    device_summary=self.device_summary,
                    action_type=action,
                )
                done_msg = (
                    "Edited ✓"
                    if action == "edit" and cfg.auto_paste
                    else ("Pasted ✓" if cfg.auto_paste else "Copied ✓")
                )
                self._emit("done", done_msg, entry=entry, action_type=action)
            except Exception as exc:
                logger.exception("Dictation processing failed: %s", exc)
                if prior_clip is not None:
                    set_clipboard_text(prior_clip)
                self._emit("error", f"Processing error: {exc}", action_type=action)

    def repolish_entry(self, entry: HistoryEntry, target_mode: str) -> HistoryEntry:
        """Re-polish a historical raw transcript using `target_mode` and save as a new entry."""
        if not self.is_ready:
            raise RuntimeError("Models are not loaded yet")
        cfg = self.config_manager.config
        lang_hint = entry.language if entry.language in ("en", "nl", "de") else cfg.language
        t0 = time.perf_counter()
        if target_mode == "verbatim":
            polished = entry.raw_text.strip()
        else:
            polished = self.rewriter.rewrite(
                entry.raw_text,
                mode=target_mode,
                language_hint=lang_hint,
                vocabulary=cfg.custom_vocabulary,
            )
        llm_ms = int((time.perf_counter() - t0) * 1000)
        new_entry = self.history_store.add_entry(
            language=entry.language,
            rewrite_mode=target_mode,
            raw_text=entry.raw_text,
            polished_text=polished or entry.raw_text,
            audio_seconds=entry.audio_seconds,
            stt_ms=entry.stt_ms,
            llm_ms=llm_ms,
            device_summary=self.device_summary,
            action_type="dictate",
        )
        self._emit("done", "Re-polished ✓", entry=new_entry, action_type="dictate")
        return new_entry

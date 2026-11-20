from __future__ import annotations

from pathlib import Path

import numpy as np

from ceres.config import ConfigManager
from ceres.engine.pipeline import DictationController, PipelineEvent
from ceres.history import HistoryStore
from ceres.system.audio import SAMPLE_RATE
from ceres.system.context import ActiveWindowContext


class _FakeRecorder:
    def __init__(self, speech_audio: np.ndarray) -> None:
        self._audio = speech_audio
        self.is_recording = False
        self.elapsed_seconds = 1.2

    def start(self, device: int | None = None) -> None:
        self.is_recording = True

    def stop(self) -> tuple[np.ndarray, float]:
        self.is_recording = False
        return self._audio, float(self._audio.size / SAMPLE_RATE)


class _FakeTranscriber:
    active_device = "GPU"

    def transcribe(self, audio: np.ndarray, language: str = "auto") -> tuple[str, str]:
        return "um can we meet tomorrow", "en"


class _FakeRewriter:
    active_device = "NPU"

    def __init__(self) -> None:
        self.last_rewrite_kwargs: dict = {}
        self.last_edit_args: tuple[str, str] | None = None

    def rewrite(self, raw_text: str, mode: str = "fluent", language_hint: str = "auto", **kwargs) -> str:
        self.last_rewrite_kwargs = {"mode": mode, "language_hint": language_hint, **kwargs}
        return "Can we meet tomorrow?"

    def edit_text(self, selected_text: str, instruction: str, **kwargs) -> str:
        self.last_edit_args = (selected_text, instruction)
        return "Polished selection."


class _FakeInjector:
    def __init__(self, selected_text: str | None = "Original text") -> None:
        self.selected_text = selected_text
        self.injected: list[tuple[str, dict]] = []

    def capture_selected_text(self, timeout_s: float = 0.25) -> tuple[str | None, str | None]:
        return self.selected_text, "prior_clip"

    def inject(self, text: str, **kwargs) -> bool:
        self.injected.append((text, kwargs))
        return True


def _make_speech_wave() -> np.ndarray:
    t = np.linspace(0, 1.0, SAMPLE_RATE, dtype=np.float32)
    return (0.15 * np.sin(2.0 * np.pi * 240.0 * t)).astype(np.float32)


def test_pipeline_dictate_and_repolish_mocked(tmp_path: Path, monkeypatch) -> None:
    cfg_mgr = ConfigManager(tmp_path / "settings.json")
    cfg_mgr.update(play_chime=False, custom_vocabulary=["OpenVINO", "Ceres"])
    hist = HistoryStore(tmp_path / "history.db")
    events: list[PipelineEvent] = []

    ctrl = DictationController(cfg_mgr, hist, on_event=events.append)
    ctrl._ready = True
    ctrl.recorder = _FakeRecorder(_make_speech_wave())  # type: ignore[assignment]
    ctrl.transcriber = _FakeTranscriber()  # type: ignore[assignment]
    fake_rewriter = _FakeRewriter()
    ctrl.rewriter = fake_rewriter  # type: ignore[assignment]
    fake_injector = _FakeInjector()
    ctrl.injector = fake_injector  # type: ignore[assignment]

    monkeypatch.setattr(
        "ceres.engine.pipeline.get_active_window_context",
        lambda: ActiveWindowContext("Code.exe", "main.py", "code"),
    )

    assert ctrl.start_listening("dictate") is True
    audio, dur = ctrl.recorder.stop()
    ctrl._process_audio(audio, dur, "dictate", None, None, ActiveWindowContext("Code.exe", "main.py", "code"))

    assert fake_injector.injected[-1][0] == "Can we meet tomorrow?"
    assert fake_rewriter.last_rewrite_kwargs["app_context"] == "code"
    assert fake_rewriter.last_rewrite_kwargs["vocabulary"] == ["OpenVINO", "Ceres"]

    recent = hist.list_recent()
    assert len(recent) == 1
    assert recent[0].app_context == "code"
    assert recent[0].action_type == "dictate"

    repolished = ctrl.repolish_entry(recent[0], "light")
    assert repolished.rewrite_mode == "light"
    assert len(hist.list_recent()) == 2


def test_pipeline_voice_edit_mode_requires_selection_and_edits(tmp_path: Path, monkeypatch) -> None:
    cfg_mgr = ConfigManager(tmp_path / "settings.json")
    cfg_mgr.update(play_chime=False)
    hist = HistoryStore(tmp_path / "history.db")
    events: list[PipelineEvent] = []

    ctrl = DictationController(cfg_mgr, hist, on_event=events.append)
    ctrl._ready = True
    ctrl.recorder = _FakeRecorder(_make_speech_wave())  # type: ignore[assignment]
    ctrl.transcriber = _FakeTranscriber()  # type: ignore[assignment]
    fake_rewriter = _FakeRewriter()
    ctrl.rewriter = fake_rewriter  # type: ignore[assignment]
    fake_injector = _FakeInjector(selected_text=None)
    ctrl.injector = fake_injector  # type: ignore[assignment]

    monkeypatch.setattr(
        "ceres.engine.pipeline.get_active_window_context",
        lambda: ActiveWindowContext("OUTLOOK.EXE", "Re: Quote", "email"),
    )

    # Empty selection fails safely without starting recorder
    assert ctrl.start_listening("edit") is False
    assert events[-1].state == "error"

    fake_injector.selected_text = "Hey send it asap"
    assert ctrl.start_listening("edit") is True
    audio, dur = ctrl.recorder.stop()
    ctrl._process_audio(
        audio,
        dur,
        "edit",
        "Hey send it asap",
        "prior_clip",
        ActiveWindowContext("OUTLOOK.EXE", "Re: Quote", "email"),
    )

    assert fake_rewriter.last_edit_args == ("Hey send it asap", "um can we meet tomorrow")
    assert fake_injector.injected[-1][0] == "Polished selection."
    assert fake_injector.injected[-1][1]["restore_text"] == "prior_clip"
    assert hist.list_recent()[0].action_type == "edit"

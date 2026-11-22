"""Thread-safe JSON configuration management for Ceres."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from ceres import SETTINGS_PATH

VALID_TRIGGER_MODES = ("hybrid", "toggle", "hold")
VALID_REWRITE_MODES = ("fluent", "light", "verbatim")
VALID_LANGUAGES = ("auto", "en", "nl", "de")
VALID_DEVICES = ("NPU", "GPU", "CPU")
VALID_THEMES = ("system", "light", "dark")

DEFAULT_WHISPER_MODEL = "OpenVINO/whisper-large-v3-turbo-int8-ov"
DEFAULT_LLM_MODEL = "OpenVINO/Qwen3-8B-int4-cw-ov"


def _normalize_vocabulary(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in raw:
        term = str(item).strip()
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(term)
        if len(cleaned) >= 60:
            break
    return cleaned


def _normalize_pill_position(raw: Any) -> list[int] | None:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            x = int(raw[0])
            y = int(raw[1])
            if not isinstance(raw[0], bool) and not isinstance(raw[1], bool):
                return [x, y]
        except (TypeError, ValueError):
            return None
    return None


@dataclass
class AppConfig:
    """User-configurable runtime and AI pipeline settings."""

    hotkey: str = "ctrl+shift+space"
    edit_hotkey: str = "ctrl+shift+e"
    trigger_mode: str = "hybrid"
    rewrite_mode: str = "fluent"
    language: str = "auto"
    preferred_device: str = "NPU"
    whisper_model_id: str = DEFAULT_WHISPER_MODEL
    llm_model_id: str = DEFAULT_LLM_MODEL
    audio_device: int | None = None
    auto_paste: bool = True
    preserve_clipboard: bool = False
    play_chime: bool = True
    custom_vocabulary: list[str] = field(default_factory=list)
    start_with_windows: bool = False
    pill_position: list[int] | None = None
    theme: str = "system"

    def normalized(self) -> AppConfig:
        """Return a sanitized copy ensuring all enum-like and structured fields are valid."""
        hotkey = (self.hotkey or "ctrl+shift+space").strip().lower() or "ctrl+shift+space"
        edit_hotkey = (self.edit_hotkey or "ctrl+shift+e").strip().lower() or "ctrl+shift+e"
        trigger_mode = self.trigger_mode if self.trigger_mode in VALID_TRIGGER_MODES else "hybrid"
        rewrite_mode = self.rewrite_mode if self.rewrite_mode in VALID_REWRITE_MODES else "fluent"
        language = self.language if self.language in VALID_LANGUAGES else "auto"
        preferred_device = (
            self.preferred_device.upper()
            if isinstance(self.preferred_device, str) and self.preferred_device.upper() in VALID_DEVICES
            else "NPU"
        )
        whisper_model_id = (self.whisper_model_id or DEFAULT_WHISPER_MODEL).strip() or DEFAULT_WHISPER_MODEL
        llm_model_id = (self.llm_model_id or DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL
        audio_device = (
            int(self.audio_device)
            if isinstance(self.audio_device, int) and not isinstance(self.audio_device, bool)
            else None
        )
        theme_raw = self.theme.strip().lower() if isinstance(self.theme, str) else "system"
        theme = theme_raw if theme_raw in VALID_THEMES else "system"
        return AppConfig(
            hotkey=hotkey,
            edit_hotkey=edit_hotkey,
            trigger_mode=trigger_mode,
            rewrite_mode=rewrite_mode,
            language=language,
            preferred_device=preferred_device,
            whisper_model_id=whisper_model_id,
            llm_model_id=llm_model_id,
            audio_device=audio_device,
            auto_paste=bool(self.auto_paste),
            preserve_clipboard=bool(self.preserve_clipboard),
            play_chime=bool(self.play_chime),
            custom_vocabulary=_normalize_vocabulary(self.custom_vocabulary),
            start_with_windows=bool(self.start_with_windows),
            pill_position=_normalize_pill_position(self.pill_position),
            theme=theme,
        )


class ConfigManager:
    """Loads and atomically persists `AppConfig` to disk."""

    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._config = self._load_from_disk()

    @property
    def config(self) -> AppConfig:
        with self._lock:
            return self._config

    def _load_from_disk(self) -> AppConfig:
        if not self.path.exists():
            cfg = AppConfig()
            self._write_atomic(cfg)
            return cfg
        try:
            raw_text = self.path.read_text(encoding="utf-8")
            raw_data = json.loads(raw_text)
            if not isinstance(raw_data, dict):
                return AppConfig()
            known_fields = {f.name for f in fields(AppConfig)}
            filtered: dict[str, Any] = {k: v for k, v in raw_data.items() if k in known_fields}
            return AppConfig(**filtered).normalized()
        except (OSError, ValueError, TypeError):
            return AppConfig()

    def reload(self) -> AppConfig:
        """Re-read configuration from disk."""
        with self._lock:
            self._config = self._load_from_disk()
            return self._config

    def save(self, config: AppConfig) -> AppConfig:
        """Validate, update in-memory state, and atomically write config to JSON."""
        with self._lock:
            normalized = config.normalized()
            self._write_atomic(normalized)
            self._config = normalized
            return normalized

    def update(self, **changes: Any) -> AppConfig:
        """Update specific configuration keys and persist."""
        with self._lock:
            data = asdict(self._config)
            known_fields = {f.name for f in fields(AppConfig)}
            for key, value in changes.items():
                if key in known_fields:
                    data[key] = value
            return self.save(AppConfig(**data))

    def _write_atomic(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(asdict(config), indent=2, ensure_ascii=False)
        tmp_path.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp_path, self.path)

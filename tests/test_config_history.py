from __future__ import annotations

from pathlib import Path

from ceres.config import AppConfig, ConfigManager
from ceres.history import HistoryStore


def test_config_roundtrip_and_normalization(tmp_path: Path) -> None:
    cfg_path = tmp_path / "settings.json"
    mgr = ConfigManager(cfg_path)
    assert mgr.config.hotkey == "ctrl+shift+space"
    assert mgr.config.trigger_mode == "hybrid"
    assert mgr.config.preferred_device == "NPU"
    assert mgr.config.theme == "system"

    updated = mgr.update(
        hotkey="alt+space",
        trigger_mode="invalid_mode",
        rewrite_mode="light",
        language="nl",
        preferred_device="gpu",
        preserve_clipboard=True,
        theme="dark",
    )
    assert updated.hotkey == "alt+space"
    assert updated.trigger_mode == "hybrid"  # invalid falls back safely
    assert updated.rewrite_mode == "light"
    assert updated.language == "nl"
    assert updated.preferred_device == "GPU"
    assert updated.preserve_clipboard is True
    assert updated.theme == "dark"

    mgr_reloaded = ConfigManager(cfg_path)
    assert mgr_reloaded.config == updated

    assert mgr.update(theme="light").theme == "light"
    assert mgr.update(theme="neon_invalid").theme == "system"


def test_config_handles_corrupted_json(tmp_path: Path) -> None:
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text("{not valid json", encoding="utf-8")
    mgr = ConfigManager(cfg_path)
    assert mgr.config == AppConfig()


def test_history_crud(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.db")
    assert store.list_recent() == []

    e1 = store.add_entry(
        language="en",
        rewrite_mode="fluent",
        raw_text="um hello world",
        polished_text="Hello world.",
        audio_seconds=1.5,
        stt_ms=120,
        llm_ms=210,
        device_summary="Whisper: NPU • Rewriter: NPU",
    )
    e2 = store.add_entry(
        language="nl",
        rewrite_mode="light",
        raw_text="eh goedemorgen",
        polished_text="Goedemorgen.",
        audio_seconds=1.1,
        stt_ms=95,
        llm_ms=140,
        device_summary="Whisper: NPU • Rewriter: NPU",
    )

    recent = store.list_recent(limit=10)
    assert [e.id for e in recent] == [e2.id, e1.id]
    assert recent[0].polished_text == "Goedemorgen."

    store.delete_entry(e1.id)
    assert [e.id for e in store.list_recent()] == [e2.id]

    store.clear_all()
    assert store.list_recent() == []

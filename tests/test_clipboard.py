from __future__ import annotations

from ceres.system.injector import get_clipboard_text, set_clipboard_text


def test_win32_unicode_clipboard_roundtrip() -> None:
    prior = get_clipboard_text()
    sample = "Café, België, Überprüfung, Straße — 100% local NPU ✓"
    try:
        assert set_clipboard_text(sample) is True
        assert get_clipboard_text() == sample
    finally:
        if prior is not None:
            set_clipboard_text(prior)

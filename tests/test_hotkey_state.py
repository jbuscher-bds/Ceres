from __future__ import annotations

import pytest

from ceres.system.hotkey import HotkeyStateMachine, format_hotkey_display, parse_hotkey


def test_parse_hotkey_variants() -> None:
    assert parse_hotkey("ctrl+shift+space") == (frozenset({"ctrl", "shift"}), "space")
    assert parse_hotkey("Control + LAlt + F9") == (frozenset({"ctrl", "alt"}), "f9")
    assert parse_hotkey("ctrl+win") == (frozenset({"ctrl", "win"}), None)
    assert format_hotkey_display("ctrl+shift+space") == "Ctrl + Shift + Space"

    with pytest.raises(ValueError):
        parse_hotkey("ctrl+nonexistentkey")


def test_hybrid_mode_tap_toggles_on_and_second_tap_stops() -> None:
    sm = HotkeyStateMachine(mode="hybrid", hold_threshold_s=0.35)
    assert sm.on_combo_down(1.00) == "start"
    assert sm.on_combo_down(1.05) is None  # OS key auto-repeat ignored
    assert sm.on_combo_up(1.12) is None  # Quick tap stays recording
    assert sm.is_recording

    assert sm.on_combo_down(4.50) == "stop"
    assert not sm.is_recording
    assert sm.on_combo_up(4.60) is None


def test_hybrid_mode_hold_acts_as_push_to_talk() -> None:
    sm = HotkeyStateMachine(mode="hybrid", hold_threshold_s=0.35)
    assert sm.on_combo_down(10.0) == "start"
    assert sm.on_combo_up(10.6) == "stop"
    assert not sm.is_recording


def test_toggle_and_hold_modes() -> None:
    toggle_sm = HotkeyStateMachine(mode="toggle")
    assert toggle_sm.on_combo_down(0.0) == "start"
    assert toggle_sm.on_combo_up(2.0) is None
    assert toggle_sm.on_combo_down(3.0) == "stop"

    hold_sm = HotkeyStateMachine(mode="hold")
    assert hold_sm.on_combo_down(0.0) == "start"
    assert hold_sm.on_combo_up(0.1) == "stop"
    assert hold_sm.on_escape() is None


def test_escape_cancels_active_recording() -> None:
    sm = HotkeyStateMachine(mode="hybrid", hold_threshold_s=0.35)
    assert sm.on_combo_down(1.0) == "start"
    assert sm.on_escape() == "cancel"
    assert not sm.is_recording
    # Releasing held key after Escape must not fire a second stop
    assert sm.on_combo_up(2.0) is None

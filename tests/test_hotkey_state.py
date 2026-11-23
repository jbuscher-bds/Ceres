from __future__ import annotations

import ctypes

import pytest

from ceres.system import hotkey as hotkey_mod
from ceres.system.hotkey import (
    _KBDLLHOOKSTRUCT,
    LLKHF_INJECTED,
    VK_ESCAPE,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_SPACE,
    WM_KEYDOWN,
    WM_KEYUP,
    GlobalHotkeyManager,
    HotkeyStateMachine,
    format_hotkey_display,
    parse_hotkey,
)
from ceres.system.injector import CERES_INJECTED_EXTRA_INFO


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


def test_global_hotkey_manager_exact_modifiers_stale_recovery_and_suppression(monkeypatch) -> None:
    mgr = GlobalHotkeyManager(hotkey="ctrl+shift+space", edit_hotkey="ctrl+shift+e", trigger_mode="hybrid")
    physical_down: set[int] = set()
    monkeypatch.setattr(mgr, "_is_vk_physically_down", lambda vk: vk in physical_down)

    def _drain_events() -> list[tuple[str, object]]:
        items: list[tuple[str, object]] = []
        while not mgr._event_queue.empty():
            items.append(mgr._event_queue.get_nowait())
        return items

    # 1. Extra modifier (Ctrl+Shift+Alt+E / AltGr) must NOT trigger ctrl+shift+e or suppress 'E'
    physical_down.update({VK_LCONTROL, VK_LSHIFT, VK_LMENU})
    assert mgr._handle_key_event(ord("E"), True) is False
    assert mgr._handle_key_event(ord("E"), False) is False
    assert _drain_events() == []

    # 2. Stale modifiers left in _pressed_vks when hardware keys are physically UP must be pruned cleanly
    physical_down.clear()
    mgr._pressed_vks.update({VK_LCONTROL, VK_LSHIFT})
    assert mgr._handle_key_event(VK_SPACE, True) is False
    assert mgr._handle_key_event(VK_SPACE, False) is False
    assert VK_LCONTROL not in mgr._pressed_vks
    assert VK_LSHIFT not in mgr._pressed_vks
    assert _drain_events() == []

    # 3. Normal typing of 'e' or 'space' never suppresses
    assert mgr._handle_key_event(ord("E"), True) is False
    assert mgr._handle_key_event(ord("E"), False) is False
    assert _drain_events() == []

    # 4. Valid Ctrl+Shift+Space suppresses only non-modifier trigger down/up, never Ctrl/Shift
    physical_down.add(VK_LCONTROL)
    assert mgr._handle_key_event(VK_LCONTROL, True) is False
    physical_down.add(VK_LSHIFT)
    assert mgr._handle_key_event(VK_LSHIFT, True) is False
    assert mgr._handle_key_event(VK_SPACE, True) is True  # starts dictation & suppresses Space
    assert mgr._handle_key_event(VK_SPACE, True) is True  # suppresses OS key-repeat
    assert mgr._handle_key_event(VK_SPACE, False) is True  # suppresses matching Space key-up
    assert mgr._dictate_state.is_recording is True
    assert _drain_events() == [("start", "dictate")]

    # 5. Escape cancels active recording and suppresses Escape down/up; idle Escape passes through
    physical_down.clear()
    assert mgr._handle_key_event(VK_ESCAPE, True) is True
    assert mgr._handle_key_event(VK_ESCAPE, False) is True
    assert mgr._dictate_state.is_recording is False
    assert _drain_events() == [("cancel", None)]

    assert mgr._handle_key_event(VK_ESCAPE, True) is False
    assert mgr._handle_key_event(VK_ESCAPE, False) is False


def test_global_hotkey_manager_external_injected_macros_and_capture(monkeypatch) -> None:
    mgr = GlobalHotkeyManager(hotkey="ctrl+shift+space", edit_hotkey="ctrl+shift+e", trigger_mode="hybrid")
    monkeypatch.setattr(hotkey_mod._user32, "CallNextHookEx", lambda *_args: 0)
    monkeypatch.setattr(mgr, "_is_vk_physically_down", lambda _vk: False)

    def _send(vk: int, is_down: bool, *, flags: int = LLKHF_INJECTED, extra: int = 0) -> int:
        kb = _KBDLLHOOKSTRUCT(vkCode=vk, scanCode=0, flags=flags, time=0, dwExtraInfo=extra)
        w_param = WM_KEYDOWN if is_down else WM_KEYUP
        return mgr._low_level_handler(0, w_param, ctypes.addressof(kb))

    def _drain_events() -> list[tuple[str, object]]:
        items: list[tuple[str, object]] = []
        while not mgr._event_queue.empty():
            items.append(mgr._event_queue.get_nowait())
        return items

    # 1. External injected shortcut (e.g. Logi Options+ MX Keys macro) starts and stops dictation
    assert _send(VK_LCONTROL, True) == 0
    assert _send(VK_LSHIFT, True) == 0
    assert _send(VK_SPACE, True) == 1
    assert _send(VK_SPACE, False) == 1
    assert _send(VK_LSHIFT, False) == 0
    assert _send(VK_LCONTROL, False) == 0
    assert mgr._dictate_state.is_recording is True
    assert _drain_events() == [("start", "dictate")]

    assert _send(VK_LCONTROL, True) == 0
    assert _send(VK_LSHIFT, True) == 0
    assert _send(VK_SPACE, True) == 1
    assert _send(VK_SPACE, False) == 1
    assert _send(VK_LSHIFT, False) == 0
    assert _send(VK_LCONTROL, False) == 0
    assert mgr._dictate_state.is_recording is False
    assert _drain_events() == [("stop", None)]

    # 2. Ceres-tagged SendInput events are ignored completely and pruned on keyup
    mgr._pressed_vks.add(VK_LCONTROL)
    for vk in (VK_LCONTROL, VK_LSHIFT, VK_SPACE):
        assert _send(vk, True, extra=CERES_INJECTED_EXTRA_INFO) == 0
    for vk in (VK_SPACE, VK_LSHIFT, VK_LCONTROL):
        assert _send(vk, False, extra=CERES_INJECTED_EXTRA_INFO) == 0
    assert mgr._dictate_state.is_recording is False
    assert VK_LCONTROL not in mgr._pressed_vks
    assert _drain_events() == []

    # 3. Interactive capture ignores plain letter 'f' alone, captures injected F13 and Ctrl+Shift+Space
    captured: list[str] = []
    mgr.start_recording_hotkey(captured.append)

    assert _send(ord("F"), True) == 1
    assert _send(ord("F"), False) == 1
    assert _drain_events() == []

    assert _send(0x7C, True) == 1  # VK_F13
    assert _send(0x7C, False) == 1
    events = _drain_events()
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "captured"
    cb, spec = payload  # type: ignore[misc]
    cb(spec)
    assert captured == ["f13"]

    mgr.start_recording_hotkey(captured.append)
    assert _send(VK_LCONTROL, True) == 0
    assert _send(VK_LSHIFT, True) == 0
    assert _send(VK_SPACE, True) == 1
    assert _send(VK_SPACE, False) == 1
    assert _send(VK_LSHIFT, False) == 0
    assert _send(VK_LCONTROL, False) == 0
    events = _drain_events()
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "captured"
    cb, spec = payload  # type: ignore[misc]
    cb(spec)
    assert captured == ["f13", "ctrl+shift+space"]

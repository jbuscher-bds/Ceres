"""Global Win32 low-level keyboard hook and testable Hybrid/Toggle/Hold state machine."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Windows hook constants
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x00000010

# Virtual key codes
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_OEM_PLUS = 0xBB
VK_OEM_COMMA = 0xBC
VK_OEM_MINUS = 0xBD
VK_OEM_PERIOD = 0xBE
VK_OEM_1 = 0xBA
VK_OEM_2 = 0xBF
VK_OEM_3 = 0xC0
VK_OEM_4 = 0xDB
VK_OEM_5 = 0xDC
VK_OEM_6 = 0xDD
VK_OEM_7 = 0xDE

MODIFIER_VKS: dict[int, str] = {
    VK_CONTROL: "ctrl",
    VK_LCONTROL: "ctrl",
    VK_RCONTROL: "ctrl",
    VK_SHIFT: "shift",
    VK_LSHIFT: "shift",
    VK_RSHIFT: "shift",
    VK_MENU: "alt",
    VK_LMENU: "alt",
    VK_RMENU: "alt",
    VK_LWIN: "win",
    VK_RWIN: "win",
}

MODIFIER_CHECK_VKS: dict[str, tuple[int, ...]] = {
    "ctrl": (VK_CONTROL, VK_LCONTROL, VK_RCONTROL),
    "shift": (VK_SHIFT, VK_LSHIFT, VK_RSHIFT),
    "alt": (VK_MENU, VK_LMENU, VK_RMENU),
    "win": (VK_LWIN, VK_RWIN),
}

_MODIFIER_ALIASES: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "lctrl": "ctrl",
    "rctrl": "ctrl",
    "shift": "shift",
    "lshift": "shift",
    "rshift": "shift",
    "alt": "alt",
    "menu": "alt",
    "lalt": "alt",
    "ralt": "alt",
    "win": "win",
    "windows": "win",
    "super": "win",
    "meta": "win",
    "lwin": "win",
    "rwin": "win",
}

_KEY_NAME_TO_VK: dict[str, int] = {
    "space": VK_SPACE,
    "enter": VK_RETURN,
    "return": VK_RETURN,
    "tab": VK_TAB,
    "backspace": VK_BACK,
    "esc": VK_ESCAPE,
    "escape": VK_ESCAPE,
    "pause": VK_PAUSE,
    "capslock": VK_CAPITAL,
    "caps": VK_CAPITAL,
    "pageup": VK_PRIOR,
    "pagedown": VK_NEXT,
    "end": VK_END,
    "home": VK_HOME,
    "left": VK_LEFT,
    "up": VK_UP,
    "right": VK_RIGHT,
    "down": VK_DOWN,
    "insert": VK_INSERT,
    "delete": VK_DELETE,
    "del": VK_DELETE,
    "plus": VK_OEM_PLUS,
    "comma": VK_OEM_COMMA,
    "minus": VK_OEM_MINUS,
    "period": VK_OEM_PERIOD,
    ";": VK_OEM_1,
    "/": VK_OEM_2,
    "`": VK_OEM_3,
    "[": VK_OEM_4,
    "\\": VK_OEM_5,
    "]": VK_OEM_6,
    "'": VK_OEM_7,
}
for _i in range(1, 25):
    _KEY_NAME_TO_VK[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    _KEY_NAME_TO_VK[_c] = ord(_c.upper())
for _d in "0123456789":
    _KEY_NAME_TO_VK[_d] = ord(_d)

_VK_TO_KEY_NAME: dict[int, str] = {
    VK_SPACE: "space",
    VK_RETURN: "enter",
    VK_TAB: "tab",
    VK_BACK: "backspace",
    VK_ESCAPE: "esc",
    VK_PAUSE: "pause",
    VK_CAPITAL: "capslock",
    VK_PRIOR: "pageup",
    VK_NEXT: "pagedown",
    VK_END: "end",
    VK_HOME: "home",
    VK_LEFT: "left",
    VK_UP: "up",
    VK_RIGHT: "right",
    VK_DOWN: "down",
    VK_INSERT: "insert",
    VK_DELETE: "delete",
    VK_OEM_PLUS: "plus",
    VK_OEM_COMMA: "comma",
    VK_OEM_MINUS: "minus",
    VK_OEM_PERIOD: "period",
    VK_OEM_1: ";",
    VK_OEM_2: "/",
    VK_OEM_3: "`",
    VK_OEM_4: "[",
    VK_OEM_5: "\\",
    VK_OEM_6: "]",
    VK_OEM_7: "'",
}
for _i in range(1, 25):
    _VK_TO_KEY_NAME[0x6F + _i] = f"f{_i}"
for _c in "abcdefghijklmnopqrstuvwxyz":
    _VK_TO_KEY_NAME[ord(_c.upper())] = _c
for _d in "0123456789":
    _VK_TO_KEY_NAME[ord(_d)] = _d

MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")


def vk_to_name(vk_code: int) -> str | None:
    """Map a Windows virtual-key code to a normalized key or modifier name."""
    if vk_code in MODIFIER_VKS:
        return MODIFIER_VKS[vk_code]
    return _VK_TO_KEY_NAME.get(vk_code)


def parse_hotkey(spec: str) -> tuple[frozenset[str], str | None]:
    """
    Parse a human-readable hotkey specification such as `"ctrl+shift+space"` or `"ctrl+win"`.

    Returns `(required_modifiers, trigger_key_name)`. `trigger_key_name` is `None`
    when the combo consists solely of modifiers (e.g. `"ctrl+win"`).
    """
    if not spec or not spec.strip():
        return frozenset({"ctrl", "shift"}), "space"

    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    modifiers: set[str] = set()
    trigger_key: str | None = None
    for part in parts:
        if part in _MODIFIER_ALIASES:
            modifiers.add(_MODIFIER_ALIASES[part])
        elif part in _KEY_NAME_TO_VK:
            trigger_key = _VK_TO_KEY_NAME[_KEY_NAME_TO_VK[part]]
        else:
            raise ValueError(f"Unsupported key token in hotkey specification: {part!r}")

    if not modifiers and trigger_key is None:
        raise ValueError(f"Empty hotkey specification: {spec!r}")
    return frozenset(modifiers), trigger_key


def format_hotkey(modifiers: frozenset[str] | set[str], trigger_key: str | None) -> str:
    """Format normalized modifiers and optional trigger key back to `"ctrl+shift+space"`."""
    ordered_mods = [m for m in MODIFIER_ORDER if m in modifiers]
    if trigger_key:
        ordered_mods.append(trigger_key)
    return "+".join(ordered_mods)


def format_hotkey_display(spec: str) -> str:
    """Pretty display label for UI badges (e.g. `Ctrl + Shift + Space`)."""
    try:
        mods, key = parse_hotkey(spec)
    except ValueError:
        return spec
    parts = [m.capitalize() for m in MODIFIER_ORDER if m in mods]
    if key:
        parts.append(
            key.upper() if (key.startswith("f") and key[1:].isdigit()) or len(key) == 1 else key.capitalize()
        )
    return " + ".join(parts)


class HotkeyStateMachine:
    """
    Deterministic state machine for Hybrid, Toggle, and Hold dictation triggers.

    - `hybrid`: quick tap (< `hold_threshold_s`) toggles hands-free recording;
      holding >= `hold_threshold_s` behaves as Push-to-Talk and stops on release.
    - `toggle`: press once to start, press again to stop.
    - `hold`: hold to record, release to stop.
    """

    def __init__(self, mode: str = "hybrid", hold_threshold_s: float = 0.35) -> None:
        self.mode = mode if mode in ("hybrid", "toggle", "hold") else "hybrid"
        self.hold_threshold_s = float(hold_threshold_s)
        self.is_recording = False
        self._combo_active = False
        self._combo_down_at = 0.0
        self._started_on_this_press = False

    def set_mode(self, mode: str) -> None:
        if mode in ("hybrid", "toggle", "hold"):
            self.mode = mode

    def reset_recording_state(self) -> None:
        """Reset recording state when recording was stopped externally (e.g., via UI button)."""
        self.is_recording = False
        self._started_on_this_press = False

    def on_combo_down(self, now: float | None = None) -> str | None:
        """Process initial combo press; returns `'start'`, `'stop'`, or `None`."""
        ts = time.monotonic() if now is None else float(now)
        if self._combo_active:
            return None  # Ignore OS key-repeat while keys remain held.
        self._combo_active = True
        self._combo_down_at = ts

        if self.mode == "toggle":
            if not self.is_recording:
                self.is_recording = True
                return "start"
            self.is_recording = False
            return "stop"

        if self.mode == "hold":
            if not self.is_recording:
                self.is_recording = True
                return "start"
            return None

        # Hybrid mode
        if not self.is_recording:
            self.is_recording = True
            self._started_on_this_press = True
            return "start"

        self.is_recording = False
        self._started_on_this_press = False
        return "stop"

    def on_combo_up(self, now: float | None = None) -> str | None:
        """Process combo release; returns `'stop'` or `None`."""
        ts = time.monotonic() if now is None else float(now)
        if not self._combo_active:
            return None
        self._combo_active = False

        if self.mode == "toggle":
            return None

        if self.mode == "hold":
            if self.is_recording:
                self.is_recording = False
                return "stop"
            return None

        # Hybrid mode
        held_duration = ts - self._combo_down_at
        if self.is_recording and self._started_on_this_press and held_duration >= self.hold_threshold_s:
            self.is_recording = False
            self._started_on_this_press = False
            return "stop"

        self._started_on_this_press = False
        return None

    def on_escape(self) -> str | None:
        """Cancel active dictation on Escape."""
        if self.is_recording:
            self.is_recording = False
            self._started_on_this_press = False
            return "cancel"
        return None


# 64-bit safe Win32 ctypes structures & prototypes
LRESULT = ctypes.c_ssize_t


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = ctypes.c_void_p

_user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = LRESULT

_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL

_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = wintypes.BOOL

_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.TranslateMessage.restype = wintypes.BOOL

_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.restype = LRESULT

_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL

_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short

_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE

_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class GlobalHotkeyManager:
    """
    Windows low-level keyboard hook (`WH_KEYBOARD_LL`) manager.

    Supports both dictation (`hotkey`) and voice-edit (`edit_hotkey`) triggers,
    dispatching events onto a separate worker queue so the Win32 hook callback
    returns within microseconds.
    """

    def __init__(
        self,
        hotkey: str = "ctrl+shift+space",
        edit_hotkey: str = "ctrl+shift+e",
        trigger_mode: str = "hybrid",
        on_start: Callable[[str], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._dictate_modifiers, self._dictate_trigger = parse_hotkey(hotkey)
        self._edit_modifiers, self._edit_trigger = parse_hotkey(edit_hotkey)
        self._dictate_state = HotkeyStateMachine(mode=trigger_mode)
        self._edit_state = HotkeyStateMachine(mode=trigger_mode)
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_cancel = on_cancel

        self._pressed_vks: set[int] = set()
        self._event_queue: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self._hook_handle: int | None = None
        self._hook_proc: Any = None
        self._hook_thread: threading.Thread | None = None
        self._dispatch_thread: threading.Thread | None = None
        self._hook_thread_id: int | None = None
        self._started_event = threading.Event()
        self._running = False

        # Interactive shortcut recording support
        self._capture_callback: Callable[[str], None] | None = None
        self._capture_mods: set[str] = set()
        self._capture_key: str | None = None

    @property
    def is_running(self) -> bool:
        return self._running and self._hook_handle is not None

    def update_settings(
        self,
        hotkey: str | None = None,
        edit_hotkey: str | None = None,
        trigger_mode: str | None = None,
    ) -> None:
        with self._lock:
            if hotkey is not None:
                self._dictate_modifiers, self._dictate_trigger = parse_hotkey(hotkey)
            if edit_hotkey is not None:
                self._edit_modifiers, self._edit_trigger = parse_hotkey(edit_hotkey)
            if trigger_mode is not None:
                self._dictate_state.set_mode(trigger_mode)
                self._edit_state.set_mode(trigger_mode)

    def reset_recording_state(self) -> None:
        with self._lock:
            self._dictate_state.reset_recording_state()
            self._edit_state.reset_recording_state()

    def start_recording_hotkey(self, on_captured: Callable[[str], None]) -> None:
        """Enter capture mode: the next valid shortcut released is passed to `on_captured`."""
        with self._lock:
            self._capture_mods = set()
            self._capture_key = None
            self._capture_callback = on_captured

    def cancel_recording_hotkey(self) -> None:
        with self._lock:
            self._capture_callback = None
            self._capture_mods = set()
            self._capture_key = None

    def start(self) -> bool:
        """Install the global keyboard hook and start the dispatch thread."""
        if self._running:
            return True
        self._running = True
        self._started_event.clear()

        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, name="CeresHotkeyDispatch", daemon=True
        )
        self._dispatch_thread.start()

        self._hook_thread = threading.Thread(target=self._hook_loop, name="CeresHotkeyHook", daemon=True)
        self._hook_thread.start()

        self._started_event.wait(timeout=3.0)
        return self._hook_handle is not None

    def stop(self) -> None:
        """Cleanly unhook the Win32 keyboard hook and stop worker threads."""
        self._running = False
        tid = self._hook_thread_id
        if tid:
            _user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
        if self._hook_thread is not None:
            self._hook_thread.join(timeout=2.0)
            self._hook_thread = None
        self._event_queue.put(("quit", None))
        if self._dispatch_thread is not None:
            self._dispatch_thread.join(timeout=2.0)
            self._dispatch_thread = None
        self._hook_thread_id = None

    def _active_modifiers(self, current_vk: int | None = None, is_down: bool = False) -> set[str]:
        mods: set[str] = set()
        for mod_name, vks in MODIFIER_CHECK_VKS.items():
            if any((_user32.GetAsyncKeyState(vk) & 0x8000) != 0 for vk in vks):
                mods.add(mod_name)
        for vk in list(self._pressed_vks):
            if vk in MODIFIER_VKS:
                mods.add(MODIFIER_VKS[vk])
        if current_vk is not None and current_vk in MODIFIER_VKS:
            mod_name = MODIFIER_VKS[current_vk]
            if is_down:
                mods.add(mod_name)
            else:
                mods.discard(mod_name)
        return mods

    def _is_combo_active(
        self,
        req_mods: frozenset[str],
        trigger_key: str | None,
        current_vk: int,
        is_down: bool,
    ) -> bool:
        mods = self._active_modifiers(current_vk=current_vk, is_down=is_down)
        if not req_mods.issubset(mods):
            return False
        if trigger_key is None:
            return len(req_mods) > 0
        key_vk = _KEY_NAME_TO_VK.get(trigger_key)
        if key_vk is None:
            return False
        if current_vk == key_vk:
            return is_down
        return key_vk in self._pressed_vks

    def _hook_loop(self) -> None:
        self._hook_thread_id = int(_kernel32.GetCurrentThreadId())
        self._hook_proc = _HOOKPROC(self._low_level_handler)
        h_mod = _kernel32.GetModuleHandleW(None)
        self._hook_handle = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc, h_mod, 0)
        if not self._hook_handle:
            err = ctypes.get_last_error()
            logger.error("SetWindowsHookExW failed with WinError %s", err)
            self._started_event.set()
            return

        self._started_event.set()
        msg = wintypes.MSG()
        try:
            while self._running:
                ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook_handle:
                _user32.UnhookWindowsHookEx(self._hook_handle)
                self._hook_handle = None

    def _low_level_handler(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0 and l_param:
            kb = _KBDLLHOOKSTRUCT.from_address(l_param)
            if (kb.flags & LLKHF_INJECTED) == 0:
                vk = int(kb.vkCode)
                is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                is_up = w_param in (WM_KEYUP, WM_SYSKEYUP)
                if is_down or is_up:
                    self._handle_key_event(vk, is_down)
        return _user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param)

    def _handle_key_event(self, vk: int, is_down: bool) -> None:
        now = time.monotonic()
        with self._lock:
            if is_down:
                self._pressed_vks.add(vk)
            else:
                self._pressed_vks.discard(vk)

            # Handle interactive shortcut capture in Settings
            if self._capture_callback is not None:
                name = vk_to_name(vk)
                if is_down and name:
                    if vk == VK_ESCAPE:
                        cb = self._capture_callback
                        self.cancel_recording_hotkey()
                        self._event_queue.put(("captured", (cb, "")))
                        return
                    if name in MODIFIER_ORDER:
                        self._capture_mods.add(name)
                    else:
                        self._capture_mods.update(self._active_modifiers(current_vk=vk, is_down=True))
                        self._capture_key = name
                        if self._capture_mods or name.startswith("f"):
                            spec = format_hotkey(self._capture_mods, self._capture_key)
                            cb = self._capture_callback
                            self.cancel_recording_hotkey()
                            self._event_queue.put(("captured", (cb, spec)))
                elif (
                    not is_down
                    and self._capture_mods
                    and self._capture_key is None
                    and len(self._capture_mods) >= 2
                    and not self._active_modifiers(current_vk=vk, is_down=False)
                ):
                    # Allow modifier-only combinations (e.g., Ctrl+Win) on release
                    spec = format_hotkey(self._capture_mods, None)
                    cb = self._capture_callback
                    self.cancel_recording_hotkey()
                    self._event_queue.put(("captured", (cb, spec)))
                return

            if is_down and vk == VK_ESCAPE:
                action = self._dictate_state.on_escape() or self._edit_state.on_escape()
                if action:
                    self._event_queue.put((action, None))
                return

            dictate_now = self._is_combo_active(self._dictate_modifiers, self._dictate_trigger, vk, is_down)
            edit_now = self._is_combo_active(self._edit_modifiers, self._edit_trigger, vk, is_down)

            if self._edit_state.is_recording:
                edit_action = (
                    self._edit_state.on_combo_down(now) if edit_now else self._edit_state.on_combo_up(now)
                )
                if edit_action == "stop":
                    self._event_queue.put(("stop", None))
                return

            if self._dictate_state.is_recording:
                dict_action = (
                    self._dictate_state.on_combo_down(now)
                    if dictate_now
                    else self._dictate_state.on_combo_up(now)
                )
                if dict_action == "stop":
                    self._event_queue.put(("stop", None))
                return

            if dictate_now:
                if self._dictate_state.on_combo_down(now) == "start":
                    self._event_queue.put(("start", "dictate"))
            else:
                self._dictate_state.on_combo_up(now)

            if edit_now:
                if self._edit_state.on_combo_down(now) == "start":
                    self._event_queue.put(("start", "edit"))
            else:
                self._edit_state.on_combo_up(now)

    def _dispatch_loop(self) -> None:
        while True:
            kind, payload = self._event_queue.get()
            if kind == "quit":
                break
            try:
                if kind == "start" and self._on_start:
                    self._on_start(str(payload or "dictate"))
                elif kind == "stop" and self._on_stop:
                    self._on_stop()
                elif kind == "cancel" and self._on_cancel:
                    self._on_cancel()
                elif kind == "captured" and payload is not None:
                    cb, spec = payload
                    cb(spec)
            except Exception as exc:
                logger.exception("Error in hotkey callback (%s): %s", kind, exc)

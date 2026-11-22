"""64-bit safe Win32 clipboard helpers and SendInput Unicode auto-paster."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import time
import uuid

logger = logging.getLogger(__name__)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_C = 0x43
VK_V = 0x56
_VK_MENU_MASK = 0xE8  # Unassigned VK to prevent Alt/Win keyup from activating menu bar


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Explicit 64-bit pointer types so HGLOBAL values are never truncated to 32-bit int.
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL

_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL

_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wintypes.BOOL

_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL

_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = ctypes.c_void_p

_user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
_user32.SetClipboardData.restype = ctypes.c_void_p

_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short

_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT

_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = ctypes.c_void_p

_kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
_kernel32.GlobalFree.restype = ctypes.c_void_p

_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalLock.restype = ctypes.c_void_p

_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalUnlock.restype = wintypes.BOOL


def _open_clipboard_with_retry(attempts: int = 6, delay_s: float = 0.015) -> bool:
    for _ in range(attempts):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(delay_s)
    return False


def get_clipboard_text() -> str | None:
    """Read UTF-16LE Unicode text from the Windows clipboard, or `None` if unavailable."""
    if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    if not _open_clipboard_with_retry():
        return None
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    """Write Unicode text (preserving accents/umlauts) to the Windows clipboard."""
    encoded = (text + "\0").encode("utf-16-le")
    h_mem = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not h_mem:
        return False

    ptr = _kernel32.GlobalLock(h_mem)
    if not ptr:
        _kernel32.GlobalFree(h_mem)
        return False
    ctypes.memmove(ptr, encoded, len(encoded))
    _kernel32.GlobalUnlock(h_mem)

    if not _open_clipboard_with_retry():
        _kernel32.GlobalFree(h_mem)
        return False
    try:
        _user32.EmptyClipboard()
        result = _user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        if not result:
            _kernel32.GlobalFree(h_mem)
            return False
        # Clipboard owns h_mem after SetClipboardData succeeds.
        return True
    finally:
        _user32.CloseClipboard()


def _send_key_events(events: list[tuple[int, int]]) -> int:
    arr = (_INPUT * len(events))()
    for idx, (vk, flags) in enumerate(events):
        arr[idx].type = INPUT_KEYBOARD
        arr[idx].union.ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    return int(_user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT)))


def _release_held_modifiers() -> None:
    """Wait briefly for user modifier keys to lift, then release any stray modifiers."""
    side_non_ctrl = (VK_LSHIFT, VK_RSHIFT, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN)
    side_ctrl = (VK_LCONTROL, VK_RCONTROL)
    alt_win_vks = (VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN)

    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        if not any((_user32.GetAsyncKeyState(vk) & 0x8000) != 0 for vk in side_non_ctrl):
            break
        time.sleep(0.015)

    release_events: list[tuple[int, int]] = []
    needs_menu_mask = False
    for vk in (*side_non_ctrl, *side_ctrl):
        if (_user32.GetAsyncKeyState(vk) & 0x8000) != 0:
            if vk in alt_win_vks:
                needs_menu_mask = True
            release_events.append((vk, KEYEVENTF_KEYUP))
    if release_events:
        if needs_menu_mask:
            release_events = [(_VK_MENU_MASK, 0), (_VK_MENU_MASK, KEYEVENTF_KEYUP), *release_events]
        _send_key_events(release_events)
        time.sleep(0.02)


def capture_selected_text(timeout_s: float = 0.25) -> tuple[str | None, str | None]:
    """
    Copy the currently highlighted selection via Ctrl+C without losing prior clipboard text.

    Returns `(selected_text, prior_clipboard)`. If nothing was selected, restores
    `prior_clipboard` and returns `(None, prior_clipboard)`.
    """
    prior_clipboard = get_clipboard_text()
    sentinel = f"__CERES_SENTINEL_{uuid.uuid4().hex}__"
    if not set_clipboard_text(sentinel):
        return None, prior_clipboard

    _release_held_modifiers()
    _send_key_events(
        [
            (VK_CONTROL, 0),
            (VK_C, 0),
            (VK_C, KEYEVENTF_KEYUP),
            (VK_CONTROL, KEYEVENTF_KEYUP),
        ]
    )

    deadline = time.monotonic() + max(0.05, float(timeout_s))
    selected: str | None = None
    while time.monotonic() < deadline:
        time.sleep(0.02)
        current = get_clipboard_text()
        if current is not None and current != sentinel:
            if current.strip():
                selected = current
            break

    if selected is None and prior_clipboard is not None:
        set_clipboard_text(prior_clipboard)
    return selected, prior_clipboard


class TextInjector:
    """Safely copies polished text to clipboard and optionally pastes via Ctrl+V."""

    def capture_selected_text(self, timeout_s: float = 0.25) -> tuple[str | None, str | None]:
        return capture_selected_text(timeout_s=timeout_s)

    def inject(
        self,
        text: str,
        *,
        auto_paste: bool = True,
        preserve_clipboard: bool = False,
        restore_text: str | None = None,
    ) -> bool:
        if not text:
            return False

        prior_clipboard = (
            restore_text
            if restore_text is not None
            else (get_clipboard_text() if preserve_clipboard else None)
        )
        if not set_clipboard_text(text):
            logger.error("Failed to copy dictated text to Windows clipboard")
            return False

        if auto_paste:
            _release_held_modifiers()
            sent = _send_key_events(
                [
                    (VK_CONTROL, 0),
                    (VK_V, 0),
                    (VK_V, KEYEVENTF_KEYUP),
                    (VK_CONTROL, KEYEVENTF_KEYUP),
                ]
            )
            if sent != 4:
                logger.warning("SendInput dispatched %s/4 paste events", sent)

        if preserve_clipboard and prior_clipboard is not None:
            time.sleep(0.22)
            set_clipboard_text(prior_clipboard)

        return True

"""Active foreground window detection & category classification for context-aware styling."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_EMAIL_PROCESSES = frozenset(
    {
        "outlook.exe",
        "olk.exe",
        "thunderbird.exe",
        "hxmail.exe",
        "mailspring.exe",
        "emclient.exe",
    }
)

_CHAT_PROCESSES = frozenset(
    {
        "teams.exe",
        "ms-teams.exe",
        "slack.exe",
        "discord.exe",
        "whatsapp.exe",
        "telegram.exe",
        "signal.exe",
        "zoom.exe",
        "webex.exe",
    }
)

_CODE_PROCESSES = frozenset(
    {
        "code.exe",
        "code - insiders.exe",
        "cursor.exe",
        "windsurf.exe",
        "antigravity.exe",
        "pycharm64.exe",
        "idea64.exe",
        "clion64.exe",
        "devenv.exe",
        "windowsterminal.exe",
        "wt.exe",
        "powershell.exe",
        "pwsh.exe",
        "cmd.exe",
        "sublime_text.exe",
        "notepad++.exe",
        "zed.exe",
    }
)

_DOCUMENT_PROCESSES = frozenset(
    {
        "winword.exe",
        "notion.exe",
        "obsidian.exe",
        "onenote.exe",
        "soffice.bin",
        "wordpad.exe",
        "powerpnt.exe",
        "excel.exe",
    }
)

_EMAIL_TITLES = (
    "gmail",
    "outlook",
    "proton mail",
    "roundcube",
    "webmail",
    "thunderbird",
    "fastmail",
    "postfach",
)
_CHAT_TITLES = ("microsoft teams", "slack", "discord", "whatsapp", "telegram", "mattermost", "rocketchat")
_CODE_TITLES = (
    "github",
    "gitlab",
    "bitbucket",
    "stackoverflow",
    "stack overflow",
    "pull request",
    "visual studio",
    "jupyter",
    "colab",
)
_DOCUMENT_TITLES = ("google docs", "notion", "confluence", "sharepoint", "overleaf", "microsoft word")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND

_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int

_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int

_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE

_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


@dataclass(frozen=True)
class ActiveWindowContext:
    """Metadata describing the foreground window at dictation start."""

    process_name: str
    window_title: str
    category: str  # "email" | "chat" | "code" | "document" | "general"


def classify_window(process_name: str, window_title: str) -> str:
    """Classify an application window into a tone/formatting category."""
    proc = (process_name or "").strip().lower()
    title = (window_title or "").strip().lower()

    if proc in _EMAIL_PROCESSES:
        return "email"
    if proc in _CHAT_PROCESSES:
        return "chat"
    if proc in _CODE_PROCESSES:
        return "code"
    if proc in _DOCUMENT_PROCESSES:
        return "document"

    if any(kw in title for kw in _EMAIL_TITLES):
        return "email"
    if any(kw in title for kw in _CHAT_TITLES):
        return "chat"
    if any(kw in title for kw in _CODE_TITLES):
        return "code"
    if any(kw in title for kw in _DOCUMENT_TITLES):
        return "document"

    return "general"


def get_active_window_context() -> ActiveWindowContext:
    """Inspect the currently focused Windows application safely."""
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return ActiveWindowContext("", "", "general")

        length = int(_user32.GetWindowTextLengthW(hwnd))
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value

        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = ""
        if pid.value:
            handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if handle:
                try:
                    size = wintypes.DWORD(512)
                    path_buf = ctypes.create_unicode_buffer(512)
                    if _kernel32.QueryFullProcessImageNameW(handle, 0, path_buf, ctypes.byref(size)):
                        proc_name = Path(path_buf.value).name
                finally:
                    _kernel32.CloseHandle(handle)

        category = classify_window(proc_name, title)
        return ActiveWindowContext(process_name=proc_name, window_title=title, category=category)
    except Exception as exc:
        logger.debug("Active window context lookup failed: %s", exc)
        return ActiveWindowContext("", "", "general")

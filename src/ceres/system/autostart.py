"""Windows Run-key autostart management via HKCU registry."""

from __future__ import annotations

import contextlib
import logging
import winreg
from pathlib import Path

from ceres import PROJECT_ROOT

logger = logging.getLogger(__name__)

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "CeresDictation"


def get_autostart_command(project_root: Path = PROJECT_ROOT) -> str:
    """Return the Windows Run command launching Ceres minimized in the system tray."""
    vbs_path = Path(project_root) / "launch_ceres.vbs"
    return f'wscript.exe "{vbs_path}" --minimized'


def get_autostart_enabled() -> bool:
    """Return True if Ceres is registered under HKCU Run."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
            return bool(str(val).strip())
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.debug("Unable to read Windows autostart registry key: %s", exc)
        return False


def set_autostart_enabled(enabled: bool, project_root: Path = PROJECT_ROOT) -> bool:
    """Enable or disable Windows login autostart for the current user."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(
                    key,
                    AUTOSTART_VALUE_NAME,
                    0,
                    winreg.REG_SZ,
                    get_autostart_command(project_root),
                )
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
        return True
    except OSError as exc:
        logger.warning("Failed to update Windows autostart registry key: %s", exc)
        return False

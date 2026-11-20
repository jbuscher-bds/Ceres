"""Application entry point, Qt 6 HiDPI setup, single-instance guard, and CLI self-test."""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import ctypes.wintypes as wintypes
import logging
import sys
from pathlib import Path

from ceres import DATA_DIR, ensure_directories

ERROR_ALREADY_EXISTS = 183


class _TelemetryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "openvino_telemetry" not in msg and "storing client ID" not in msg


def _setup_logging() -> None:
    ensure_directories()
    log_path = Path(DATA_DIR) / "ceres.log"
    telemetry_filter = _TelemetryFilter()
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    stream_handler = logging.StreamHandler(sys.stdout)
    file_handler.addFilter(telemetry_filter)
    stream_handler.addFilter(telemetry_filter)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[file_handler, stream_handler],
    )


def _acquire_single_instance_mutex() -> int | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, "Local\\CeresVoiceDictationSingleInstance")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    return int(handle)


def _release_mutex(handle: int | None) -> None:
    if handle:
        ctypes.WinDLL("kernel32").CloseHandle(wintypes.HANDLE(handle))


def _create_qt_app() -> object:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ceres.ui.theme import get_app_qicon

    with contextlib.suppress(Exception):
        ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID("jbuscher-bds.Ceres.Desktop")

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Ceres")
    app.setWindowIcon(get_app_qicon())
    return app


def run_self_test() -> int:
    """Verify Qt 6 HiDPI pill WS_EX_NOACTIVATE, dual WH_KEYBOARD_LL hook, context lookup, and devices."""
    from ceres.engine.model_manager import get_device_info
    from ceres.system.context import get_active_window_context
    from ceres.ui.main_window import CeresWindow

    qt_app = _create_qt_app()
    win = CeresWindow(minimized=True, skip_model_load=True)
    try:
        qt_app.processEvents()  # type: ignore[attr-defined]
        pill_ok = win.pill.has_noactivate_style()
        pill_size = win.pill.logical_size()
        hook_ok = win.hotkey_manager.is_running
        devices = get_device_info()
        ctx = get_active_window_context()
        print(f"SELF_TEST pill_ws_ex_noactivate={pill_ok}")
        print(f"SELF_TEST pill_logical_size={pill_size}")
        print(f"SELF_TEST global_hotkey_hook={hook_ok}")
        print(f"SELF_TEST active_context={ctx.category}")
        print(f"SELF_TEST openvino_devices={devices}")
        if not pill_ok or not hook_ok:
            return 1
        return 0
    finally:
        win.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ceres", description="Ceres local NPU voice dictation")
    parser.add_argument("--minimized", action="store_true", help="Start minimized in the Windows system tray")
    parser.add_argument("--self-test", action="store_true", help="Run system UI/hook self-test and exit")
    args = parser.parse_args(argv)

    _setup_logging()

    if args.self_test:
        return run_self_test()

    mutex_handle = _acquire_single_instance_mutex()
    if mutex_handle is None:
        ctypes.WinDLL("user32").MessageBoxW(
            None,
            "Ceres is already running in your Windows system tray.",
            "Ceres",
            0x00000040,
        )
        return 0

    try:
        from ceres.ui.main_window import CeresWindow

        qt_app = _create_qt_app()
        win = CeresWindow(minimized=args.minimized)
        _ = win  # Keep strong reference for event loop lifetime
        return int(qt_app.exec())  # type: ignore[attr-defined]
    finally:
        _release_mutex(mutex_handle)


if __name__ == "__main__":
    raise SystemExit(main())

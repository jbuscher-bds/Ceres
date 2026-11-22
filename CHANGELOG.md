# Changelog

All notable changes to **Ceres** are documented in this file.

## [0.2.0] — 2026-11-20

### Added
- **Native Qt 6 (PySide6) HiDPI UI**: Replaced CustomTkinter + pystray with native Qt 6 widgets, vector `QSystemTrayIcon`, and a smooth 60 FPS translucent `QPainter` pill overlay (`360x58` DIPs) that scales cleanly on 14" HiDPI displays and follows the active foreground monitor.
- **Voice Edit Mode (`Ctrl + Shift + E`)**: Highlight text in any app, press the edit shortcut, speak a command (summarize, translate, adjust tone), and replace the selection via NPU `Qwen3-8B`.
- **Custom Vocabulary**: User-defined domain terms, names, and acronyms injected into the NPU rewriting prompt.
- **Speech-Band Spectral VAD**: Rejects low-frequency fan rumble and mains hum (<180 Hz) in addition to broadband RMS gating.
- **History Search, Markdown Export & Re-polish**: Live search bar, Markdown export, and one-click re-polishing on saved transcripts.
- **Windows Login Autostart & Engineering Tooling**: Registry Run-key toggle, `ruff` + `mypy` + `pytest` GitHub Actions CI, `setup.ps1`, PyInstaller spec, and MIT license.

### Fixed
- Eliminated 30 Hz audio-meter redraw storms on the main window and system tray by routing RMS levels exclusively to the floating pill animation timer.
- Suppressed OpenVINO telemetry initialization noise on restricted environments.

## [0.1.0] — 2026-11-18

### Added
- Initial release with OpenVINO GenAI two-stage pipeline (`Whisper` + `Qwen3-8B-int4-cw-ov` on Intel AI Boost NPU), isolated NPU Whisper probing, Win32 global `Hybrid` / `Toggle` / `Hold` hotkeys, `WS_EX_NOACTIVATE` overlay pill, and multilingual EN/NL/DE rewriting guardrails.

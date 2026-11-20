# Contributing to Ceres

Thank you for your interest in contributing to **Ceres**!

## Development Setup

1. Clone the repository and run the setup script on Windows (Python 3.12–3.14):
   ```powershell
   .\setup.ps1
   ```
2. Run quality checks before opening a pull request:
   ```powershell
   .\.venv\Scripts\ruff.exe check src tests scripts
   .\.venv\Scripts\mypy.exe src
   .\.venv\Scripts\pytest.exe -v
   ```
3. To run the Qt 6 & Win32 hook smoke check:
   ```powershell
   .\.venv\Scripts\python.exe -m ceres --self-test
   ```

## Guidelines

- Keep Win32 `ctypes` declarations explicitly typed with 64-bit safe pointer/integer prototypes (`c_void_p`, `c_ssize_t`).
- Keep unit tests hardware-independent where possible so CI can run on standard `windows-latest` runners without an Intel NPU.
- Never commit local models (`models/`), virtual environments (`.venv/`), or personal dictation databases (`data/`).

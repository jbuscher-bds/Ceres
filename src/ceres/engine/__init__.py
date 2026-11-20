"""OpenVINO GenAI speech-to-text and sentence-rewriting engine for Intel AI Boost NPU."""

from __future__ import annotations

import os

# Prevent OpenVINO telemetry initialization on import (privacy-first offline app, no GA/file writes).
_had_ci = "CI" in os.environ
_prev_ci = os.environ.get("CI")
os.environ["CI"] = "true"
try:
    import openvino as _ov  # noqa: F401
    import openvino_genai as _ovg  # noqa: F401
finally:
    if _had_ci and _prev_ci is not None:
        os.environ["CI"] = _prev_ci
    else:
        os.environ.pop("CI", None)

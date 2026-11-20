"""OpenVINO device discovery, model catalog, and Hugging Face downloader."""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

from ceres import MODELS_DIR

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    """Metadata for a supported OpenVINO Hugging Face repository."""

    repo_id: str
    display_name: str
    approx_size_gb: float
    kind: str  # "whisper" | "llm"


WHISPER_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec(
        repo_id="OpenVINO/whisper-large-v3-turbo-int8-ov",
        display_name="Whisper Large V3 Turbo (INT8 — Best Accuracy EN/NL/DE, Arc GPU)",
        approx_size_gb=0.80,
        kind="whisper",
    ),
    ModelSpec(
        repo_id="OpenVINO/whisper-base-int8-ov",
        display_name="Whisper Base (INT8 — Ultra-Light Intel NPU)",
        approx_size_gb=0.15,
        kind="whisper",
    ),
)

LLM_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec(
        repo_id="OpenVINO/Qwen3-8B-int4-cw-ov",
        display_name="Qwen3 8B (INT4-CW — Multilingual EN/NL/DE NPU)",
        approx_size_gb=4.30,
        kind="llm",
    ),
)


def get_model_spec(repo_id: str) -> ModelSpec | None:
    for spec in (*WHISPER_CATALOG, *LLM_CATALOG):
        if spec.repo_id == repo_id:
            return spec
    return None


def model_local_dir(repo_id: str, models_dir: Path = MODELS_DIR) -> Path:
    """Resolve local folder path for a Hugging Face repository ID."""
    folder_name = repo_id.strip().split("/")[-1]
    return Path(models_dir) / folder_name


def is_model_downloaded(repo_id: str, models_dir: Path = MODELS_DIR) -> bool:
    """Return True if the OpenVINO IR XML and BIN weights exist locally."""
    target = model_local_dir(repo_id, models_dir=models_dir)
    if not target.is_dir():
        return False
    has_xml = any(target.glob("*.xml"))
    has_bin = any(target.glob("*.bin"))
    return has_xml and has_bin


def get_device_info() -> dict[str, str]:
    """Return `{device_id: full_device_name}` for all available OpenVINO devices."""
    try:
        import openvino as ov

        core = ov.Core()
        info: dict[str, str] = {}
        for dev in core.available_devices:
            try:
                info[dev] = str(core.get_property(dev, "FULL_DEVICE_NAME"))
            except Exception:
                info[dev] = dev
        return info
    except Exception as exc:
        logger.warning("Failed to query OpenVINO devices: %s", exc)
        return {"CPU": "CPU"}


def get_fallback_chain(preferred: str = "NPU", available: list[str] | None = None) -> list[str]:
    """Compute ordered OpenVINO device fallback chain."""
    if available is None:
        available = list(get_device_info().keys())
    pref = (preferred or "NPU").upper()
    if pref == "CPU":
        base_order = ["CPU"]
    elif pref == "GPU":
        base_order = ["GPU", "NPU", "CPU"]
    else:
        base_order = ["NPU", "GPU", "CPU"]

    chain = [dev for dev in base_order if dev in available]
    if not chain:
        return ["CPU"]
    return chain


def _dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        if item.is_file():
            with contextlib.suppress(OSError):
                total += item.stat().st_size
    return total


@contextlib.contextmanager
def _prefer_ipv4() -> Iterator[None]:
    """Temporarily prefer IPv4 (AF_INET) to avoid broken ISP IPv6 TLS resets."""
    orig_getaddrinfo = socket.getaddrinfo

    def _ipv4_first(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002, ANN001, ANN202
        if family in (0, socket.AF_UNSPEC):
            try:
                v4 = orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
                if v4:
                    return v4
            except socket.gaierror:
                pass
        return orig_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = _ipv4_first
    try:
        yield
    finally:
        socket.getaddrinfo = orig_getaddrinfo


def download_model(
    repo_id: str,
    models_dir: Path = MODELS_DIR,
    progress_cb: Callable[[float, str], None] | None = None,
) -> Path:
    """
    Download an OpenVINO model snapshot from Hugging Face Hub with progress updates.

    Returns the local model directory path.
    """
    target_dir = model_local_dir(repo_id, models_dir=models_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    if is_model_downloaded(repo_id, models_dir=models_dir):
        if progress_cb:
            progress_cb(1.0, f"{target_dir.name} already ready")
        return target_dir

    expected_bytes = 0
    with _prefer_ipv4():
        try:
            info = HfApi().model_info(repo_id, files_metadata=True)
            expected_bytes = sum(int(s.size or 0) for s in (info.siblings or []))
        except Exception as exc:
            logger.debug("Could not fetch remote size metadata for %s: %s", repo_id, exc)
    if expected_bytes <= 0:
        spec = get_model_spec(repo_id)
        expected_bytes = int((spec.approx_size_gb if spec else 1.5) * (1024**3))

    stop_watch = threading.Event()
    last_pct = [-1]

    def _watch_progress() -> None:
        while not stop_watch.is_set():
            if progress_cb:
                current = _dir_size_bytes(target_dir)
                frac = min(0.98, max(0.01, current / max(1, expected_bytes)))
                pct_step = int(frac * 100)
                if pct_step != last_pct[0]:
                    last_pct[0] = pct_step
                    mb_done = current / (1024 * 1024)
                    mb_total = expected_bytes / (1024 * 1024)
                    progress_cb(frac, f"Downloading {target_dir.name}: {mb_done:.0f} / {mb_total:.0f} MB")
            time.sleep(0.4)

    watcher = threading.Thread(target=_watch_progress, daemon=True)
    watcher.start()
    try:
        last_err: Exception | None = None
        prev_xet = os.environ.get("HF_HUB_DISABLE_XET")
        try:
            with _prefer_ipv4():
                for attempt in range(1, 4):
                    try:
                        if attempt >= 2:
                            os.environ["HF_HUB_DISABLE_XET"] = "1"
                        snapshot_download(repo_id=repo_id, local_dir=str(target_dir))
                        last_err = None
                        break
                    except Exception as exc:
                        last_err = exc
                        logger.warning("Download attempt %d/3 failed for %s: %s", attempt, repo_id, exc)
                        if attempt < 3:
                            time.sleep(2.0 * attempt)
        finally:
            if prev_xet is None:
                os.environ.pop("HF_HUB_DISABLE_XET", None)
            else:
                os.environ["HF_HUB_DISABLE_XET"] = prev_xet
        if last_err is not None:
            raise last_err
    finally:
        stop_watch.set()
        watcher.join(timeout=1.0)

    if progress_cb:
        progress_cb(1.0, f"Downloaded {target_dir.name}")
    return target_dir

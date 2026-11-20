"""OpenVINO GenAI Whisper speech-to-text pipeline with NPU caching & hallucination filtering."""

from __future__ import annotations

import contextlib
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import openvino_genai as ov_genai

from ceres import OV_CACHE_DIR
from ceres.engine.model_manager import get_fallback_chain
from ceres.system.audio import SAMPLE_RATE, split_audio_chunks

logger = logging.getLogger(__name__)

_KNOWN_UNSUPPORTED_NPU_WHISPER = frozenset({"whisper-large-v3-turbo-int8-ov"})

_HALLUCINATION_EXACT = frozenset(
    {
        "",
        "you",
        "thank you for watching",
        "thanks for watching",
        "thanks for watching!",
        "thank you for watching!",
        "please subscribe",
        "subscribe",
        "like and subscribe",
        "subtitles by",
        "ondertitels ingediend door",
        "ondertiteling door",
        "bedankt voor het kijken",
        "bedankt voor het luisteren",
        "untertitel der amara.org-community",
        "untertitel im auftrag des zdf",
        "vielen dank fürs zuschauen",
        "danke fürs zuschauen",
        "musik",
        "music",
        "applause",
        "laughter",
    }
)

_HALLUCINATION_SUBSTRINGS = (
    "amara.org",
    "thank you for watching",
    "thanks for watching",
    "ondertitels ingediend door",
    "ondertiteling door",
    "bedankt voor het kijken",
    "untertitel im auftrag",
    "untertitel der amara",
    "vielen dank fürs zuschauen",
    "danke fürs zuschauen",
    "subtitles by ",
    "captioned by ",
    "transcribed by ",
)

_NON_SPEECH_BRACKET_RE = re.compile(r"^[\s\[\]()*♪♫.,!?;:\-–—]+$")


def is_hallucination(text: str) -> bool:
    """Return True if Whisper output is an empty marker or known silence hallucination."""
    cleaned = text.strip()
    if not cleaned:
        return True
    if _NON_SPEECH_BRACKET_RE.match(cleaned):
        return True
    lowered = cleaned.lower().strip(" .!?,;:\"'♪♫[]()")
    if lowered in _HALLUCINATION_EXACT:
        return True
    return any(sub in lowered for sub in _HALLUCINATION_SUBSTRINGS)


class WhisperTranscriber:
    """Manages `openvino_genai.WhisperPipeline` on Intel AI Boost NPU with GPU/CPU fallback."""

    def __init__(self) -> None:
        self.pipe: ov_genai.WhisperPipeline | None = None
        self.active_device: str = "unloaded"
        self.model_dir: Path | None = None

    def _probe_whisper_npu(self, model_path: Path, dev_cache: Path, cache_dir: Path) -> bool:
        """
        Verify Whisper NPU compilation in an isolated subprocess before loading in-process.

        Prevents Level Zero `ZE_RESULT_ERROR_DEVICE_LOST` from poisoning the NPU
        context used by the LLM rewriter in the main process.
        """
        if model_path.name in _KNOWN_UNSUPPORTED_NPU_WHISPER:
            logger.info("Skipping NPU for %s (known unsupported on NPU); using GPU/CPU", model_path.name)
            return False

        probe_file = Path(cache_dir) / "whisper_npu_probe.json"
        probe_data: dict[str, bool] = {}
        if probe_file.exists():
            try:
                loaded = json.loads(probe_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    probe_data = {str(k): bool(v) for k, v in loaded.items()}
            except Exception:
                probe_data = {}

        if model_path.name in probe_data:
            return probe_data[model_path.name]

        probe_code = (
            "import sys, numpy as np, openvino_genai as ovg; "
            "p = ovg.WhisperPipeline(sys.argv[1], 'NPU', CACHE_DIR=sys.argv[2]); "
            "cfg = p.get_generation_config(); "
            "cfg.task = 'transcribe'; cfg.return_timestamps = False; "
            "p.generate(np.zeros(16000, dtype=np.float32).tolist(), cfg)"
        )
        ok = False
        try:
            logger.info("Running one-time isolated NPU probe for %s...", model_path.name)
            proc = subprocess.run(
                [sys.executable, "-c", probe_code, str(model_path), str(dev_cache)],
                timeout=240,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            ok = proc.returncode == 0
            if not ok:
                logger.warning(
                    "Isolated NPU probe failed for %s (rc=%s): %s",
                    model_path.name,
                    proc.returncode,
                    proc.stderr[-400:],
                )
        except Exception as exc:
            logger.warning("Isolated NPU probe errored for %s: %s", model_path.name, exc)
            ok = False

        probe_data[model_path.name] = ok
        try:
            probe_file.parent.mkdir(parents=True, exist_ok=True)
            probe_file.write_text(json.dumps(probe_data, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        return ok

    def load(
        self,
        model_dir: Path,
        preferred_device: str = "NPU",
        cache_dir: Path = OV_CACHE_DIR,
    ) -> str:
        """Load and warm up WhisperPipeline on the best available device."""
        model_path = Path(model_dir)
        last_exc: Exception | None = None

        for device in get_fallback_chain(preferred_device):
            dev_cache = Path(cache_dir) / f"whisper_{model_path.name}_{device.lower()}"
            dev_cache.mkdir(parents=True, exist_ok=True)
            if device == "NPU" and not self._probe_whisper_npu(model_path, dev_cache, Path(cache_dir)):
                continue
            try:
                logger.info("Loading WhisperPipeline (%s) on %s...", model_path.name, device)
                pipe = ov_genai.WhisperPipeline(str(model_path), device, CACHE_DIR=str(dev_cache))
                # Run a 1-second silent warmup so graph compilation completes upfront.
                warmup_audio = np.zeros(SAMPLE_RATE, dtype=np.float32).tolist()
                cfg = pipe.get_generation_config()
                self._apply_config(cfg, language="auto")
                pipe.generate(warmup_audio, cfg)

                self.pipe = pipe
                self.active_device = device
                self.model_dir = model_path
                logger.info("WhisperPipeline ready on %s", device)
                return device
            except Exception as exc:
                logger.warning("WhisperPipeline failed on %s (%s); trying next device", device, exc)
                last_exc = exc

        raise RuntimeError(f"Unable to initialize WhisperPipeline on any device: {last_exc}") from last_exc

    @staticmethod
    def _apply_config(cfg: ov_genai.WhisperGenerationConfig, language: str) -> None:
        with contextlib.suppress(Exception):
            cfg.task = "transcribe"
        with contextlib.suppress(Exception):
            cfg.return_timestamps = False
        with contextlib.suppress(Exception):
            if language in ("en", "nl", "de"):
                cfg.language = f"<|{language}|>"
            else:
                cfg.language = None

    def transcribe(self, audio: np.ndarray, language: str = "auto") -> tuple[str, str]:
        """
        Transcribe float32 16 kHz mono audio into `(raw_text, detected_language)`.
        """
        if self.pipe is None:
            raise RuntimeError("WhisperTranscriber is not loaded")

        chunks = split_audio_chunks(audio, sample_rate=SAMPLE_RATE, max_chunk_s=28.0)
        if not chunks:
            return "", language

        parts: list[str] = []
        detected_lang = language
        for chunk in chunks:
            cfg = self.pipe.get_generation_config()
            self._apply_config(cfg, language=language)
            result = self.pipe.generate(chunk.tolist(), cfg)

            texts = getattr(result, "texts", None)
            chunk_text = " ".join(str(t) for t in texts).strip() if texts else str(result).strip()
            res_lang = getattr(result, "language", None)
            if res_lang and language == "auto":
                detected_lang = str(res_lang).strip("<|> ")
            if chunk_text and not is_hallucination(chunk_text):
                parts.append(chunk_text)

        combined = " ".join(parts).strip()
        if is_hallucination(combined):
            return "", detected_lang
        return combined, detected_lang or language

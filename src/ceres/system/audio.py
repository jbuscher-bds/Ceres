"""Microphone audio capture, silence gating, chunking, and subtle audio cues."""

from __future__ import annotations

import contextlib
import ctypes
import io
import logging
import math
import struct
import threading
import time
import wave
from collections.abc import Callable
from typing import Any

import numpy as np

sd: Any
try:
    import sounddevice as _sd

    sd = _sd
except Exception:  # pragma: no cover - fallback when sounddevice/PortAudio fails to import
    sd = None

winsound: Any
try:
    import winsound as _winsound

    winsound = _winsound
except ImportError:  # pragma: no cover
    winsound = None

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000


def _build_chime_wav(freq_hz: float, duration_s: float = 0.09, volume: float = 0.16) -> bytes:
    """Synthesize a soft fade-in/fade-out 16-bit PCM WAV chime in memory."""
    num_samples = max(1, int(SAMPLE_RATE * duration_s))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for i in range(num_samples):
            t = i / SAMPLE_RATE
            # Smooth raised-cosine envelope to avoid clicks.
            env = math.sin(math.pi * (i / num_samples)) ** 2
            sample = volume * env * math.sin(2.0 * math.pi * freq_hz * t)
            frames.extend(struct.pack("<h", max(-32767, min(32767, int(sample * 32767)))))
        wf.writeframes(bytes(frames))
    return buf.getvalue()


_START_CHIME_WAV = _build_chime_wav(660.0, 0.08, 0.14)
_STOP_CHIME_WAV = _build_chime_wav(494.0, 0.09, 0.12)
_CANCEL_CHIME_WAV = _build_chime_wav(330.0, 0.10, 0.12)


def play_cue(kind: str) -> None:
    """Play a gentle non-blocking sound cue ('start', 'stop', or 'cancel')."""
    if winsound is None:
        return
    wav_data = {
        "start": _START_CHIME_WAV,
        "stop": _STOP_CHIME_WAV,
        "cancel": _CANCEL_CHIME_WAV,
    }.get(kind)
    if not wav_data:
        return

    def _play() -> None:
        with contextlib.suppress(Exception):
            winsound.PlaySound(wav_data, winsound.SND_MEMORY | winsound.SND_ASYNC)

    threading.Thread(target=_play, daemon=True).start()


def list_input_devices() -> list[dict]:
    """Return available microphone input devices."""
    devices: list[dict] = []
    if sd is None:
        return devices
    try:
        all_devs = sd.query_devices()
        default_in = (
            sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
        )
        for idx, dev in enumerate(all_devs):
            max_ch = int(dev.get("max_input_channels", 0))
            if max_ch <= 0:
                continue
            devices.append(
                {
                    "index": idx,
                    "name": str(dev.get("name", f"Input {idx}")),
                    "channels": max_ch,
                    "default": idx == default_in,
                }
            )
    except Exception as exc:
        logger.warning("Failed to list audio input devices: %s", exc)
    return devices


def trim_silence(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    frame_ms: int = 20,
    rms_threshold: float = 0.007,
    pad_ms: int = 240,
    min_active_frames: int = 12,
    voice_ratio_threshold: float = 0.35,
) -> tuple[np.ndarray, bool]:
    """
    Detect speech using broadband RMS + speech-band spectral VAD (180–3800 Hz).

    Returns `(trimmed_audio_float32, has_speech)`. Rejects silence, short clicks,
    and low-frequency fan/AC rumble below 180 Hz.
    """
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return np.array([], dtype=np.float32), False

    # Remove DC offset from cheap microphone preamps before energy check.
    arr = arr - float(np.mean(arr))

    frame_samples = max(1, int(sample_rate * frame_ms / 1000))
    num_frames = arr.size // frame_samples
    if num_frames == 0:
        rms = float(np.sqrt(np.mean(arr * arr)))
        return (arr, True) if rms >= rms_threshold else (np.array([], dtype=np.float32), False)

    usable = arr[: num_frames * frame_samples].reshape(num_frames, frame_samples)
    frame_rms = np.sqrt(np.mean(usable * usable, axis=1))

    # Speech-band spectral ratio (180 Hz .. 3800 Hz) filters out fan rumble / mains hum.
    freqs = np.fft.rfftfreq(frame_samples, d=1.0 / sample_rate)
    voice_mask = (freqs >= 180.0) & (freqs <= 3800.0)
    window = np.hanning(frame_samples).astype(np.float32)
    spectrum = np.fft.rfft(usable * window, axis=1)
    power = (spectrum.real * spectrum.real) + (spectrum.imag * spectrum.imag)
    total_power = np.sum(power, axis=1) + 1e-12
    voice_ratio = np.sum(power[:, voice_mask], axis=1) / total_power

    noise_floor = float(np.percentile(frame_rms, 10))
    adaptive_threshold = noise_floor * 2.0 if noise_floor < 0.04 else rms_threshold
    effective_threshold = max(rms_threshold, adaptive_threshold)

    raw_active = (frame_rms >= effective_threshold) & (voice_ratio >= voice_ratio_threshold)
    # Hangover smoothing bridges brief 20-40ms consonantal dips between active frames.
    active_mask = raw_active.copy()
    if num_frames >= 3:
        for idx in range(1, num_frames - 1):
            if raw_active[idx - 1] and raw_active[idx + 1] and frame_rms[idx] >= effective_threshold * 0.5:
                active_mask[idx] = True

    active = np.flatnonzero(active_mask)
    if active.size < min_active_frames:
        return np.array([], dtype=np.float32), False

    pad_frames = max(0, int(pad_ms / frame_ms))
    start_frame = max(0, int(active[0]) - pad_frames)
    end_frame = min(num_frames, int(active[-1]) + 1 + pad_frames)

    start_idx = start_frame * frame_samples
    end_idx = min(arr.size, end_frame * frame_samples)
    return np.ascontiguousarray(arr[start_idx:end_idx], dtype=np.float32), True


def split_audio_chunks(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    max_chunk_s: float = 28.0,
) -> list[np.ndarray]:
    """
    Split long recordings into Whisper-friendly chunks (<= `max_chunk_s`),
    cutting at the quietest 200ms window in the trailing search band so words
    are not sliced mid-syllable.
    """
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return []

    max_samples = max(sample_rate, int(max_chunk_s * sample_rate))
    if arr.size <= max_samples:
        return [arr]

    min_search_s = min(18.0, max_chunk_s * 0.65)
    min_search_samples = int(min_search_s * sample_rate)
    win_samples = max(1, int(0.20 * sample_rate))

    chunks: list[np.ndarray] = []
    start = 0
    total = arr.size
    while start < total:
        remaining = total - start
        if remaining <= max_samples:
            chunks.append(np.ascontiguousarray(arr[start:], dtype=np.float32))
            break

        search_start = start + min_search_samples
        search_end = start + max_samples
        segment = arr[search_start:search_end]
        if segment.size <= win_samples:
            split_at = search_end
        else:
            step = max(1, win_samples // 4)
            best_idx = segment.size // 2
            best_energy = float("inf")
            for offset in range(0, segment.size - win_samples + 1, step):
                win = segment[offset : offset + win_samples]
                energy = float(np.mean(win * win))
                if energy < best_energy:
                    best_energy = energy
                    best_idx = offset + win_samples // 2
            split_at = search_start + best_idx

        chunks.append(np.ascontiguousarray(arr[start:split_at], dtype=np.float32))
        start = split_at

    return chunks


class _WinMMFallbackStream:
    """Minimal ctypes winmm.dll waveIn 16kHz mono fallback if PortAudio fails."""

    WAVE_MAPPER = 0xFFFFFFFF
    WAVE_FORMAT_PCM = 1
    WHDR_DONE = 0x00000001

    class _WAVEFORMATEX(ctypes.Structure):
        _fields_ = [
            ("wFormatTag", ctypes.c_uint16),
            ("nChannels", ctypes.c_uint16),
            ("nSamplesPerSec", ctypes.c_uint32),
            ("nAvgBytesPerSec", ctypes.c_uint32),
            ("nBlockAlign", ctypes.c_uint16),
            ("wBitsPerSample", ctypes.c_uint16),
            ("cbSize", ctypes.c_uint16),
        ]

    class _WAVEHDR(ctypes.Structure):
        _fields_ = [
            ("lpData", ctypes.c_void_p),
            ("dwBufferLength", ctypes.c_uint32),
            ("dwBytesRecorded", ctypes.c_uint32),
            ("dwUser", ctypes.c_size_t),
            ("dwFlags", ctypes.c_uint32),
            ("dwLoops", ctypes.c_uint32),
            ("lpNext", ctypes.c_void_p),
            ("reserved", ctypes.c_size_t),
        ]

    def __init__(self, on_samples: Callable[[np.ndarray], None]) -> None:
        self._on_samples = on_samples
        self._winmm = ctypes.windll.winmm
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._h_wave_in = ctypes.c_void_p()

    def start(self) -> None:
        wfx = self._WAVEFORMATEX(
            wFormatTag=self.WAVE_FORMAT_PCM,
            nChannels=1,
            nSamplesPerSec=SAMPLE_RATE,
            nAvgBytesPerSec=SAMPLE_RATE * 2,
            nBlockAlign=2,
            wBitsPerSample=16,
            cbSize=0,
        )
        res = self._winmm.waveInOpen(
            ctypes.byref(self._h_wave_in),
            self.WAVE_MAPPER,
            ctypes.byref(wfx),
            0,
            0,
            0,
        )
        if res != 0:
            raise RuntimeError(f"winmm waveInOpen failed with code {res}")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        buf_bytes = 2048  # 64 ms at 16 kHz 16-bit mono
        num_bufs = 4
        raw_bufs = [ctypes.create_string_buffer(buf_bytes) for _ in range(num_bufs)]
        headers = []
        for buf in raw_bufs:
            hdr = self._WAVEHDR(
                lpData=ctypes.cast(buf, ctypes.c_void_p),
                dwBufferLength=buf_bytes,
            )
            self._winmm.waveInPrepareHeader(self._h_wave_in, ctypes.byref(hdr), ctypes.sizeof(hdr))
            self._winmm.waveInAddBuffer(self._h_wave_in, ctypes.byref(hdr), ctypes.sizeof(hdr))
            headers.append(hdr)

        self._winmm.waveInStart(self._h_wave_in)
        try:
            idx = 0
            while not self._stop_event.is_set():
                hdr = headers[idx]
                if hdr.dwFlags & self.WHDR_DONE:
                    recorded = hdr.dwBytesRecorded
                    if recorded > 0:
                        raw = ctypes.string_at(hdr.lpData, recorded)
                        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                        self._on_samples(pcm)
                    hdr.dwFlags &= ~self.WHDR_DONE
                    hdr.dwBytesRecorded = 0
                    self._winmm.waveInAddBuffer(self._h_wave_in, ctypes.byref(hdr), ctypes.sizeof(hdr))
                    idx = (idx + 1) % num_bufs
                else:
                    time.sleep(0.01)
        finally:
            self._winmm.waveInStop(self._h_wave_in)
            self._winmm.waveInReset(self._h_wave_in)
            for hdr in headers:
                self._winmm.waveInUnprepareHeader(self._h_wave_in, ctypes.byref(hdr), ctypes.sizeof(hdr))
            self._winmm.waveInClose(self._h_wave_in)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None


class AudioRecorder:
    """Thread-safe 16 kHz mono audio recorder with smoothed RMS level callbacks."""

    def __init__(self, on_level: Callable[[float], None] | None = None) -> None:
        self._on_level = on_level
        self._lock = threading.RLock()
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._fallback_stream: _WinMMFallbackStream | None = None
        self._recording = False
        self._started_at = 0.0
        self._smoothed_level = 0.0
        self._last_level_emit = 0.0

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def elapsed_seconds(self) -> float:
        with self._lock:
            if not self._recording:
                return 0.0
            return max(0.0, time.monotonic() - self._started_at)

    def start(self, device: int | None = None) -> None:
        """Begin capturing 16 kHz mono float32 audio."""
        with self._lock:
            if self._recording:
                return
            self._chunks = []
            self._smoothed_level = 0.0
            self._last_level_emit = 0.0
            self._started_at = time.monotonic()

            if sd is not None:
                try:
                    stream = sd.InputStream(
                        samplerate=SAMPLE_RATE,
                        channels=1,
                        dtype="float32",
                        blocksize=512,
                        device=device,
                        callback=self._sd_callback,
                    )
                    stream.start()
                    self._stream = stream
                    self._recording = True
                    return
                except Exception as exc:
                    logger.warning("sounddevice InputStream failed (%s); falling back to winmm", exc)

            fallback = _WinMMFallbackStream(self._handle_audio_block)
            fallback.start()
            self._fallback_stream = fallback
            self._recording = True

    def _sd_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:  # noqa: ANN001
        if status:
            logger.debug("Audio stream status: %s", status)
        block = np.asarray(indata[:, 0], dtype=np.float32).copy()
        self._handle_audio_block(block)

    def _handle_audio_block(self, block: np.ndarray) -> None:
        if block.size == 0:
            return
        with self._lock:
            if not self._recording:
                return
            self._chunks.append(block)

        rms = float(np.sqrt(np.mean(block * block)))
        # Scale conversational speech RMS (~0.01..0.18) smoothly into 0.0..1.0.
        normalized = min(1.0, max(0.0, (rms - 0.004) / 0.11))
        self._smoothed_level = 0.65 * self._smoothed_level + 0.35 * normalized

        now = time.monotonic()
        if self._on_level is not None and (now - self._last_level_emit) >= 0.033:
            self._last_level_emit = now
            with contextlib.suppress(Exception):
                self._on_level(self._smoothed_level)

    def stop(self) -> tuple[np.ndarray, float]:
        """Stop capturing and return `(float32_audio, total_duration_seconds)`."""
        with self._lock:
            if not self._recording:
                return np.array([], dtype=np.float32), 0.0
            self._recording = False
            duration = max(0.0, time.monotonic() - self._started_at)
            stream = self._stream
            fallback = self._fallback_stream
            self._stream = None
            self._fallback_stream = None

        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                logger.warning("Error closing sounddevice stream: %s", exc)
        if fallback is not None:
            try:
                fallback.stop()
            except Exception as exc:
                logger.warning("Error closing winmm fallback stream: %s", exc)

        with self._lock:
            if not self._chunks:
                return np.array([], dtype=np.float32), duration
            merged = np.concatenate(self._chunks).astype(np.float32, copy=False)
            self._chunks = []
            return merged, duration

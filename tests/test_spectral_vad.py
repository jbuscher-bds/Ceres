from __future__ import annotations

import numpy as np

from ceres.system.audio import SAMPLE_RATE, trim_silence


def test_spectral_vad_rejects_low_frequency_fan_rumble() -> None:
    t = np.arange(SAMPLE_RATE * 2, dtype=np.float32) / SAMPLE_RATE
    # Strong 60 Hz hum + 90 Hz fan rumble well above RMS threshold, but outside 180-3800 Hz speech band
    rumble = 0.08 * np.sin(2.0 * np.pi * 60.0 * t) + 0.05 * np.sin(2.0 * np.pi * 90.0 * t)
    trimmed, has_speech = trim_silence(rumble.astype(np.float32))
    assert not has_speech
    assert trimmed.size == 0


def test_spectral_vad_accepts_speech_band_over_quiet_fan_hum() -> None:
    t = np.arange(SAMPLE_RATE * 2, dtype=np.float32) / SAMPLE_RATE
    audio = 0.015 * np.sin(2.0 * np.pi * 70.0 * t)
    # Add 0.8s speech-band 440 Hz + 1200 Hz harmonic signal in [0.6s .. 1.4s]
    s = int(0.6 * SAMPLE_RATE)
    e = int(1.4 * SAMPLE_RATE)
    audio[s:e] += 0.14 * np.sin(2.0 * np.pi * 440.0 * t[s:e]) + 0.07 * np.sin(2.0 * np.pi * 1200.0 * t[s:e])
    trimmed, has_speech = trim_silence(audio.astype(np.float32))
    assert has_speech
    assert trimmed.size >= int(0.7 * SAMPLE_RATE)

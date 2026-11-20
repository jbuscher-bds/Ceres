from __future__ import annotations

import numpy as np

from ceres.system.audio import SAMPLE_RATE, split_audio_chunks, trim_silence


def test_trim_silence_rejects_empty_and_quiet_audio() -> None:
    empty, ok_empty = trim_silence(np.array([], dtype=np.float32))
    assert not ok_empty
    assert empty.size == 0

    quiet = np.random.default_rng(42).normal(0.0, 0.001, size=SAMPLE_RATE * 2).astype(np.float32)
    trimmed, ok_quiet = trim_silence(quiet)
    assert not ok_quiet
    assert trimmed.size == 0


def test_trim_silence_rejects_short_desk_click() -> None:
    audio = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
    # Single 40ms transient spike (2 frames < 12 min_active_frames)
    audio[8000:8640] = 0.25
    trimmed, has_speech = trim_silence(audio)
    assert not has_speech
    assert trimmed.size == 0


def test_trim_silence_keeps_speech_with_padding() -> None:
    audio = np.zeros(SAMPLE_RATE * 4, dtype=np.float32)
    t = np.linspace(0, 1.0, SAMPLE_RATE, dtype=np.float32)
    # 1.0s of 220Hz tone in the middle [1.5s .. 2.5s]
    audio[int(1.5 * SAMPLE_RATE) : int(2.5 * SAMPLE_RATE)] = 0.15 * np.sin(2.0 * np.pi * 220.0 * t)
    trimmed, has_speech = trim_silence(audio)
    assert has_speech
    # Should trim significantly below 4s while retaining ~1.0s + 2*0.24s padding
    assert int(1.1 * SAMPLE_RATE) <= trimmed.size <= int(1.8 * SAMPLE_RATE)


def test_split_audio_chunks_prefers_quiet_gap() -> None:
    # 35 seconds total: loud speech everywhere except a silent pause at 22.0s..22.5s
    total = SAMPLE_RATE * 35
    t = np.arange(total, dtype=np.float32) / SAMPLE_RATE
    audio = 0.12 * np.sin(2.0 * np.pi * 300.0 * t)
    quiet_start = int(22.0 * SAMPLE_RATE)
    quiet_end = int(22.5 * SAMPLE_RATE)
    audio[quiet_start:quiet_end] = 0.0

    chunks = split_audio_chunks(audio, sample_rate=SAMPLE_RATE, max_chunk_s=28.0)
    assert len(chunks) == 2
    # Split point should fall inside the quiet 22.0s..22.5s window
    assert int(21.9 * SAMPLE_RATE) <= chunks[0].size <= int(22.6 * SAMPLE_RATE)
    assert chunks[0].size + chunks[1].size == total

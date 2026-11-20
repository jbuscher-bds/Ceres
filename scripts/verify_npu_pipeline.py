"""Live Intel AI Boost NPU end-to-end verification for Ceres."""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from ceres import MODELS_DIR, OV_CACHE_DIR, ensure_directories
from ceres.config import DEFAULT_LLM_MODEL, DEFAULT_WHISPER_MODEL
from ceres.engine.model_manager import download_model, get_device_info
from ceres.engine.rewriter import SentenceRewriter, build_rewrite_messages
from ceres.engine.transcriber import WhisperTranscriber


def _synthesize_speech_16k(text: str) -> np.ndarray | None:
    """Use Windows SAPI SpeechSynthesizer to generate 16kHz mono float32 WAV."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_path = Path(tf.name)
    try:
        escaped = text.replace("'", "''")
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$voices = $s.GetInstalledVoices() | Where-Object { $_.Enabled }; "
            "if ($voices.Count -eq 0) { throw 'No SAPI voices available' }; "
            "$en = $voices | Where-Object { $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'en' } | Select-Object -First 1; "
            "if ($en) { $s.SelectVoice($en.VoiceInfo.Name) } else { $s.SelectVoice($voices[0].VoiceInfo.Name) }; "
            "$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
            f"$s.SetOutputToWaveFile('{wav_path}', $fmt); "
            f"$s.Speak('{escaped}'); "
            "$s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
        with wave.open(str(wav_path), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception as exc:
        print(f"SpeechSynthesizer helper skipped ({exc})")
        return None
    finally:
        wav_path.unlink(missing_ok=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ensure_directories()

    devices = get_device_info()
    print("Detected OpenVINO devices:", devices)
    assert "NPU" in devices, "Intel AI Boost NPU must be detected!"

    print("\n--- Ensuring OpenVINO Models Are Downloaded ---")
    whisper_dir = download_model(DEFAULT_WHISPER_MODEL, MODELS_DIR)
    llm_dir = download_model(DEFAULT_LLM_MODEL, MODELS_DIR)

    print("\n--- Loading & Compiling LLMPipeline on NPU ---")
    t0 = time.perf_counter()
    llm = SentenceRewriter()
    llm_dev = llm.load(llm_dir, model_id=DEFAULT_LLM_MODEL, preferred_device="NPU", cache_dir=OV_CACHE_DIR)
    llm_cold_s = time.perf_counter() - t0
    print(f"LLM active device: {llm_dev} (load+compile+warmup: {llm_cold_s:.2f}s)")
    assert llm_dev == "NPU", f"Expected Qwen3-8B to run on NPU, got {llm_dev}"

    print("\n--- Loading & Compiling WhisperPipeline (NPU Probe -> NPU/GPU) ---")
    t1 = time.perf_counter()
    stt = WhisperTranscriber()
    stt_dev = stt.load(whisper_dir, preferred_device="NPU", cache_dir=OV_CACHE_DIR)
    stt_cold_s = time.perf_counter() - t1
    print(f"Whisper active device: {stt_dev} (load+compile+warmup: {stt_cold_s:.2f}s)")

    print("\n--- Verifying Pure-NPU Whisper Option (OpenVINO/whisper-base-int8-ov) ---")
    base_dir = download_model("OpenVINO/whisper-base-int8-ov", MODELS_DIR)
    stt_npu = WhisperTranscriber()
    stt_npu_dev = stt_npu.load(base_dir, preferred_device="NPU", cache_dir=OV_CACHE_DIR)
    print(f"whisper-base-int8-ov active device: {stt_npu_dev}")
    assert stt_npu_dev == "NPU"

    print("\n--- Verifying Qwen3 Non-Thinking Prompt Template ---")
    sample_prompt = llm._format_prompt(build_rewrite_messages("um hello", mode="fluent", language_hint="en"))
    print("Prompt suffix repr:", repr(sample_prompt[-80:]))

    print("\n--- End-to-End Speech Audio -> Whisper -> Qwen3-8B NPU ---")
    audio = _synthesize_speech_16k(
        "Um hello team, I think we should, wait no, let us move the meeting to Thursday at four o'clock."
    )
    if audio is not None and audio.size > 0:
        t_stt = time.perf_counter()
        raw_transcript, detected_lang = stt.transcribe(audio, language="auto")
        stt_ms = int((time.perf_counter() - t_stt) * 1000)

        t_llm = time.perf_counter()
        polished = llm.rewrite(raw_transcript, mode="fluent", language_hint=detected_lang or "en")
        llm_ms = int((time.perf_counter() - t_llm) * 1000)

        print(f"Audio duration : {audio.size / 16000:.2f}s")
        print(f"Detected lang  : {detected_lang}")
        print(f"Whisper raw    : {raw_transcript!r} ({stt_ms} ms)")
        print(f"LLM polished   : {polished!r} ({llm_ms} ms)")
        assert raw_transcript
        assert "thursday" in polished.lower()

    print("\n--- Multilingual Rewriter Verification (EN / NL / DE + Context + Vocabulary) ---")
    cases = [
        (
            "en_correction",
            "en",
            "fluent",
            "general",
            None,
            "Um so I was thinking like maybe we should uh push the release to Monday, wait no Tuesday morning, because yeah Friday is totally packed.",
            lambda out: (
                "tuesday" in out.lower() and "monday" not in out.lower() and "um" not in out.lower().split()
            ),
        ),
        (
            "en_question",
            "en",
            "fluent",
            "email",
            None,
            "Uh can you tell me if the client already signed the contract?",
            lambda out: "?" in out and "signed" in out.lower(),
        ),
        (
            "nl_correction",
            "nl",
            "fluent",
            "chat",
            None,
            "Eh ik wilde zeg maar vragen of we de vergadering om drie uur, nee wacht om vier uur kunnen inplannen, want dan is iedereen erbij.",
            lambda out: "vier" in out.lower() and "drie" not in out.lower() and "vergadering" in out.lower(),
        ),
        (
            "nl_light",
            "nl",
            "light",
            "general",
            None,
            "Euh stuur mij straks nog even het nieuwe document door alsjeblieft.",
            lambda out: (
                "document" in out.lower() and "alsjeblieft" in out.lower() and "euh" not in out.lower()
            ),
        ),
        (
            "de_correction",
            "de",
            "fluent",
            "general",
            None,
            "Ähm ich wollte nur kurz Bescheid sagen, dass wir halt morgen leider erst um neun, nein warte um zehn Uhr im Büro sind.",
            lambda out: (
                ("zehn" in out.lower() or "10" in out.lower())
                and "neun" not in out.lower()
                and "büro" in out.lower()
            ),
        ),
        (
            "de_question",
            "de",
            "fluent",
            "general",
            None,
            "Äh kannst du mir bitte sagen, wann der Bericht fertig ist?",
            lambda out: "bericht" in out.lower() and "report" not in out.lower() and "?" in out,
        ),
        (
            "code_vocab",
            "en",
            "fluent",
            "code",
            ["OpenVINO", "PySide6", "jbuscher-bds"],
            "Um please check the open vino pipeline and update pi side six config for jay buscher bds.",
            lambda out: "OpenVINO" in out and "PySide6" in out and "jbuscher-bds" in out,
        ),
    ]

    for name, lang, mode, ctx_cat, vocab, raw, check_fn in cases:
        t_start = time.perf_counter()
        out = llm.rewrite(raw, mode=mode, language_hint=lang, app_context=ctx_cat, vocabulary=vocab)
        ms = int((time.perf_counter() - t_start) * 1000)
        print(f"\n[{name} | {lang.upper()} | {mode} | ctx={ctx_cat} | {ms} ms]")
        print(f"  RAW: {raw}")
        print(f"  OUT: {out}")
        assert check_fn(out), f"Quality assertion failed for {name}: {out!r}"

    print("\n--- Voice Edit Mode Verification on NPU ---")
    t_edit = time.perf_counter()
    edited = llm.edit_text(
        "Kannst du mir das bis morgen schicken? Es ist wirklich dringend und wir brauchen alle Zahlen.",
        "mach das höflicher für eine Kunden E-Mail",
    )
    edit_ms = int((time.perf_counter() - t_edit) * 1000)
    print(f"[voice_edit_de | {edit_ms} ms] OUT: {edited}")
    assert "sie" in edited.lower() or "ihnen" in edited.lower() or "könnten" in edited.lower(), (
        f"Expected formal German edit output, got {edited!r}"
    )

    print("\nALL NPU VERIFICATION CHECKS PASSED!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

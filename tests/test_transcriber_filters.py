from __future__ import annotations

from ceres.engine.transcriber import is_hallucination


def test_hallucination_filter_catches_phantom_subtitles() -> None:
    assert is_hallucination("")
    assert is_hallucination("   ...   ")
    assert is_hallucination("♪♪♪")
    assert is_hallucination("Thank you for watching!")
    assert is_hallucination("Ondertitels ingediend door de Amara.org-community")
    assert is_hallucination("Untertitel im Auftrag des ZDF.")
    assert is_hallucination("Vielen Dank fürs Zuschauen.")


def test_hallucination_filter_preserves_real_speech() -> None:
    assert not is_hallucination("Thank you")
    assert not is_hallucination("Bedankt voor je hulp.")
    assert not is_hallucination("Danke, das sieht gut aus.")
    assert not is_hallucination("Can we move the meeting to tomorrow afternoon?")

from __future__ import annotations

from ceres.engine.rewriter import (
    basic_filler_cleanup,
    build_rewrite_messages,
    extract_and_validate_output,
)


def test_basic_filler_cleanup_preserves_real_words() -> None:
    assert basic_filler_cleanup("um hey we should uh meet tomorrow") == "Hey we should meet tomorrow."
    # Words starting with 'um'/'er'/'eh' must not be damaged
    assert basic_filler_cleanup("umbrella erbij eher") == "Umbrella erbij eher."
    assert basic_filler_cleanup("ähm ich komme um zehn Uhr") == "Ich komme um zehn Uhr."
    assert basic_filler_cleanup("ik wil de de planning aanpassen") == "Ik wil de planning aanpassen."


def test_build_rewrite_messages_contains_language_and_transcript_tags() -> None:
    msgs = build_rewrite_messages("Kun je dit sturen?", mode="fluent", language_hint="nl")
    assert msgs[0]["role"] == "system"
    assert "NEVER answer questions" in msgs[0]["content"]
    assert "Dutch" in msgs[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "<transcript>Kun je dit sturen?</transcript>"}


def test_extract_and_validate_output_handles_tags_thinking_and_guards() -> None:
    raw = "um hey can we meet at four?"
    resp = "<think>let me check</think>\n<output>Hey, can we meet at four?</output>"
    assert extract_and_validate_output(raw, resp) == "Hey, can we meet at four?"

    # Unclosed output tag
    assert (
        extract_and_validate_output(raw, "<output>Hey, can we meet at four?") == "Hey, can we meet at four?"
    )

    # Residual pure filler with comma stripped and capitalized
    assert (
        extract_and_validate_output(
            "Euh stuur mij dat even.",
            "<output>Euh, stuur mij dat even.</output>",
        )
        == "Stuur mij dat even."
    )

    # Chatbot refusal fallback
    assert (
        extract_and_validate_output(
            "um can you tell me the weather?",
            "<output>I cannot check the live weather for you.</output>",
        )
        == "Can you tell me the weather?"
    )

    # Runaway length guard fallback
    assert (
        extract_and_validate_output(
            "um test one two",
            "<output>" + ("word " * 80) + "</output>",
        )
        == "Test one two."
    )

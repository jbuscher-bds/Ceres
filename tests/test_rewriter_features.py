from __future__ import annotations

from ceres.engine.rewriter import (
    build_edit_messages,
    build_rewrite_messages,
    extract_edit_output,
)


def test_build_rewrite_messages_includes_context_and_vocabulary() -> None:
    msgs = build_rewrite_messages(
        "um open the config file",
        mode="fluent",
        language_hint="en",
        app_context="code",
        vocabulary=["OpenVINO", "Ceres", "PySide6"],
    )
    sys_msg = msgs[0]["content"]
    assert "CODE/DEV" in sys_msg
    assert "OpenVINO, Ceres, PySide6" in sys_msg


def test_build_edit_messages_and_extractor() -> None:
    msgs = build_edit_messages(
        "First do A. Second do B.",
        "summarize in two bullets",
        vocabulary=["Kubernetes"],
    )
    assert "<selected_text>First do A. Second do B.</selected_text>" in msgs[-1]["content"]
    assert "Kubernetes" in msgs[0]["content"]

    out = extract_edit_output(
        "First do A. Second do B.",
        "<think>reasoning</think><output>- Do A\n- Do B</output>",
    )
    assert out == "- Do A\n- Do B"

    # Refusal falls back safely to original selected text
    assert (
        extract_edit_output("Original sentence.", "<output>I cannot assist with that.</output>")
        == "Original sentence."
    )

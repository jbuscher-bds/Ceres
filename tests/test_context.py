from __future__ import annotations

from ceres.system.context import classify_window


def test_classify_window_by_process_name() -> None:
    assert classify_window("OUTLOOK.EXE", "Inbox - Work") == "email"
    assert classify_window("ms-teams.exe", "Daily Standup") == "chat"
    assert classify_window("Code.exe", "pipeline.py - Ceres") == "code"
    assert classify_window("WINWORD.EXE", "Proposal.docx") == "document"


def test_classify_window_browser_titles_and_fallback() -> None:
    assert classify_window("msedge.exe", "Gmail - Compose") == "email"
    assert classify_window("chrome.exe", "Pull Request #42 - GitHub") == "code"
    assert classify_window("firefox.exe", "WhatsApp Web") == "chat"
    assert classify_window("chrome.exe", "Project Notes - Google Docs") == "document"
    assert classify_window("notepad.exe", "Untitled - Notepad") == "general"

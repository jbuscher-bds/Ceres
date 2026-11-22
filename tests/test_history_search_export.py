from __future__ import annotations

import sqlite3
from pathlib import Path

from ceres.history import HistoryStore, export_markdown


def test_history_migrates_v1_schema_searches_and_exports(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_history.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                language TEXT NOT NULL,
                rewrite_mode TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                polished_text TEXT NOT NULL,
                audio_seconds REAL NOT NULL,
                stt_ms INTEGER NOT NULL,
                llm_ms INTEGER NOT NULL,
                device_summary TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO history (
                created_at, language, rewrite_mode, raw_text, polished_text,
                audio_seconds, stt_ms, llm_ms, device_summary
            ) VALUES ('2026-11-18T12:00:00', 'de', 'fluent', 'äh hallo', 'Hallo.', 1.0, 100, 200, 'GPU/NPU')
            """
        )
        conn.commit()

    store = HistoryStore(db_path)
    migrated = store.list_recent()
    assert len(migrated) == 1
    assert migrated[0].action_type == "dictate"

    store.add_entry(
        language="nl",
        rewrite_mode="edit",
        raw_text="maak dit korter",
        polished_text="Korte samenvatting.",
        audio_seconds=2.0,
        stt_ms=150,
        llm_ms=400,
        device_summary="GPU/NPU",
        action_type="edit",
    )

    hits = store.search("samenvatting")
    assert len(hits) == 1
    assert hits[0].action_type == "edit"

    md_path = export_markdown(store.list_recent(), tmp_path / "export.md")
    md_text = md_path.read_text(encoding="utf-8")
    assert "# Ceres Dictation History" in md_text
    assert "Korte samenvatting." in md_text
    assert "`EDIT`" in md_text

    # Backwards compatibility: existing v2 DB that still contains `app_context TEXT NOT NULL DEFAULT 'general'`
    v2_db = tmp_path / "v2_history.db"
    with sqlite3.connect(v2_db) as conn:
        conn.execute(
            """
            CREATE TABLE history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                language TEXT NOT NULL,
                rewrite_mode TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                polished_text TEXT NOT NULL,
                audio_seconds REAL NOT NULL,
                stt_ms INTEGER NOT NULL,
                llm_ms INTEGER NOT NULL,
                device_summary TEXT NOT NULL,
                app_context TEXT NOT NULL DEFAULT 'general',
                action_type TEXT NOT NULL DEFAULT 'dictate'
            )
            """
        )
        conn.commit()
    v2_store = HistoryStore(v2_db)
    inserted = v2_store.add_entry(
        language="de",
        rewrite_mode="fluent",
        raw_text="hallo welt",
        polished_text="Hallo Welt.",
        audio_seconds=1.2,
        stt_ms=110,
        llm_ms=190,
        device_summary="GPU/NPU",
    )
    assert inserted.polished_text == "Hallo Welt."
    assert len(v2_store.list_recent()) == 1

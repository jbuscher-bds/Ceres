"""SQLite persistence, search, and Markdown export for dictation history."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ceres import HISTORY_DB_PATH


@dataclass(frozen=True)
class HistoryEntry:
    """Single saved dictation or voice-edit entry."""

    id: int
    created_at: str
    language: str
    rewrite_mode: str
    raw_text: str
    polished_text: str
    audio_seconds: float
    stt_ms: int
    llm_ms: int
    device_summary: str
    action_type: str = "dictate"


class HistoryStore:
    """Thread-safe SQLite store for dictation transcripts, voice edits, and latencies."""

    def __init__(self, db_path: Path = HISTORY_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
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
                    action_type TEXT NOT NULL DEFAULT 'dictate'
                )
                """
            )
            cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(history)").fetchall()}
            if "action_type" not in cols:
                conn.execute("ALTER TABLE history ADD COLUMN action_type TEXT NOT NULL DEFAULT 'dictate'")
            conn.commit()

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> HistoryEntry:
        return HistoryEntry(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            language=str(row["language"]),
            rewrite_mode=str(row["rewrite_mode"]),
            raw_text=str(row["raw_text"]),
            polished_text=str(row["polished_text"]),
            audio_seconds=float(row["audio_seconds"]),
            stt_ms=int(row["stt_ms"]),
            llm_ms=int(row["llm_ms"]),
            device_summary=str(row["device_summary"]),
            action_type=str(row["action_type"] or "dictate"),
        )

    def add_entry(
        self,
        *,
        language: str,
        rewrite_mode: str,
        raw_text: str,
        polished_text: str,
        audio_seconds: float,
        stt_ms: int,
        llm_ms: int,
        device_summary: str,
        action_type: str = "dictate",
        created_at: str | None = None,
    ) -> HistoryEntry:
        """Insert a dictation record and return the persisted entry."""
        timestamp = created_at or datetime.now(UTC).astimezone().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO history (
                    created_at, language, rewrite_mode, raw_text, polished_text,
                    audio_seconds, stt_ms, llm_ms, device_summary, action_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    language,
                    rewrite_mode,
                    raw_text,
                    polished_text,
                    float(audio_seconds),
                    int(stt_ms),
                    int(llm_ms),
                    device_summary,
                    action_type,
                ),
            )
            conn.commit()
            assert cur.lastrowid is not None
            entry_id = int(cur.lastrowid)
        return HistoryEntry(
            id=entry_id,
            created_at=timestamp,
            language=language,
            rewrite_mode=rewrite_mode,
            raw_text=raw_text,
            polished_text=polished_text,
            audio_seconds=float(audio_seconds),
            stt_ms=int(stt_ms),
            llm_ms=int(llm_ms),
            device_summary=device_summary,
            action_type=action_type,
        )

    def list_recent(self, limit: int = 50) -> list[HistoryEntry]:
        """Return newest dictation entries first."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, language, rewrite_mode, raw_text, polished_text,
                       audio_seconds, stt_ms, llm_ms, device_summary, action_type
                FROM history
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def search(self, query: str, limit: int = 50) -> list[HistoryEntry]:
        """Filter history entries matching `query` across text, language, or action."""
        needle = query.strip()
        if not needle:
            return self.list_recent(limit=limit)
        pattern = f"%{needle}%"
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, language, rewrite_mode, raw_text, polished_text,
                       audio_seconds, stt_ms, llm_ms, device_summary, action_type
                FROM history
                WHERE polished_text LIKE ?
                   OR raw_text LIKE ?
                   OR language LIKE ?
                   OR action_type LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, max(1, int(limit))),
            ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def delete_entry(self, entry_id: int) -> None:
        """Delete a single entry by ID."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM history WHERE id = ?", (int(entry_id),))
            conn.commit()

    def clear_all(self) -> None:
        """Remove all dictation history records."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM history")
            conn.commit()


def export_markdown(entries: list[HistoryEntry], out_path: Path) -> Path:
    """Export history entries to a clean UTF-8 Markdown file."""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Ceres Dictation History", ""]
    if not entries:
        lines.append("_No dictation entries recorded._")
    else:
        for entry in entries:
            ts = entry.created_at.replace("T", " ")[:19]
            lines.append(
                f"## {ts} — `{entry.action_type.upper()}` · `{entry.language.upper()}` · `{entry.rewrite_mode}`"
            )
            lines.append("")
            lines.append(entry.polished_text)
            if entry.raw_text.strip() and entry.raw_text.strip() != entry.polished_text.strip():
                lines.append("")
                lines.append(f"> Raw speech: {entry.raw_text}")
            lines.append("")
            lines.append(
                f"*Audio: {entry.audio_seconds:.1f}s · STT: {entry.stt_ms} ms · LLM: {entry.llm_ms} ms ({entry.device_summary})*"
            )
            lines.append("")
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target

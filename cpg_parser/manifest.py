from __future__ import annotations

import sqlite3
from pathlib import Path


class Manifest:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS file_state (
                file_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS elements (
                file_id TEXT NOT NULL,
                element_type TEXT NOT NULL CHECK (element_type IN ('node', 'edge')),
                element_id TEXT NOT NULL,
                PRIMARY KEY (file_id, element_type, element_id)
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Manifest":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def content_hash(self, file_identifier: str) -> str | None:
        row = self.connection.execute(
            "SELECT content_hash FROM file_state WHERE file_id = ?", (file_identifier,)
        ).fetchone()
        return row[0] if row else None

    def previous_elements(self, file_identifier: str, element_type: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT element_id FROM elements WHERE file_id = ? AND element_type = ?",
            (file_identifier, element_type),
        )
        return {row[0] for row in rows}

    def stale_elements(
        self,
        file_identifier: str,
        node_ids: set[str],
        edge_ids: set[str],
    ) -> tuple[set[str], set[str]]:
        return (
            self.previous_elements(file_identifier, "node") - node_ids,
            self.previous_elements(file_identifier, "edge") - edge_ids,
        )

    def replace_file(
        self,
        file_identifier: str,
        content_hash: str,
        updated_at: str,
        node_ids: set[str],
        edge_ids: set[str],
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO file_state(file_id, content_hash, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(file_id) DO UPDATE SET content_hash=excluded.content_hash, updated_at=excluded.updated_at",
                (file_identifier, content_hash, updated_at),
            )
            self.connection.execute("DELETE FROM elements WHERE file_id = ?", (file_identifier,))
            self.connection.executemany(
                "INSERT INTO elements(file_id, element_type, element_id) VALUES (?, 'node', ?)",
                ((file_identifier, identifier) for identifier in sorted(node_ids)),
            )
            self.connection.executemany(
                "INSERT INTO elements(file_id, element_type, element_id) VALUES (?, 'edge', ?)",
                ((file_identifier, identifier) for identifier in sorted(edge_ids)),
            )


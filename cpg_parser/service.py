from __future__ import annotations

import hashlib
import os
import traceback
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analyzer import CPGAnalyzer
from .constants import SCHEMA_VERSION, TOPIC_EDGES, TOPIC_ERRORS, TOPIC_METADATA, TOPIC_NODES
from .discovery import discover_files, git_metadata
from .ids import file_id, stable_hash
from .manifest import Manifest
from .publisher import Publisher, Record


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class FileProcessResult:
    path: str
    file_id: str
    content_hash: str
    status: str
    nodes: int = 0
    edges: int = 0
    deleted_nodes: int = 0
    deleted_edges: int = 0
    warnings: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ParserService:
    def __init__(
        self,
        repo_path: Path,
        repo_id: str,
        publisher: Publisher,
        manifest: Manifest,
    ) -> None:
        self.repo_path = repo_path.resolve()
        self.repo_id = repo_id
        self.publisher = publisher
        self.manifest = manifest
        self.repo_url, self.commit_sha = git_metadata(self.repo_path)

    def process(self, relative_file: str | None = None, force: bool = False) -> list[FileProcessResult]:
        if relative_file:
            candidate = (self.repo_path / relative_file).resolve()
            try:
                candidate.relative_to(self.repo_path)
            except ValueError as exc:
                raise ValueError("--file must stay inside --repo") from exc
            if not candidate.is_file() or candidate.suffix != ".py":
                raise ValueError(f"Python file does not exist: {relative_file}")
            files = [candidate]
        else:
            files, _ = discover_files(self.repo_path)
        return [self._process_file(path, force=force) for path in files]

    def _process_file(self, path: Path, force: bool) -> FileProcessResult:
        relative = path.relative_to(self.repo_path).as_posix()
        identifier = file_id(self.repo_id, relative)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if self.manifest.content_hash(identifier) == digest and not force:
            return FileProcessResult(relative, identifier, digest, "skipped")
        event_time = utc_now()
        run_id = uuid.uuid4().hex
        common = {
            "schema_version": SCHEMA_VERSION,
            "event_time": event_time,
            "repo_id": self.repo_id,
            "file_id": identifier,
            "run_id": run_id,
            "content_hash": digest,
        }
        try:
            source = raw.decode("utf-8")
            analysis = CPGAnalyzer(source, identifier, relative).analyze()
        except Exception as exc:
            return self._publish_error(path, relative, identifier, digest, raw, common, exc)

        node_ids = {node.id for node in analysis.nodes}
        edge_ids = {edge.id for edge in analysis.edges}
        stale_nodes, stale_edges = self.manifest.stale_elements(identifier, node_ids, edge_ids)
        records: list[Record] = []
        for stale_id in sorted(stale_edges):
            value = {**common, "op": "delete", "edge": {"id": stale_id}}
            value["event_id"] = stable_hash("edge", "delete", stale_id, digest)
            records.append((TOPIC_EDGES, stale_id, value))
        for stale_id in sorted(stale_nodes):
            value = {**common, "op": "delete", "node": {"id": stale_id}}
            value["event_id"] = stable_hash("node", "delete", stale_id, digest)
            records.append((TOPIC_NODES, stale_id, value))
        for node in analysis.nodes:
            value = {**common, "op": "upsert", "node": node.as_dict()}
            value["event_id"] = stable_hash("node", "upsert", node.id, digest)
            records.append((TOPIC_NODES, node.id, value))
        for edge in analysis.edges:
            value = {**common, "op": "upsert", "edge": edge.as_dict()}
            value["event_id"] = stable_hash("edge", "upsert", edge.id, digest)
            records.append((TOPIC_EDGES, edge.id, value))
        metadata = self._metadata(
            relative,
            identifier,
            digest,
            raw,
            common,
            "ok",
            analysis.node_counts(),
            analysis.edge_counts(),
            analysis.warnings,
        )
        records.append((TOPIC_METADATA, identifier, metadata))
        self.publisher.publish_transaction(records)
        self.manifest.replace_file(identifier, digest, event_time, node_ids, edge_ids)
        return FileProcessResult(
            relative,
            identifier,
            digest,
            "processed",
            nodes=len(node_ids),
            edges=len(edge_ids),
            deleted_nodes=len(stale_nodes),
            deleted_edges=len(stale_edges),
            warnings=len(analysis.warnings),
        )

    def _publish_error(
        self,
        path: Path,
        relative: str,
        identifier: str,
        digest: str,
        raw: bytes,
        common: dict[str, Any],
        exc: Exception,
    ) -> FileProcessResult:
        line = getattr(exc, "lineno", 0) or 0
        column = getattr(exc, "offset", 0) or 0
        error_identifier = stable_hash(identifier, digest, type(exc).__name__, line, column)
        error = {
            **common,
            "op": "error",
            "event_id": error_identifier,
            "error": {
                "id": error_identifier,
                "stage": "parse",
                "exception_type": type(exc).__name__,
                "message": str(exc)[:1000],
                "line": line,
                "column": column,
                "recoverable": True,
                "traceback": "".join(traceback.format_exception_only(type(exc), exc)).strip()[:2000],
            },
        }
        metadata = self._metadata(relative, identifier, digest, raw, common, "error", {}, {}, [], str(exc))
        self.publisher.publish_transaction(
            [(TOPIC_ERRORS, error_identifier, error), (TOPIC_METADATA, identifier, metadata)]
        )
        return FileProcessResult(relative, identifier, digest, "error", error=str(exc))

    def _metadata(
        self,
        relative: str,
        identifier: str,
        digest: str,
        raw: bytes,
        common: dict[str, Any],
        status: str,
        node_counts: dict[str, int],
        edge_counts: dict[str, int],
        warnings: list[str],
        error_message: str = "",
    ) -> dict[str, Any]:
        text = raw.decode("utf-8", errors="replace")
        metadata = {
            "_id": identifier,
            "repo_id": self.repo_id,
            "repo_url": self.repo_url,
            "path": relative,
            "content_hash": digest,
            "size_bytes": len(raw),
            "line_count": len(text.splitlines()),
            "commit_sha": self.commit_sha,
            "parse_status": status,
            "node_counts": node_counts,
            "edge_counts": edge_counts,
            "warnings": warnings,
            "error_message": error_message[:1000],
            "processed_at": common["event_time"],
            "run_id": common["run_id"],
            "schema_version": SCHEMA_VERSION,
        }
        value = {**common, "op": "upsert", "metadata": metadata}
        value["event_id"] = stable_hash("metadata", identifier, digest, status)
        return value


def transactional_id(repo_id: str) -> str:
    return f"lab04-{stable_hash(repo_id)[:12]}-{os.getpid()}"


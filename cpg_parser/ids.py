from __future__ import annotations

import hashlib
from pathlib import Path


def stable_hash(*parts: object) -> str:
    encoded = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_relative_path(repo_path: Path, file_path: Path) -> str:
    return file_path.resolve().relative_to(repo_path.resolve()).as_posix()


def file_id(repo_id: str, relative_path: str) -> str:
    return stable_hash(repo_id, relative_path)


def node_id(file_identifier: str, structural_path: str, node_type: str) -> str:
    return stable_hash(file_identifier, structural_path, node_type)


def edge_id(kind: str, source_id: str, target_id: str, discriminator: str = "") -> str:
    return stable_hash(kind, source_id, target_id, discriminator)


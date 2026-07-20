from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GraphNode:
    id: str
    kind: str
    ast_type: str
    file_id: str
    structural_path: str
    line: int = 0
    column: int = 0
    end_line: int = 0
    end_column: int = 0
    name: str = ""
    value: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "ast_type": self.ast_type,
            "file_id": self.file_id,
            "structural_path": self.structural_path,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "name": self.name,
            "value": self.value,
        }


@dataclass(slots=True)
class GraphEdge:
    id: str
    kind: str
    source_id: str
    target_id: str
    file_id: str
    discriminator: str = ""
    variable: str = ""
    resolved: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "file_id": self.file_id,
            "discriminator": self.discriminator,
            "variable": self.variable,
            "resolved": self.resolved,
        }


@dataclass(slots=True)
class AnalysisResult:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def node_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in self.nodes:
            counts[node.kind] = counts.get(node.kind, 0) + 1
        return counts

    def edge_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for edge in self.edges:
            counts[edge.kind] = counts.get(edge.kind, 0) + 1
        return counts


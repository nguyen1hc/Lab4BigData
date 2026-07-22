"""Validate that committed notebooks are genuinely executed and evidence-backed."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "book"
EVIDENCE = ROOT / "evidence" / "runtime" / "verification.json"
EXPECTED = [
    "architecture.ipynb",
    "task1_repository.ipynb",
    "task2_parser.ipynb",
    "task3_kafka.ipynb",
    "task4_neo4j.ipynb",
    "task5_mongodb.ipynb",
    "task6_replay.ipynb",
]


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n" or len(header) < 24:
        raise ValueError(f"Not a valid PNG: {path}")
    return struct.unpack(">II", header[16:24])


def main() -> int:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    if evidence.get("schema_version") != "2.0":
        raise AssertionError("Evidence schema is stale; run capture_replay_evidence.py")
    assertions = evidence.get("assertions", {})
    if not assertions or not all(assertions.values()):
        raise AssertionError("Replay evidence is missing or contains failed assertions")
    canonical = json.dumps(
        evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()

    failures: list[str] = []
    for name in EXPECTED:
        path = BOOK / name
        if not path.is_file():
            failures.append(f"{name}: missing")
            continue
        nb = json.loads(path.read_text(encoding="utf-8"))
        provenance = nb.get("metadata", {}).get("lab04_execution", {})
        if provenance.get("engine") != "nbclient":
            failures.append(f"{name}: missing nbclient provenance")
        if provenance.get("evidence_sha256") != digest:
            failures.append(f"{name}: evidence hash does not match verification.json")
        markdown = "\n".join(
            "".join(cell.get("source", []))
            for cell in nb.get("cells", [])
            if cell.get("cell_type") == "markdown"
        )
        if "## Reflection" not in markdown:
            failures.append(f"{name}: missing Reflection section")
        code_cells = [cell for cell in nb.get("cells", []) if cell.get("cell_type") == "code"]
        if not code_cells:
            failures.append(f"{name}: no code cells")
        for index, cell in enumerate(code_cells, start=1):
            if cell.get("execution_count") is None:
                failures.append(f"{name} cell {index}: not executed")
            outputs = cell.get("outputs", [])
            if not outputs:
                failures.append(f"{name} cell {index}: no output")
            if any(output.get("output_type") == "error" for output in outputs):
                failures.append(f"{name} cell {index}: error output")
            serialized = json.dumps(outputs)
            if "COMMAND EXITED" in serialized or "Traceback (most recent call last)" in serialized:
                failures.append(f"{name} cell {index}: failed-command text in output")
            if index == len(code_cells) and "PASS:" not in serialized:
                failures.append(f"{name}: final code cell has no PASS assertion")

    for filename in ("neo4j-browser.png", "mongodb-ui.png"):
        path = BOOK / "figures" / filename
        try:
            width, height = png_size(path)
            if width < 800 or height < 500:
                failures.append(f"{filename}: screenshot is too small ({width}x{height})")
        except (OSError, ValueError) as exc:
            failures.append(str(exc))

    if failures:
        raise AssertionError("Notebook validation failed:\n- " + "\n- ".join(failures))
    print(
        json.dumps(
            {
                "notebooks": len(EXPECTED),
                "evidence_sha256": digest,
                "replay_assertions": len(assertions),
                "status": "PASS",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

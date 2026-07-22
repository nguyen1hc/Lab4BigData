"""Run the complete replay scenario and atomically capture verifiable evidence.

The script temporarily restores the selected source file to its locked Git
version, publishes that baseline, reapplies the existing replay modification,
and verifies modified, forced-unchanged, and Spark-restart replays.  The original
working-tree bytes are restored in a ``finally`` block and also copied to a
recovery backup before any source edit is made.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = ROOT / "source-repo"
DEFAULT_STATE_DB = ROOT / "state" / "optimum.sqlite"
DEFAULT_EVIDENCE = ROOT / "evidence" / "runtime" / "verification.json"
REPO_ID = "huggingface/optimum"
REPLAY_FILE = "optimum/version.py"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def run(command: list[str], *, timeout: int = 120, input_text: str | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode:
        detail = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{detail}")
    return result.stdout.strip()


def run_bytes(command: list[str], *, timeout: int = 30) -> bytes:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=timeout)
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{detail}")
    return result.stdout


def run_json(command: list[str], *, timeout: int = 120) -> dict[str, Any]:
    return json.loads(run(command, timeout=timeout))


def docker(*arguments: str, timeout: int = 120, input_text: str | None = None) -> str:
    return run(["docker", "compose", *arguments], timeout=timeout, input_text=input_text)


def require_stack() -> None:
    required = {"broker", "connect", "mongo", "neo4j", "spark-metadata"}
    running = set(docker("ps", "--status", "running", "--services").splitlines())
    missing = sorted(required - running)
    if missing:
        raise RuntimeError(f"Required Docker services are not running: {', '.join(missing)}")

    status = connector_status()
    if status.get("connector", {}).get("state") != "RUNNING":
        raise RuntimeError(f"Neo4j connector is not RUNNING: {status}")
    tasks = status.get("tasks", [])
    if not tasks or any(task.get("state") != "RUNNING" for task in tasks):
        raise RuntimeError(f"Neo4j connector task is not RUNNING: {status}")


def connector_status() -> dict[str, Any]:
    output = docker(
        "exec",
        "-T",
        "connect",
        "curl",
        "-fsS",
        "http://localhost:8083/connectors/cpg-neo4j-sink/status",
        timeout=30,
    )
    return json.loads(output)


def kafka_end_offset(topic: str) -> int:
    output = docker(
        "exec",
        "-T",
        "broker",
        "kafka-get-offsets",
        "--bootstrap-server",
        "broker:29092",
        "--topic",
        topic,
        timeout=60,
    )
    offsets = [int(line.rsplit(":", 1)[1]) for line in output.splitlines() if ":" in line]
    return sum(offsets)


def checkpoint_offset() -> int:
    command = (
        "latest=$(find /opt/checkpoints/source-metadata-v1/offsets -maxdepth 1 "
        "-type f ! -name '.*' -printf '%f\\n' | sort -n | tail -1); "
        "test -n \"$latest\"; tail -1 /opt/checkpoints/source-metadata-v1/offsets/$latest"
    )
    output = docker("exec", "-T", "spark-metadata", "bash", "-lc", command, timeout=30)
    payload = json.loads(output.splitlines()[-1])
    return int(payload["cpg.source-metadata.v1"]["0"])


def neo4j_password() -> str:
    return docker("exec", "-T", "neo4j", "printenv", "LAB04_NEO4J_PASSWORD", timeout=30)


def neo4j_csv(query: str) -> list[dict[str, str]]:
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "neo4j",
        "cypher-shell",
        "--format",
        "plain",
        "-u",
        "neo4j",
        "-p",
        neo4j_password(),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        input=query,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode:
        detail = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(f"cypher-shell failed ({result.returncode}); credentials redacted\n{detail}")
    output = result.stdout.strip()
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"Unexpected cypher-shell output: {output}")
    return list(csv.DictReader(lines, skipinitialspace=True))


def neo4j_snapshot(file_identifier: str) -> dict[str, Any]:
    counts_query = f"""
MATCH (n:CPGNode)
WITH count(n) AS nodes, count(DISTINCT n.id) AS unique_nodes
MATCH ()-[r:CPG_EDGE]->()
WITH nodes, unique_nodes, count(r) AS edges, count(DISTINCT r.id) AS unique_edges
OPTIONAL MATCH (fn:CPGNode {{file_id: '{file_identifier}'}})
WITH nodes, unique_nodes, edges, unique_edges, count(fn) AS file_nodes
OPTIONAL MATCH ()-[fr:CPG_EDGE {{file_id: '{file_identifier}'}}]->()
RETURN nodes, unique_nodes, edges, unique_edges, file_nodes, count(fr) AS file_edges
""".strip()
    row = neo4j_csv(counts_query)[0]
    kinds_rows = neo4j_csv(
        "MATCH ()-[r:CPG_EDGE]->() RETURN r.kind AS kind, count(*) AS count ORDER BY kind"
    )
    return {
        "nodes": int(row["nodes"]),
        "unique_nodes": int(row["unique_nodes"]),
        "edges": int(row["edges"]),
        "unique_edges": int(row["unique_edges"]),
        "file_nodes": int(row["file_nodes"]),
        "file_edges": int(row["file_edges"]),
        "edge_kinds": {item["kind"].strip('"'): int(item["count"]) for item in kinds_rows},
    }


def mongo_snapshot(file_identifier: str) -> dict[str, Any]:
    script = f"""
const d = db.getSiblingDB('lab04');
const filter = {{repo_id: '{REPO_ID}'}};
const doc = d.source_metadata.findOne({{_id: '{file_identifier}'}});
const others = d.source_metadata.find(
  {{repo_id: '{REPO_ID}', _id: {{$ne: '{file_identifier}'}}}},
  {{_id: 1, content_hash: 1, kafka_offset: 1}}
).sort({{_id: 1}}).toArray().map(x => ({{
  _id: x._id,
  content_hash: x.content_hash,
  kafka_offset: Number(x.kafka_offset)
}}));
print(JSON.stringify({{
  documents: d.source_metadata.countDocuments(filter),
  distinct_files: d.source_metadata.distinct('_id', filter).length,
  document: doc ? {{
    _id: doc._id,
    path: doc.path,
    content_hash: doc.content_hash,
    node_counts: doc.node_counts,
    edge_counts: doc.edge_counts,
    processed_at: doc.processed_at,
    run_id: doc.run_id,
    kafka_offset: Number(doc.kafka_offset)
  }} : null,
  other_documents: others
}}));
""".strip()
    output = docker("exec", "-T", "mongo", "mongosh", "--quiet", "--eval", script, timeout=60)
    payload = json.loads(output.splitlines()[-1])
    other_documents = payload.pop("other_documents")
    payload["other_documents_count"] = len(other_documents)
    payload["other_documents_digest"] = stable_json_hash(other_documents)
    return payload


def parse_file(repo: Path, state_db: Path, *, force: bool) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "cpg_parser",
        "parse",
        "--repo",
        str(repo),
        "--repo-id",
        REPO_ID,
        "--file",
        REPLAY_FILE,
        "--state-db",
        str(state_db),
        "--bootstrap-servers",
        "127.0.0.1:9092",
    ]
    if force:
        command.append("--force")
    return run_json(command, timeout=300)


def wait_for_stage(
    file_identifier: str,
    expected_hash: str,
    expected_nodes: int,
    expected_edges: int,
    previous_mongo_offset: int,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    deadline = time.monotonic() + timeout_seconds
    last_detail = "no observation"
    while time.monotonic() < deadline:
        try:
            neo4j = neo4j_snapshot(file_identifier)
            mongo = mongo_snapshot(file_identifier)
            checkpoint = checkpoint_offset()
            document = mongo.get("document") or {}
            ready = (
                neo4j["file_nodes"] == expected_nodes
                and neo4j["file_edges"] == expected_edges
                and document.get("content_hash") == expected_hash
                and int(document.get("kafka_offset", -1)) > previous_mongo_offset
                and checkpoint > int(document.get("kafka_offset", -1))
            )
            if ready:
                return neo4j, mongo, checkpoint
            last_detail = json.dumps(
                {
                    "file_nodes": neo4j["file_nodes"],
                    "file_edges": neo4j["file_edges"],
                    "mongo_hash": document.get("content_hash"),
                    "mongo_offset": document.get("kafka_offset"),
                    "checkpoint": checkpoint,
                },
                sort_keys=True,
            )
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            last_detail = str(exc)
        time.sleep(3)
    raise TimeoutError(f"Timed out waiting for sinks: {last_detail}")


def capture_stage(
    name: str,
    repo: Path,
    target: Path,
    source_bytes: bytes,
    state_db: Path,
    previous_mongo_offset: int,
    timeout_seconds: int,
    *,
    force: bool,
) -> dict[str, Any]:
    target.write_bytes(source_bytes)
    expected_hash = sha256_bytes(source_bytes)
    parser_summary = parse_file(repo, state_db, force=force)
    result = parser_summary["files"][0]
    if result["status"] != "processed":
        raise RuntimeError(f"Stage {name} was not processed: {parser_summary}")
    neo4j, mongo, checkpoint = wait_for_stage(
        result["file_id"],
        expected_hash,
        int(result["nodes"]),
        int(result["edges"]),
        previous_mongo_offset,
        timeout_seconds,
    )
    return {
        "name": name,
        "captured_at": utc_now(),
        "source_hash": expected_hash,
        "source_lines": len(source_bytes.decode("utf-8", errors="replace").splitlines()),
        "parser": result,
        "neo4j": neo4j,
        "mongo": mongo,
        "kafka_metadata_end_offset": kafka_end_offset("cpg.source-metadata.v1"),
        "spark_checkpoint_offset": checkpoint,
    }


def validate_evidence(evidence: dict[str, Any]) -> dict[str, bool]:
    stages = evidence["stages"]
    baseline = stages["baseline"]
    modified = stages["modified"]
    unchanged = stages["forced_unchanged"]
    restarted = stages["restart_replay"]
    all_stages = [baseline, modified, unchanged, restarted]
    expected_files = evidence["repository"]["processed_python_files"]
    assertions = {
        "only_requested_file_processed": all(
            stage["parser"]["path"] == REPLAY_FILE for stage in all_stages
        ),
        "source_hash_changed": baseline["source_hash"] != modified["source_hash"],
        "mongo_hash_matches_source": all(
            stage["mongo"]["document"]["content_hash"] == stage["source_hash"]
            for stage in all_stages
        ),
        "neo4j_counts_match_parser": all(
            stage["neo4j"]["file_nodes"] == stage["parser"]["nodes"]
            and stage["neo4j"]["file_edges"] == stage["parser"]["edges"]
            for stage in all_stages
        ),
        "modified_graph_changed": (
            baseline["neo4j"]["file_nodes"], baseline["neo4j"]["file_edges"]
        )
        != (modified["neo4j"]["file_nodes"], modified["neo4j"]["file_edges"]),
        "modified_emitted_stale_deletes": (
            modified["parser"]["deleted_nodes"] + modified["parser"]["deleted_edges"] > 0
        ),
        "forced_replay_has_no_stale_elements": (
            unchanged["parser"]["deleted_nodes"] == 0
            and unchanged["parser"]["deleted_edges"] == 0
        ),
        "unchanged_replay_keeps_graph_counts": unchanged["neo4j"] == modified["neo4j"],
        "restart_replay_keeps_graph_counts": restarted["neo4j"] == modified["neo4j"],
        "neo4j_ids_are_unique": all(
            stage["neo4j"]["nodes"] == stage["neo4j"]["unique_nodes"]
            and stage["neo4j"]["edges"] == stage["neo4j"]["unique_edges"]
            for stage in all_stages
        ),
        "mongo_has_one_document_per_file": all(
            stage["mongo"]["documents"] == stage["mongo"]["distinct_files"] == expected_files
            for stage in all_stages
        ),
        "unchanged_files_were_not_rewritten": len(
            {stage["mongo"]["other_documents_digest"] for stage in all_stages}
        )
        == 1,
        "mongo_offsets_advance": all(
            left["mongo"]["document"]["kafka_offset"]
            < right["mongo"]["document"]["kafka_offset"]
            for left, right in zip(all_stages, all_stages[1:])
        ),
        "spark_consumed_each_stage": all(
            stage["spark_checkpoint_offset"] == stage["kafka_metadata_end_offset"]
            for stage in all_stages
        ),
        "checkpoint_resumed_after_restart": (
            evidence["spark_restart"]["checkpoint_before_restart"]
            < restarted["spark_checkpoint_offset"]
        ),
        "neo4j_dlq_is_empty": evidence["neo4j_dlq_end_offset"] == 0,
    }
    return assertions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--state-db", type=Path, default=DEFAULT_STATE_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--timeout", type=int, default=240, help="Seconds to wait for each sink stage")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    target = (repo / REPLAY_FILE).resolve()
    try:
        target.relative_to(repo)
    except ValueError as exc:
        raise RuntimeError("Replay target escaped the source repository") from exc
    if not target.is_file():
        raise RuntimeError(f"Replay target does not exist: {target}")

    require_stack()
    original_bytes = target.read_bytes()
    baseline_bytes = run_bytes(["git", "-C", str(repo), "show", f"HEAD:{REPLAY_FILE}"])
    if original_bytes == baseline_bytes:
        raise RuntimeError(
            f"{REPLAY_FILE} has no replay modification. Add the demo function before capturing evidence."
        )
    diff = run(["git", "-C", str(repo), "diff", "--", REPLAY_FILE])
    if not diff:
        raise RuntimeError("Replay source differs from HEAD but git diff is empty")

    backup_dir = ROOT / "tmp" / "replay-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"version-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py.bak"
    backup_path.write_bytes(original_bytes)

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    file_identifier = sha256_file_id(REPO_ID, REPLAY_FILE)
    discovery = run_json([sys.executable, "-m", "cpg_parser", "discover", "--repo", str(repo)])
    initial_mongo = mongo_snapshot(file_identifier)
    initial_offset = int((initial_mongo.get("document") or {}).get("kafka_offset", -1))

    try:
        # Synchronize the manifest and both databases to the current modified
        # working tree. This makes the following baseline transition reliable
        # even if the local SQLite manifest was recreated.
        sync_summary = parse_file(repo, args.state_db.resolve(), force=True)
        sync_result = sync_summary["files"][0]
        _, synced_mongo, _ = wait_for_stage(
            file_identifier,
            sha256_bytes(original_bytes),
            int(sync_result["nodes"]),
            int(sync_result["edges"]),
            initial_offset,
            args.timeout,
        )
        if synced_mongo["documents"] != discovery["processed_python_files"]:
            raise RuntimeError(
                "MongoDB does not contain one document for every processed source file. "
                "Run scripts/parse-optimum.ps1 and wait for Spark before capturing replay evidence."
            )
        previous_offset = int(synced_mongo["document"]["kafka_offset"])

        baseline = capture_stage(
            "baseline",
            repo,
            target,
            baseline_bytes,
            args.state_db.resolve(),
            previous_offset,
            args.timeout,
            force=True,
        )
        modified = capture_stage(
            "modified",
            repo,
            target,
            original_bytes,
            args.state_db.resolve(),
            baseline["mongo"]["document"]["kafka_offset"],
            args.timeout,
            force=False,
        )
        unchanged = capture_stage(
            "forced_unchanged",
            repo,
            target,
            original_bytes,
            args.state_db.resolve(),
            modified["mongo"]["document"]["kafka_offset"],
            args.timeout,
            force=True,
        )

        checkpoint_before_restart = unchanged["spark_checkpoint_offset"]
        docker("restart", "spark-metadata", timeout=120)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            running = set(docker("ps", "--status", "running", "--services").splitlines())
            if "spark-metadata" in running:
                break
            time.sleep(2)
        else:
            raise TimeoutError("spark-metadata did not return to RUNNING after restart")

        restarted = capture_stage(
            "restart_replay",
            repo,
            target,
            original_bytes,
            args.state_db.resolve(),
            unchanged["mongo"]["document"]["kafka_offset"],
            args.timeout,
            force=True,
        )
        evidence: dict[str, Any] = {
            "schema_version": "2.0",
            "captured_at": utc_now(),
            "repository": {
                "repo_id": REPO_ID,
                "url": discovery["repo_url"],
                "commit_sha": discovery["commit_sha"],
                "raw_python_files": discovery["raw_python_files"],
                "processed_python_files": discovery["processed_python_files"],
                "baseline_python_lines": discovery["total_lines"]
                - (restarted["source_lines"] - baseline["source_lines"]),
                "modified_python_lines": discovery["total_lines"],
                "parseable_files": discovery["parseable_files"],
                "parse_success_rate": discovery["parse_success_rate"],
            },
            "replay_file": {
                "path": REPLAY_FILE,
                "file_id": restarted["parser"]["file_id"],
                "git_diff": diff,
                "backup_path": str(backup_path.relative_to(ROOT)),
            },
            "connector_status": connector_status(),
            "preflight_sync": {
                "parser": sync_result,
                "mongo_offset": previous_offset,
            },
            "stages": {
                "baseline": baseline,
                "modified": modified,
                "forced_unchanged": unchanged,
                "restart_replay": restarted,
            },
            "spark_restart": {
                "checkpoint_before_restart": checkpoint_before_restart,
                "checkpoint_after_replay": restarted["spark_checkpoint_offset"],
            },
            "neo4j_dlq_end_offset": kafka_end_offset("cpg.neo4j-dlq.v1"),
        }
        evidence["assertions"] = validate_evidence(evidence)
        failed = [name for name, passed in evidence["assertions"].items() if not passed]
        if failed:
            raise AssertionError(f"Replay evidence assertions failed: {', '.join(failed)}")
        try:
            import jsonschema
        except ImportError as exc:
            raise RuntimeError(
                "Evidence validation requires: python -m pip install -e .[test]"
            ) from exc
        schema = json.loads(
            (ROOT / "schemas" / "replay-evidence.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.validate(evidence, schema)

        temporary_output = output.with_suffix(output.suffix + ".tmp")
        temporary_output.write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        temporary_output.replace(output)
        print(json.dumps({"evidence": str(output), "assertions": evidence["assertions"]}, indent=2))
        return 0
    finally:
        target.write_bytes(original_bytes)


def sha256_file_id(repo_id: str, relative_path: str) -> str:
    encoded = "\x1f".join((repo_id, relative_path)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())

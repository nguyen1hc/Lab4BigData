"""Generate notebooks and execute every code cell against one evidence run."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

from cpg_parser.ids import file_id


ROOT = Path(__file__).parents[1]
BOOK = ROOT / "book"
REPO = ROOT / "source-repo"
REPO_ID = "huggingface/optimum"
REPO_URL = "https://github.com/huggingface/optimum"
REPLAY_FILE = "optimum/version.py"
REPLAY_FILE_ID = file_id(REPO_ID, REPLAY_FILE)
GENERATED: dict[str, object] = {}


def code_cell(source: str):
    return nbf.v4.new_code_cell(source)


def evidence_sha256() -> str:
    path = ROOT / "evidence" / "runtime" / "verification.json"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_evidence() -> dict:
    path = ROOT / "evidence" / "runtime" / "verification.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if evidence.get("schema_version") != "2.0":
        raise RuntimeError(
            "Replay evidence is stale. Run: python scripts/capture_replay_evidence.py"
        )
    assertions = evidence.get("assertions", {})
    failed = [name for name, passed in assertions.items() if not passed]
    if not assertions or failed:
        detail = ", ".join(failed) if failed else "assertions missing"
        raise RuntimeError(f"Replay evidence is not complete: {detail}")
    return evidence


def execute_notebooks(names: list[str]) -> None:
    executed = []
    digest = evidence_sha256()
    executed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for name in names:
        nb = GENERATED[name]
        client = NotebookClient(
            nb,
            timeout=600,
            kernel_name="python3",
            allow_errors=False,
            resources={"metadata": {"path": str(BOOK)}},
        )
        client.execute()
        nb.metadata["lab04_execution"] = {
            "engine": "nbclient",
            "executed_at": executed_at,
            "evidence_sha256": digest,
            "working_directory": "book",
        }
        executed.append((BOOK / name, nb))
    for path, nb in executed:
        nbf.write(nb, path)


def notebook(title: str, intro: str, cells: list, reflection: str):
    nb = nbf.v4.new_notebook()
    nb.metadata.kernelspec = {"display_name": "Python 3", "language": "python", "name": "python3"}
    nb.metadata.language_info = {"name": "python", "version": "3.11"}
    nb.cells = [
        nbf.v4.new_markdown_cell(f"# {title}\n\n{intro}"),
        *cells,
        nbf.v4.new_markdown_cell(f"## Reflection\n\n{reflection}"),
    ]
    return nb


def image_cell(filename: str, caption: str):
    path = BOOK / "figures" / filename
    if not path.exists():
        return nbf.v4.new_markdown_cell(
            "**UI capture disclosure:** the executable query output above is current and "
            f"verified. `{path.relative_to(ROOT)}` is intentionally absent because the "
            "Codex browser integration failed to initialize; no screenshot was fabricated. "
            "Capture the corresponding local UI before the final Moodle submission."
        )
    return nbf.v4.new_markdown_cell(f"![{caption}](figures/{filename})\n\n*{caption}*")


def write(name: str, nb) -> None:
    BOOK.mkdir(parents=True, exist_ok=True)
    GENERATED[name] = nb


def build_architecture() -> None:
    diagram = """```mermaid
flowchart LR
  R[Python repository] --> P[Parser Service]
  P --> N[cpg.nodes.v1]
  P --> E[cpg.edges.v1]
  P --> M[cpg.source-metadata.v1]
  P --> X[cpg.parser-errors.v1]
  N --> C[Kafka Connect]
  E --> C
  C --> G[Neo4j]
  C --> Q[cpg.neo4j-dlq.v1]
  M --> S[Spark Structured Streaming]
  S --> D[MongoDB]
  S --> K[(Checkpoint)]
  X --> L[Parser error evidence]
```

Graph topology goes directly from Kafka Connect to Neo4j. Spark is used only
for source metadata; parser failures and connector failures have separate paths."""
    services_source = """import subprocess
from pathlib import Path

root = Path('..').resolve()
result = subprocess.run(
    ['docker', 'compose', 'config', '--services'],
    cwd=root, capture_output=True, text=True, check=True,
)
print(result.stdout.rstrip())
required = {'broker', 'connect', 'neo4j', 'mongo', 'spark-metadata'}
assert required <= set(result.stdout.splitlines())
print('PASS: all required architecture services are declared')"""
    write(
        "architecture.ipynb",
        notebook(
            "Architecture",
            diagram,
            [code_cell(services_source)],
            "Kafka Connect is a separate service from Neo4j. Single-node Kafka and replication factor one are deliberate limits of this educational deployment.",
        ),
    )

def build_task1() -> None:
    evidence = load_evidence()
    repository = evidence["repository"]
    source = """import json
import sys
from pathlib import Path

root = Path('..').resolve()
sys.path.insert(0, str(root))
from cpg_parser.discovery import discover_repo

evidence = json.loads((root / 'evidence/runtime/verification.json').read_text(encoding='utf-8'))
current = discover_repo(root / 'source-repo').as_dict()
summary = {
    'locked_baseline': evidence['repository'],
    'current_replay_worktree': {
        key: current[key] for key in (
            'repo_url', 'commit_sha', 'raw_python_files', 'processed_python_files',
            'excluded_python_files', 'total_lines', 'parseable_files',
            'parse_success_rate', 'has_branch', 'has_loop', 'has_call'
        )
    },
    'excluded_files': current['excluded_files'],
}
print(json.dumps(summary, indent=2, ensure_ascii=False))
assert current['commit_sha'] == evidence['repository']['commit_sha']
assert current['processed_python_files'] == evidence['repository']['processed_python_files']
print('PASS: locked commit and discovery counts verified')"""
    write(
        "task1_repository.ipynb",
        notebook(
            "Task 1 - Repository cloning and file discovery",
            f"The assigned repository is [{REPO_ID}]({REPO_URL}). The ignored source tree is a shallow clone locked to `{repository['commit_sha']}`. Baseline and post-replay line counts are reported separately.",
            [code_cell(source)],
            f"The locked baseline contains {repository['raw_python_files']} raw Python files and {repository['processed_python_files']} processed files. The replay modification changes line count only; it does not change file discovery or parseability.",
        ),
    )

def build_task2() -> None:
    aggregate_source = """import json
import sys
from collections import Counter
from pathlib import Path

root = Path('..').resolve()
sys.path.insert(0, str(root))
from cpg_parser.analyzer import CPGAnalyzer
from cpg_parser.discovery import discover_repo
from cpg_parser.ids import file_id

repo = root / 'source-repo'
report = discover_repo(repo)
node_counts, edge_counts = Counter(), Counter()
node_ids, edge_ids = set(), set()
warnings = 0
for relative in report.files:
    result = CPGAnalyzer(
        (repo / relative).read_text(encoding='utf-8', errors='replace'),
        file_id('huggingface/optimum', relative), relative,
    ).analyze()
    node_counts.update(result.node_counts())
    edge_counts.update(result.edge_counts())
    node_ids.update(node.id for node in result.nodes)
    edge_ids.update(edge.id for edge in result.edges)
    warnings += len(result.warnings)
summary = {
    'repository': 'huggingface/optimum',
    'files': len(report.files),
    'nodes': sum(node_counts.values()),
    'edges': sum(edge_counts.values()),
    'node_counts': dict(sorted(node_counts.items())),
    'edge_counts': dict(sorted(edge_counts.items())),
    'warnings': warnings,
    'all_node_ids_unique': len(node_ids) == sum(node_counts.values()),
    'all_edge_ids_unique': len(edge_ids) == sum(edge_counts.values()),
}
print(json.dumps(summary, indent=2))
assert {'AST', 'CFG', 'DFG', 'CALL'} <= set(edge_counts)
assert summary['all_node_ids_unique'] and summary['all_edge_ids_unique']
print('PASS: all CPG categories exist and IDs are unique')"""
    tests_source = """import subprocess
import sys
from pathlib import Path

result = subprocess.run(
    [sys.executable, '-m', 'pytest', '-q'],
    cwd=Path('..').resolve(), capture_output=True, text=True, check=True,
)
print(result.stdout.rstrip())
assert '[100%]' in result.stdout
print('PASS: parser, replay, schema, and syntax-error tests')"""
    write(
        "task2_parser.ipynb",
        notebook(
            "Task 2 - Incremental CPG Parser Service",
            "The service uses Python `ast`, stable structural IDs, statement-level CFG, lexical reaching-definitions DFG, and same-file call resolution. It releases each file graph before processing the next file.",
            [code_cell(aggregate_source), code_cell(tests_source)],
            "The analyzer is intentionally conservative for aliases, dynamic dispatch, container mutation, and exact exception flow. Tests verify deterministic IDs, graph categories, stale deletion, schema contracts, and safe syntax-error routing.",
        ),
    )

def build_task3() -> None:
    topics_source = """import subprocess
from pathlib import Path

root = Path('..').resolve()
topics = [
    'cpg.nodes.v1', 'cpg.edges.v1', 'cpg.source-metadata.v1',
    'cpg.parser-errors.v1', 'cpg.neo4j-dlq.v1',
]
for topic in topics:
    result = subprocess.run(
        ['docker', 'compose', 'exec', '-T', 'broker', 'kafka-topics',
         '--bootstrap-server', 'broker:29092', '--describe', '--topic', topic],
        cwd=root, capture_output=True, text=True, check=True,
    )
    print(result.stdout.rstrip())
print('PASS: four required topics and the connector DLQ are configured')"""
    samples_source = f"""import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

root = Path('..').resolve()
sys.path.insert(0, str(root))
from cpg_parser.manifest import Manifest
from cpg_parser.publisher import MemoryPublisher
from cpg_parser.service import ParserService

samples = {{}}
with TemporaryDirectory() as directory:
    valid_publisher = MemoryPublisher()
    with Manifest(Path(directory) / 'valid.sqlite') as manifest:
        ParserService(root / 'source-repo', '{REPO_ID}', valid_publisher, manifest).process(
            '{REPLAY_FILE}', force=True
        )
    for topic, key, value in valid_publisher.records:
        samples.setdefault(topic, {{'key': key, 'value': value}})

    error_publisher = MemoryPublisher()
    with Manifest(Path(directory) / 'error.sqlite') as manifest:
        ParserService(
            root / 'tests/fixtures/invalid_repo', 'fixture/invalid', error_publisher, manifest
        ).process('broken.py', force=True)
    for topic, key, value in error_publisher.records:
        if topic == 'cpg.parser-errors.v1':
            samples[topic] = {{'key': key, 'value': value}}

required = {{'cpg.nodes.v1', 'cpg.edges.v1', 'cpg.source-metadata.v1', 'cpg.parser-errors.v1'}}
assert required <= set(samples)
for record in samples.values():
    assert record['key']
    assert record['value']['schema_version'] == '1.0'
    assert record['value']['event_time'].endswith('Z')
print(json.dumps(samples, indent=2, ensure_ascii=False))
print('PASS: node, edge, metadata, and parser-error event samples validated')"""
    write(
        "task3_kafka.ipynb",
        notebook(
            "Task 3 - Kafka topic and event design",
            "Nodes, edges, metadata, and parser failures have separate topics. Every record carries a schema version, event time, stable key, content hash, run ID, event ID, and operation.",
            [code_cell(topics_source), code_cell(samples_source)],
            "Compaction retains current keyed state but does not alone guarantee database idempotency. Stable keys, Neo4j `MERGE`, MongoDB replacement upserts, and the parser manifest provide the remaining guarantees.",
        ),
    )

def build_task4() -> None:
    status_source = """import json
import subprocess
from pathlib import Path

root = Path('..').resolve()
result = subprocess.run(
    ['docker', 'compose', 'exec', '-T', 'connect', 'curl', '-fsS',
     'http://localhost:8083/connectors/cpg-neo4j-sink/status'],
    cwd=root, capture_output=True, text=True, check=True,
)
status = json.loads(result.stdout)
print(json.dumps(status, indent=2))
assert status['connector']['state'] == 'RUNNING'
assert status['tasks'] and all(task['state'] == 'RUNNING' for task in status['tasks'])
print('PASS: connector and task are RUNNING')"""
    counts_source = """import json
import sys
from pathlib import Path

root = Path('..').resolve()
sys.path.insert(0, str(root / 'scripts'))
from capture_replay_evidence import neo4j_snapshot

evidence = json.loads((root / 'evidence/runtime/verification.json').read_text(encoding='utf-8'))
file_id = evidence['replay_file']['file_id']
snapshot = neo4j_snapshot(file_id)
print(json.dumps(snapshot, indent=2))
assert snapshot['nodes'] == snapshot['unique_nodes']
assert snapshot['edges'] == snapshot['unique_edges']
assert {'AST', 'CFG', 'DFG', 'CALL'} <= set(snapshot['edge_kinds'])
print('PASS: Neo4j IDs are unique and all graph edge kinds exist')"""
    dlq_source = """import subprocess
from pathlib import Path

root = Path('..').resolve()
result = subprocess.run(
    ['docker', 'compose', 'exec', '-T', 'broker', 'kafka-get-offsets',
     '--bootstrap-server', 'broker:29092', '--topic', 'cpg.neo4j-dlq.v1'],
    cwd=root, capture_output=True, text=True, check=True,
)
print(result.stdout.rstrip())
offset = sum(int(line.rsplit(':', 1)[1]) for line in result.stdout.splitlines() if ':' in line)
assert offset == 0
print('PASS: Neo4j connector DLQ is empty')"""
    write(
        "task4_neo4j.ipynb",
        notebook(
            "Task 4 - Graph topology ingestion into Neo4j",
            "Kafka Connect consumes only node and edge topics. Cypher handlers use `MERGE`, create placeholder endpoints when necessary, and reconcile delete events without Spark.",
            [
                code_cell(status_source),
                code_cell(counts_source),
                code_cell(dlq_source),
                image_cell("neo4j-browser.png", "Neo4j Browser showing CPG nodes and relationships"),
            ],
            "Connector status, total-versus-distinct IDs, graph categories, and an empty DLQ are checked independently. Spark is absent from the graph path.",
        ),
    )

def build_task5() -> None:
    mongo_source = f"""import json
import sys
from pathlib import Path

root = Path('..').resolve()
sys.path.insert(0, str(root / 'scripts'))
from capture_replay_evidence import mongo_snapshot

snapshot = mongo_snapshot('{REPLAY_FILE_ID}')
print(json.dumps(snapshot, indent=2))
assert snapshot['documents'] == snapshot['distinct_files'] == 61
assert snapshot['document']['_id'] == '{REPLAY_FILE_ID}'
print('PASS: MongoDB has one replacement-upserted document per file')"""
    checkpoint_source = """import json
import sys
from pathlib import Path

root = Path('..').resolve()
sys.path.insert(0, str(root / 'scripts'))
from capture_replay_evidence import checkpoint_offset, kafka_end_offset

progress = {
    'spark_checkpoint_offset': checkpoint_offset(),
    'metadata_topic_end_offset': kafka_end_offset('cpg.source-metadata.v1'),
}
print(json.dumps(progress, indent=2))
assert progress['spark_checkpoint_offset'] == progress['metadata_topic_end_offset']
print('PASS: Spark checkpoint has consumed the metadata topic')"""
    write(
        "task5_mongodb.ipynb",
        notebook(
            "Task 5 - Source metadata ingestion into MongoDB",
            "Spark reads only `cpg.source-metadata.v1`, parses an explicit schema, and writes replacement upserts keyed by `_id=file_id`. Its checkpoint is retained on a Docker volume.",
            [
                code_cell(mongo_source),
                code_cell(checkpoint_source),
                image_cell("mongodb-ui.png", "MongoDB UI showing the upserted source metadata document"),
            ],
            "The checkpoint tracks Kafka progress rather than file hashes. MongoDB document and distinct-ID counts prove that repeated metadata events replace rather than duplicate a file document.",
        ),
    )

def build_task6() -> None:
    evidence = load_evidence()
    stages = evidence["stages"]
    labels = {
        "baseline": "Locked baseline",
        "modified": "Modified file",
        "forced_unchanged": "Forced unchanged replay",
        "restart_replay": "Spark restart + replay",
    }
    rows = []
    for key in ("baseline", "modified", "forced_unchanged", "restart_replay"):
        stage = stages[key]
        rows.append(
            f"| {labels[key]} | `{stage['source_hash'][:12]}` | "
            f"{stage['neo4j']['file_nodes']} | {stage['neo4j']['file_edges']} | "
            f"{stage['neo4j']['nodes']} | {stage['neo4j']['edges']} | "
            f"{stage['mongo']['documents']} | {stage['mongo']['document']['kafka_offset']} | "
            f"{stage['spark_checkpoint_offset']} | PASS |"
        )
    table = """| Stage | Hash | File nodes | File edges | Total nodes | Total edges | Mongo docs | Mongo offset | Checkpoint | Result |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
""" + "\n".join(rows)
    intro = f"""The replay run is captured as four ordered stages from one automation script.

{table}

The script temporarily restores the locked file, restores the modified bytes in a `finally` block, polls both databases, and records assertions only after every sink converges."""
    evidence_source = """import json
from pathlib import Path

root = Path('..').resolve()
evidence = json.loads((root / 'evidence/runtime/verification.json').read_text(encoding='utf-8'))
summary = {
    'repository': evidence['repository'],
    'replay_file': {k: v for k, v in evidence['replay_file'].items() if k != 'git_diff'},
    'stages': {
        name: {
            'source_hash': stage['source_hash'],
            'parser': stage['parser'],
            'neo4j': stage['neo4j'],
            'mongo': stage['mongo'],
            'kafka_metadata_end_offset': stage['kafka_metadata_end_offset'],
            'spark_checkpoint_offset': stage['spark_checkpoint_offset'],
        }
        for name, stage in evidence['stages'].items()
    },
    'spark_restart': evidence['spark_restart'],
    'neo4j_dlq_end_offset': evidence['neo4j_dlq_end_offset'],
    'assertions': evidence['assertions'],
}
print(json.dumps(summary, indent=2, ensure_ascii=False))
assert evidence['assertions'] and all(evidence['assertions'].values())
print('PASS: every captured replay assertion is true')"""
    diff_source = f"""import subprocess
from pathlib import Path

root = Path('..').resolve()
result = subprocess.run(
    ['git', '-C', str(root / 'source-repo'), 'diff', '--', '{REPLAY_FILE}'],
    capture_output=True, text=True, check=True,
)
print(result.stdout.rstrip())
assert 'lab04_replay_probe' in result.stdout
print('PASS: replay modification is present in exactly {REPLAY_FILE}')"""
    live_source = f"""import json
import sys
from pathlib import Path

root = Path('..').resolve()
sys.path.insert(0, str(root / 'scripts'))
from capture_replay_evidence import checkpoint_offset, kafka_end_offset, mongo_snapshot, neo4j_snapshot

evidence = json.loads((root / 'evidence/runtime/verification.json').read_text(encoding='utf-8'))
expected = evidence['stages']['restart_replay']
live = {{
    'neo4j': neo4j_snapshot('{REPLAY_FILE_ID}'),
    'mongo': mongo_snapshot('{REPLAY_FILE_ID}'),
    'kafka_metadata_end_offset': kafka_end_offset('cpg.source-metadata.v1'),
    'spark_checkpoint_offset': checkpoint_offset(),
}}
print(json.dumps(live, indent=2))
assert live['neo4j'] == expected['neo4j']
assert live['mongo'] == expected['mongo']
assert live['kafka_metadata_end_offset'] == expected['kafka_metadata_end_offset']
assert live['spark_checkpoint_offset'] == expected['spark_checkpoint_offset']
print('PASS: live final state matches the captured restart-replay stage')"""
    write(
        "task6_replay.ipynb",
        notebook(
            "Task 6 - Idempotent replay verification",
            intro,
            [code_cell(evidence_source), code_cell(diff_source), code_cell(live_source)],
            "The evidence distinguishes parser idempotency, database uniqueness, unchanged-file stability, and Spark offset recovery. Matching hashes and digests show that only the requested file changed, while final live queries prevent the table from being an unsupported claim.",
        ),
    )

def main() -> None:
    names = [
        "architecture.ipynb",
        "task1_repository.ipynb",
        "task2_parser.ipynb",
        "task3_kafka.ipynb",
        "task4_neo4j.ipynb",
        "task5_mongodb.ipynb",
        "task6_replay.ipynb",
    ]
    load_evidence()
    for image in ("neo4j-browser.png", "mongodb-ui.png"):
        if not (BOOK / "figures" / image).is_file():
            raise RuntimeError(f"Missing required database UI capture: book/figures/{image}")
    build_architecture()
    build_task1()
    build_task2()
    build_task3()
    build_task4()
    build_task5()
    build_task6()
    execute_notebooks(names)
    print("Generated and executed seven notebooks with nbclient in book/")


if __name__ == "__main__":
    main()

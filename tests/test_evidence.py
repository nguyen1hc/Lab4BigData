import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from capture_replay_evidence import sha256_file_id, validate_evidence  # noqa: E402
from cpg_parser.ids import file_id  # noqa: E402


def stage(name, source_hash, nodes, edges, offset, checkpoint):
    return {
        "name": name,
        "captured_at": "2026-07-22T00:00:00Z",
        "source_hash": source_hash,
        "source_lines": 10,
        "parser": {
            "path": "optimum/version.py",
            "nodes": 7 if name == "baseline" else 29,
            "edges": 6 if name == "baseline" else 36,
            "deleted_nodes": 0,
            "deleted_edges": 0,
        },
        "neo4j": {
            "nodes": nodes,
            "unique_nodes": nodes,
            "edges": edges,
            "unique_edges": edges,
            "file_nodes": 7 if name == "baseline" else 29,
            "file_edges": 6 if name == "baseline" else 36,
            "edge_kinds": {"AST": 1, "CFG": 1, "DFG": 1, "CALL": 1},
        },
        "mongo": {
            "documents": 61,
            "distinct_files": 61,
            "other_documents_digest": "same",
            "document": {"kafka_offset": offset, "content_hash": source_hash},
        },
        "spark_checkpoint_offset": checkpoint,
        "kafka_metadata_end_offset": checkpoint,
    }


def test_capture_file_id_matches_parser_contract():
    assert sha256_file_id("huggingface/optimum", "optimum/version.py") == file_id(
        "huggingface/optimum", "optimum/version.py"
    )


def test_complete_replay_evidence_assertions_pass():
    baseline = stage("baseline", "a" * 64, 100, 150, 10, 12)
    modified = stage("modified", "b" * 64, 122, 180, 12, 14)
    modified["parser"]["deleted_edges"] = 1
    unchanged = deepcopy(modified)
    unchanged["name"] = "forced_unchanged"
    unchanged["parser"]["deleted_edges"] = 0
    unchanged["mongo"]["document"]["kafka_offset"] = 14
    unchanged["spark_checkpoint_offset"] = 16
    unchanged["kafka_metadata_end_offset"] = 16
    restarted = deepcopy(modified)
    restarted["name"] = "restart_replay"
    restarted["parser"]["deleted_edges"] = 0
    restarted["mongo"]["document"]["kafka_offset"] = 16
    restarted["spark_checkpoint_offset"] = 18
    restarted["kafka_metadata_end_offset"] = 18
    evidence = {
        "repository": {"processed_python_files": 61},
        "stages": {
            "baseline": baseline,
            "modified": modified,
            "forced_unchanged": unchanged,
            "restart_replay": restarted,
        },
        "spark_restart": {"checkpoint_before_restart": 16},
        "neo4j_dlq_end_offset": 0,
    }
    assertions = validate_evidence(evidence)
    assert assertions
    assert all(assertions.values())

    import json
    import jsonschema

    contract = deepcopy(evidence)
    contract.update(
        {
            "schema_version": "2.0",
            "captured_at": "2026-07-22T00:00:00Z",
            "replay_file": {
                "path": "optimum/version.py",
                "file_id": "f" * 64,
                "git_diff": "diff --git a/optimum/version.py b/optimum/version.py",
            },
            "connector_status": {},
            "assertions": assertions,
        }
    )
    contract["repository"].update(
        {
            "repo_id": "huggingface/optimum",
            "url": "https://github.com/huggingface/optimum.git",
            "commit_sha": "c" * 40,
            "raw_python_files": 74,
            "baseline_python_lines": 13807,
            "modified_python_lines": 13814,
            "parseable_files": 61,
            "parse_success_rate": 1.0,
        }
    )
    contract["spark_restart"]["checkpoint_after_replay"] = 18
    schema = json.loads((ROOT / "schemas/replay-evidence.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(contract, schema)


def test_generator_creates_source_only_notebooks(monkeypatch):
    import generate_book

    baseline = stage("baseline", "a" * 64, 100, 150, 10, 12)
    modified = stage("modified", "b" * 64, 122, 180, 12, 14)
    unchanged = deepcopy(modified)
    unchanged["mongo"]["document"]["kafka_offset"] = 14
    unchanged["spark_checkpoint_offset"] = 16
    unchanged["kafka_metadata_end_offset"] = 16
    restarted = deepcopy(modified)
    restarted["mongo"]["document"]["kafka_offset"] = 16
    restarted["spark_checkpoint_offset"] = 18
    restarted["kafka_metadata_end_offset"] = 18
    evidence = {
        "repository": {
            "commit_sha": "c" * 40,
            "raw_python_files": 74,
            "processed_python_files": 61,
        },
        "replay_file": {"file_id": "f" * 64},
        "stages": {
            "baseline": baseline,
            "modified": modified,
            "forced_unchanged": unchanged,
            "restart_replay": restarted,
        },
    }
    monkeypatch.setattr(generate_book, "load_evidence", lambda: evidence)
    generate_book.GENERATED.clear()
    generate_book.build_architecture()
    generate_book.build_task1()
    generate_book.build_task2()
    generate_book.build_task3()
    generate_book.build_task4()
    generate_book.build_task5()
    generate_book.build_task6()
    assert len(generate_book.GENERATED) == 7
    for notebook in generate_book.GENERATED.values():
        for cell in notebook.cells:
            if cell.cell_type != "code":
                continue
            compile(cell.source, "<notebook-cell>", "exec")
            assert cell.execution_count is None
            assert cell.outputs == []

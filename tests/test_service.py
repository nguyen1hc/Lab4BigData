import json
from pathlib import Path

import jsonschema

from cpg_parser.constants import TOPIC_EDGES, TOPIC_ERRORS, TOPIC_METADATA, TOPIC_NODES
from cpg_parser.manifest import Manifest
from cpg_parser.publisher import MemoryPublisher
from cpg_parser.service import ParserService


ROOT = Path(__file__).parents[1]


def make_service(repo, tmp_path):
    publisher = MemoryPublisher()
    manifest = Manifest(tmp_path / "manifest.sqlite")
    service = ParserService(repo, "fixture/sample", publisher, manifest)
    return service, publisher, manifest


def test_unchanged_file_skips_without_force(tmp_path):
    repo = ROOT / "tests" / "fixtures" / "sample_repo"
    service, publisher, manifest = make_service(repo, tmp_path)
    try:
        first = service.process("app.py")
        emitted = len(publisher.records)
        second = service.process("app.py")
        assert first[0].status == "processed"
        assert second[0].status == "skipped"
        assert len(publisher.records) == emitted
    finally:
        manifest.close()


def test_modified_file_emits_deletes_and_upserts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    file_path = repo / "app.py"
    file_path.write_text("def f(x):\n    y = x + 1\n    return y\n", encoding="utf-8")
    service, publisher, manifest = make_service(repo, tmp_path)
    try:
        service.process("app.py")
        publisher.records.clear()
        file_path.write_text("def f(x):\n    return x\n", encoding="utf-8")
        result = service.process("app.py")
        assert result[0].deleted_nodes > 0
        assert result[0].deleted_edges > 0
        assert any(value["op"] == "delete" for _, _, value in publisher.records)
        assert any(value["op"] == "upsert" for _, _, value in publisher.records)
    finally:
        manifest.close()


def test_events_match_json_schemas(tmp_path):
    repo = ROOT / "tests" / "fixtures" / "sample_repo"
    service, publisher, manifest = make_service(repo, tmp_path)
    try:
        service.process("app.py", force=True)
        schemas = {
            TOPIC_NODES: "node-event.schema.json",
            TOPIC_EDGES: "edge-event.schema.json",
            TOPIC_METADATA: "metadata-event.schema.json",
        }
        for topic, _, value in publisher.records:
            if topic not in schemas:
                continue
            schema = json.loads((ROOT / "schemas" / schemas[topic]).read_text(encoding="utf-8"))
            jsonschema.validate(value, schema)
    finally:
        manifest.close()


def test_syntax_error_is_routed_and_does_not_update_manifest(tmp_path):
    repo = ROOT / "tests" / "fixtures" / "invalid_repo"
    service, publisher, manifest = make_service(repo, tmp_path)
    try:
        result = service.process("broken.py")
        assert result[0].status == "error"
        assert {topic for topic, _, _ in publisher.records} == {TOPIC_ERRORS, TOPIC_METADATA}
        error = next(value for topic, _, value in publisher.records if topic == TOPIC_ERRORS)
        schema = json.loads((ROOT / "schemas" / "error-event.schema.json").read_text(encoding="utf-8"))
        jsonschema.validate(error, schema)
        assert manifest.content_hash(result[0].file_id) is None
    finally:
        manifest.close()


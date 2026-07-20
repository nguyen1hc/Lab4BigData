from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .discovery import discover_repo, infer_repo_id
from .manifest import Manifest
from .publisher import JsonlPublisher, KafkaPublisher
from .service import ParserService, transactional_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Incremental Python CPG Parser Service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="Discover and score Python files")
    discover.add_argument("--repo", required=True, type=Path)

    parse = subparsers.add_parser("parse", help="Parse files and emit Kafka-compatible events")
    parse.add_argument("--repo", required=True, type=Path)
    parse.add_argument("--repo-id", default=os.getenv("REPO_ID", ""))
    parse.add_argument("--file", help="Process one repository-relative .py file")
    parse.add_argument("--force", action="store_true", help="Replay even when content hash is unchanged")
    parse.add_argument("--state-db", type=Path, default=Path("state/parser.sqlite"))
    destination = parse.add_mutually_exclusive_group()
    destination.add_argument("--bootstrap-servers", default=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
    destination.add_argument("--output-dir", type=Path, help="Write JSONL evidence instead of Kafka")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "discover":
        print(json.dumps(discover_repo(args.repo).as_dict(), indent=2, ensure_ascii=False))
        return 0

    repo_id = args.repo_id or infer_repo_id(args.repo)
    publisher = (
        JsonlPublisher(args.output_dir)
        if args.output_dir
        else KafkaPublisher(args.bootstrap_servers, transactional_id(repo_id))
    )
    try:
        with Manifest(args.state_db) as manifest:
            service = ParserService(args.repo, repo_id, publisher, manifest)
            results = service.process(relative_file=args.file, force=args.force)
        summary = {
            "repo_id": repo_id,
            "processed": sum(result.status == "processed" for result in results),
            "skipped": sum(result.status == "skipped" for result in results),
            "errors": sum(result.status == "error" for result in results),
            "files": [result.as_dict() for result in results],
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 1 if summary["errors"] else 0
    finally:
        publisher.close()


from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Protocol

Record = tuple[str, str, dict[str, Any]]


class Publisher(Protocol):
    def publish_transaction(self, records: Iterable[Record]) -> None: ...

    def close(self) -> None: ...


class MemoryPublisher:
    def __init__(self) -> None:
        self.records: list[Record] = []

    def publish_transaction(self, records: Iterable[Record]) -> None:
        self.records.extend(records)

    def close(self) -> None:
        return


class JsonlPublisher:
    """Offline evidence sink with the same record contracts as Kafka."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def publish_transaction(self, records: Iterable[Record]) -> None:
        handles: dict[str, Any] = {}
        try:
            for topic, key, value in records:
                handle = handles.get(topic)
                if handle is None:
                    handle = (self.output_dir / f"{topic}.jsonl").open("a", encoding="utf-8")
                    handles[topic] = handle
                handle.write(json.dumps({"key": key, "value": value}, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            for handle in handles.values():
                handle.close()

    def close(self) -> None:
        return


class KafkaPublisher:
    def __init__(self, bootstrap_servers: str, transactional_id: str) -> None:
        try:
            from confluent_kafka import Producer
        except ImportError as exc:
            raise RuntimeError("Kafka publishing requires: pip install -e .[kafka]") from exc
        self.producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                "client.id": "lab04-cpg-parser",
                "transactional.id": transactional_id,
                "enable.idempotence": True,
                "acks": "all",
                "compression.type": "gzip",
                "linger.ms": 10,
                "broker.address.family": "v4",
            }
        )
        self.producer.init_transactions(30)

    def publish_transaction(self, records: Iterable[Record]) -> None:
        self.producer.begin_transaction()
        try:
            for topic, key, value in records:
                payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                while True:
                    try:
                        self.producer.produce(topic, key=key.encode("utf-8"), value=payload)
                        break
                    except BufferError:
                        self.producer.poll(0.5)
                self.producer.poll(0)
            self.producer.commit_transaction(60)
        except Exception:
            self.producer.abort_transaction(30)
            raise

    def close(self) -> None:
        self.producer.flush(10)


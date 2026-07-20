#!/usr/bin/env bash
set -euo pipefail

bootstrap="${KAFKA_BOOTSTRAP_SERVERS:-broker:29092}"

create_topic() {
  local topic="$1"
  local policy="$2"
  kafka-topics --bootstrap-server "$bootstrap" --create --if-not-exists \
    --topic "$topic" --partitions 1 --replication-factor 1 \
    --config "cleanup.policy=$policy"
}

create_topic cpg.nodes.v1 compact
create_topic cpg.edges.v1 compact
create_topic cpg.source-metadata.v1 compact
create_topic cpg.parser-errors.v1 delete
create_topic cpg.neo4j-dlq.v1 delete

kafka-configs --bootstrap-server "$bootstrap" --entity-type topics \
  --entity-name cpg.parser-errors.v1 --alter --add-config retention.ms=604800000
kafka-configs --bootstrap-server "$bootstrap" --entity-type topics \
  --entity-name cpg.neo4j-dlq.v1 --alter --add-config retention.ms=604800000

kafka-topics --bootstrap-server "$bootstrap" --list


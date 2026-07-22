---
title: Lab 04 - Spark Streaming
---

# Incremental Code Property Graph Streaming

This book documents a reproducible pipeline that parses a Python repository one
file at a time, publishes graph and metadata events through Kafka, writes graph
topology directly to Neo4j through Kafka Connect, and writes metadata to MongoDB
through Spark Structured Streaming.

The assigned source is
[`huggingface/optimum`](https://github.com/huggingface/optimum), locked at commit
`a6c775e11118d62712057bd3a8c5649898a5312d`. Its shallow clone is ignored; this
submission contains only group-authored code, schemas, executed notebooks, and
filtered evidence.

## Submission contract

- Public GitHub repository containing all team-written source code.
- Public GitHub Pages root URL for this book.
- No ZIP, PDF, or Word upload.
- Every task includes approach, real output, verification, and reflection.

## Current verified pipeline

- Parser, JSON contract, and evidence tests: **13 passed**.
- Neo4j sink connector and task: **RUNNING**.
- Discovery: **74 raw / 61 processed Python files**, 13,807 lines, **100% parseable**.
- Baseline graph: **62,375 nodes / 77,819 edges**, all IDs unique.
- Modified and forced-replay graph: **62,397 nodes / 77,849 edges**, all IDs unique.
- MongoDB: **61 documents / 61 distinct file IDs** after every replay.
- Spark checkpoint: before/after offsets are generated from the current replay evidence in Task 6.
- Neo4j dead-letter queue: **empty**.

# Lab 04 - Incremental CPG Streaming

This repository implements the complete Lab 04 pipeline:

```text
Python repository -> Parser Service -> Kafka nodes/edges -> Kafka Connect -> Neo4j
                                  \-> Kafka metadata -> Spark -> MongoDB
                                  \-> Kafka parser errors
```

The parser processes one Python file at a time, creates stable graph identifiers,
and reconciles deleted elements through a SQLite manifest. Graph topology never
passes through Spark.

## What is already verified

The assigned source is [huggingface/optimum](https://github.com/huggingface/optimum),
locked at commit `a6c775e11118d62712057bd3a8c5649898a5312d`. The shallow
clone lives in ignored `source-repo/`; third-party source is not duplicated in
the submission repository.

| Check | Result |
|---|---:|
| Unit, contract, and evidence tests | 13 passed |
| Python discovery | 74 raw / 61 processed / 13,807 lines |
| Python AST parse rate | 61/61 (100%) |
| Baseline Neo4j nodes | 62,375 unique / 62,375 total |
| Baseline Neo4j edges | 77,819 unique / 77,819 total |
| Modified/replayed nodes | 62,397 unique / 62,397 total |
| Modified/replayed edges | 77,849 unique / 77,849 total |
| MongoDB source documents | 61 IDs / 61 documents |
| Spark checkpoint restart | Captured dynamically in `evidence/runtime/verification.json` |
| Neo4j DLQ records | 0 |

## Requirements

- Docker Desktop with at least 16 GB assigned memory.
- Python 3.11+ and Git.
- Approximately 15 GB free disk space for the first image pull.

The stack pins Kafka/Connect 7.8.0, Neo4j 5.26.28 Community, Neo4j Kafka
Connector 5.5.0, Spark 3.5.7, MongoDB Spark Connector 10.7.0, and MongoDB 8.0.

## Quick start

1. Install the parser and Kafka client:

   ```powershell
   python -m pip install -e ".[kafka,test]"
   ```

   Create an ignored local environment file and replace its placeholder secret:

   ```powershell
   Copy-Item .env.example .env
   # Edit NEO4J_PASSWORD in .env before starting Docker.
   ```

2. Start the complete stack:

   ```powershell
   docker compose up -d --build
   docker compose ps
   ```

3. Clone and discover the locked Optimum repository:

   ```powershell
   git clone --depth 1 https://github.com/huggingface/optimum.git source-repo
   python -m cpg_parser discover --repo source-repo
   ```

   The reproducible equivalent is `./scripts/bootstrap-optimum.ps1`.

4. Parse all eligible files:

   ```powershell
   python -m cpg_parser parse `
     --repo source-repo `
     --repo-id huggingface/optimum `
     --state-db state/optimum.sqlite `
     --bootstrap-servers 127.0.0.1:9092
   ```

   The equivalent wrapper is `./scripts/parse-optimum.ps1`.

5. Verify the sinks:

   ```powershell
   ./scripts/verify-stack.ps1
   ./scripts/verify-optimum.ps1
   ```

Neo4j Browser is available at <http://localhost:7474>, Kafka Connect at
<http://localhost:8083>, and MongoDB at `mongodb://localhost:27017`.

## Parser commands

```powershell
# Raw/filtered file counts and parseability
python -m cpg_parser discover --repo PATH

# Incremental full-repository run
python -m cpg_parser parse --repo PATH --repo-id ORG/REPO

# Reprocess one modified file
python -m cpg_parser parse --repo PATH --repo-id ORG/REPO --file package/module.py

# Deliberately replay unchanged content
python -m cpg_parser parse --repo PATH --repo-id ORG/REPO --file package/module.py --force

# Produce inspectable JSONL without Kafka
python -m cpg_parser parse --repo PATH --repo-id ORG/REPO --output-dir evidence/runtime
```

On Windows, use `127.0.0.1:9092`; the producer also forces IPv4 to avoid a
Docker Desktop `localhost`/IPv6 mismatch.

## Event contracts

- `cpg.nodes.v1`: compacted, keyed by node ID.
- `cpg.edges.v1`: compacted, keyed by edge ID.
- `cpg.source-metadata.v1`: compacted, keyed by file ID.
- `cpg.parser-errors.v1`: seven-day retention, keyed by error ID.
- `cpg.neo4j-dlq.v1`: Kafka Connect failures; it must remain empty.

Every value contains `schema_version`, UTC `event_time`, `repo_id`, `file_id`,
`run_id`, `content_hash`, `event_id`, and `op`. JSON Schemas live under
`schemas/`.

Neo4j credentials are read from ignored `.env`. Kafka Connect resolves
`${env:LAB04_NEO4J_PASSWORD}` through its environment ConfigProvider; no real
credential is stored in the committed connector JSON or notebooks.

## Replay demonstration

1. Record Neo4j counts, MongoDB metadata, and Kafka offsets.
2. Create branch `lab04-replay-demo` inside ignored `source-repo/`.
3. Add `lab04_replay_probe`, containing an `if` and two returns, to
   `optimum/version.py` and retain `git diff` as evidence.
4. Parse only that file without deleting `state/` or the Spark checkpoint volume.
5. Run the same file with `--force`; graph and document counts must not change.
6. Confirm unique IDs, updated Mongo hash, and an empty DLQ.
7. Restart `spark-metadata`, replay once more, and show that its checkpoint
   resumes from the previous Kafka offset.

## Static-analysis limits

This is an educational file-local CPG rather than a replacement for Joern:

- CFG models standard statements, branches, loops, returns, raises, context
  managers, and conservative try/match flow.
- DFG uses lexical-scope reaching definitions and does not fully resolve
  aliases, attributes, containers, closures, or runtime mutation.
- Calls resolve same-file function names; dynamic dispatch and external calls
  point to stable `ExternalSymbol` nodes.

These limits should be stated explicitly in the Jupyter Book.

## Capture evidence and build the book

Run this sequence only after the full repository baseline has reached Neo4j and
MongoDB and the replay modification is present in `optimum/version.py`:

```powershell
python -m pytest
python scripts/capture_replay_evidence.py
python scripts/generate_book.py
python scripts/validate_notebooks.py
jupyter-book build --html --strict
```

`capture_replay_evidence.py` performs four measured stages: locked baseline,
modified file, forced unchanged replay, and Spark restart plus replay. It backs
up and restores the modified source bytes, polls both sinks, verifies unchanged
file metadata by digest, and writes `verification.json` only when every
assertion passes.

`generate_book.py` does not manufacture outputs or execution counts. It executes
every code cell with `nbclient`, records the evidence SHA-256 in notebook
metadata, and replaces the committed notebooks only after all seven execute
successfully. `validate_notebooks.py` rejects stale evidence, missing outputs,
tracebacks, absent PASS assertions, or screenshots that are too small.

The GitHub workflow validates the committed executed notebooks and publishes
`_build/html` through GitHub Pages; CI intentionally does not require Kafka or
database services.

## Troubleshooting

- Connector registration waits up to 60 seconds for both connector and task to
  become `RUNNING`; inspect `docker compose logs connect-init` if it times out.
- Kafka producer cannot reach `::1`: use `127.0.0.1:9092`.
- Spark starts slowly on first run: packages are cached in the `spark-ivy`
  volume; wait for the query before publishing metadata.
- Mongo document count grows after replay: confirm `_id=file_id`,
  `operationType=replace`, and `upsertDocument=true`.
- Neo4j duplicates appear: inspect the element key and confirm all Sink writes
  use `MERGE`; check `cpg.neo4j-dlq.v1`.

Do not submit a ZIP, PDF, or Word file. Moodle receives exactly the public root
URL of the published Jupyter Book.

See [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md) for the two manual UI
captures and the exact GitHub Pages publishing sequence.

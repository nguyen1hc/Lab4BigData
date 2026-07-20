$ErrorActionPreference = 'Stop'

Write-Host 'Kafka Connect status'
Invoke-RestMethod -Uri 'http://localhost:8083/connectors/cpg-neo4j-sink/status' | ConvertTo-Json -Depth 8

Write-Host 'Neo4j uniqueness and edge counts'
$neo4jPassword = (docker compose exec -T neo4j printenv LAB04_NEO4J_PASSWORD).Trim()
"MATCH (n:CPGNode) WITH count(n) AS nodes, count(DISTINCT n.id) AS unique_nodes MATCH ()-[r:CPG_EDGE]->() RETURN nodes, unique_nodes, count(r) AS edges, count(DISTINCT r.id) AS unique_edges" |
  docker compose exec -T neo4j cypher-shell -u neo4j -p $neo4jPassword

Write-Host 'Neo4j edges by kind'
"MATCH ()-[r:CPG_EDGE]->() RETURN r.kind AS kind, count(*) AS count ORDER BY kind" |
  docker compose exec -T neo4j cypher-shell -u neo4j -p $neo4jPassword

Write-Host 'MongoDB source metadata'
docker compose exec -T mongo mongosh --quiet --eval @"
db = db.getSiblingDB('lab04');
printjson({documents: db.source_metadata.countDocuments({})});
db.source_metadata.find({}, {_id:1, path:1, content_hash:1, kafka_offset:1}).limit(5).forEach(printjson);
"@

Write-Host 'Neo4j DLQ count'
docker compose exec -T broker kafka-get-offsets `
  --bootstrap-server broker:29092 --topic cpg.neo4j-dlq.v1

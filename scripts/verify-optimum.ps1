$ErrorActionPreference = "Stop"

docker compose exec -T connect curl -fsS `
    http://localhost:8083/connectors/cpg-neo4j-sink/status

$neo4jPassword = (docker compose exec -T neo4j printenv LAB04_NEO4J_PASSWORD).Trim()
"MATCH (n:CPGNode) WITH count(n) AS nodes, count(DISTINCT n.id) AS unique_nodes MATCH ()-[r:CPG_EDGE]->() RETURN nodes, unique_nodes, count(r) AS edges, count(DISTINCT r.id) AS unique_edges" |
    docker compose exec -T neo4j cypher-shell -u neo4j -p $neo4jPassword

docker compose exec -T mongo mongosh --quiet --eval `
    "db=db.getSiblingDB('lab04'); printjson({documents:db.source_metadata.countDocuments({repo_id:'huggingface/optimum'}), distinct_files:db.source_metadata.distinct('_id',{repo_id:'huggingface/optimum'}).length})"

docker compose exec -T broker kafka-get-offsets --bootstrap-server broker:29092 `
    --topic cpg.neo4j-dlq.v1

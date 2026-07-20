#!/usr/bin/env sh
set -eu

connect_url="${CONNECT_URL:-http://connect:8083}"
name="cpg-neo4j-sink"
config="/config/neo4j-sink-config.json"

if curl -fsS "$connect_url/connectors/$name" >/dev/null 2>&1; then
  curl -fsS -X PUT -H 'Content-Type: application/json' --data-binary "@$config" \
    "$connect_url/connectors/$name/config"
else
  { printf '{"name":"%s","config":' "$name"; cat "$config"; printf '}'; } | \
    curl -fsS -X POST -H 'Content-Type: application/json' --data-binary @- \
      "$connect_url/connectors"
fi

printf '\n'
curl -fsS "$connect_url/connectors/$name/status"
printf '\n'


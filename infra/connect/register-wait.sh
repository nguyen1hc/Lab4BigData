#!/usr/bin/env sh
set -u

connect_url="${CONNECT_URL:-http://connect:8083}"
name="cpg-neo4j-sink"

# Connector creation and task startup are asynchronous. The original register
# call may observe a short-lived 404 immediately after a successful POST.
/config/register.sh || true

attempt=1
while [ "$attempt" -le 30 ]; do
  status="$(curl -fsS "$connect_url/connectors/$name/status" 2>/dev/null || true)"
  if [ -n "$status" ] && \
     printf '%s' "$status" | grep -q '"connector":{"state":"RUNNING"' && \
     printf '%s' "$status" | grep -q '"tasks":\[{"id":0,"state":"RUNNING"'; then
    printf '%s\n' "$status"
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 2
done

printf '%s\n' "$status"
exit 1

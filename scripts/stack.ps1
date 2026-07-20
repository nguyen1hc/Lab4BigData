param(
    [ValidateSet('up', 'status', 'logs', 'stop')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'

switch ($Action) {
    'up' {
        docker compose up -d --build
        docker compose ps
    }
    'status' {
        docker compose ps
        try {
            Invoke-RestMethod -Uri 'http://localhost:8083/connectors/cpg-neo4j-sink/status' | ConvertTo-Json -Depth 8
        } catch {
            Write-Warning "Kafka Connect status is not ready: $($_.Exception.Message)"
        }
    }
    'logs' {
        docker compose logs --tail 200 connect spark-metadata
    }
    'stop' {
        docker compose stop
    }
}


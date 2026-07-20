$ErrorActionPreference = "Stop"

python -m cpg_parser parse `
    --repo source-repo `
    --repo-id huggingface/optimum `
    --state-db state/optimum.sqlite `
    --bootstrap-servers 127.0.0.1:9092

param(
    [string]$Destination = "source-repo"
)

$ErrorActionPreference = "Stop"
$repositoryUrl = "https://github.com/huggingface/optimum.git"
$lockedCommit = "a6c775e11118d62712057bd3a8c5649898a5312d"

if (-not (Test-Path -LiteralPath $Destination)) {
    git clone --depth 1 $repositoryUrl $Destination
}

$actual = (git -C $Destination rev-parse HEAD).Trim()
if ($actual -ne $lockedCommit) {
    git -C $Destination fetch --depth 1 origin $lockedCommit
    git -C $Destination checkout --detach $lockedCommit
}

python -m cpg_parser discover --repo $Destination

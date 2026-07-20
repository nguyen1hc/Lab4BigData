param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$arguments = @(
    '-m', 'cpg_parser', 'parse',
    '--repo', 'tests/fixtures/sample_repo',
    '--repo-id', 'fixture/sample',
    '--state-db', 'state/fixture-parser.sqlite',
    '--bootstrap-servers', 'localhost:9092'
)
if ($Force) {
    $arguments += '--force'
}
python @arguments


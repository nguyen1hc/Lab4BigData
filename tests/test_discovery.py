from pathlib import Path

from cpg_parser.discovery import discover_repo


def test_discovery_reports_raw_and_filtered_counts():
    repo = Path(__file__).parent / "fixtures" / "sample_repo"
    report = discover_repo(repo)
    assert report.raw_python_files == 3
    assert report.processed_python_files == 1
    assert report.excluded_python_files == 2
    assert report.parse_success_rate == 1.0
    assert report.has_branch and report.has_loop and report.has_call
    assert report.files == ["app.py"]


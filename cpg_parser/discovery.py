from __future__ import annotations

import ast
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


EXCLUDED_PARTS = {".git", ".venv", "venv", ".tox", "build", "dist", "site-packages", "__pycache__", "tests"}


@dataclass(slots=True)
class DiscoveryReport:
    repo_path: str
    repo_url: str
    commit_sha: str
    raw_python_files: int
    processed_python_files: int
    excluded_python_files: int
    total_lines: int
    parseable_files: int
    parse_success_rate: float
    has_branch: bool
    has_loop: bool
    has_call: bool
    files: list[str]
    excluded_files: list[str]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def git_metadata(repo: Path) -> tuple[str, str]:
    return _git(repo, "remote", "get-url", "origin"), _git(repo, "rev-parse", "HEAD")


def should_exclude(repo: Path, file_path: Path) -> bool:
    relative = file_path.relative_to(repo)
    lowered_parts = {part.lower() for part in relative.parts[:-1]}
    name = relative.name.lower()
    return bool(
        lowered_parts & EXCLUDED_PARTS
        or name == "setup.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_pb2.py")
        or name.endswith("_pb2_grpc.py")
        or "generated" in lowered_parts
    )


def discover_files(repo: Path) -> tuple[list[Path], list[Path]]:
    repo = repo.resolve()
    raw = sorted(path for path in repo.rglob("*.py") if path.is_file())
    included = [path for path in raw if not should_exclude(repo, path)]
    excluded = [path for path in raw if path not in included]
    return included, excluded


def discover_repo(repo: Path) -> DiscoveryReport:
    repo = repo.resolve()
    if not repo.is_dir():
        raise ValueError(f"Repository path does not exist: {repo}")
    included, excluded = discover_files(repo)
    total_lines = 0
    parseable = 0
    has_branch = False
    has_loop = False
    has_call = False
    for path in included:
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source = path.read_text(encoding="utf-8", errors="replace")
        total_lines += len(source.splitlines())
        try:
            tree = ast.parse(source, filename=str(path), type_comments=True)
            parseable += 1
            has_branch = has_branch or any(isinstance(node, (ast.If, ast.Match)) for node in ast.walk(tree))
            has_loop = has_loop or any(isinstance(node, (ast.For, ast.AsyncFor, ast.While)) for node in ast.walk(tree))
            has_call = has_call or any(isinstance(node, ast.Call) for node in ast.walk(tree))
        except SyntaxError:
            pass
    repo_url, commit_sha = git_metadata(repo)
    return DiscoveryReport(
        repo_path=str(repo),
        repo_url=repo_url,
        commit_sha=commit_sha,
        raw_python_files=len(included) + len(excluded),
        processed_python_files=len(included),
        excluded_python_files=len(excluded),
        total_lines=total_lines,
        parseable_files=parseable,
        parse_success_rate=round(parseable / len(included), 4) if included else 0.0,
        has_branch=has_branch,
        has_loop=has_loop,
        has_call=has_call,
        files=[path.relative_to(repo).as_posix() for path in included],
        excluded_files=[path.relative_to(repo).as_posix() for path in excluded],
    )


def infer_repo_id(repo: Path) -> str:
    repo_url, _ = git_metadata(repo.resolve())
    if repo_url:
        normalized = repo_url.removesuffix(".git").replace("\\", "/")
        if "github.com" in normalized:
            suffix = normalized.split("github.com", 1)[1].lstrip("/: ")
            if suffix:
                return suffix
    return repo.resolve().name


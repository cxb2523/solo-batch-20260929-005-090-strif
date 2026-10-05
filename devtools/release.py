"""
Reentrant release state machine for strif.

This script is the single entry point for release version resolution:

  1. Explicit ``--version X.Y.Z`` argument.
  2. The git tag on HEAD, with any leading ``v`` stripped.
  3. A local version carrying the commit short hash, e.g. ``0.2.25+g1a2b3c4``.

It verifies that ``pyproject.toml``'s ``project.version``,
``src/strif/__init__.py``'s ``__version__`` and the package version recorded in
``uv.lock`` are all equal, that any git tag matches that version, and that the
working tree has no uncommitted changes. It then runs the lint, type-check and
test/coverage gates and only builds the sdist/wheel once every blocking gate is
green.

The script is strictly read-only with respect to the source tree: it reports
errors but never rewrites project files. Re-running it with the same inputs is
idempotent -- existing ``dist/`` artifacts for the same version are reused, not
rebuilt, and a failed run never leaves anything in ``dist/``.

Gate strictness is configured in ``pyproject.toml`` under ``[tool.release]``:

  fail_on_type_errors = true   # basedpyright errors block the release
  fail_on_coverage = true      # low coverage blocks the release
  coverage_min_percent = 80    # coverage threshold
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib
from rich import reconfigure
from rich.console import Console
from rich.table import Table

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT_PY = ROOT / "src" / "strif" / "__init__.py"
UV_LOCK = ROOT / "uv.lock"
DIST_DIR = ROOT / "dist"
PACKAGE = "strif"

reconfigure(emoji=False)
console = Console()

STATUS_MARK = {
    "ok": "[green]✓[/green]",
    "fail": "[red]✗[/red]",
    "warn": "[yellow]⚠[/yellow]",
    "skip": "[dim]–[/dim]",
}
STATUS_WORD = {"ok": "PASS", "fail": "FAIL", "warn": "WARN", "skip": "SKIP"}


@dataclass
class Row:
    check: str
    value: str
    status: str
    note: str = ""


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)

    def add(self, check: str, value: str, status: str, note: str = "") -> None:
        self.rows.append(Row(check, value, status, note))

    @property
    def failed(self) -> bool:
        return any(row.status == "fail" for row in self.rows)

    def render(self, title: str) -> None:
        table = Table(title=title, show_lines=False)
        table.add_column("Check", style="bold")
        table.add_column("Value")
        table.add_column("Result", justify="center")
        table.add_column("Note")
        for row in self.rows:
            mark = f"{STATUS_MARK[row.status]} {STATUS_WORD[row.status]}"
            table.add_row(row.check, row.value, mark, row.note)
        console.print()
        console.print(table)
        console.print()


def run_quiet(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)


def git(*args: str) -> str:
    result = run_quiet(["git", *args])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def read_pyproject() -> dict[str, Any]:
    with PYPROJECT.open("rb") as file:
        return tomllib.load(file)


def read_init_version() -> str | None:
    text = INIT_PY.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return match.group(1) if match else None


def read_lock_version() -> str | None:
    with UV_LOCK.open("rb") as file:
        data = tomllib.load(file)
    for package in data.get("package", []):
        if package.get("name") == PACKAGE:
            return package.get("version")
    return None


def release_config(pyproject: dict[str, Any]) -> dict[str, Any]:
    config = pyproject.get("tool", {}).get("release", {})
    return {
        "fail_on_type_errors": bool(config.get("fail_on_type_errors", True)),
        "fail_on_coverage": bool(config.get("fail_on_coverage", True)),
        "coverage_min_percent": float(config.get("coverage_min_percent", 80)),
    }


def strip_v(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def base_version(version: str) -> str:
    return version.split("+", 1)[0]


def resolve_version(
    explicit: str | None, tags: list[str], project_version: str | None
) -> tuple[str, str]:
    if explicit:
        return explicit, "--version argument"
    if tags:
        tag = sorted(tags)[-1]
        return strip_v(tag), f"git tag {tag}"
    short_hash = git("rev-parse", "--short", "HEAD")
    base = base_version(project_version) if project_version else "0.0.0"
    return f"{base}+g{short_hash}", "local (commit short hash)"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_gate(report: Report, name: str, cmd: list[str], blocking: bool = True) -> bool:
    console.print(f"[bold cyan]>> {' '.join(cmd)}[/bold cyan]")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode == 0:
        report.add(name, "exit 0", "ok")
        return True
    if blocking:
        report.add(name, f"exit {result.returncode}", "fail")
    else:
        report.add(name, f"exit {result.returncode}", "warn", "not blocking per [tool.release]")
    return False


def run_test_gate(report: Report, min_coverage: float, enforce_coverage: bool) -> bool:
    with tempfile.TemporaryDirectory(prefix="strif-cov-") as tmp:
        env = dict(os.environ, COVERAGE_FILE=str(Path(tmp) / "coverage.dat"))
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--cov=strif",
            "--cov-report=term",
            "-q",
        ]
        console.print(f"[bold cyan]>> {' '.join(cmd)}[/bold cyan]")
        result = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True)
        output = result.stdout + result.stderr
        console.print(output)

    ok = True
    if result.returncode == 0:
        report.add("gate: pytest", "exit 0", "ok")
    else:
        report.add("gate: pytest", f"exit {result.returncode}", "fail")
        ok = False

    coverage = parse_coverage(output)
    if coverage is None:
        report.add("gate: coverage", "unknown", "fail", "could not parse coverage TOTAL")
        return False
    if coverage >= min_coverage:
        report.add("gate: coverage", f"{coverage:.1f}% >= {min_coverage:.0f}%", "ok")
    elif enforce_coverage:
        report.add("gate: coverage", f"{coverage:.1f}% < {min_coverage:.0f}%", "fail")
        ok = False
    else:
        report.add(
            "gate: coverage",
            f"{coverage:.1f}% < {min_coverage:.0f}%",
            "warn",
            "not blocking per [tool.release]",
        )
    return ok


def parse_coverage(output: str) -> float | None:
    match = re.search(r"^TOTAL\b.*?(\d+(?:\.\d+)?)%\s*$", output, re.M)
    return float(match.group(1)) if match else None


def build_artifacts(report: Report, version: str) -> bool:
    wheel_name = f"{PACKAGE}-{version.replace('-', '_')}-py3-none-any.whl"
    sdist_name = f"{PACKAGE}-{version}.tar.gz"
    artifacts = [DIST_DIR / sdist_name, DIST_DIR / wheel_name]

    if all(path.exists() for path in artifacts):
        note = "reused existing artifacts (idempotent re-run)"
    else:
        tmp = Path(tempfile.mkdtemp(prefix="strif-build-"))
        try:
            console.print("[bold cyan]>> uv build --out-dir <tmp>[/bold cyan]")
            result = subprocess.run(["uv", "build", "--out-dir", str(tmp)], cwd=ROOT)
            if result.returncode != 0:
                report.add(
                    "build: sdist/wheel",
                    f"exit {result.returncode}",
                    "fail",
                    "dist/ left untouched",
                )
                return False
            built = sorted(tmp.glob("*.tar.gz")) + sorted(tmp.glob("*.whl"))
            DIST_DIR.mkdir(exist_ok=True)
            for path in built:
                shutil.move(str(path), DIST_DIR / path.name)
            note = "built into dist/"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    ok = True
    for path in artifacts:
        if path.exists():
            report.add(f"artifact: {path.name}", f"sha256 {sha256_of(path)}", "ok", note)
        else:
            report.add(f"artifact: {path.name}", "missing", "fail")
            ok = False
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Reentrant release state machine for strif.")
    parser.add_argument(
        "--version", dest="version", help="explicit release version (highest priority)"
    )
    args = parser.parse_args()

    report = Report()
    pyproject = read_pyproject()
    config = release_config(pyproject)

    project_version = pyproject.get("project", {}).get("version")
    init_version = read_init_version()
    lock_version = read_lock_version()

    report.add(
        "version: pyproject.toml project.version",
        project_version or "missing",
        "ok" if project_version else "fail",
    )
    report.add(
        "version: src/strif/__init__.py __version__",
        init_version or "missing",
        "ok" if init_version else "fail",
    )
    report.add(
        "version: uv.lock strif package",
        lock_version or "missing",
        "ok" if lock_version else "fail",
    )

    versions_equal = project_version is not None and project_version == init_version == lock_version
    report.add(
        "check: three versions equal",
        " == ".join(str(v) for v in (project_version, init_version, lock_version)),
        "ok" if versions_equal else "fail",
    )

    try:
        tags = [tag for tag in git("tag", "--points-at", "HEAD").splitlines() if tag]
        dirty = bool(git("status", "--porcelain"))
        git_error = None
    except RuntimeError as error:
        tags, dirty, git_error = [], True, str(error)

    resolved, source = resolve_version(args.version, tags, project_version)
    report.add("version: resolved", resolved, "ok", f"source: {source}")

    if git_error:
        report.add("check: git state", "unavailable", "fail", git_error)
    else:
        tag_versions = [strip_v(tag) for tag in tags]
        tags_match = all(tv == resolved and tv == project_version for tv in tag_versions)
        report.add(
            "check: git tag matches version",
            ", ".join(tags) if tags else "(no tag on HEAD)",
            "ok" if tags_match else "fail",
        )
        report.add(
            "check: working tree clean",
            "clean" if not dirty else "uncommitted changes",
            "ok" if not dirty else "fail",
        )

    resolved_matches_project = project_version is not None and base_version(
        resolved
    ) == base_version(project_version)
    report.add(
        "check: resolved version matches project.version",
        f"{resolved} vs {project_version}",
        "ok" if resolved_matches_project else "fail",
    )

    if report.failed:
        for name in (
            "gate: lint",
            "gate: typecheck",
            "gate: pytest",
            "gate: coverage",
            "build: sdist/wheel",
        ):
            report.add(name, "not run", "skip", "precondition failed")
    else:
        run_gate(
            report, "gate: lint", [sys.executable, "devtools/lint.py", "--check", "--skip-types"]
        )
        run_gate(
            report,
            "gate: typecheck",
            ["basedpyright", "--stats", "src", "tests", "devtools"],
            blocking=config["fail_on_type_errors"],
        )
        run_test_gate(report, config["coverage_min_percent"], config["fail_on_coverage"])

        if not report.failed:
            build_artifacts(report, base_version(resolved))
        else:
            report.add("build: sdist/wheel", "not run", "skip", "gate failed; dist/ left untouched")

    report.render("Release Check Summary")

    if report.failed:
        console.print("[bold red]✗ Release check FAILED[/bold red]")
        return 1
    console.print("[bold green]✓ Release check PASSED[/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

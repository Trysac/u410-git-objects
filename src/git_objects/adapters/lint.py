"""Commit message linting adapter using trysac-commit-msg-lint."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from git_objects.models import GitCommit
    from git_objects.store import ObjectStore

try:
    from commit_msg_lint import Config, lint
except ImportError as err:
    raise ImportError(
        "The 'lint' extra is required to use this module. "
        "Install it via 'pip install trysac-git-objects[lint]'."
    ) from err


def lint_commits(
    store: ObjectStore,
    start_rev: str = "HEAD",
    config: Config | None = None,
) -> Iterator[tuple[GitCommit, list[Any]]]:
    """Lint commit messages traversed from start_rev."""
    cfg = config if config is not None else Config()
    for commit in store.walk_commits(start_rev):
        findings = lint(commit.message, config=cfg)
        yield commit, findings

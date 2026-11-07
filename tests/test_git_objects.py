"""Behavior tests for the public API of git_objects."""

from __future__ import annotations

import pytest

import git_objects


def test_normalize_trims_and_lowercases() -> None:
    assert git_objects.normalize("  Hello World ") == "hello world"


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_normalize_rejects_blank_input(value: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        git_objects.normalize(value)


def test_version_is_semver() -> None:
    parts = git_objects.__version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)

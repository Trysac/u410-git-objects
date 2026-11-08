"""Pure Python zero-dependency Git object store reader and writer."""

from __future__ import annotations

from git_objects.errors import (
    CorruptObjectError,
    DeltaDepthExceededError,
    GitObjectsError,
    InvalidRefError,
    ObjectNotFoundError,
    ObjectSizeLimitError,
    PackParseError,
    RefUpdateError,
)
from git_objects.models import GitCommit, GitObject, GitTag, GitTreeEntry, TreeChange
from git_objects.store import ObjectStore

__version__ = "0.1.0"

__all__ = [
    "CorruptObjectError",
    "DeltaDepthExceededError",
    "GitCommit",
    "GitObject",
    "GitObjectsError",
    "GitTag",
    "GitTreeEntry",
    "InvalidRefError",
    "ObjectNotFoundError",
    "ObjectSizeLimitError",
    "ObjectStore",
    "PackParseError",
    "RefUpdateError",
    "TreeChange",
    "__version__",
    "normalize",
]


def normalize(text: str) -> str:
    """Normalize a string by stripping surrounding whitespace and converting to lowercase."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("text must not be empty")
    return stripped.lower()

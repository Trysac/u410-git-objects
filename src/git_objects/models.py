"""Data models for Git objects, commits, trees and changes."""

from __future__ import annotations

from typing import NamedTuple


class GitObject(NamedTuple):
    """Raw Git object representation."""

    oid: str
    type: str
    data: bytes


class GitTreeEntry(NamedTuple):
    """An entry within a tree object."""

    mode: str
    name: str
    oid: str


class GitCommit(NamedTuple):
    """Parsed Git commit object."""

    oid: str
    tree: str
    parents: list[str]
    author: str
    author_time: int
    author_tz: str
    committer: str
    committer_time: int
    committer_tz: str
    message: str
    raw: bytes


class GitTag(NamedTuple):
    """Parsed Git annotated tag object."""

    oid: str
    object_oid: str
    object_type: str
    tag_name: str
    tagger: str
    message: str


class TreeChange(NamedTuple):
    """A change detected between two trees."""

    change_type: str  # 'added', 'deleted', 'modified', 'renamed'
    path: str
    old_path: str | None
    old_oid: str | None
    new_oid: str | None
    old_mode: str | None
    new_mode: str | None
    similarity: float | None = None

"""Parsers for Git commits, trees and tags."""

from __future__ import annotations

from git_objects.errors import CorruptObjectError
from git_objects.models import GitCommit, GitTag, GitTreeEntry


def parse_tree(data: bytes) -> list[GitTreeEntry]:
    """Parse the binary content of a Git tree object."""
    entries: list[GitTreeEntry] = []
    idx = 0
    total_len = len(data)

    while idx < total_len:
        # Read mode (space-delimited)
        space_idx = data.find(b" ", idx)
        if space_idx == -1:
            raise CorruptObjectError("Corrupt tree entry: missing mode separator")
        mode_str = data[idx:space_idx].decode("ascii", errors="replace")

        # Read name (null-terminated)
        null_idx = data.find(b"\x00", space_idx + 1)
        if null_idx == -1:
            raise CorruptObjectError("Corrupt tree entry: missing name terminator")
        name = data[space_idx + 1 : null_idx].decode("utf-8", errors="replace")

        # Read 20-byte SHA-1
        sha_start = null_idx + 1
        sha_end = sha_start + 20
        if sha_end > total_len:
            raise CorruptObjectError("Corrupt tree entry: truncated SHA")
        oid = data[sha_start:sha_end].hex()

        entries.append(GitTreeEntry(mode=mode_str, name=name, oid=oid))
        idx = sha_end

    return entries


def serialize_tree(entries: list[GitTreeEntry]) -> bytes:
    """Serialize a list of GitTreeEntry tuples into canonical Git tree format."""

    # Git sorts tree entries with a trailing slash if it's a tree
    def sort_key(e: GitTreeEntry) -> bytes:
        suffix = b"/" if e.mode.startswith("4") or e.mode == "040000" else b""
        return e.name.encode("utf-8") + suffix

    sorted_entries = sorted(entries, key=sort_key)
    out = bytearray()
    for e in sorted_entries:
        # Mode should not have leading zero if 5 digits, but standard git uses '100644' or '40000'
        mode_bytes = e.mode.encode("ascii")
        name_bytes = e.name.encode("utf-8")
        sha_bytes = bytes.fromhex(e.oid)
        out.extend(mode_bytes + b" " + name_bytes + b"\x00" + sha_bytes)
    return bytes(out)


def parse_commit(oid: str, data: bytes) -> GitCommit:
    """Parse raw commit object bytes."""
    lines = data.split(b"\n")
    tree = ""
    parents: list[str] = []
    author_line = ""
    committer_line = ""
    body_start_idx = 0

    for i, line in enumerate(lines):
        if line == b"":
            body_start_idx = i + 1
            break
        if line.startswith(b"tree "):
            tree = line[5:].decode("ascii", errors="replace")
        elif line.startswith(b"parent "):
            parents.append(line[7:].decode("ascii", errors="replace"))
        elif line.startswith(b"author "):
            author_line = line[7:].decode("utf-8", errors="replace")
        elif line.startswith(b"committer "):
            committer_line = line[10:].decode("utf-8", errors="replace")

    message = b"\n".join(lines[body_start_idx:]).decode("utf-8", errors="replace")

    def parse_ident(ident_str: str) -> tuple[str, int, str]:
        # Format: Name <email> timestamp timezone
        parts = ident_str.rsplit(" ", 2)
        if len(parts) == 3:
            name_email, t_str, tz = parts
            try:
                time_val = int(t_str)
            except ValueError:
                time_val = 0
            return name_email, time_val, tz
        return ident_str, 0, "+0000"

    author_name, author_time, author_tz = parse_ident(author_line)
    committer_name, committer_time, committer_tz = parse_ident(committer_line)

    return GitCommit(
        oid=oid,
        tree=tree,
        parents=parents,
        author=author_name,
        author_time=author_time,
        author_tz=author_tz,
        committer=committer_name,
        committer_time=committer_time,
        committer_tz=committer_tz,
        message=message,
        raw=data,
    )


def serialize_commit(
    tree: str,
    parents: list[str],
    message: str,
    author: str,
    committer: str,
    author_time: int = 0,
    author_tz: str = "+0000",
    committer_time: int = 0,
    committer_tz: str = "+0000",
) -> bytes:
    """Serialize commit fields into Git commit object format."""
    out = bytearray()
    out.extend(f"tree {tree}\n".encode("ascii"))
    for p in parents:
        out.extend(f"parent {p}\n".encode("ascii"))

    # Author line
    if "<" in author and ">" in author and author.endswith(">"):
        out.extend(f"author {author} {author_time} {author_tz}\n".encode())
    elif "<" in author:
        out.extend(f"author {author}\n".encode())
    else:
        out.extend(f"author {author} {author_time} {author_tz}\n".encode())

    # Committer line
    if "<" in committer and ">" in committer and committer.endswith(">"):
        out.extend(f"committer {committer} {committer_time} {committer_tz}\n".encode())
    elif "<" in committer:
        out.extend(f"committer {committer}\n".encode())
    else:
        out.extend(f"committer {committer} {committer_time} {committer_tz}\n".encode())

    out.extend(b"\n")
    out.extend(message.encode("utf-8"))
    return bytes(out)


def parse_tag(oid: str, data: bytes) -> GitTag:
    """Parse raw annotated tag object bytes."""
    lines = data.split(b"\n")
    target_oid = ""
    target_type = ""
    tag_name = ""
    tagger = ""
    body_start_idx = 0

    for i, line in enumerate(lines):
        if line == b"":
            body_start_idx = i + 1
            break
        if line.startswith(b"object "):
            target_oid = line[7:].decode("ascii", errors="replace")
        elif line.startswith(b"type "):
            target_type = line[5:].decode("ascii", errors="replace")
        elif line.startswith(b"tag "):
            tag_name = line[4:].decode("utf-8", errors="replace")
        elif line.startswith(b"tagger "):
            tagger = line[7:].decode("utf-8", errors="replace")

    message = b"\n".join(lines[body_start_idx:]).decode("utf-8", errors="replace")
    return GitTag(
        oid=oid,
        object_oid=target_oid,
        object_type=target_type,
        tag_name=tag_name,
        tagger=tagger,
        message=message,
    )
